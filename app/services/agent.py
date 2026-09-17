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
from app.services import agents, llm, rag, team_service
from app.services.tts import LANGUAGES

log = get_logger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
MAX_HISTORY_TURNS = 14          # every turn resends history: fewer turns = fewer billed tokens
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

GREETING_SUFFIX = {
    "confirm_meeting": {"hi": "आपकी {meeting} की meeting confirm करने के लिए call किया है।",
                        "en": "I'm calling to confirm your meeting on {meeting}."},
    "follow_up": {"hi": "जैसा आपने कहा था, follow up के लिए call किया है।", "en": "I'm following up as you asked."},
    # A call they could not take: open the way a person would, not by repeating the original pitch.
    "missed_previous": {"hi": "मैंने पहले call किया था, शायद आप busy थे। अभी बात कर सकते हैं?",
                        "en": "I tried calling earlier, you were probably busy. Is now a better time?"},
}


def greeting(agent_id: int, lead: dict, language: str) -> str:
    persona = agents.get_profile(agent_id)
    name = (lead.get("name") or "").strip()
    if name.lower().startswith("lead"):
        name = ""
    english = language.startswith("en")
    template = persona["greeting_en"] if english else persona["greeting_hi"]
    purpose = lead.get("call_purpose") or ""
    key = "en" if english else "hi"
    if purpose == "inbound":
        template = INBOUND_GREETING[key] if not name else INBOUND_GREETING_NAMED[key]
    elif purpose in CONTINUATION or lead.get("last_contacted_at"):
        # Not a first contact: short opener, then the suffix says why we are calling.
        template = (RETURNING_GREETING if name else RETURNING_GREETING_ANON)[key]
    values = {"name": name, "agent": persona["agent_name"], "company": persona["company_name"]}
    # Unknown or malformed placeholders are left as typed instead of crashing the call.
    text = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), template)
    suffix = GREETING_SUFFIX.get(purpose, {}).get(key)
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


def _system_prompt(persona: dict, lead: dict, knowledge: list[dict], agent_id: int | None = None) -> str:
    now = datetime.now(IST)
    brief = company_brief(agent_id) if agent_id else ""
    history = past_conversations(agent_id, lead) if agent_id else ""
    kb = "\n\n".join(f"[{i + 1}] ({k['title']}) {k['text'][:KNOWLEDGE_CHARS]}" for i, k in enumerate(knowledge)) or \
        ("(no passage matched this question: use the Company brief)" if brief else
         "(empty — no company information is available for this question)")
    grounding = (
        "Use ONLY the Knowledge section below for any fact about the company: what it does, services, pricing, "
        "clients, timelines. It is EMPTY for this turn, so do NOT describe the company or its offerings at all. "
        "Say a specialist will walk them through the details, then propose the call to action."
        if not (knowledge or brief) else
        "Use ONLY the Company brief and Knowledge sections below for any fact about the company. "
        "If the answer is not in the knowledge base, politely state you will have a human follow up. "
        "Never add services, prices or claims that are not written there. DO NOT hallucinate."
    )
    # Who the agent can honestly name when it promises a human will follow up.
    team_lines = chr(10).join(team_service.directory_lines()) or "- No named colleagues: say 'our team' rather than inventing a name."
    lead_lines = "\n".join(f"- {label}: {lead.get(key)}" for key, label in [
        ("name", "Name"), ("company", "Company"), ("city", "City"), ("status", "Current status"),
        ("qualification", "Previous qualification"), ("summary", "Previous call summary"),
        ("requirements", "Known requirements"), ("objections", "Known objections"),
        ("meeting_at", "Booked meeting"), ("notes", "Notes"),
        ("call_goal", "GOAL OF THIS CALL (follow this first)")] if lead.get(key))

    return f"""You are {persona['agent_name']}, a senior sales consultant at {persona['company_name']}{' — ' + persona['company_tagline'] if persona['company_tagline'] else ''}, speaking with a prospect on a live PHONE CALL.{(' Company website: ' + persona['website_url'] + ' (say it as a spoken domain if asked).') if persona.get('website_url') else ''}

# Grounding (most important rule)
{grounding}
{(chr(10) + '# GOAL OF THIS CALL — do this before anything else' + chr(10) + lead['call_goal'] + chr(10)) if lead.get('call_goal') else ''}
# What you can and cannot do
You can do exactly four things, and they all happen automatically from what is said on this call:
book or change a meeting, schedule a callback at a time they choose, send them an email, and pass a
message to the team (it reaches the team right after this call ends).
You CANNOT phone anyone while this call is running, walk to a showroom, check a live system, or make
a colleague appear. Never claim you are doing any of that "right now".
- When they ask you to tell the team something ("team ko bata do", "unko call karke bol do"), say once
  that you are passing the message on and that someone will call them back, then STOP. Do not repeat
  it every turn, and do not follow it with a sales question.
- If they need a person immediately and a transfer is possible, transfer instead of promising.
- If something goes wrong on your side, never explain it and never use the words error, technical, system or problem. Say one ordinary line — "एक मिनट" / "माफ़ कीजिए, ज़रा रुकिए" — and either connect them to a person or promise a callback. The caller should never hear that software failed.
- Speak like a person, not like software. Never use internal words on a call: system, database, CRM, record, entry, update, log, ticket, backend, API, knowledge base, profile. Say it the way a shopkeeper would — "आपकी details मेरे सामने हैं", "मैंने note कर लिया है", "team को बता देता हूँ".
- Never say a task is done when all you did was note it. "मैं message pahuncha deta hoon, team aapko
  call karegi" is honest. "मैंने team को बता दिया है" is a lie unless the call has ended.

# What you already know
The Prospect section below IS the CRM record for this caller: their booked meeting, email, requirements and notes are already in front of you.
- Never say you will "check the system", "check the database", "look it up" or "confirm and get back". You have the record now: answer straight from it.
- If they ask what is booked or stored, read it out of the Prospect section ("आपकी meeting 17 September, 2:30 PM पर book है").
- If a field is empty there, say plainly that you do not have it on record and ask them for it once. Never promise to check and then ask the same question again.

# Objective
{persona['objective']}
Primary call to action: {persona['call_to_action']}

# How to speak (this is voice, not chat)
- 1-2 short sentences per turn, natural spoken language, no lists, markdown, emojis or URLs.
- Ask exactly one question at a time. Never repeat the greeting.
- Never say a sentence you already said in this call. If you must ask something again, rephrase it shorter and differently, and never ask the same thing a third time — move on or close.
- Read the conversation above before you reply. If you already asked something and they answered — even with just "haan", "नहीं" or a correction — that question is DONE. Never re-ask it. Asking a third time makes the customer shout "kitni baar bolunga".
- Once they have asked for something specific (a callback, a message to the team, an email), that request is the call. Confirm it and close. Do NOT return to qualifying or product questions afterwards — asking "और कोई model देखना चाहेंगे?" after someone has asked you to hang up is the fastest way to lose them.
- When they say the call is over ("रख दीजिए फोन", "call rakho", "बस इतना ही काम था", "मिलते हैं"), end it on that turn. Never ask another question first.
- If they sound annoyed or repeat themselves ("kitni baar bolunga", "मैंने बोला ना", "अरे नहीं"), you have misunderstood. Do NOT repeat your question. Apologise in half a line, state plainly what you will do, and act on it.
- When they correct a detail (a spelling, a date, an email), accept the correction, repeat the corrected version back once, and never revert to your earlier version.
- If the customer refuses twice (any form of "no", "नहीं", "nahi", "not interested"), stop asking. Accept it warmly in one line, thank them, and end the call. Do not offer a specialist, another date, or a further question after a second refusal.
- Reply in the customer's language: Hindi or Hinglish -> Hindi (Devanagari); English -> English. Supported: {', '.join(LANGUAGES.values())}.
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

# Sales playbook
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

# Prospect
{lead_lines or '- No details on file'}

# Earlier conversations with this prospect (newest first)
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
VOICE_OUTPUT = f"""# Output
Say your reply directly as plain spoken text: 1-2 short sentences, at most 30 words in total. No JSON, quotes, labels or markdown.
Never output tool calls, tags or crm_update: meetings, emails and follow-ups are saved automatically from the transcript.
When a meeting is agreed, just confirm the day and time back to the customer out loud.
If the call is wrapping up, you said goodbye, they answered 'no' to needing anything else, or the goal is achieved, put {END_MARK} at the very end. Do not ask any more questions if you are ending the call.
{END_MARK} is what actually hangs up the phone. Any farewell you speak ("take care", "see you", "have a great day", "धन्यवाद", "अच्छा दिन हो") MUST carry {END_MARK} in the same reply — otherwise the line stays open and the customer has to ask you to hang up. Never speak a goodbye without it.
Thanks, "ok bye", "theek hai", silence after the goal is achieved: say one short farewell with {END_MARK}. Do not offer more help a second time."""


def build_messages(agent_id: int, history: list[dict], customer_text: str, lead: dict, use_embeddings: bool = True, top_k: int = 5) -> tuple[list[dict], list[dict]]:
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

    messages = [{"role": "system", "content": _system_prompt(persona, lead, knowledge, agent_id)}]
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
    persona = agents.get_profile(agent_id)
    transfer = ""
    if persona.get("transfer_on_request") and "".join(c for c in persona.get("transfer_number", "") if c.isdigit()):
        transfer = ("\nIf the customer asks to speak to a person, manager or team, or you cannot help them, connect them "
                    "immediately. Say ONE short line in the customer's own language and nothing else — no apology, no "
                    "explanation, no question, no recap: English \"Sure, connecting you now.\" / Hindi \"जी बिलकुल, "
                    f"अभी connect करता हूँ.\" Then put {TRANSFER_MARK} at the very end. Never ask why they want a person "
                    "and never offer to help instead.")
    messages[0]["content"] = system + VOICE_OUTPUT + transfer
    if language:
        # Placed next to the latest customer turn: earlier turns in another language otherwise win.
        name = LANGUAGES.get(language, language)
        script = " in Devanagari script (English business words are fine)" if language == "hi-IN" else ""
        messages[-1]["content"] += f"\n\n(Reply in {name}{script}, whatever language earlier turns used.)"
    yield from llm.stream(messages, max_tokens=90, temperature=0.7)  # short spoken replies also cut TTS characters


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


def summarize(history: list[dict]) -> dict:
    transcript = "\n".join(f"{'Agent' if t['role'] == 'assistant' else 'Customer'}: {t['text']}" for t in history)
    result = llm.complete(
        [{"role": "system", "content": SUMMARY_PROMPT.format(today=datetime.now(IST).strftime("%A %d %B %Y, %H:%M"))},
         {"role": "user", "content": transcript}],
        json_mode=True, max_tokens=500, temperature=0.1, providers=settings.summary_llm_providers, timeout=20)
    log.info("Call summary by %s/%s in %sms", result.provider, result.model, result.latency_ms)
    return llm.parse_json(result.text)
