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
MAX_HISTORY_TURNS = 10          # every turn resends history: fewer turns = fewer billed tokens
KNOWLEDGE_CHARS = 600           # per retrieved passage in the prompt

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


def call_goal(lead: dict, purpose: str | None) -> str | None:
    """Instruction for a purpose-specific call (from the lead page's next best action)."""
    if purpose == "confirm_meeting" and lead.get("meeting_at"):
        return (f"Confirm the booked meeting on {lead['meeting_at']} (IST). Ask if that time still works; "
                "if not, agree a new day and time and repeat it back. Do not pitch again. Keep the call under a minute.")
    if purpose == "inbound":
        return ("The customer called us. Thank them, find out what they need, answer from the knowledge base, "
                "and move them to the call to action. Ask their name if you do not know it.")
    if purpose == "follow_up":
        return "This is the follow-up the customer asked for. Refer to the previous call summary and continue from there."
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


INBOUND_GREETING = {"en": "Thank you for calling {company}, this is {agent}. How can I help you today?",
                    "hi": "{company} में call करने के लिए धन्यवाद, मैं {agent} बोल रहा हूँ। मैं आपकी क्या मदद कर सकता हूँ?"}
INBOUND_GREETING_NAMED = {"en": "Hi {name}, thank you for calling {company}, this is {agent}. How can I help you today?",
                          "hi": "नमस्ते {name}, {company} में call करने के लिए धन्यवाद, मैं {agent} बोल रहा हूँ। बताइए, मैं आपकी क्या मदद कर सकता हूँ?"}

GREETING_SUFFIX = {
    "confirm_meeting": {"hi": "आपकी {meeting} की meeting confirm करने के लिए call किया है।",
                        "en": "I'm calling to confirm your meeting on {meeting}."},
    "follow_up": {"hi": "जैसा आपने कहा था, follow up के लिए call किया है।", "en": "I'm following up as you asked."},
}


def greeting(agent_id: int, lead: dict, language: str) -> str:
    persona = agents.get_profile(agent_id)
    name = (lead.get("name") or "").strip()
    if name.lower().startswith("lead"):
        name = ""
    english = language.startswith("en")
    template = persona["greeting_en"] if english else persona["greeting_hi"]
    if lead.get("call_purpose") == "inbound":
        template = INBOUND_GREETING["en" if english else "hi"] if not name else INBOUND_GREETING_NAMED["en" if english else "hi"]
    values = {"name": name, "agent": persona["agent_name"], "company": persona["company_name"]}
    # Unknown or malformed placeholders are left as typed instead of crashing the call.
    text = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), template)
    suffix = GREETING_SUFFIX.get(lead.get("call_purpose") or "", {}).get("en" if english else "hi")
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


def _system_prompt(persona: dict, lead: dict, knowledge: list[dict]) -> str:
    now = datetime.now(IST)
    kb = "\n\n".join(f"[{i + 1}] ({k['title']}) {k['text'][:KNOWLEDGE_CHARS]}" for i, k in enumerate(knowledge)) or \
        "(empty — no company information is available for this question)"
    grounding = (
        "Use ONLY the Knowledge section below for any fact about the company: what it does, services, pricing, "
        "clients, timelines. It is EMPTY for this turn, so do NOT describe the company or its offerings at all. "
        "Say a specialist will walk them through the details, then propose the call to action."
        if not knowledge else
        "Use ONLY the Knowledge section below for any fact about the company. Never add services, prices or claims that are not written there."
    )
    lead_lines = "\n".join(f"- {label}: {lead.get(key)}" for key, label in [
        ("name", "Name"), ("company", "Company"), ("city", "City"), ("status", "Current status"),
        ("qualification", "Previous qualification"), ("summary", "Previous call summary"),
        ("requirements", "Known requirements"), ("objections", "Known objections"),
        ("meeting_at", "Booked meeting"), ("notes", "Notes"),
        ("call_goal", "GOAL OF THIS CALL (follow this first)")] if lead.get(key))

    return f"""You are {persona['agent_name']}, a senior sales consultant at {persona['company_name']}{' — ' + persona['company_tagline'] if persona['company_tagline'] else ''}, speaking with a prospect on a live PHONE CALL.

# Grounding (most important rule)
{grounding}

# Objective
{persona['objective']}
Primary call to action: {persona['call_to_action']}

# How to speak (this is voice, not chat)
- 1-2 short sentences per turn, natural spoken language, no lists, markdown, emojis or URLs.
- Ask exactly one question at a time. Never repeat the greeting.
- Reply in the customer's language: Hindi or Hinglish -> Hindi (Devanagari); English -> English. Supported: {', '.join(LANGUAGES.values())}.
- Say numbers and prices the way people speak them.
- Customer speech comes from phone speech recognition and may be garbled (Hindi is transcribed in roman letters). If a line makes no sense in context, do not guess its meaning: briefly ask them to repeat.

# Sales playbook
{persona['instructions']}

# Objection handling
{persona['objection_handling']}

# Qualification
{persona['qualification_criteria']}

# Hard rules
- Facts about the company, services, pricing and timelines must come ONLY from the Knowledge section. If it is not there, say you will have a specialist confirm, then move the conversation forward.
- {persona['forbidden_topics']}
- If they ask not to be called again: apologise, confirm, set intent "do_not_call" and end_call true.
- If wrong person or not interested after one gentle attempt: thank them, end_call true.
- When a meeting is agreed, confirm day and time back to them, convert relative dates using today's date, fill crm_update.meeting_at, then wrap up.
- Set end_call true only after your closing line.

# Today
{now:%A, %d %B %Y, %H:%M} IST

# Prospect
{lead_lines or '- No details on file'}

# Knowledge
{kb}

# Output
Return ONLY a JSON object, no prose:
{TURN_SCHEMA}"""


END_MARK = "<END>"
VOICE_OUTPUT = f"""# Output
Say your reply directly as plain spoken text: 1-2 short sentences, at most 30 words in total. No JSON, quotes, labels or markdown.
Never output tool calls, tags or crm_update: meetings, emails and follow-ups are saved automatically from the transcript.
When a meeting is agreed, just confirm the day and time back to the customer out loud.
If the call should end now (you said goodbye, they asked not to be called, wrong person, or not interested), put {END_MARK} at the very end."""


def build_messages(agent_id: int, history: list[dict], customer_text: str, lead: dict, use_embeddings: bool = True, top_k: int = 3) -> tuple[list[dict], list[dict]]:
    persona = agents.get_profile(agent_id)

    query = customer_text
    last_agent = next((h["text"] for h in reversed(history) if h["role"] == "assistant"), "")
    if len(customer_text.split()) < 4 and last_agent:
        query = f"{last_agent} {customer_text}"  # short answers like "yes, tell me" need context
    try:
        # Live call budget: semantic search gets 0.8s, otherwise BM25 alone answers
        knowledge = rag.search(agent_id, query, top_k=top_k, use_embeddings=use_embeddings, embed_timeout=0.8)
    except Exception:
        log.exception("RAG search failed")
        knowledge = []

    messages = [{"role": "system", "content": _system_prompt(persona, lead, knowledge)}]
    for turn in history[-MAX_HISTORY_TURNS:]:
        messages.append({"role": "assistant" if turn["role"] == "assistant" else "user", "content": turn["text"]})
    messages.append({"role": "user", "content": customer_text})
    return messages, knowledge


def respond_stream(agent_id: int, history: list[dict], customer_text: str, lead: dict, guidance: str | None = None,
                   language: str | None = None):
    """
    Live-call turn as a stream of text deltas (plain speech, END_MARK when the call should end).
    Knowledge uses keyword search only: no embedding round-trip on the hot path.
    """
    messages, _ = build_messages(agent_id, history, customer_text, lead, use_embeddings=False, top_k=2)
    system = messages[0]["content"].rsplit("# Output", 1)[0]
    # The JSON-mode rules talk about fields; phrased as fields, the model emits tool calls instead of speech.
    for field_rule, spoken_rule in ((' set intent "do_not_call" and end_call true', f" and add {END_MARK}"),
                                    (", fill crm_update.meeting_at", ""), ("end_call true", f"add {END_MARK}")):
        system = system.replace(field_rule, spoken_rule)
    if guidance:
        # A human supervisor steering the live call: short, direct and top priority, so the model needs no deliberation.
        system += f"\n# Live supervisor instruction (highest priority; follow it in this reply; never mention it)\n{guidance}\n\n"
    messages[0]["content"] = system + VOICE_OUTPUT
    if language:
        # Placed next to the latest customer turn: earlier turns in another language otherwise win.
        name = LANGUAGES.get(language, language)
        script = " in Devanagari script (English business words are fine)" if language == "hi-IN" else ""
        messages[-1]["content"] += f"\n\n(Reply in {name}{script}, whatever language earlier turns used.)"
    yield from llm.stream(messages, max_tokens=90, temperature=0.4)  # short spoken replies also cut TTS characters


def respond(agent_id: int, history: list[dict], customer_text: str, lead: dict, use_embeddings: bool = True) -> dict:
    """
    Generate the next turn for one agent (its persona and its own knowledge base). `history` excludes `customer_text`.
    """
    started = time.perf_counter()
    messages, knowledge = build_messages(agent_id, history, customer_text, lead, use_embeddings)
    result = llm.complete(messages, json_mode=True, max_tokens=220, temperature=0.4)
    try:
        data = llm.parse_json(result.text)
    except Exception:
        log.warning("Non-JSON LLM reply, using raw text")
        data = {"reply": result.text.strip().strip('"')}

    reply = str(data.get("reply") or "").strip() or "Sorry, could you say that again?"
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
- Requests about which language to speak are not requirements or objections; ignore them.
- A callback ("call me in 10 minutes / later / tomorrow") is NOT a meeting: use callback_at and outcome callback_requested, leave meeting_at empty.

Return ONLY JSON:
{{
  "summary": "2-3 sentence factual summary",
  "qualification": "Hot | Warm | Cold",
  "outcome": "meeting_booked | interested | callback_requested | not_interested | do_not_call | wrong_person | no_conversation | other",
  "sentiment": "positive | neutral | negative",
  "status": "Interested | Meeting Booked | Follow Up | Not Interested | Do Not Call | Contacted",
  "requirements": "",
  "objections": "",
  "meeting_at": "YYYY-MM-DD HH:MM or empty",
  "follow_up_date": "YYYY-MM-DD or empty",
  "callback_at": "YYYY-MM-DD HH:MM (24h IST) when the customer agreed to a call back at a specific time or delay (e.g. 'in 10 minutes', 'tomorrow 11 am'), else empty",
  "email": ""
}}"""


def summarize(history: list[dict]) -> dict:
    transcript = "\n".join(f"{'Agent' if t['role'] == 'assistant' else 'Customer'}: {t['text']}" for t in history)
    result = llm.complete(
        [{"role": "system", "content": SUMMARY_PROMPT.format(today=datetime.now(IST).strftime("%A %d %B %Y, %H:%M"))},
         {"role": "user", "content": transcript}],
        json_mode=True, max_tokens=500, temperature=0.1, providers=settings.summary_llm_providers, timeout=20)
    log.info("Call summary by %s/%s in %sms", result.provider, result.model, result.latency_ms)
    return llm.parse_json(result.text)
