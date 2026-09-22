"""The compressed system prompt must keep every rule a live call depends on; a rewrite that drops one fails here."""
import re

from app.services import agent, agents


class Persona(dict):
    def __missing__(self, key):
        return ""

RULES = {
    "actions are promises": r"bhej deta hoon",
    "never claim done": r"never \"bhej diya\", \"book ho gaya\", \"team ko bata diya\"",
    "no software words": r"system, database, CRM, record",
    "busy is not refusal": r"Busy .*NOT a refusal",
    "no is not farewell": r"\"No\" is not a farewell",
    "latest intent wins": r"Latest clear intent wins",
    "character budget": r"Never over 200; average 100-130",
    "one question per turn": r"ONE question per turn",
    "greeting once": r"Greeting once, ever",
    "never repeat a sentence": r"Never say a sentence you already said",
    "answered questions are done": r"is DONE",
    "two refusals stop": r"Two refusals",
    "language and script": r"language AND script of their last turn",
    "AI honesty": r"never claim to be human",
    "WhatsApp first": r"offer WhatsApp on this number first",
    "email spelled back": r"read it back once",
    "kabhi bhi is not a time": r"\"Kabhi bhi\"/\"anytime\" is not a time",
    "visit is the meeting": r"IS the meeting",
    "no phone number request": r"Never ask for their phone number",
    "two cool signals": r"SECOND cool signal",
    "wrong number": r"Wrong number",
    "facts from knowledge only": r"ONLY from the Company brief and Knowledge",
    "DNC explicit only": r"only when they EXPLICITLY say stop calling",
    "end only after closing line": r"End the call only after your closing line",
    "engagement is not refusal": r"is engagement, never a refusal",
    "no pitch after goodbye": r"restart the pitch after your goodbye",
    "team names only from list": r"Name a colleague only from this list",
    "tts guardrail follows setting": r"about \d+ spoken characters",
    "closing intent required": r"never \"other\" on a close",
}


def test_every_live_call_rule_is_in_the_prompt():
    persona = Persona(agents.PROFILE_DEFAULTS)
    text = agent._system_prompt(persona, Persona(), [], None)
    missing = [name for name, pattern in RULES.items() if not re.search(pattern, text, re.S)]
    assert not missing, f"rules dropped from the prompt: {missing}"


def test_prompt_stays_within_the_cost_budget():
    """~4.3k input tokens per turn is the cost baseline; a rule added back must not silently double it."""
    persona = Persona(agents.PROFILE_DEFAULTS)
    text = agent._system_prompt(persona, Persona(), [], None)
    assert len(text) < 19000, f"prompt grew to {len(text)} chars; keep the LLM cost per turn down"
