"""
Plivo voice webhooks (stateless: all call state is in the shared store).

answer  -> greeting + <GetInput speech>
input   -> start AI turn in background, short wait, then reply or hold
wait    -> poll for the AI turn result (silent 1s waits, no hold tone)
silence -> re-prompt, then politely end
ring / hangup / recording -> lifecycle updates
"""

import asyncio

import plivo.utils
from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket
from plivo import plivoxml
from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.services import agent, agents, call_session, events, tts
from app.services.call_service import CallService, session_agent

log = get_logger(__name__)
router = APIRouter(prefix="/api/plivo", tags=["plivo-webhooks"], include_in_schema=False)

MAX_SILENT_PROMPTS = 2
FIRST_WAIT_SECONDS = 2.0   # Plivo gives the action URL ~3s before "Error Reaching Action URL"
POLL_WAIT_SECONDS  = 2.0
HINTS = "yes,no,haan,nahi,hello,price,pricing,demo,meeting,interested,busy,callback,later,email,whatsapp"

PROMPTS = {
    "repeat": {"en": "Sorry, I didn't catch that. Could you say that again?",
               "hi": "माफ़ कीजिए, मैं सुन नहीं पाया। क्या आप दोबारा बोल सकते हैं?"},
    # Used when the caller DID speak but we failed to produce a reply: blaming their audio ("I didn't
    # catch that") for our own failure is what made the agent feel deaf.
    "continue": {"en": "Sorry, go on — I'm listening.",
                 "hi": "जी बोलिए, मैं सुन रहा हूँ।"},
    "goodbye": {"en": "Thanks for your time. Have a great day!",
                "hi": "आपके समय के लिए धन्यवाद। आपका दिन शुभ हो!"},
    # Nothing here names a fault: the caller only needs to know a person is coming, not why.
    "error": {"en": "Sorry about that. Someone from our team will call you right back. Thank you.",
              "hi": "माफ़ कीजिए। हमारी team आपको अभी call करेगी। धन्यवाद।"},
    "handover": {"en": "One moment — let me put you through to someone from our team.",
                 "hi": "एक मिनट, मैं आपको अपनी team से जोड़ता हूँ।"},
    # The model offered to connect someone but no line is configured. Saying nothing left the caller on a
    # silent line after "connecting you now"; this at least promises the call back we can actually make.
    "no_transfer": {"en": "I can't put you through right now, but I'll have someone from the team call you back shortly.",
                    "hi": "अभी मैं आपको connect नहीं कर पा रहा, पर हमारी team आपको जल्दी call कर लेगी।"},
}


async def verified(request: Request) -> dict:
    form = dict(await request.form())
    params = {**dict(request.query_params), **form}
    if settings.plivo_validate_signature:
        signature = request.headers.get("X-Plivo-Signature-V3")
        nonce = request.headers.get("X-Plivo-Signature-V3-Nonce")
        url = settings.base_url + request.url.path + (f"?{request.url.query}" if request.url.query else "")
        if not (signature and nonce and plivo.utils.validate_v3_signature(
                request.method, url, nonce, settings.plivo_auth_token, signature, form)):
            log.warning("Rejected unsigned Plivo webhook %s", request.url.path)
            raise HTTPException(403, "Invalid signature")
    return params


def xml(r: plivoxml.ResponseElement) -> Response:
    return Response(r.to_string(), media_type="application/xml")


def lang_key(session: dict) -> str:
    lang = session.get("language")
    if not lang:
        persona = agents.get_profile(session_agent(session)) if session_agent(session) else {}
        lang = persona.get("default_language", "en-IN")
    return "hi" if (lang or "").startswith("hi") else "en"


def asr_language(session: dict) -> str:
    # Plivo speech recognition does not support hi-IN: sending it makes Plivo reject the XML
    # and drop the call. en-IN transcribes Hindi/Hinglish speech as roman text, which the LLM
    # understands; the agent still replies (and speaks) in Hindi.
    return "en-IN"


def synthesize_to_id(text: str, session: dict) -> str | None:
    """Run TTS and return an audio_id, or None on failure."""
    persona = agents.get_profile(session_agent(session))
    language = tts.detect_language(text, session.get("language") or "en-IN")
    try:
        return tts.cached_audio_id(text, language, persona["voice_speaker"])
    except Exception as e:
        log.warning("TTS failed for text %r: %s", text[:60], e)
        return None


def speak(parent, session: dict, text: str, *, audio_id: str | None = None):
    """Add a <Play> (preferred) or <Speak> (fallback) to `parent`."""
    aid = audio_id or synthesize_to_id(text, session)
    if aid:
        parent.add(plivoxml.PlayElement(tts.audio_url(aid)))
    else:
        # TTS unavailable – Plivo's <Speak> keeps the call alive
        parent.add(plivoxml.SpeakElement(text, language="en-IN"))


def listen(r, session: dict, *, text: str | None = None, audio_id: str | None = None):
    """Add <GetInput> (with optional prompt) to the response.

    When the caller says nothing, Plivo does NOT call the action URL: it
    moves on to the next element, and with none left it hangs up ("End Of
    XML Instructions"). So a second GetInput with a "could you repeat"
    prompt follows, then a polite goodbye. No <Redirect> is used (Plivo
    rejected a trailing <Redirect> here with "Invalid Redirect XML").
    Blocking (may run TTS): call from a worker thread.
    """
    sid, cid = session["id"], session.get("call_id")

    def get_input():
        # speechModel is left at Plivo's default: "phone_call" returned no transcripts for en-IN.
        return plivoxml.GetInputElement(
            action=f"{settings.base_url}/api/plivo/input?sid={sid}&cid={cid}", method="POST",
            input_type="speech", language=asr_language(session),
            execution_timeout=10, speech_end_timeout=2, hints=HINTS, redirect=True, log=True)

    gi = get_input()
    if audio_id:
        gi.add(plivoxml.PlayElement(tts.audio_url(audio_id)))
    elif text:
        speak(gi, session, text)
    r.add(gi)

    retry = get_input()
    speak(retry, session, PROMPTS["repeat"][lang_key(session)])
    r.add(retry)
    hangup_with(r, session, PROMPTS["goodbye"][lang_key(session)])


def hangup_with(r, session: dict, text: str):
    speak(r, session, text)
    r.add(plivoxml.HangupElement())


TRANSFER_LINES = {"en": "Please hold, I am connecting you to our team.", "hi": "कृपया लाइन पर बने रहिए, मैं आपको हमारी टीम से जोड़ रहा हूँ।"}


def transfer_numbers(persona: dict, agent_id: int | None = None) -> list[str]:
    """The agent's own transfer list, or the member who created the workspace when it has none."""
    from app.services import team_service
    return team_service.transfer_digits(persona, agent_id)


def transfer_line(persona: dict, agent_id: int | None = None) -> str:
    """What was dialled, for the call record and the missed-transfer email."""
    return ", ".join("+" + n for n in transfer_numbers(persona, agent_id))


def transfer_targets(persona: dict, session: dict | None = None, agent_id: int | None = None) -> list[str]:
    """The numbers to ring, in order, for this call.

    The agent's own transfer number rings first (it contains all its team members).
    A colleague calling in from one of those lines is dropped from the list: ringing the number
    someone is speaking on reaches their own busy line, never a person. /transfer-done indexes
    into this same list, so both sides must build it identically.
    """
    numbers = transfer_numbers(persona, agent_id if agent_id is not None else session_agent(session or {}))
    speaking_from = agents.phone_digits(((session or {}).get("lead") or {}).get("phone") or "")
    if speaking_from:
        numbers = [n for n in numbers if n != speaking_from]
    return numbers


def dial_human(r, persona: dict, caller_id: str | None, session: dict | None = None, idx: int = 0,
               agent_id: int | None = None) -> bool:
    """<Dial> the next transfer target. Returns False when there is nobody left to ring,
    so the caller gets a spoken fallback instead of silence followed by a dead line."""
    numbers = transfer_targets(persona, session, agent_id)
    if idx >= len(numbers):
        return False

    number = numbers[idx]
    action_url = f"{settings.base_url}/api/plivo/transfer-done?idx={idx + 1}"
    if session:
        action_url += f"&cid={session['call_id']}&sid={session['id']}"
        
    dial = plivoxml.DialElement(caller_id=caller_id or None, timeout=30,
                                action=action_url, method="POST", redirect=True)
    dial.add(plivoxml.NumberElement(number))
    r.add(dial)
    return True


def inbound_route(persona: dict, agent_id: int, caller: str | None = None, internal: bool = False) -> str:
    """ai | forward | message for an incoming call right now."""
    from app.services.call_service import within_calling_hours
    numbers = transfer_numbers(persona, agent_id)

    # A paused agent is on hold for customers: the AI does not take the call. A human number takes it
    # if one is set, otherwise the caller leaves a message. The team can still ring in and test it.
    if not internal and agents.is_paused(agent_id):
        return "forward" if numbers else "message"

    open_now = within_calling_hours(agents.get_automation(agent_id))
    mode = persona.get("inbound_mode", "ai") if open_now else persona.get("after_hours_mode", "ai")

    # If forwarding to human, don't forward if the team is calling their own agent number to test/use it.
    if mode == "forward":
        if not numbers:
            return "ai"
        # A team member calling in is the only person on that line: forwarding would ring
        # their own busy number, so the AI takes the call instead.
        if caller and agents.phone_digits(caller) in numbers:
            return "ai"


    return mode if mode in ("ai", "forward", "message") else "ai"


@router.post("/answer")
async def answer(request: Request):
    p = await verified(request)
    calls = CallService()
    if p.get("sid") and p.get("cid"):
        session = await asyncio.to_thread(calls.on_answer, int(p["cid"]), p.get("CallUUID"))
    else:
        session = await asyncio.to_thread(calls.create_inbound, p.get("From", ""), p.get("To", ""), p.get("CallUUID", ""))

    r = plivoxml.ResponseElement()
    if not session:
        r.add(plivoxml.HangupElement())
        return xml(r)

    agent_id = session_agent(session)
    persona = agents.get_profile(agent_id)
    if p.get("sid") and persona.get("detect_voicemail") and str(p.get("Machine") or "").lower() == "true":
        # Plivo's answering-machine detection (asked for only when the persona switch is on) said a
        # machine picked up: hang up before the greeting costs anything; the call ends as a no-answer.
        call_session.update(session["id"], voicemail=True)
        events.record("call.voicemail", "Answering machine detected by Plivo, hung up", call_id=session.get("call_id"), actor="system")
        r.add(plivoxml.HangupElement())
        return xml(r)
    if not p.get("sid"):
        internal = (session.get("lead") or {}).get("call_purpose") in ("admin", "team")
        route = inbound_route(persona, agent_id, p.get("From"), internal=internal)
        # Nobody left to ring (e.g. the only transfer number is the line calling in): the AI
        # answers instead of the caller hearing a hold line and then dead air.
        if route == "forward" and not transfer_targets(persona, session):
            route = "ai"
        if route == "forward":
            await asyncio.to_thread(calls.mark_transferred, session["call_id"],
                                    "Forwarded to " + calls.transfer_label(transfer_line(persona, agent_id)),
                                    "forward", transfer_line(persona, agent_id))
            
            key = lang_key(session)
            text = TRANSFER_LINES[key]
            call_session.add_turn(session, "assistant", text)
            call_session.save(session)
            audio_id = await asyncio.to_thread(synthesize_to_id, text, session)
            speak(r, session, text, audio_id=audio_id)
            
            if persona["record_calls"]:
                r.add(plivoxml.RecordElement(action=f"{settings.base_url}/api/plivo/recording?cid={session['call_id']}",
                                             record_session=True, redirect=False, max_length=3600, file_format="mp3"))
            dial_human(r, persona, p.get("To"), session)
            return xml(r)
        if route == "message":
            key = lang_key(session)
            text = persona.get("after_hours_message") or (
                "We are closed right now. Please leave a message after the beep." if key == "en"
                else "हम अभी बंद हैं। कृपया बीप के बाद अपना संदेश छोड़ें।")
            call_session.add_turn(session, "assistant", text)
            call_session.save(session)
            audio_id = await asyncio.to_thread(synthesize_to_id, text, session)
            speak(r, session, text, audio_id=audio_id)
            r.add(plivoxml.RecordElement(action=f"{settings.base_url}/api/plivo/voicemail?cid={session['call_id']}",
                                         method="POST", max_length=120, play_beep=True))
            return xml(r)
    if persona["record_calls"]:
        r.add(plivoxml.RecordElement(action=f"{settings.base_url}/api/plivo/recording?cid={session['call_id']}",
                                     record_session=True, redirect=False, max_length=3600, file_format="mp3"))

    greeting_text = agent.greeting(agent_id, session.get("lead") or {}, session.get("language") or "en-IN")
    call_session.add_turn(session, "assistant", greeting_text)
    call_session.save(session)

    if settings.voice_mode == "stream":
        # Real-time pipeline: the greeting and the whole conversation run over the audio stream.
        stream_url = settings.base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + f"/api/plivo/stream?sid={session['id']}"
        r.add(plivoxml.StreamElement(stream_url, bidirectional=True, keepCallAlive=True, contentType="audio/x-mulaw;rate=8000"))
        return xml(r)

    # Pre-synthesise greeting audio BEFORE building XML so that a slow or
    # failed TTS call cannot leave the response element half-built.
    greeting_audio_id = await asyncio.to_thread(synthesize_to_id, greeting_text, session)
    await asyncio.to_thread(listen, r, session, text=greeting_text if not greeting_audio_id else None, audio_id=greeting_audio_id)
    return xml(r)


@router.post("/input")
async def speech_input(request: Request):
    p = await verified(request)
    session = call_session.get(p.get("sid"))
    r = plivoxml.ResponseElement()
    if not session:
        r.add(plivoxml.HangupElement())
        return xml(r)

    text = (p.get("Speech") or "").strip()
    if not text:
        r.add(plivoxml.RedirectElement(
            f"{settings.base_url}/api/plivo/silence?sid={session['id']}&cid={session.get('call_id')}", method="POST"))
        return xml(r)

    log.info("Customer said (%s): %s", session["id"][:8], text)
    CallService().begin_turn(session["id"], text)
    return xml(await _reply_or_hold(session["id"], FIRST_WAIT_SECONDS))


@router.post("/wait")
async def wait(request: Request):
    p = await verified(request)
    return xml(await _reply_or_hold(p.get("sid"), POLL_WAIT_SECONDS))


async def _reply_or_hold(session_id: str, max_wait: float) -> plivoxml.ResponseElement:
    r = plivoxml.ResponseElement()
    waited, session = 0.0, None
    while waited <= max_wait:
        session = call_session.get(session_id)
        if not session or (session.get("pending") or {}).get("state") != "processing":
            break
        await asyncio.sleep(0.15)
        waited += 0.15

    if not session:
        r.add(plivoxml.HangupElement())
        return r

    pending = session.get("pending") or {}
    if pending.get("state") == "ready":
        call_session.update(session_id, pending=None)
        if pending.get("end_call"):
            r.add(plivoxml.PlayElement(tts.audio_url(pending["audio_id"])))
            r.add(plivoxml.HangupElement())
        else:
            await asyncio.to_thread(listen, r, session, audio_id=pending["audio_id"])
    elif pending.get("state") == "error":
        call_session.update(session_id, pending=None)
        agent_id = session_agent(session)
        persona = agents.get_profile(agent_id) if agent_id else {}
        # Put the caller through to a person rather than ending the call on our own failure.
        handed_over = False
        if transfer_targets(persona, session):
            speak(r, session, PROMPTS["handover"][lang_key(session)])
            handed_over = dial_human(r, persona, agents.caller_id(agent_id), session)
            if handed_over and session.get("call_id"):
                await asyncio.to_thread(CallService().mark_transferred, int(session["call_id"]),
                                        "Handed to team after an AI error", "error",
                                        transfer_line(persona, agent_id))
        if not handed_over:
            await asyncio.to_thread(hangup_with, r, session, PROMPTS["error"][lang_key(session)])
    else:
        # Still thinking: short silence (no hold tone), then poll again
        r.add(plivoxml.WaitElement(length=1))
        r.add(plivoxml.RedirectElement(
            f"{settings.base_url}/api/plivo/wait?sid={session_id}&cid={session.get('call_id')}", method="POST"))
    return r


@router.post("/silence")
async def silence(request: Request):
    """Called by Plivo's GetInput redirect=True when the caller is silent."""
    p = await verified(request)
    session = call_session.get(p.get("sid"))
    r = plivoxml.ResponseElement()
    if not session:
        r.add(plivoxml.HangupElement())
        return xml(r)

    # If the AI is still thinking (e.g. a slow first response), wait for it
    # instead of treating the silence as the caller being quiet.
    pending = session.get("pending") or {}
    if pending.get("state") == "processing":
        return xml(await _reply_or_hold(session["id"], FIRST_WAIT_SECONDS))

    session["silent_prompts"] = session.get("silent_prompts", 0) + 1
    call_session.save(session)
    if session["silent_prompts"] > MAX_SILENT_PROMPTS:
        text = PROMPTS["goodbye"][lang_key(session)]
        audio_id = await asyncio.to_thread(synthesize_to_id, text, session)
        speak(r, session, text, audio_id=audio_id)
        r.add(plivoxml.HangupElement())
    else:
        text = PROMPTS["repeat"][lang_key(session)]
        audio_id = await asyncio.to_thread(synthesize_to_id, text, session)
        await asyncio.to_thread(listen, r, session, text=text if not audio_id else None, audio_id=audio_id)
    return xml(r)


@router.websocket("/stream")
async def stream(ws: WebSocket, sid: str = ""):
    """Plivo bidirectional audio stream. The session id is an unguessable token issued in /answer."""
    from app.services.voice_stream import CallStream
    await ws.accept()
    if not call_session.get(sid):
        await ws.close(code=4404, reason="unknown session")
        return
    await CallStream(ws, sid).run()


def int_or_none(value) -> int | None:
    """Webhook params are strings and can be absent or the literal "None": never let int() raise here."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


@router.post("/ring")
async def ring(request: Request):
    p = await verified(request)
    cid = int_or_none(p.get("cid"))
    if cid:
        await asyncio.to_thread(CallService().on_ring, cid)
    return {"ok": True}


@router.post("/hangup")
async def hangup(request: Request):
    p = await verified(request)
    call_id = int_or_none(p.get("cid"))
    if not call_id and p.get("CallUUID"):
        from app.core.database import get_db
        from app.models.call import Call
        with get_db() as db:
            call_id = db.scalar(select(Call.id).where(Call.call_uuid == p["CallUUID"]))
    if call_id:
        try:
            duration = int(float(p.get("Duration") or p.get("BillDuration") or 0))
        except ValueError:
            duration = 0
        await asyncio.to_thread(CallService().on_hangup, int(call_id), p.get("CallStatus", ""), duration,
                                p.get("HangupCauseName") or p.get("HangupCause"), p.get("CallUUID"))
    return {"ok": True}


@router.post("/recording")
async def recording(request: Request):
    p = await verified(request)
    cid = int_or_none(p.get("cid"))
    if cid and p.get("RecordUrl"):
        await asyncio.to_thread(CallService().on_recording, cid, p["RecordUrl"])
    return {"ok": True}


@router.post("/transfer")
async def transfer(request: Request):
    """XML for a live call handed to a human (from the AI or a supervisor)."""
    p = await verified(request)
    session = call_session.get(p.get("sid")) or {}
    agent_id = session_agent(session) if session else None
    r = plivoxml.ResponseElement()
    persona = agents.get_profile(agent_id) if agent_id else {}
    dial_session = session if session.get("call_id") else None
    if not dial_human(r, persona, agents.caller_id(agent_id), dial_session):
        # No reachable team number: say something instead of cutting the caller off mid-sentence.
        if session:
            await asyncio.to_thread(hangup_with, r, session, PROMPTS["error"][lang_key(session)])
        else:
            r.add(plivoxml.HangupElement())
        return xml(r)
    # The caller of PlivoService.transfer (voice_stream / supervisor) records the timeline event,
    # so nothing is logged here — a second event would show the same handover twice.
    return xml(r)


# Nobody on the team picked up: the caller hears that the request has been passed on and a call back is coming
# (never "I sent an email"). With the AI fallback on, the agent stays on the line for anything else.
FALLBACK_LINES = {"en": "The team is on another call right now, but I have passed your request to them and they will call you back shortly. Anything else I can help with meanwhile?",
                  "hi": "टीम अभी दूसरी call पर है, पर मैंने आपकी बात उन तक पहुँचा दी है, वो आपको जल्दी call करेंगे। इस बीच और कुछ पूछना है?"}
FALLBACK_CLOSE = {"en": "The team is on another call right now, but I have passed your request to them and they will call you back shortly. Thank you.",
                  "hi": "टीम अभी दूसरी call पर है, पर मैंने आपकी बात उन तक पहुँचा दी है, वो आपको जल्दी call करेंगे। धन्यवाद।"}


def _notify_missed(session: dict, persona: dict, status: str):
    """Tell the team about a forwarded call nobody answered: who, their number, what they wanted, the call id."""
    from app.services.notification_service import notify_team, send_email, email_sent

    if not persona.get("notify_missed_calls", True):
        return
    lead = session.get("lead") or {}
    who = lead.get("name") or lead.get("phone") or "Unknown caller"
    request_text = (session.get("transfer_reason") or "").strip()
    lines = [f"{who} called {persona.get('company_name')} and asked for the team; the transfer to {transfer_line(persona, session_agent(session))} was not answered ({status}).",
             "", f"Phone: {lead.get('phone') or '—'}", f"Request: {request_text or 'not captured — see the transcript'}",
             f"Call ID: {session.get('call_id') or '—'}", f"Agent: {persona.get('agent_name')} · {persona.get('company_name')}"]
    for key, label in (("company", "Company"), ("status", "Stage"), ("summary", "Last summary"), ("meeting_at", "Meeting")):
        if lead.get(key):
            lines.append(f"{label}: {lead[key]}")
    lines += ["", "The caller was told the team will call back; a callback is on the lead."]
    subject = f"Callback needed: {who} - {persona.get('company_name')}"
    delivered = set(notify_team(subject, "\n".join(lines), lead_id=session.get("lead_id"), agent_id=session.get("agent_id")))
    # This agent's own team list (per-agent members may not be in the shared accounts list).
    for m in persona.get("team_members") or []:
        address = (m.get("email") or "").strip().lower() if isinstance(m, dict) else ""
        if address and address not in delivered:
            # A team member, not the customer: filed as "system" so the Email Centre does not show it
            # as AI mail to the lead whose name is on the row.
            if email_sent(send_email(address, subject, "\n".join(lines), lead_id=session.get("lead_id"), agent_id=session.get("agent_id"), actor="system")):
                delivered.add(address)


def _book_callback_after_missed_transfer(session: dict, persona: dict) -> None:
    """A callback task on the lead with the caller's request, and the call marked callback_requested."""
    from app.core.database import get_db
    from app.models.call import Call
    from app.services.call_service import _next_callback_slot
    from app.services.crm_service import CRMService
    lead_id, agent_id = session.get("lead_id"), session.get("agent_id")
    request_text = (session.get("transfer_reason") or "").strip()
    if lead_id and agent_id:
        crm = CRMService(agent_id)
        current = crm.get(lead_id) or {}
        note = f"[missed transfer] Asked for the team: {request_text or 'see transcript'}"
        notes = ((current.get("notes") or "") + "\n" + note).strip()[-2000:]
        when = _next_callback_slot(agents.get_automation(agent_id))
        crm.update(lead_id, {"callback_at": when, "call_status": "Pending", "notes": notes}, actor="system",
                   event_type="callback.scheduled", title=f"Callback {when}: team did not pick up the transfer")
    if session.get("call_id"):
        with get_db() as db:
            call = db.get(Call, session["call_id"])
            if call is not None:
                call.outcome = "callback_requested"


@router.post("/transfer-done")
async def transfer_done(request: Request):
    """After the transferred leg ends. Answered: done. Not answered: the AI takes the call back (or apologises), and the admin is emailed."""
    p = await verified(request)
    r = plivoxml.ResponseElement()
    if (p.get("DialStatus") or "").lower() in ("completed", "answer", "answered"):
        r.add(plivoxml.HangupElement())
        return xml(r)

    status = p.get("DialStatus") or "no answer"
    session = call_session.get(p.get("sid") or "") or {}
    agent_id = session_agent(session) if session else None
    persona = agents.get_profile(agent_id) if agent_id else {}
    
    try:
        idx = int(p.get("idx") or request.query_params.get("idx") or 1)
    except ValueError:
        idx = 1

    # Ring the next team member. transfer_targets() is the same list dial_human() indexed into,
    # so idx still points at the number after the one that just failed.
    if dial_human(r, persona, agents.caller_id(agent_id), session, idx=idx):
        return xml(r)

    cid = int_or_none(p.get("cid") or session.get("call_id"))
    if cid:
        await asyncio.to_thread(CallService().mark_transferred, cid, f"Team did not answer ({status})", None)
    if session:
        # Off the reply path: the caller hears the fallback line while the callback and the email are made.
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _book_callback_after_missed_transfer, session, persona)
        loop.run_in_executor(None, _notify_missed, session, persona, status)

    key = lang_key(session)
    if persona.get("forward_fallback", "ai") == "ai":
        # Fall back to AI
        greeting = FALLBACK_LINES[key] if session else "Sorry, our team is busy."
        if session:
            call_session.add_turn(session, "assistant", greeting)
            call_session.save(session)
            if settings.voice_mode == "stream":
                stream_url = settings.base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + f"/api/plivo/stream?sid={session['id']}"
                r.add(plivoxml.StreamElement(stream_url, bidirectional=True, keepCallAlive=True, contentType="audio/x-mulaw;rate=8000"))
                return xml(r)
            audio_id = await asyncio.to_thread(synthesize_to_id, greeting, session)
            await asyncio.to_thread(listen, r, session, text=greeting if not audio_id else None, audio_id=audio_id)
            return xml(r)

    # AI fallback off: the caller is told the request is with the team and the call ends; the callback and
    # the email above carry the request, so no voicemail beep is needed.
    text = FALLBACK_CLOSE[key]
    if session:
        call_session.add_turn(session, "assistant", text)
        call_session.save(session)
        audio_id = await asyncio.to_thread(synthesize_to_id, text, session)
        speak(r, session, text, audio_id=audio_id)
        r.add(plivoxml.HangupElement())
    else:
        r.add(plivoxml.SpeakElement(text, voice="WOMAN", language="en-IN"))
        r.add(plivoxml.HangupElement())
    return xml(r)

def _attach_voicemail(call_id: int, url: str):
    """Store the voicemail recording and put it in the transcript so the summary sees it."""
    from app.core.database import get_db
    from app.models.call import Call

    CallService().on_recording(call_id, url)
    with get_db() as db:
        session_id = db.scalar(select(Call.session_id).where(Call.id == call_id))
    session = call_session.get(session_id) if session_id else None
    if session:
        call_session.add_turn(session, "user", f"[Left a voicemail: {url}]")
        call_session.save(session)


@router.post("/voicemail")
async def voicemail(request: Request):
    p = await verified(request)
    cid = int_or_none(p.get("cid") or request.query_params.get("cid"))
    url = p.get("RecordUrl")
    if cid and url:
        await asyncio.to_thread(_attach_voicemail, cid, url)
    # Plivo plays nothing after <Record>, so the call would sit in silence until it times out.
    r = plivoxml.ResponseElement()
    r.add(plivoxml.HangupElement())
    return xml(r)


@router.post("/team-alert")
async def plivo_team_alert(request: Request, sid: str = Query(None)):
    p = await verified(request)
    session = call_session.get(p.get("sid") or sid)
    if not session:
        return xml(plivoxml.ResponseElement())

    text = f"Urgent alert from AI Sales Agent. A customer needs immediate attention. They said: {session.get('team_action')}. Press any key to accept, or hang up."

    r = plivoxml.ResponseElement()
    audio_id = await asyncio.to_thread(synthesize_to_id, text, session)
    # The keypress is what "accept" means, so the message plays *inside* <GetDigits>: a plain
    # <Speak> followed by a redirect fired the callback whether or not anybody accepted.
    gd = plivoxml.GetDigitsElement(action=f"{settings.base_url}/api/plivo/team-alert-done?sid={session['id']}",
                                  method="POST", num_digits=1, timeout=10, redirect=True)
    speak(gd, session, text, audio_id=audio_id)
    r.add(gd)
    r.add(plivoxml.HangupElement())
    return xml(r)


def _dial_customer_back(session: dict):
    from app.services.plivo_service import PlivoService

    lead = session.get("lead") or {}
    # The courtesy call back reaches the same customer as the call it apologises for: their language,
    # not English, which had this one line arriving in a language they may not speak.
    backcall_session = call_session.create(agent_id=session["agent_id"], lead_id=session["lead_id"],
                                           lead=lead, language=lead.get("language") or "en-IN")
    # No call row exists for this short courtesy call: passing the original call id would let its
    # hangup webhook overwrite the real call's status and duration.
    PlivoService().dial(session["customer_phone"], backcall_session["id"], None, max_minutes=2,
                        endpoint="customer-alert")


@router.post("/team-alert-done")
async def plivo_team_alert_done(request: Request, sid: str = Query(None)):
    p = await verified(request)
    session = call_session.get(p.get("sid") or sid)
    r = plivoxml.ResponseElement()
    if not session:
        return xml(r)

    accepted = bool((p.get("Digits") or "").strip())
    if accepted and session.get("customer_phone"):
        try:
            await asyncio.to_thread(_dial_customer_back, session)
        except Exception as e:  # noqa: BLE001 - never fail the team member's call over the callback
            log.error("Failed to dial customer back call: %s", e)
    elif not accepted:
        log.info("Team alert for session %s was not accepted", session["id"][:8])

    r.add(plivoxml.HangupElement())
    return xml(r)


@router.post("/customer-alert")
async def plivo_customer_alert(request: Request, sid: str = Query(None)):
    p = await verified(request)
    session = call_session.get(p.get("sid") or sid)
    if not session:
        return xml(plivoxml.ResponseElement())


    text = "Hi, this is the AI assistant calling back. I have informed the team about your urgent request, and they are acting on it now. Thank you."
    r = plivoxml.ResponseElement()
    audio_id = await asyncio.to_thread(synthesize_to_id, text, session)
    speak(r, session, text, audio_id=audio_id)
    r.add(plivoxml.HangupElement())
    return xml(r)
