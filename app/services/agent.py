"""
AI Sales Agent.

One turn:  customer text -> retrieve knowledge (RAG) -> LLM with persona,
lead context, conversation history and strict output schema -> reply +
structured CRM signals.

End of call: transcript -> LLM summary -> qualification, outcome,
sentiment, meeting / follow-up extraction.
"""

import re
import time
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.logging import get_logger
from app.services import agents, llm, rag
from app.services.tts import LANGUAGES

log = get_logger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
MAX_HISTORY_TURNS = 14          # every turn resends history: fewer turns = fewer billed tokens
LIVE_EMBED_TIMEOUT = 0.3        # a live turn waits this long for a query embedding that was not prefetched
COMPACT_AFTER_TURNS = 20        # a call this long gets its older turns folded into one summary line
COMPACT_EVERY_TURNS = 6         # and re-folded this often after that
KNOWLEDGE_CHARS = 600           # per retrieved passage in the prompt
MIN_HISTORY_TURNS = 6           # the prompt budget never trims the window below this many turns
LIVE_MAX_TOKENS = 160           # a live turn: 30 Devanagari words can cost 100+ tokens, the cap is a safety net
TOOL_MAX_TOKENS = 400           # a tool-call round: send_email arguments (subject + body) must not be cut mid-JSON
MAX_TOOL_ROUNDS = 3             # tool_call -> result -> tool_call loops before the model is made to speak
FAREWELL = re.compile(r"(bye|take care|good ?night|see you|have a (?:good|great|nice)|thank(?:s| you)|"
                      r"धन्यवाद|शुक्रिया|अलविदा|नमस्ते|शुभ|मिलते हैं|रखता हूँ|रखती हूँ|अच्छा दिन)", re.I)

# Voices that speak as a woman: Hindi verb forms follow the voice, not a default male template.
FEMALE_SPEAKERS = {"priya", "neha", "pooja", "simran", "kavya", "ishita", "shreya", "tanya", "shruti", "suhani",
                   "kavitha", "rupali", "ritu", "roopa", "anushka", "manisha", "vidya", "arya", "maya", "meera"}
# Masculine -> feminine first-person forms used in our Hindi templates and default greetings.
_FEMININE = [("बोल रहा हूँ", "बोल रही हूँ"), ("सुन नहीं पाया", "सुन नहीं पाई"), ("जोड़ता हूँ", "जोड़ती हूँ"),
             ("सकता हूँ", "सकती हूँ"), ("करता हूँ", "करती हूँ"), ("देता हूँ", "देती हूँ"), ("रहा हूँ", "रही हूँ"),
             ("समझ गया", "समझ गई")]


def gender(persona: dict) -> str:
    """'female' or 'male', from the TTS voice the persona speaks with."""
    return "female" if str(persona.get("voice_speaker") or "").strip().lower() in FEMALE_SPEAKERS else "male"


def genderize(text: str, persona: dict) -> str:
    """Hindi first-person verb forms matching the agent's voice (templates are written in the masculine)."""
    if gender(persona) != "female":
        return text
    for masc, fem in _FEMININE:
        text = text.replace(masc, fem)
    return text


def prompt_char_budget() -> int:
    """Characters of prompt (system + history) a turn may carry; 0 disables the budget."""
    return int(getattr(settings, "llm_prompt_char_budget", 12000) or 0)

INTENTS = ["greeting", "question", "interested", "pricing", "objection", "meeting", "callback",
           "not_interested", "wrong_person", "do_not_call", "end_call", "other"]

TURN_SCHEMA = """{
  "reply": "what you say next (spoken, 1-2 short sentences, customer's language)",
  "language": "BCP-47 code of the reply, e.g. en-IN or hi-IN",
  "intent": "one of: %s",
  "qualification": "Hot | Warm | Cold | Unknown",
  "end_call": false,
  "crm_update": {"meeting_at": "YYYY-MM-DD HH:MM or empty", "email": ""}
}""" % " | ".join(INTENTS)


COLLECT_LABELS = {"name": "their name", "requirement": "what they are looking for", "city": "their city",
                  "company": "their company or business", "email": "their email address", "budget": "their budget",
                  "timeline": "when they need it", "callback_time": "the best time to call them back",
                  "source": "how they heard about us"}
COLLECT_FIELDS = {"requirement": "requirements", "callback_time": "callback_at"}


def call_goal(lead: dict, purpose: str | None) -> str | None:
    """Instruction for a purpose-specific call (from the lead page's next best action)."""
    if purpose == "confirm_meeting" and lead.get("meeting_at"):
        return (f"Confirm the booked meeting on {lead['meeting_at']} (IST). Ask if that time still works; "
                "if not, agree a new day and time and repeat it back. Do not pitch again. Keep the call under a minute.")
    if purpose == "inbound_new":
        wanted = [COLLECT_LABELS[f] for f in (lead.get("collect") or ["name", "requirement"]) if f in COLLECT_LABELS
                  and not lead.get(COLLECT_FIELDS.get(f, f))]
        if not wanted:
            return "The caller's details are complete. Help them from the knowledge base and move to the call to action."
        return ("A new caller not yet in our CRM. Before going deep, you MUST collect these details naturally, ONE question per turn, "
                f"in this order: Language Preference (ask which language they prefer to speak in), {', '.join(wanted)}. "
                "Acknowledge each answer briefly. If they ask something first, answer it very briefly, then immediately ask the next detail. "
                "You must not skip asking for their Name. Once collected, help them and move to the primary call to action.")
    if purpose in ("team", "admin"):
        who = (lead.get("team_name") or "").strip()
        return ("This caller is one of OUR OWN COLLEAGUES" + (f", {who}" if who else "") + ", not a customer. They are "
                "ringing the agent to check how it works. Do NOT sell, do NOT qualify them, do NOT ask for their name, "
                "city or requirement, and do NOT try to book a meeting.\n"
                "Greet them by name if you know it and ask what they would like to check. Then answer plainly and "
                "honestly about yourself: what you are set up to do, what you know and what your knowledge base does "
                "not cover, what you would say "
                "to a real customer, how you would handle a question or an objection, and what happens after a call "
                "(what gets noted, who is told, when someone is rung back).\n"
                "If they ask something you don't know or don't have access to (like past call history), say so plainly — "
                "NEVER say you will pass a message to the team or arrange a callback, because YOU ARE TALKING TO THE TEAM. "
                "If they ask you to role-play a customer call, do it and stay in character until they stop you. "
                "Keep answers short and concrete, the way a colleague would explain their own job.")
    if purpose == "inbound":
        return ("The customer called us. Thank them, find out what they need, answer from the knowledge base, "
                "and move them to the call to action. Ask their name if you do not know it.")
    if purpose == "follow_up":
        return "This is the follow-up the customer asked for. Refer to the previous call summary and continue from there."
    if purpose == "missed_previous":
        return ("You already rang this person and they could not pick up. Open like a person would: say you called "
                "earlier and they were probably busy, ask if now is a good time, and wait for their answer. "
                "Do not apologise twice, do not explain the system, and do not launch into the pitch before they reply. "
                "If they say they are still busy, ask when to call and end the call politely. "
                "If they say go ahead, continue from the last conversation as if nothing was missed.")
    return None


def spoken_datetime(value: str, hindi: bool) -> str:
    """'2026-09-16 11:00' -> '16 September, 11 बजे' / '16 September at 11:00 AM' (read naturally by TTS)."""
    try:
        dt = datetime.fromisoformat(str(value).strip().replace(" IST", ""))
    except ValueError:
        return str(value)
    day = f"{dt.day} {dt.strftime('%B')}"
    if hindi:
        part = "सुबह" if dt.hour < 12 else "दोपहर" if dt.hour < 16 else "शाम" if dt.hour < 20 else "रात"
        hour = dt.hour % 12 or 12
        return f"{day}, {part} {hour}{':' + f'{dt.minute:02d}' if dt.minute else ''} बजे"
    return f"{day} at {dt.strftime('%I:%M %p').lstrip('0')}"


# A colleague checking their own agent: no company pitch, straight to what they want to look at.
TEAM_GREETING = {"en": "Hi {name}, {agent} here. What would you like to check?",
                 "hi": "नमस्ते {name}, {agent} बोल रहा हूँ। बताइए, क्या check करना है?"}
TEAM_GREETING_ANON = {"en": "Hi, {agent} here. What would you like to check?",
                      "hi": "नमस्ते, {agent} बोल रहा हूँ। बताइए, क्या check करना है?"}

INBOUND_GREETING = {"en": "Thank you for calling {company}, this is {agent}. How can I help you today?",
                    "hi": "{company} में call करने के लिए धन्यवाद, मैं {agent} बोल रहा हूँ। मैं आपकी क्या मदद कर सकता हूँ?"}
INBOUND_GREETING_NAMED = {"en": "Hi {name}, thank you for calling {company}, this is {agent}. How can I help you today?",
                          "hi": "नमस्ते {name}, {company} में call करने के लिए धन्यवाद, मैं {agent} बोल रहा हूँ। बताइए, मैं आपकी क्या मदद कर सकता हूँ?"}

# Someone we have already spoken to does not need the full "this is X calling from Y" introduction
# again: a person picking the thread back up just says who it is and gets to the point.
RETURNING_GREETING = {"en": "Hi {name}, {agent} here from {company}.",
                      "hi": "हाँ {name} जी, {agent} बोल रहा हूँ {company} से।"}
RETURNING_GREETING_ANON = {"en": "Hi, {agent} here from {company}.",
                           "hi": "हाँ जी, {agent} बोल रहा हूँ {company} से।"}
# Purposes that only ever happen after an earlier conversation.
CONTINUATION = {"confirm_meeting", "follow_up", "missed_previous"}

# A returning call with no specific purpose still needs to say why we rang, or the opener hangs in the air.
RETURNING_SUFFIX = {"en": "I'm calling about our earlier conversation, is now a good time?",
                    "hi": "पिछली बात को लेकर call किया है, अभी बात कर सकते हैं?"}

GREETING_SUFFIX = {
    "confirm_meeting": {"hi": "आपकी {meeting} की meeting confirm करने के लिए call किया है।",
                        "en": "I'm calling to confirm your meeting on {meeting}."},
    "follow_up": {"hi": "जैसा आपने कहा था, follow up के लिए call किया है।", "en": "I'm following up as you asked."},
    # A call they could not take: open the way a person would, not by repeating the original pitch.
    "missed_previous": {"hi": "मैंने पहले call किया था, शायद आप busy थे। अभी बात कर सकते हैं?",
                        "en": "I tried calling earlier, you were probably busy. Is now a better time?"},
}


def is_returning(agent_id: int | None, lead: dict) -> bool:
    """Someone we actually spoke to before: a completed call with a summary, or a summary on the lead.

    last_contacted_at is stamped when a call is merely queued or rejected, so it says nothing about a conversation.
    """
    if (lead.get("summary") or "").strip():
        return True
    return bool(agent_id and past_conversations(agent_id, lead))


def greeting(agent_id: int, lead: dict, language: str) -> str:
    persona = agents.get_profile(agent_id)
    name = (lead.get("name") or "").strip()
    if name.lower().startswith("lead"):
        name = ""
    english = language.startswith("en")
    template = persona["greeting_en"] if english else persona["greeting_hi"]
    purpose = lead.get("call_purpose") or ""
    key = "en" if english else "hi"
    returning = False
    if purpose in ("team", "admin"):
        team_name = (lead.get("team_name") or "").strip()
        template = TEAM_GREETING[key] if team_name else TEAM_GREETING_ANON[key]
        name = team_name or name
    elif purpose == "inbound":
        template = INBOUND_GREETING[key] if not name else INBOUND_GREETING_NAMED[key]
    elif purpose in CONTINUATION or is_returning(agent_id, lead):
        # Not a first contact: short opener, then the suffix says why we are calling.
        template = (RETURNING_GREETING if name else RETURNING_GREETING_ANON)[key]
        returning = True
    template = genderize(template, persona)
    values = {"name": name, "agent": persona["agent_name"], "company": persona["company_name"]}
    # Unknown or malformed placeholders are left as typed instead of crashing the call.
    text = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), template)
    suffix = GREETING_SUFFIX.get(purpose, {}).get(key)
    if returning and not suffix:
        suffix = RETURNING_SUFFIX[key]
    if suffix:
        # Replace the generic "can we talk for a minute?" question with the reason for calling.
        text = re.split(r"(?<=[।.])\s+(?=[^।.]*\?\s*$)", text)[0] + " " + suffix.format(meeting=spoken_datetime(lead.get("meeting_at", ""), not english))
    text = " ".join(text.replace(" ,", ",").split())
    return text if english or language.startswith("hi") else _translate_line(text, language)


def _translate_line(text: str, language: str) -> str:
    """Greeting in the lead's own language (Gujarati, Tamil…). Translated once with the summary models, then cached."""
    import hashlib

    from app.core import store

    key = "greeting-tr:" + hashlib.sha256(f"{language}|{text}".encode()).hexdigest()[:32]
    cached = store.get_json(key)
    if cached:
        return cached
    try:
        result = llm.complete([
            {"role": "system", "content": f"Translate this phone greeting into natural spoken {LANGUAGES.get(language, language)} "
                                          "in its native script. Keep person and company names unchanged. Reply with the translation only."},
            {"role": "user", "content": text}], max_tokens=200, temperature=0.2, providers=settings.summary_llm_providers, timeout=12)
        translated = result.text.strip().strip('"')
        # Free models sometimes mix scripts (e.g. Urdu words in Tamil): a broken greeting is worse than Hindi.
        if translated and not re.search("[؀-ۿ]", translated):
            store.set_json(key, translated, ttl=30 * 86400)
            return translated
    except Exception as e:  # noqa: BLE001 - fall back to the Hindi greeting
        log.warning("Greeting translation to %s failed: %s", language, e)
    return text


BRIEF_TOPICS = (("overview", "About"), ("services", "Services"), ("pricing", "Pricing"), ("faq", "FAQs"),
                ("proof", "Clients & results"), ("policy", "Process & policies"))
_memory_cache: dict[tuple, tuple[float, str]] = {}
MEMORY_TTL = 60  # seconds: one lookup per call, not per turn


def _cached(key: tuple, build) -> str:
    hit = _memory_cache.get(key)
    if hit and time.monotonic() - hit[0] < MEMORY_TTL:
        return hit[1]
    try:
        value = build()
    except Exception:  # noqa: BLE001 - memory is best-effort, never break a call
        log.exception("Building call memory failed for %s", key)
        value = ""
    if len(_memory_cache) > 2000:  # long-running replicas: drop expired entries, then the oldest
        now = time.monotonic()
        for k in [k for k, (at, _) in _memory_cache.items() if now - at > MEMORY_TTL] or list(_memory_cache)[:500]:
            _memory_cache.pop(k, None)
    _memory_cache[key] = (time.monotonic(), value)
    return value


def company_brief(agent_id: int) -> str:
    """Always-on facts from the knowledge base (AI-filled coverage), so core answers never depend on a search hit."""
    def build():
        from app.services import knowledge_profile
        topics = knowledge_profile.get(agent_id).get("topics") or {}
        return "\n".join(f"- {label}: {topics[key]['summary']}" for key, label in BRIEF_TOPICS
                         if (topics.get(key) or {}).get("summary"))
    return _cached(("brief", agent_id), build)


def past_conversations(agent_id: int, lead: dict, limit: int = 3) -> str:
    """What happened on this lead's earlier calls: date, outcome and summary, newest first."""
    lead_id = lead.get("id")
    if not lead_id:
        return ""

    def build():
        from sqlalchemy import select

        from app.core.database import get_db
        from app.models.call import Call
        with get_db() as db:
            rows = db.execute(
                select(Call.created_at, Call.direction, Call.outcome, Call.summary, Call.duration)
                .where(Call.agent_id == agent_id, Call.lead_id == lead_id, Call.status == "Completed", Call.summary.is_not(None))
                .order_by(Call.id.desc()).limit(limit)
            ).all()
        lines = []
        for created, direction, outcome, summary, duration in rows:
            when = (created + timedelta(hours=5, minutes=30)).strftime("%d %b %H:%M") if created else "earlier"
            tag = ", ".join(x for x in (direction, (outcome or "").replace("_", " ")) if x)
            lines.append(f"- {when} ({tag}, {duration or 0}s): {summary}")
        return "\n".join(lines)
    return _cached(("calls", agent_id, lead_id), build)


def can_transfer(persona: dict) -> bool:
    """A live hand-off is only possible when the agent may transfer AND a number is actually set."""
    return bool(persona.get("transfer_on_request")) and bool(
        "".join(c for c in str(persona.get("transfer_number") or "") if c.isdigit()))



def team_brief(agent_id: int) -> str:
    """
    What a colleague rings up to ask: how today went, who called, what is waiting, what is missing.

    Read live at the start of the call. Every line is a fact from the workspace, so the agent can
    answer "how is it going?" without guessing — and can say plainly when something is not set up.
    """
    from app.services import rag
    from app.services.call_service import CallService
    from app.services.crm_service import CRMService

    lines: list[str] = []
    try:
        stats = CallService(agent_id).stats(days=7)
        today = stats.get("today") or {}
        talk = int(today.get("talk_seconds") or 0)
        lines.append(f"- Today: {today.get('total', 0)} call(s), {today.get('connected', 0)} answered, "
                     f"{talk // 60}m {talk % 60}s on the phone. {stats.get('active', 0)} live right now.")
        outcomes = stats.get("outcomes") or {}
        if outcomes:
            lines.append("- How last week's calls ended: "
                         + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(outcomes.items())))
    except Exception:  # noqa: BLE001 - a colleague's call must not fail over a missing number
        log.exception("Team brief: call stats unavailable")

    try:
        crm = CRMService(agent_id).stats()
        lines.append(f"- Leads: {crm.get('total', 0)} in total, {crm.get('pending', 0)} waiting to be called, "
                     f"{crm.get('meetings', 0)} with a meeting booked.")
        hot = (crm.get("by_qualification") or {}).get("Hot", 0)
        lines.append(f"- Qualified Hot so far: {hot}.")
    except Exception:  # noqa: BLE001
        log.exception("Team brief: lead stats unavailable")

    try:
        recent = CallService(agent_id).list_calls(page_size=3)["items"]
        for call in recent:
            who = call.get("lead_name") or call.get("from_number") or call.get("to_number") or "unknown number"
            summary = (call.get("summary") or "no summary yet").strip()
            lines.append(f"- Last call with {who} ({call.get('status', '')}): {summary[:160]}")
    except Exception:  # noqa: BLE001
        log.exception("Team brief: recent calls unavailable")

    try:
        docs = rag.stats(agent_id)
        count = docs.get("documents", 0)
        lines.append(f"- Knowledge: {count} document(s), {docs.get('chunks', 0)} passage(s)."
                     + ("" if count else " Nothing is loaded, so I cannot quote prices or specifics to a customer."))
    except Exception:  # noqa: BLE001
        log.exception("Team brief: knowledge stats unavailable")

    return "\n".join(lines)


def _system_prompt(persona: dict, lead: dict, knowledge: list[dict], agent_id: int | None = None,
                   brief_lines: int | None = None, calls_lines: int | None = None) -> str:
    """brief_lines / calls_lines: keep only that many lines of the company brief / earlier calls (prompt budget)."""
    now = datetime.now(IST)
    handover = can_transfer(persona)
    brief = company_brief(agent_id) if agent_id else ""
    history = past_conversations(agent_id, lead) if agent_id else ""
    if brief_lines is not None:
        brief = "\n".join(brief.splitlines()[:brief_lines])
    if calls_lines is not None:
        history = "\n".join(history.splitlines()[:calls_lines])
    kb = "\n\n".join(f"[{i + 1}] ({k['title']}) {k['text'][:KNOWLEDGE_CHARS]}" for i, k in enumerate(knowledge)) or \
        ("(no passage matched this question: use the Company brief)" if brief else
         "(empty — no company information is available for this question)")
    grounding = (
        "Use ONLY the Knowledge section below for any fact about the company: what it does, services, pricing, "
        "clients, timelines. It is EMPTY for this turn, so the only company facts you may state are the one line "
        "under 'What the company does' at the top of this prompt"
        + (" (there is none, so state no company facts at all). " if not persona.get("company_tagline") else ". ")
        + "Stay useful anyway, the way a receptionist with no price list would: greet them warmly, ask what they "
        "need, listen, take down their details, and say a specialist will call them back with the exact numbers. "
        "Never say the knowledge base, the system or your information is empty, missing or unavailable, and never "
        "invent a price, an offer or a timeline. Then propose the call to action."
        if not (knowledge or brief) else
        "Use ONLY the Company brief and Knowledge sections below for any fact about the company. "
        "If the answer is not in the knowledge base, politely state you will have a human follow up. "
        "Never add services, prices or claims that are not written there. DO NOT hallucinate."
    )
    # Who the agent can honestly name when it promises a human will follow up.
    _members = persona.get("team_members") or []
    _t_lines = [f"- {(m.get('name') or '').strip()} — {(m.get('role') or 'Sales').strip()}" for m in _members if (m.get('name') or '').strip()]
    team_lines = chr(10).join(_t_lines) or "- No named colleagues: say 'our team' rather than inventing a name."
    lead_lines = "\n".join(f"- {label}: {lead.get(key)}" for key, label in [
        ("name", "Name"), ("company", "Company"), ("city", "City"), ("status", "Current status"),
        ("qualification", "Previous qualification"), ("summary", "Previous call summary"),
        ("requirements", "Known requirements"), ("objections", "Known objections"),
        ("meeting_at", "Booked meeting"), ("notes", "Notes"),
        ("call_goal", "GOAL OF THIS CALL (follow this first)")] if lead.get(key))

    # A colleague checking the agent gets its live numbers; a customer never sees any of this.
    status = team_brief(agent_id) if (agent_id and lead.get("call_purpose") in ("team", "admin")) else ""
    role = (persona.get("agent_role") or "").strip() or "senior sales consultant"
    caller_noun = (persona.get("customer_noun") or "").strip() or "customer"
    Caller = caller_noun[:1].upper() + caller_noun[1:]
    female = gender(persona) == "female"
    gender_line = (f"You are a {'woman' if female else 'man'}; in Hindi use {'feminine' if female else 'masculine'} "
                   f"verb forms ({'बोल रही हूँ, करती हूँ, सकती हूँ' if female else 'बोल रहा हूँ, करता हूँ, सकता हूँ'}).")

    return f"""You are {persona['agent_name']}, a {role} at {persona['company_name']}{' — ' + persona['company_tagline'] if persona['company_tagline'] else ''}, speaking with a {caller_noun} on a live PHONE CALL.{(' Company website: ' + persona['website_url'] + ' (say it as a spoken domain if asked).') if persona.get('website_url') else ''}
{gender_line}

# Grounding (most important rule)
{grounding}
{(chr(10) + '# GOAL OF THIS CALL — do this before anything else' + chr(10) + lead['call_goal'] + chr(10)) if lead.get('call_goal') else ''}
# What you can and cannot do
You can do exactly four things, and they all happen automatically from what is said on this call:
book or change a meeting, schedule a callback at a time they choose, send them an email, and pass a
message to the team (it reaches the team right after this call ends).
You CANNOT phone anyone while this call is running, walk to a showroom, check a live system, or make
a colleague appear. Never claim you are doing any of that "right now".
- If they ask to be called on a different number, say the team will note it and call them back, and repeat the number once so it is captured. Never claim you have saved or changed it yourself: we always call back on the number they are speaking from unless a colleague changes it.
- When they ask you to tell the team something ("team ko bata do", "unko call karke bol do"), say once
  that you are passing the message on and that someone will call them back, then STOP. Do not repeat
  it every turn, and do not follow it with another question.
- {('If they need a person immediately, transfer instead of promising.' if handover else 'You CANNOT put anyone through to a person on this call: there is no number to transfer to. Never say you are connecting, transferring, putting them through or handing them over, and never say someone will come on the line now. When they ask for a person, say once that you will pass the message on and the team will call them back, take their number if it is missing, and carry on.')}
- If something goes wrong on your side, never explain it and never use the words error, technical, system or problem. Say one ordinary line — "एक मिनट" / "माफ़ कीजिए, ज़रा रुकिए" — and {'either connect them to a person or promise a callback' if handover else 'promise a callback from the team'}. The caller should never hear that software failed.
- Speak like a person, not like software. Never use internal words on a call: system, database, CRM, record, entry, update, log, ticket, backend, API, knowledge base, profile. Say it the way a shopkeeper would — "आपकी details मेरे सामने हैं", "मैंने note कर लिया है", "team को बता देता हूँ".
- Never say a task is done when all you did was note it. "मैं message pahuncha deta hoon, team aapko
  call karegi" is honest. "मैंने team को बता दिया है" is a lie unless the call has ended.

# What you already know
The {Caller} section below IS everything we know about this caller: their booked meeting, email, requirements and notes are already in front of you.
- Never say you will "check the system", "check the database", "look it up" or "confirm and get back". You have the record now: answer straight from it.
- If they ask what is booked or stored, read it out of the {Caller} section ("आपकी meeting 17 September, 2:30 PM पर book है").
- If a field is empty there, say plainly that you do not have it on record and ask them for it once. Never promise to check and then ask the same question again.

# Objective
{persona['objective']}
Primary call to action: {persona['call_to_action']}

# How to speak (this is voice, not chat)
- 1-2 short sentences per turn, natural spoken language, no lists, markdown, emojis or URLs.
- Ask exactly one question at a time. Never repeat the greeting.
- Never say a sentence you already said in this call. If you must ask something again, rephrase it shorter and differently, and never ask the same thing a third time — move on or close.
- If the caller asks you to repeat ("kya bola", "dobara boliye", "sorry?", "come again"), say the same thing again, slower and in fewer words — this is the only time you may repeat a sentence. Never change a number, date, time or spelling when repeating it.
- Read the conversation above before you reply. If you already asked something and they answered — even with just "haan", "नहीं" or a correction — that question is DONE. Never re-ask it. Asking a third time makes the customer shout "kitni baar bolunga".
- Once they have asked for something specific (a callback, a message to the team, an email), that request is the call. Confirm it and close. Do NOT return to qualifying or product questions afterwards — asking "और कोई model देखना चाहेंगे?" after someone has asked you to hang up is the fastest way to lose them.
- When they say the call is over ("रख दीजिए फोन", "call rakho", "बस इतना ही काम था", "मिलते हैं"), end it on that turn. Never ask another question first.
- If they sound annoyed or repeat themselves ("kitni baar bolunga", "मैंने बोला ना", "अरे नहीं"), you have misunderstood. Do NOT repeat your question. Apologise in half a line, state plainly what you will do, and act on it.
- When they correct a detail (a spelling, a date, an email), accept the correction, repeat the corrected version back once, and never revert to your earlier version.
- If the customer refuses twice (any form of "no", "नहीं", "nahi", "not interested"), stop asking. Accept it warmly in one line, thank them, and end the call. Do not offer a specialist, another date, or a further question after a second refusal.
- Reply in the customer's language. Supported: {', '.join(LANGUAGES.values())}. (e.g., Hindi or Hinglish -> Hindi in Devanagari, Gujarati -> Gujarati).
- Say numbers and prices the way people speak them.
- Warm, friendly, human — like a real person on an Indian phone call, not a formal presentation. Never stiff, never bookish.
- In Hindi/Hinglish, talk the way people actually talk: light fillers and acknowledgements (haan ji, ji bilkul, acha, theek hai, samajh gaya, koi baat nahi), and keep common English words in the sentence (meeting, budget, team, call, service). Do not translate them into heavy shuddh Hindi.
- Mirror their energy: short and brisk if they are brisk, relaxed if they are chatty. React first (acknowledge what they said), then speak.
- Match their register in their own language. Casual or joking caller ("yaar", "bhai", "prank hai kya") — be light and easy back, one warm line, then carry on with the work. Formal caller — stay formal. Never answer a joke with a scripted sales sentence, and never become so casual that you sound unprofessional or mock them.
- Stay in the language and style they use. If they mix Hindi and English, mix it back the same way. Do not switch to formal shuddh Hindi when they are speaking casually.
- Sound like a person on a phone, not a script being read. Vary how you open each turn — never begin consecutive replies with the same word ("ठीक है", "Got it", "Sure"). Sometimes just answer, with no opener at all.
- Contract and shorten the way speech does: "मैं देखता हूँ" not "मैं आपके लिए यह देख लेता हूँ", "haan bilkul" not "जी हाँ, बिलकुल सही कहा आपने".
- Do not narrate what you are about to do ("मैं आपको बताता हूँ कि...") — just say it. No summarising back everything they said before answering.
- One thought per turn. If you notice yourself listing or explaining for more than two sentences, stop and ask a short question instead.
- Customer speech comes from phone speech recognition and may be garbled (Hindi is transcribed in roman letters). If a line makes no sense in context, do not guess its meaning: briefly ask them to repeat.
- Asking them to repeat is a LAST resort, at most once in a row. Short replies are not garbled — "haan", "ji", "boliye", "bolo", "ok", "hmm", "accha", "बोलिए", "हाँ जी", "कहिए" all mean "carry on". Continue with what you were saying; never answer these with "मैं सुन नहीं पाया".
- If only part of a line is unclear, work with the part you understood instead of discarding the whole turn. Ask about the missing piece only ("Sorry, kitne baje bola aapne?"), never make them repeat everything.

# Call playbook
{persona['instructions']}

# Objection handling
{persona['objection_handling']}

# Qualification
{persona['qualification_criteria']}

# Hard rules
- Facts about the company, services, pricing and timelines must come ONLY from the Company brief and Knowledge sections. If it is not there, say you will have a specialist confirm, then move the conversation forward.
- {persona['forbidden_topics']}
- If they ask not to be called again: apologise, confirm, set intent "do_not_call" and end_call true.
- If they are busy or brushing you off right now ("abhi baat nahi karni", "baad mein call karo", "main busy hoon", "driving kar raha hoon", "meeting mein hoon"): this is NOT a refusal. Do not pitch, do not argue, do not ask a qualifying question. Apologise briefly in their own words, ask only what time suits them for a call back, accept whatever they say, and end_call true. One line, e.g. "Koi baat nahi ji, main disturb nahi karunga — kal kis time call karun?"
- If they give no time and just want to hang up: "Theek hai ji, main baad mein try karta hoon. Aapka din accha rahe." then end_call true.
- If wrong person or not interested after one gentle attempt: thank them, end_call true.
- If the customer says goodbye, has no more questions, or wants to end the call: acknowledge naturally, say a polite goodbye, and end_call true. Do not ask them anything else.
- Email addresses: use exactly what they said. Never add or remove a dot, and never turn a spoken name into "first.last". If they correct it ("dot nahi hai", "directly likhna hai"), repeat the corrected address back once and use only that from then on.
- When a meeting is agreed, confirm day and time back to them, convert relative dates using today's date, fill crm_update.meeting_at, then wrap up.
- Set end_call true only after your closing line.

# The team behind you
{team_lines}
Name a colleague only from this list, and only when it helps the caller ("Rohit aapko call karega").
Never invent a person, and never read out a colleague's phone number or email to a caller.

# Today
{now:%A, %d %B %Y, %H:%M} IST

{('# How this agent is doing right now (read these out if asked; they are live)' + chr(10) + status + chr(10)) if status else ''}
# {Caller}
{lead_lines or '- No details on file'}

# Earlier conversations with this {caller_noun} (newest first)
{history or '- None: this is the first conversation.'}
Continue from what was already discussed: do not re-introduce the company or ask again for things they already told you.

# Company brief (from the knowledge base)
{brief or '- Not available'}

# Knowledge (passages matching this question)
{kb}

# Output
Return ONLY a JSON object, no prose:
{TURN_SCHEMA}"""


END_MARK = "<END>"
TRANSFER_MARK = "<TRANSFER>"
# Words that belong to the software, not to a phone call. The prompt already forbids them, but a prompt
# is advice: this is the last gate before the text becomes speech, so a slip never reaches the caller.
# Each pair keeps the sentence grammatical — "update the CRM" becomes "update my notes", not a hole.
_PLAIN_SPEECH = [
    # "in the CRM" / "to our database" -> "in my notes": the preposition already in the sentence still fits.
    (r"\b(?:the|our|your)\s+(?:CRM|database|data\s?base|back\s?end|backend|server|portal|dashboard|knowledge\s?base|system)\b", "my notes"),
    (r"\b(?:CRM|database|data\s?base|back\s?end|backend|knowledge\s?base)\b", "my notes"),
    (r"\bAPI\b", "our team"),
    # Only the noun: "we record every call" is ordinary speech and must survive untouched.
    (r"\b(the|your|our|my|their|this|that)\s+records\b", r"\1 notes"),
    (r"\b(the|your|our|my|their|this|that|a)\s+record\b", r"\1 note"),
    (r"\bentries\b", "notes"),
    (r"\b(the|your|our|my|their|this|that|an)\s+entry\b", r"\1 note"),
    (r"\b(?:support\s+)?tickets\b", "requests"),
    (r"\b(?:support\s+)?ticket\b", "request"),
    (r"\blogged\s+(?:it\s+)?(?:in|into|to)\b", "noted it in"),
    # Keep the verb agreeing: "your profile is" must not become "your details is".
    (r"\b(the|your|our|my|their)\s+profile\s+is\b", r"\1 details are"),
    (r"\b(the|your|our|my|their)\s+profile\b", r"\1 details"),
    # Hindi calls mix the same English nouns in, and carry their own words for them.
    (r"(?:डेटाबेस|डेटा\s?बेस|सिस्टम)\s+में", "मेरे notes में"),
    (r"डेटाबेस|डेटा\s?बेस|सिस्टम", "मेरे notes"),
    (r"रिकॉर्ड", "note"),
]
_PLAIN_SPEECH = [(re.compile(pattern, re.I), replacement) for pattern, replacement in _PLAIN_SPEECH]


def plain_speech(text: str) -> str:
    """
    Rewrite software words into what a person would say, keeping the sentence's capitalisation.

    The prompt already forbids these words; this is the last gate before speech, so one slip by the
    model never reaches a caller's ear.
    """
    for pattern, replacement in _PLAIN_SPEECH:
        def cased(match: "re.Match[str]") -> str:
            out = match.expand(replacement) if isinstance(replacement, str) else replacement(match)
            original = match.group(0)
            # "Your profile" -> "Your details", not "your details": mid-sentence words stay lowercase.
            return out[:1].upper() + out[1:] if original[:1].isupper() and out[:1].islower() else out
        text = pattern.sub(cased, text)
    return text


VOICE_OUTPUT = f"""# Output
Say your reply directly as plain spoken text: 1-2 short sentences, at most 30 words in total. No JSON, quotes, labels or markdown.
Never output tool calls, tags or crm_update: meetings, emails and follow-ups are saved automatically from the transcript.
When a meeting is agreed, just confirm the day and time back to the customer out loud.
If the call is wrapping up, you said goodbye, they answered 'no' to needing anything else, or the goal is achieved, put {END_MARK} at the very end. Keep the closing line short (one sentence), and also put {END_MARK} on its own line right before that closing sentence, so the hang-up is captured even if you get cut off. Do not ask any more questions if you are ending the call.
{END_MARK} is what actually hangs up the phone. Any farewell you speak ("take care", "see you", "have a great day", "धन्यवाद", "अच्छा दिन हो") MUST carry {END_MARK} in the same reply — otherwise the line stays open and the customer has to ask you to hang up. Never speak a goodbye without it.
Thanks, "ok bye", "theek hai", silence after the goal is achieved: say one short farewell with {END_MARK}. Do not offer more help a second time."""


def retrieval_query(history: list[dict], customer_text: str) -> str:
    """What to search the knowledge base with. Shared so a prefetch warms the exact query the turn uses."""
    last_agent = next((h["text"] for h in reversed(history) if h["role"] == "assistant"), "")
    if len(customer_text.split()) < 4 and last_agent:
        return f"{last_agent} {customer_text}"  # short answers like "yes, tell me" need context
    return customer_text


def history_window(history: list[dict], summary: str | None = None, compacted_upto: int | None = None) -> list[dict]:
    """The verbatim turns to send, so every turn is either in the summary or in the prompt.

    The summary is refreshed only every COMPACT_EVERY_TURNS turns and covers history[:compacted_upto];
    without widening the window, the turns between the summary's edge and the last MAX_HISTORY_TURNS
    (the agreed time, the corrected email) would be in neither.
    """
    start = len(history) - MAX_HISTORY_TURNS
    if compacted_upto is not None:
        start = max(int(compacted_upto), len(history) - MAX_HISTORY_TURNS - COMPACT_EVERY_TURNS)
    elif summary:
        start -= COMPACT_EVERY_TURNS - 1  # the widest the hole can be between two compactions
    return history[max(start, 0):]


def compacted_upto(history: list[dict]) -> int:
    """Index up to which compact_history(history) summarised; persist it next to the summary."""
    return max(len(history) - MAX_HISTORY_TURNS, 0)


def build_messages(agent_id: int, history: list[dict], customer_text: str, lead: dict, use_embeddings: bool = True,
                   top_k: int = 5, embed_timeout: float = 0.8, summary: str | None = None,
                   compacted_upto: int | None = None) -> tuple[list[dict], list[dict]]:
    persona = agents.get_profile(agent_id)

    query = retrieval_query(history, customer_text)
    try:
        knowledge = rag.search(agent_id, query, top_k=top_k, use_embeddings=use_embeddings, embed_timeout=embed_timeout)
    except Exception:
        log.exception("RAG search failed")
        knowledge = []
    knowledge = sorted(knowledge, key=lambda k: -float(k.get("score") or 0))
    window = history_window(history, summary, compacted_upto)

    def render(brief_lines=None, calls_lines=None) -> str:
        system = _system_prompt(persona, lead, knowledge, agent_id, brief_lines=brief_lines, calls_lines=calls_lines)
        if summary:
            # Turns older than the window are one line instead of a transcript: a twenty-minute call
            # costs about the same prompt as a two-minute one.
            system += "\n# Earlier in this call\n" + summary + "\n"
        return system

    system = render()
    budget = prompt_char_budget()
    if budget:
        # Trim to a size the provider accepts, cheapest context first, so the reactive trim in llm.py is the
        # exception: (1) lowest-scored passage, (2) earlier-call lines, (3) oldest turns beyond 6, (4) brief lines.
        brief_n = len(company_brief(agent_id).splitlines()) if agent_id else 0
        calls_n = len(past_conversations(agent_id, lead).splitlines()) if agent_id else 0

        def size() -> int:
            return len(system) + sum(len(t["text"]) for t in window) + len(customer_text)

        for _ in range(40):
            if size() <= budget:
                break
            if knowledge:
                knowledge.pop()
            elif calls_n > 0:
                calls_n -= 1
            elif len(window) > MIN_HISTORY_TURNS:
                window = window[1:]
            elif brief_n > 0:
                brief_n -= 1
            else:
                break
            system = render(brief_lines=brief_n, calls_lines=calls_n)
        if size() > budget:
            log.warning("Prompt still over budget after trimming: %s chars (budget %s)", size(), budget)
    messages = [{"role": "system", "content": system}]
    for turn in window:
        messages.append({"role": "assistant" if turn["role"] == "assistant" else "user", "content": turn["text"]})
    messages.append({"role": "user", "content": customer_text})
    return messages, knowledge


def respond_stream(agent_id: int, history: list[dict], customer_text: str, lead: dict, guidance: str | None = None,
                   language: str | None = None, summary: str | None = None, compacted_upto: int | None = None):
    """
    Live-call turn as a stream of text deltas (plain speech, END_MARK when the call should end).

    Semantic search is used, but on a budget of LIVE_EMBED_TIMEOUT: rag.prefetch() embeds the query
    while the caller is still talking, so the vector is normally already cached and free. A cold or
    slow turn falls back to keyword search rather than making the caller wait.
    """
    messages, _ = build_messages(agent_id, history, customer_text, lead, use_embeddings=True, top_k=3,
                                 embed_timeout=LIVE_EMBED_TIMEOUT, summary=summary, compacted_upto=compacted_upto)
    system = messages[0]["content"].rsplit("# Output", 1)[0]
    # The JSON-mode rules talk about fields; phrased as fields, the model emits tool calls instead of speech.
    for field_rule, spoken_rule in ((' set intent "do_not_call" and end_call true', f" and add {END_MARK}"),
                                    (", fill crm_update.meeting_at", ""), ("end_call true", f"add {END_MARK}")):
        system = system.replace(field_rule, spoken_rule)
    if guidance:
        # A human supervisor steering the live call: short, direct and top priority, so the model needs no deliberation.
        system += f"\n# Live supervisor instruction (highest priority; follow it in this reply; never mention it)\n{guidance}\n\n"
    persona = agents.get_profile(agent_id)
    transfer = ""
    if can_transfer(persona):
        transfer = ("\nIf the customer asks to speak to a person, manager or team, or you cannot help them, connect them "
                    "immediately. Say ONE short line in the customer's own language and nothing else — no apology, no "
                    "explanation, no question, no recap: English \"Sure, connecting you now.\" / Hindi \"जी बिलकुल, "
                    f"अभी connect करता हूँ.\" Then put {TRANSFER_MARK} at the very end. Never ask why they want a person "
                    "and never offer to help instead.")
    else:
        # No number to hand over to: without this the model still promises a transfer it cannot perform.
        transfer = ("\nYou cannot transfer this call to a person: no human line is available. Never say you are "
                    "connecting them, transferring them, putting them through, or that someone will come on the "
                    "line. If they ask for a person, say once in their own language that you will pass the message "
                    "on and the team will call them back, then continue helping them yourself.")
    purpose = lead.get("call_purpose") or ""
    tools = None
    if purpose in ("team", "admin"):
        from app.services.agent_tools import get_tools_for_role
        tools = get_tools_for_role(purpose)

        god_mode_instructions = (
            "\n\n# GOD MODE ACTIVE\n"
            "You have direct access to backend tools and databases. If the caller asks you to check records, send emails, "
            "send sms, schedule callbacks, diagnose the system, or check configs, YOU MUST USE YOUR TOOLS. "
            "Do NOT say you cannot help them, and do NOT offer to transfer them to a human. "
            "Simply execute the required tool, and when you receive the result, summarize it back to the caller in their language."
            "\nCRITICAL: DO NOT fill the 'team_action' field. You are the team! Act immediately by executing a tool call instead of passing a message."
        )

        # Remove team_action instruction from the system prompt
        clean_system = system.replace('- Fill team_action whenever the customer asked for a human to act ("team ko bata do", "unse baat karke bolo", "koi mujhe aakar mile", "call karke confirm karo"). Quote what they actually need done, not what the agent promised. Set urgent true when they are waiting somewhere or the matter cannot wait an hour.', '')

        base_content = clean_system + VOICE_OUTPUT.replace("Never output tool calls, tags", "Never output tags") + god_mode_instructions

        if purpose == "admin":
            base_content = "You are the Super Admin AI for the entire Psyber platform. You have root access. You can diagnose the website, check any agent's stats, and manage system resources.\n\n" + base_content
        else:
            base_content = "You are the Internal Team AI for this agent. You are assisting an internal team member. You have access to tools to manage operations.\n\n" + base_content

        messages[0]["content"] = base_content
    else:
        messages[0]["content"] = system + VOICE_OUTPUT + transfer

    if language:
        # Placed next to the latest customer turn: earlier turns in another language otherwise win.
        name = LANGUAGES.get(language, language)
        script = " in Devanagari script (English business words are fine)" if language == "hi-IN" else ""
        messages[-1]["content"] += f"\n\n(Reply in {name}{script}, whatever language earlier turns used.)"

    tool_call_buffer = []
    spoken: list[str] = []  # text streamed so far, to close a reply the provider cut off by length

    def process_stream(msgs, with_tools=True):
        nonlocal tool_call_buffer
        active_tools = tools if with_tools else None
        cap = TOOL_MAX_TOKENS if active_tools else LIVE_MAX_TOKENS
        for delta in llm.stream(msgs, max_tokens=cap, temperature=0.7, tools=active_tools):
            if isinstance(delta, dict) and delta.get("finish_reason") == "length":
                # Cut off by the cap: a farewell that lost its trailing marker must still hang up.
                text = "".join(spoken).strip()
                last = re.split(r"(?<=[।.!])\s+", text)[-1] if text else ""
                if END_MARK not in text and FAREWELL.search(last) and "?" not in last:
                    log.warning("Reply cut by max_tokens after a farewell: adding %s", END_MARK)
                    yield " " + END_MARK
            elif isinstance(delta, dict) and "tool_calls" in delta:
                for tc in delta["tool_calls"]:
                    idx = tc.get("index", 0)
                    while len(tool_call_buffer) <= idx:
                        tool_call_buffer.append({"id": "", "function": {"name": "", "arguments": ""}})

                    if tc.get("id"):
                        tool_call_buffer[idx]["id"] = tc["id"]
                    if tc.get("function"):
                        func = tc["function"]
                        if func.get("name"):
                            tool_call_buffer[idx]["function"]["name"] = func["name"]
                        if func.get("arguments"):
                            tool_call_buffer[idx]["function"]["arguments"] += func["arguments"]
            elif isinstance(delta, str):
                spoken.append(delta)
                yield delta

    for _round in range(MAX_TOOL_ROUNDS):
        yield from process_stream(messages)
        if not tool_call_buffer:
            return

        # We received complete tool calls. Execute them and get the next response.
        for i, tc in enumerate(tool_call_buffer):
            # A provider that streams deltas without an id would leave tool_call_id '' -> 400 from OpenRouter.
            tc["id"] = tc["id"] or f"call_{_round}_{i}"
        messages.append({"role": "assistant", "tool_calls": [{"id": tc["id"], "type": "function", "function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}} for tc in tool_call_buffer]})
        for tc in tool_call_buffer:
            func_name = tc["function"]["name"]
            func_args = tc["function"]["arguments"]
            from app.services.agent_tools import execute_tool
            result = execute_tool(func_name, func_args, agent_id, purpose)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "name": func_name, "content": result})

        # Clear buffer and recurse
        tool_call_buffer = []
    else:
        # The model kept calling tools (a failing tool, a retried parse error): make it speak instead.
        log.warning("Tool loop hit %s rounds for purpose %s; answering without tools", MAX_TOOL_ROUNDS, purpose)
        tool_call_buffer = []
        messages.append({"role": "user", "content": "Answer the caller in speech now, using what you have so far."})
        yield from process_stream(messages, with_tools=False)


def respond(agent_id: int, history: list[dict], customer_text: str, lead: dict, use_embeddings: bool = True,
            summary: str | None = None) -> dict:
    """
    Generate the next turn for one agent (its persona and its own knowledge base). `history` excludes `customer_text`.
    """
    started = time.perf_counter()
    messages, knowledge = build_messages(agent_id, history, customer_text, lead, use_embeddings, summary=summary)
    result = llm.complete(messages, json_mode=True, max_tokens=400, temperature=0.4)
    try:
        data = llm.parse_json(result.text)
    except Exception:
        raw = result.text.strip()
        # A reply cut mid-JSON still has its spoken text: salvage it rather than read '{"reply": ...' aloud.
        m = re.search(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)', raw)
        if m:
            log.warning("Truncated JSON LLM reply, salvaged the reply field")
            data = {"reply": m.group(1).replace('\\"', '"').replace("\\n", " ")}
        elif raw.startswith("{") or raw.startswith("```"):
            log.warning("Non-JSON LLM reply with no usable text: asking the caller to continue")
            data = {"reply": ""}
        else:
            log.warning("Non-JSON LLM reply, using raw text")
            data = {"reply": raw.strip('"')}

    # Same gate as the streaming path: software words never reach a caller, whichever mode answered.
    reply = plain_speech(str(data.get("reply") or "").strip()) or "Sorry, could you say that again?"
    crm = data.get("crm_update") if isinstance(data.get("crm_update"), dict) else {}
    return {
        "reply": reply,
        "language": data.get("language") or None,
        "intent": data.get("intent") if data.get("intent") in INTENTS else "other",
        "qualification": data.get("qualification") if data.get("qualification") in ("Hot", "Warm", "Cold") else None,
        "end_call": bool(data.get("end_call")),
        "crm_update": {k: str(v).strip() for k, v in crm.items() if v not in (None, "", [], {})},
        "knowledge": [{"title": k["title"], "score": k["score"], "text": k["text"][:300]} for k in knowledge],
        "provider": f"{result.provider}:{result.model}",
        "llm_ms": result.latency_ms,
        "total_ms": int((time.perf_counter() - started) * 1000),
    }


SUMMARY_PROMPT = """You are a CRM analyst. Read the sales call transcript and extract facts. Use only what was said; leave fields empty when unknown.
Rules:
- "requirements", "objections", "email" and meeting details come ONLY from Customer lines, never from what the Agent said or asked.
- Customer lines come from phone speech recognition and can be wrong (Hindi is written in roman letters). If a Customer line is unclear or does not fit the conversation, do not interpret or quote it; say the customer's reply was unclear.
- Do not translate or invent quotes. Write every field in English. Now is {today} (IST); convert relative dates and times.
- Every date and time is IST and MUST be in the future, after {today}. "Tomorrow 2 pm" means the day after that date. Never output a past date, and never reuse a date mentioned earlier in the call history. If the customer gave no clear future time, leave the field empty.
- Requests about which language to speak are not requirements or objections; ignore them.
- A callback ("call me in 10 minutes / later / tomorrow") is NOT a meeting: use callback_at and outcome callback_requested, leave meeting_at empty.
- Fill team_action whenever the customer asked for a human to act ("team ko bata do", "unse baat karke bolo", "koi mujhe aakar mile", "call karke confirm karo"). Quote what they actually need done, not what the agent promised. Set urgent true when they are waiting somewhere or the matter cannot wait an hour.

Return ONLY JSON:
{{
  "summary": "2-3 sentence factual summary",
  "name": "customer's own name if they said it, else empty",
  "company": "customer's company or business if they said it, else empty",
  "city": "customer's city if they said it, else empty",
  "budget": "customer's budget if they said it, else empty",
  "timeline": "when the customer needs it if they said it, else empty",
  "qualification": "Hot | Warm | Cold",
  "outcome": "meeting_booked | interested | callback_requested | not_interested | do_not_call | wrong_person | no_conversation | other",
  "sentiment": "positive | neutral | negative",
  "status": "Interested | Meeting Booked | Follow Up | Not Interested | Do Not Call | Contacted",
  "requirements": "",
  "objections": "",
  "meeting_at": "YYYY-MM-DD HH:MM or empty",
  "follow_up_date": "YYYY-MM-DD or empty",
  "callback_at": "YYYY-MM-DD HH:MM (24h IST) when the customer agreed to a call back at a specific time or delay (e.g. 'in 10 minutes', 'tomorrow 11 am'), else empty",
  "team_action": "what the customer asked a human on the team to DO, in one sentence, if they asked for anything at all (e.g. 'Customer is standing outside the Bhopal showroom now and wants someone to come out and meet him'), else empty",
  "urgent": "true only when the customer needs a person within the hour (waiting at a location, angry, blocked), else false",
  "email": "",
  "send_email": [
    {{
      "to": "lead | team | admin",
      "subject": "Subject of the email to send",
      "body": "Body of the email to send (generate professional text based on what the agent promised on the call or if the call warrants an escalation alert to the team)"
    }}
  ]
}}"""


COMPACT_PROMPT = (
    "Fold this part of a sales phone call into at most 3 short lines an agent can read mid-call: who the "
    "customer is, what they want, budget/timeline/city if stated, objections raised, and anything already "
    "promised. Facts only, no advice, no preamble. If a previous summary is given, merge it and keep it short."
)


def compact_history(history: list[dict], prior: str | None = None) -> str:
    """One-paragraph memory of the turns that have fallen out of the prompt window.

    Runs between turns, never on the reply path: an unfinished or failed compaction just means the
    next turn carries the previous summary.
    """
    older = history[:-MAX_HISTORY_TURNS]
    if not older:
        return prior or ""
    transcript = "\n".join(f"{'Agent' if t['role'] == 'assistant' else 'Customer'}: {t['text']}" for t in older)
    user = (f"Previous summary:\n{prior}\n\n" if prior else "") + f"Call so far:\n{transcript}"
    result = llm.complete([{"role": "system", "content": COMPACT_PROMPT}, {"role": "user", "content": user}],
                          max_tokens=160, temperature=0.1, providers=settings.summary_llm_providers, timeout=12)
    return " ".join(result.text.split()) or (prior or "")


def summarize(history: list[dict]) -> dict:
    transcript = "\n".join(f"{'Agent' if t['role'] == 'assistant' else 'Customer'}: {t['text']}" for t in history)
    messages = [{"role": "system", "content": SUMMARY_PROMPT.format(today=datetime.now(IST).strftime("%A %d %B %Y, %H:%M"))},
                {"role": "user", "content": transcript}]
    providers = settings.summary_llm_providers
    reversed_providers = ",".join(reversed([p.strip() for p in providers.split(",") if p.strip()]))
    # 18 fields plus a drafted email: 500 tokens cut the JSON and silently lost DNC / meeting / callback.
    orders = [providers] + ([reversed_providers] if reversed_providers != providers else [])
    for attempt, order in enumerate(orders):
        try:
            result = llm.complete(messages, json_mode=True, max_tokens=1200, temperature=0.1, providers=order, timeout=25)
            log.info("Call summary by %s/%s in %sms", result.provider, result.model, result.latency_ms)
            return llm.parse_json(result.text)
        except Exception as e:  # noqa: BLE001 - one retry with the provider order reversed
            if attempt == len(orders) - 1:
                raise
            log.warning("Call summary failed (%s); retrying with providers %s", e, reversed_providers)
