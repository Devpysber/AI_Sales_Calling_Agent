import pytest

from app.services.voice_stream import is_caller_closing, is_farewell, is_post_farewell_noise


@pytest.mark.parametrize("text", [
    "No no thank you",
    "No thanks",
    "Nahi thank you",
    "Nahin, that's all",
    "नहीं धन्यवाद",
    "ok bye",
    "haan okay bye",
    "that's all",
    "nothing else",
    "theek hai bye",
    "rakhta hoon",
    "chalo theek hai",
    "Okay, thank you, bye.",
    "No thanks, that's all.",
    "Thank you, take care.",
    "Bye bye.",
])
def test_caller_closing_true(text):
    assert is_caller_closing(text)


@pytest.mark.parametrize("text", [
    "No, tell me more",
    "No, wait",
    "No, I have a question",
    "No, that's not what I meant",
    "No, but I'm interested",
    "No, not right now",
    "No problem",
    "No actually the budget is 8 lakh",
    "Nahi, bataiye",
    "Nahi, ek minute",
    "okay",
    "haan",
    "No no, wait. I have one more question.",
    # "thank you" / "no" inside a sentence that keeps talking is never a sign-off
    "Thank you, but I have another question.",
    "No no, don't hang up.",
    "Phone mat rakho, ek sawal hai",
    "call mat kaato",
    "फोन मत रखो",
    "Okay, one last thing.",
    "Thanks, can you also tell me the price?",
    "Thank you for explaining that. What happens next?",
])
def test_caller_closing_false(text):
    assert not is_caller_closing(text)


@pytest.mark.parametrize("text", [
    "Hello",
    "Hello?",
    "Haan ji",
    "Okay sir",
    "Okay",
    "Hmm",
    "Yes",
    "Hello hello",
])
def test_post_farewell_noise_true(text):
    assert is_post_farewell_noise(text)


@pytest.mark.parametrize("text", [
    "Hello, one more thing",
    "Wait, I have another question",
    "No, don't hang up. I need to ask something",
    "Are you there?",
    "What about the price?",
])
def test_post_farewell_noise_false(text):
    assert not is_post_farewell_noise(text)


@pytest.mark.parametrize("text", [
    "Theek hai, options bhej deta hoon isi number pe. Bye.",
    "Thank you, have a great day.",
    "Okay, thank you. Goodbye.",
    "ठीक है, कल 4 बजे call करता हूँ। धन्यवाद।",
])
def test_farewell_true(text):
    assert is_farewell(text)


@pytest.mark.parametrize("text", [
    "Thanks. What kind of car are you looking at?",
    "Take care of that and tell me, which model?",
    "Anything else you need, or shall we wrap up?",
])
def test_farewell_false(text):
    assert not is_farewell(text)


# --- Edge cases from the production scenario list: the deterministic (regex) layer must not misread these ---

from app.services.voice_stream import BACKCHANNEL, CALLER_GOODBYE, HOLD, KEEP_LINE


@pytest.mark.parametrize("text", [
    "Wait, don't hang up.",
    "Wait, what's the price?",
    "Hello, one more thing, can it speak Hindi?",
    "Actually wait, tell me the pricing",
    "Not interested... wait, tell me the pricing.",
])
def test_post_farewell_genuine_continuation_is_not_noise_or_closing(text):
    assert not is_post_farewell_noise(text)
    assert not is_caller_closing(text)


@pytest.mark.parametrize("text", ["Hold on.", "One second.", "ek minute", "रुकिए", "Wait", "just a sec"])
def test_hold_is_not_a_closing(text):
    assert HOLD.search(text)
    assert not is_caller_closing(text)


@pytest.mark.parametrize("text", ["Okay.", "Hmm okay.", "Yes okay.", "Okay sir."])
def test_repeated_okay_is_not_a_closing(text):
    assert not is_caller_closing(text)


def test_keep_line_overrides_goodbye_token():
    for text in ("Wait, don't hang up.", "phone mat rakho", "call mat kaato yaar"):
        assert KEEP_LINE.search(text)
        assert not is_caller_closing(text)
    assert CALLER_GOODBYE.search("ok bye") and is_caller_closing("ok bye")


def test_backchannel_is_listening_not_barge_in():
    for text in ("hmm", "haan ji", "ok", "accha", "हाँ"):
        assert BACKCHANNEL.match(text)
    assert not BACKCHANNEL.match("okay okay I understand")
