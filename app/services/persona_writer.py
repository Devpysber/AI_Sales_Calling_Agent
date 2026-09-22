"""
Draft an agent's persona from its own knowledge base.

A workspace ships with generic sales copy in its persona — an objective about booking discovery calls,
objection handling about ROI — and an operator who never rewrites it gets an agent that pitches
consulting to someone asking about wedding photographers. The knowledge base already says what the
business actually does, so the persona can be written from it rather than from a template.

Returns a draft for a person to read and accept; nothing is saved here.
"""

import re

from app.core.config import settings
from app.core.logging import get_logger
from app.services import agents, knowledge_profile, llm, rag

log = get_logger(__name__)

# Only the fields that describe the business and how to sell it. Identity (agent name, company, voice,
# language, greeting) is the operator's own and is never rewritten from documents.
FIELDS = {
    "company_tagline": "What the business is, in one line under 60 characters, said the way a customer would say it.",
    "agent_role": "What this agent is on the phone, 2-4 words: 'wedding planning advisor', 'clinic front desk'.",
    "customer_noun": "What the person on the line is to this business, ONE word: customer, couple, patient, vendor.",
    "objective": ("What a successful call achieves, in one or two sentences under 300 characters. Name the ONE "
                  "outcome the agent is steering toward, not a list of everything the business does. If the "
                  "business serves two different kinds of caller, write the outcome for the one this agent rings."),
    "call_to_action": ("The single next step the agent ASKS FOR, as a statement of that step, under 120 characters. "
                       "It is a thing that happens after the call — a callback, a visit, details sent, a name passed "
                       "to a team. Never a question, and never 'tell me what you need', which is just talking."),
    "instructions": ("How to run this call, 3-5 short lines, one instruction per line. Open, the ONE thing to find "
                     "out first, what to explain in a sentence when they ask, and when to close. Use the business's "
                     "own words for its services. No line longer than 120 characters."),
    "objection_handling": ("The objections THIS business hears, 3-4 of them, ONE PER LINE, each written exactly as "
                           "'Objection: how to answer'. Take them from what the documents say people ask and worry "
                           "about. The answer is one short spoken sentence, and never promises what the documents "
                           "do not support."),
    "qualification_criteria": ("What makes a lead Hot, Warm or Cold here, in one line, using signals THIS business "
                               "can actually hear on a call — a date, a budget, a service named, a decision made."),
    "forbidden_topics": ("What this agent must never promise or claim, taken from the documents' own limits — what "
                         "the business is not party to, does not process, does not guarantee. 1-2 lines."),
}

PROMPT = """You write the playbook for a phone agent that answers for ONE business. You are given what that
business's own documents say. Write each field from those documents, in the business's own words and numbers.

Rules:
- Never invent a service, a price or a promise the documents do not support.
- Where the documents state a limit ("we are not a party to contracts", "we do not process payments"),
  that belongs in what the agent must never claim.
- Write for speech: short sentences a person would say out loud. No marketing language, no "leverage",
  "solutions", "utilise", "seamless". A shopkeeper explaining something to a neighbour.
- Specific beats general. "Ask which city and which month the wedding is in" is worth having;
  "understand their requirements" is not.
- If the documents say nothing useful about a field, return an empty string rather than a guess.
- Write in English. The agent translates itself on the call.

Fields:
{fields}

Return ONLY a JSON object with exactly these keys and string values."""


# "Tell me what you are looking for" is not a next step, it is the conversation. A call to action that
# comes back as a question leaves the agent with nothing to close on.
_QUESTION_CTA = re.compile(r"^(what|which|how|when|where|who|why|can|could|would|do|does|are|is|tell me)\b", re.I)
# "Objection: answer. Next objection: answer" on one line — split before each new label.
_NEXT_OBJECTION = re.compile(r"(?<=[.!?])\s+(?=[A-Z][^:]{2,40}:)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Asking for information is not a next step either, however politely it is phrased.
# A pair returned as two lines, sometimes bulleted: "- Objection: X" then "Answer: Y".
_OBJECTION_PAIR = re.compile(r"(?im)^[\s\-\*\d.)]*objection\s*:\s*(.+?)\s*\n[\s\-\*]*answer\s*:\s*")
_ASKING = re.compile(r"\b(tell me|let me know|share (with|your)|what (kind|sort|type) of)\b", re.I)


def _tidy(field: str, value: str) -> str:
    """Repair the shapes the model gets wrong, rather than sending a malformed field to the form."""
    value = value.strip()
    if not value:
        return ""
    if field == "objection_handling":
        # "Objection: X" on one line and "Answer: Y" on the next is two halves of one entry; the page
        # and the live prompt both expect the pair on a single line.
        value = _OBJECTION_PAIR.sub(lambda m: m.group(1).rstrip(" .;,") + ": ", value)
    if field == "objection_handling" and "\n" not in value and value.count(":") > 1:
        # Several objections returned as one run-on line: the page expects one per line, and so does
        # the prompt that reads them back on a call.
        value = _NEXT_OBJECTION.sub("\n", value)
    if field == "instructions" and "\n" not in value and value.count(". ") >= 2:
        value = _SENTENCE_END.sub("\n", value)
    if field == "customer_noun":
        value = value.split()[0].strip(".,")[:40]          # one word, whatever was asked for
    if field == "call_to_action":
        # A politeness prefix hid the shape: "Please tell me what you are looking for" is the
        # conversation, not the step that follows it.
        bare = re.sub(r"^(please|kindly|could you|can you|may i|i would like to|let me)\s+", "", value, flags=re.I)
        if value.endswith("?") or _QUESTION_CTA.match(bare) or _ASKING.search(bare):
            return ""                                      # better empty than a question the form calls a next step
    return value


def draft(agent_id: int) -> dict:
    """Persona fields drafted from this agent's documents, plus what they were drawn from.

    `fields` is empty when the agent has no knowledge base worth reading, and the caller shows the
    operator why rather than a blank form.
    """
    profile = agents.get_profile(agent_id)
    stats = rag.stats(agent_id)
    if not stats.get("chunks"):
        return {"fields": {}, "reason": "This agent has no documents yet. Upload what it sells first.",
                "sources": []}

    topics = (knowledge_profile.get(agent_id) or {}).get("topics") or {}
    covered = {label: (topics.get(key) or {}).get("summary") or ""
               for key, label in (("overview", "Company overview"), ("services", "Services and products"),
                                  ("pricing", "Pricing"), ("faq", "Common questions"),
                                  ("proof", "Proof and results"), ("policy", "Process and policies"))}
    evidence = "\n".join(f"{label}: {text}" for label, text in covered.items() if text)
    if not evidence:
        # Coverage has not run or found nothing: read the documents directly rather than refusing.
        evidence = knowledge_profile._corpus(agent_id)[0][:8000]
    if not evidence.strip():
        return {"fields": {}, "reason": "The documents have not been read yet. Try again in a moment.",
                "sources": []}

    company = profile.get("company_name") or "this business"
    fields = "\n".join(f"- {name}: {what}" for name, what in FIELDS.items())
    result = llm.complete(
        [{"role": "system", "content": PROMPT.format(fields=fields)},
         {"role": "user", "content": f"The business is {company}.\n\nWhat its documents say:\n{evidence}"}],
        json_mode=True, max_tokens=1600, temperature=0.2,
        providers=settings.summary_llm_providers, timeout=40)
    data = llm.parse_json(result.text)

    drafted, limits = {}, agents.PROFILE_LIMITS
    for name in FIELDS:
        value = _tidy(name, str(data.get(name) or "").strip())
        if value:
            drafted[name] = value[:limits.get(name, 3000)]
    return {"fields": drafted, "reason": "", "model": f"{result.provider}/{result.model}",
            "sources": [label for label, text in covered.items() if text]}


# Fields the agent may fill for itself, and only while they are empty. Identity — name, company, voice,
# language, greeting — is the operator's and is never written by a machine.
AUTOFILL = ("company_tagline", "agent_role", "customer_noun", "objective", "call_to_action",
            "instructions", "objection_handling", "qualification_criteria", "forbidden_topics")


def autofill(agent_id: int) -> dict:
    """Fill the empty playbook fields from the knowledge base, once the documents have been read.

    Runs after the coverage audit, so a workspace whose operator uploaded documents and never opened
    the Playbook tab still calls with a playbook about its own business rather than an empty one.
    Only empty fields are written: anything a person typed is left exactly as they left it, and the
    change is recorded so it can be seen and undone.
    """
    profile = agents.get_profile(agent_id)
    empty = [f for f in AUTOFILL if not str(profile.get(f) or "").strip()]
    if not empty:
        return {}
    drafted = draft(agent_id).get("fields") or {}
    filling = {f: drafted[f] for f in empty if drafted.get(f)}
    if not filling:
        return {}
    agents.update_profile(agent_id, filling, actor="ai")
    from app.services import events
    events.record("agent.updated", f"Playbook written from the knowledge base ({len(filling)} fields)",
                  ", ".join(filling), agent_id=agent_id, actor="ai")
    log.info("Autofilled %s persona fields for agent %s from its documents", len(filling), agent_id)
    return filling
