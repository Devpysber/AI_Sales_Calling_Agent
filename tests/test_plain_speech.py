"""A caller must never hear a software word, whichever path produced the reply."""

from app.services.agent import plain_speech
from app.services.voice_stream import ReplyFilter


def streamed(*deltas: str) -> str:
    """What the caller actually hears, assembled the way the live call assembles it."""
    f = ReplyFilter()
    return "".join(f.feed(d) for d in deltas) + f.flush()


def test_software_words_are_rewritten():
    assert plain_speech("I'll update the CRM right away.") == "I'll update my notes right away."
    assert plain_speech("Let me check the system.") == "Let me check my notes."
    assert plain_speech("I have logged it in our database.") == "I have noted it in my notes."
    assert plain_speech("I'll raise a support ticket.") == "I'll raise a request."


def test_capitalisation_and_agreement_survive():
    assert plain_speech("Your profile is complete.") == "Your details are complete."
    assert plain_speech("Database updated.") == "My notes updated."


def test_ordinary_speech_is_left_alone():
    # "record" as a verb, and a sentence with nothing to fix, must come through untouched.
    assert plain_speech("We record every call for quality.") == "We record every call for quality."
    assert plain_speech("Sure, I can book that for Tuesday at 3.") == "Sure, I can book that for Tuesday at 3."


def test_hindi_replies_are_covered():
    spoken = plain_speech("मैंने आपका रिकॉर्ड सिस्टम में डाल दिया है।")
    assert "रिकॉर्ड" not in spoken and "सिस्टम" not in spoken


def test_a_word_split_across_stream_chunks_is_still_caught():
    # The model streams "data" then "base"; rewriting each chunk alone would miss it.
    assert streamed("I'll note it ", "in the data", "base ", "right away.") == "I'll note it in my notes right away."


def test_markers_still_work_through_the_rewrite():
    f = ReplyFilter()
    heard = "".join(f.feed(d) for d in ["Booked for Tuesday", " at 3.", "<END>"]) + f.flush()
    assert heard == "Booked for Tuesday at 3." and f.end_call

    g = ReplyFilter()
    heard = "".join(g.feed(d) for d in ["Sure, connecting you now.", "<TRANSFER>"]) + g.flush()
    assert heard == "Sure, connecting you now." and g.transfer


def test_a_product_the_agent_sells_is_not_rewritten():
    """A real call had the agent say it builds "My notes and lead management tools": the rewrite fired
    on the product being sold, not on a slip about our own store."""
    assert plain_speech("We build CRM and lead management tools for property agents") ==         "We build CRM and lead management tools for property agents"
    assert plain_speech("क्या आप अभी किसी CRM tool का use कर रहे हैं") ==         "क्या आप अभी किसी CRM tool का use कर रहे हैं"
    assert plain_speech("Do you use any database software today?") == "Do you use any database software today?"
    # ...while a genuine slip about our own store is still cleaned up.
    assert plain_speech("I will update the CRM now") == "I will update my notes now"


def test_a_product_survives_the_streamed_path_too():
    assert streamed("We build ", "CRM tools", " for agents.") == "We build CRM tools for agents."
