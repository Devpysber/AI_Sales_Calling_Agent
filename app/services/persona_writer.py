"""
Draft an agent's persona from its own knowledge base.

A workspace ships with generic sales copy in its persona — an objective about booking discovery calls,
objection handling about ROI — and an operator who never rewrites it gets an agent that pitches
consulting to someone asking about wedding photographers. The knowledge base already says what the
business actually does, so the persona can be written from it rather than from a template.

Returns a draft for a person to read and accept; nothing is saved here.
"""

from app.core.config import settings
from app.core.logging import get_logger
from app.services import agents, knowledge_profile, llm, rag

log = get_logger(__name__)

# Only the fields that describe the business and how to sell it. Identity (agent name, company, voice,
# language, greeting) is the operator's own and is never rewritten from documents.
FIELDS = {
    "company_tagline": "One line on what the business does, under 60 characters, in the words a customer would use.",
    "agent_role": "What this agent is on the phone, 2-4 words: 'wedding planning advisor', 'clinic front desk'.",
    "customer_noun": "What the person on the line is to this business, one word: customer, couple, patient, vendor.",
    "objective": "What a successful call achieves for THIS business, one or two sentences, under 300 characters.",
    "call_to_action": "The single next step the agent asks for, one sentence, under 120 characters.",
    "instructions": "How to run the call for this business: opening, what to ask, what to explain. 3-5 short lines.",
    "objection_handling": "The objections THIS business actually hears, one per line as 'Objection: how to answer'. 3-4 lines.",
    "qualification_criteria": "What makes a lead Hot, Warm or Cold for this business, one line.",
    "forbidden_topics": "What this agent must never promise or claim, from the documents' own limits. 1-2 lines.",
}

PROMPT = """You write the playbook for a phone agent that answers for ONE business. You are given what that
business's own documents say. Write each field from those documents, in the business's own words and numbers.

Rules:
- Never invent a service, a price or a promise the documents do not support.
- Where the documents state a limit ("we are not a party to contracts", "we do not process payments"),
  that belongs in what the agent must never claim.
- Write for speech: plain sentences a person would say on a call, no marketing language, no lists inside a field.
- If the documents say nothing useful about a field, return an empty string for it rather than a guess.
- Write in English. The agent translates itself on the call.

Fields:
{fields}

Return ONLY a JSON object with exactly these keys and string values."""


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
        value = str(data.get(name) or "").strip()
        if value:
            drafted[name] = value[:limits.get(name, 3000)]
    return {"fields": drafted, "reason": "", "model": f"{result.provider}/{result.model}",
            "sources": [label for label, text in covered.items() if text]}
