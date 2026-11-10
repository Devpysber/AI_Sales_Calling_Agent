from app.services import tts


def test_synthesize_humanizes_percent_before_engine_call(monkeypatch):
    # conftest's fake_ai fixture stubs tts.synthesize wholesale; undo that
    # here so the real function (and its humanize wiring) runs.
    monkeypatch.undo()
    seen = {}

    def fake_sarvam_synthesize(text, language=None, speaker=None):
        seen["text"] = text
        return b""

    monkeypatch.setattr(tts, "_local", lambda: False)
    monkeypatch.setattr(tts, "_sarvam_synthesize", fake_sarvam_synthesize)
    tts.synthesize("The GST rate is 18%", language="en-IN")
    assert "18 percent" in seen["text"]
    assert "%" not in seen["text"]


def test_synthesize_pcm_humanizes_percent_before_engine_call(monkeypatch):
    monkeypatch.undo()
    seen = {}

    def fake_sarvam_synthesize_pcm(text, language=None, speaker=None):
        seen["text"] = text
        return b""

    monkeypatch.setattr(tts, "_local", lambda: False)
    monkeypatch.setattr(tts, "_sarvam_synthesize_pcm", fake_sarvam_synthesize_pcm)
    tts.synthesize_pcm("The GST rate is 18%", language="en-IN")
    assert "18 percent" in seen["text"]
    assert "%" not in seen["text"]
