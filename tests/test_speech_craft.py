from app.services.speech_craft import humanize_voice_speech


def test_rupee_amount_converted_to_lakh():
    out = humanize_voice_speech("The price is 150000 rupees")
    assert "1 lakh 50 thousand rupees" in out


def test_percent_converted_to_spoken_form():
    out = humanize_voice_speech("The interest rate is 18%")
    assert "18 percent" in out
    assert "%" not in out


def test_decimal_percent_converted_to_spoken_form():
    out = humanize_voice_speech("GST is 7.5%")
    assert "7.5 percent" in out
