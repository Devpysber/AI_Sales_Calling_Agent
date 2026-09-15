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
from fastapi import APIRouter, HTTPException, Request, Response, WebSocket
from plivo import plivoxml

from app.core.config import settings
from app.core.logging import get_logger
from app.services import agent, agents, call_session, tts
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
    "goodbye": {"en": "Thanks for your time. Have a great day!",
                "hi": "आपके समय के लिए धन्यवाद। आपका दिन शुभ हो!"},
    "error": {"en": "Sorry, I'm having a technical issue. Our team will call you back shortly. Goodbye.",
              "hi": "माफ़ कीजिए, अभी तकनीकी समस्या है। हमारी टीम आपको जल्द ही कॉल करेगी। नमस्ते।"},
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
    return "hi" if (session.get("language") or "").startswith("hi") else "en"


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


def dial_human(r, persona: dict, caller_id: str | None, session: dict | None = None):
    """<Dial> the agent's transfer number; if nobody picks up the caller hears a short message instead of silence."""
    number = agents.phone_digits(persona.get("transfer_number", ""))
    dial = plivoxml.DialElement(caller_id=caller_id or None, timeout=30,
                                action=f"{settings.base_url}/api/plivo/transfer-done" + (f"?cid={session['call_id']}" if session else ""),
                                method="POST", redirect=True)
    dial.add(plivoxml.NumberElement(number))
    r.add(dial)
    return r


def inbound_route(persona: dict, agent_id: int, caller: str | None = None) -> str:
    """ai | forward | message for an incoming call right now."""
    from app.services.call_service import within_calling_hours
    has_number = bool("".join(c for c in persona.get("transfer_number", "") if c.isdigit()))
    open_now = within_calling_hours(agents.get_automation(agent_id))
    mode = persona.get("inbound_mode", "ai") if open_now else persona.get("after_hours_mode", "ai")
    if mode == "forward" and (not has_number or (caller and agents.phone_digits(caller) == agents.phone_digits(persona.get("transfer_number")))):
        return "ai"  # no number, or the team's own phone is calling: forwarding would ring the caller back
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
    if not p.get("sid"):
        route = inbound_route(persona, agent_id, p.get("From"))
        if route == "forward":
            await asyncio.to_thread(calls.mark_transferred, session["call_id"], "Forwarded to " + persona["transfer_number"], "forward")
            return xml(dial_human(r, persona, p.get("To"), session))
        if route == "message":
            key = "hi" if (session.get("language") or "").startswith("hi") else "en"
            text = persona.get("after_hours_message") or (
                "We are closed right now. Please call again during business hours. Thank you." if key == "en"
                else "अभी हमारा ऑफिस बंद है। कृपया ऑफिस समय में दोबारा कॉल करें। धन्यवाद।")
            call_session.add_turn(session, "assistant", text)
            call_session.save(session)
            hangup_with(r, session, text)
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


@router.post("/ring")
async def ring(request: Request):
    p = await verified(request)
    if p.get("cid"):
        await asyncio.to_thread(CallService().on_ring, int(p["cid"]))
    return {"ok": True}


@router.post("/hangup")
async def hangup(request: Request):
    p = await verified(request)
    call_id = p.get("cid")
    if not call_id and p.get("CallUUID"):
        from sqlalchemy import select
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
    if p.get("cid") and p.get("RecordUrl"):
        await asyncio.to_thread(CallService().on_recording, int(p["cid"]), p["RecordUrl"])
    return {"ok": True}


@router.post("/transfer")
async def transfer(request: Request):
    """XML for a live call handed to a human (from the AI or a supervisor)."""
    p = await verified(request)
    session = call_session.get(p.get("sid")) or {}
    agent_id = session_agent(session) if session else None
    r = plivoxml.ResponseElement()
    persona = agents.get_profile(agent_id) if agent_id else {}
    if not "".join(c for c in persona.get("transfer_number", "") if c.isdigit()):
        r.add(plivoxml.HangupElement())
        return xml(r)
    caller = agents.caller_id(agent_id)
    return xml(dial_human(r, persona, caller, session if session.get("call_id") else None))


@router.post("/transfer-done")
async def transfer_done(request: Request):
    """After the transferred leg ends: nothing more to say if it was answered, a short apology if not."""
    p = await verified(request)
    r = plivoxml.ResponseElement()
    if (p.get("DialStatus") or "").lower() not in ("completed", "answer", "answered"):
        cid = p.get("cid")
        if cid:
            await asyncio.to_thread(CallService().mark_transferred, int(cid), f"Transfer not answered ({p.get('DialStatus')})", None)
        r.add(plivoxml.SpeakElement("Sorry, our team is not available right now. We will call you back soon.", voice="WOMAN", language="en-IN"))
    r.add(plivoxml.HangupElement())
    return xml(r)
