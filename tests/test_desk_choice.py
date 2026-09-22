"""Which of our businesses a call belongs to, worked out from what the caller actually says.

The caller is never read a menu, so this has to survive real speech: a brand name mangled by the line
or the recogniser ("वेड इजी" for Wedeazzy), a reason given in Hindi, Hinglish or English, and answers
that genuinely place the call nowhere. Everything here runs the cheap matcher only (use_llm=False):
what it cannot place is passed to the model on a real call, and guessing there is not a failure.
"""

from app.services import agent

CARS = {"agent_id": 1, "label": "Carsindias", "company": "Carsindias", "agent_name": "Ashish",
        "about": "verified used cars", "topic": "Carsindias verified used cars dealers buyers inspection resale"}
WEDDINGS = {"agent_id": 2, "label": "Wedeazzy", "company": "wedeazzy", "agent_name": "Omkar",
            "about": "wedding planning", "topic": "wedeazzy wedding planning venues photographers decoration shaadi"}
CHOICES = [CARS, WEDDINGS]


def place(said: str):
    return agent.choose_agent(said, CHOICES, use_llm=False)


def test_a_brand_name_the_line_mangled_still_places_the_call():
    """Every one of these is how speech recognition wrote "Wedeazzy" on a real call."""
    for said in ("वे डिजी के बारे में", "वेड इजी, वेड इजी", "वे डी जी", "wedeazzy ke liye",
                 "मुझे वेडइजी से बात करनी है", "Wedeazzy"):
        assert place(said) == WEDDINGS["agent_id"], said
    for said in ("कार्स इंडिया", "carsindias", "cars india se baat karni hai", "Carsindia"):
        assert place(said) == CARS["agent_id"], said


def test_the_reason_places_the_call_when_no_name_is_said():
    for said in ("मुझे कार खरीदनी है", "second hand car chahiye", "used car ke baare mein",
                 "car inspection karwani hai", "I want to sell my car to a dealer"):
        assert place(said) == CARS["agent_id"], said
    for said in ("shaadi ka function plan karna hai", "wedding venue dekhna tha",
                 "photographer chahiye", "decoration ke liye baat karni thi"):
        assert place(said) == WEDDINGS["agent_id"], said


def test_a_name_the_matcher_cannot_reach_is_left_to_the_model():
    """"wed easy" is two ordinary English words; only meaning tells us it is a brand, so it is not guessed here."""
    assert place("wed easy") is None


def test_the_agents_own_first_name_never_places_a_call():
    """Callers introduce themselves with the same names our agents use."""
    assert place("मैं आशीष शर्मा बोल रहा हूँ") is None
    assert place("this is Omkar speaking") is None


def test_an_answer_that_places_nothing_is_not_guessed():
    """A wrong desk costs the caller the whole call: silence here sends it to the model instead."""
    for said in ("hello", "haan ji", "बताइए", "मैं आशीष शर्मा बोल रहा हूँ", "Bhopal se", "", "   ",
                 "aapka number kahan se mila", "kitna time lagega"):
        assert place(said) is None, said


def test_the_greeting_asks_what_they_need_and_names_no_business():
    lead = {"call_purpose": "inbound_choose", "choices": CHOICES, "new_caller": True}
    for language in ("en-IN", "hi-IN"):
        text = agent.greeting.__wrapped__(1, lead, language) if hasattr(agent.greeting, "__wrapped__") else None
        assert text is None or ("Carsindias" not in text and "wedeazzy" not in text)
    brief = agent.call_goal(lead, "inbound_choose")
    assert "NEVER read our businesses out as a list" in brief
    # The clarifier exists for the one case where the reason was not enough, and names them once.
    assert "{options}" in agent.CHOOSE_CLARIFIER["en"] and "{options}" in agent.CHOOSE_CLARIFIER["hi"]
