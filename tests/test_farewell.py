from app.services.voice_stream import is_caller_closing, is_farewell, is_post_farewell_noise


def test_is_farewell_ignores_request_words_outside_last_sentence():
    assert is_farewell("I'll send the options on this number. Take care.")
    assert is_farewell("Options bhej deta hoon. Bye.")
    assert not is_farewell("Which number should I send it to?")
    assert is_farewell("Theek hai, main kal 4 baje call karta hoon. Dhanyavaad, नमस्ते.")


def test_caller_closing_allows_refusal_prefix():
    assert is_caller_closing("No no thank you")
    assert is_caller_closing("nahi nahi thank you ji")


def test_post_farewell_noise():
    assert is_post_farewell_noise("Hello")
    assert is_post_farewell_noise("Hello. Yes, sir.")
    assert not is_post_farewell_noise("hello I have one more question")
