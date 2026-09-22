"""Edge TTS fallback must pick a voice matching the persona's gender, not just language."""
from app.services import tts


def test_edge_voice_matches_female_speaker_english():
    voice = tts._EDGE_VOICE_MAP["en-IN"][1]
    assert "Neerja" in voice


def test_edge_voice_matches_male_speaker_hindi():
    voice = tts._EDGE_VOICE_MAP["hi-IN"][0]
    assert "Madhur" in voice


def test_edge_mp3_bytes_picks_female_voice_for_female_speaker(monkeypatch):
    seen = {}

    class FakeCommunicate:
        def __init__(self, text, voice):
            seen["voice"] = voice

        async def stream(self):
            if False:
                yield {}

    monkeypatch.setattr("edge_tts.Communicate", FakeCommunicate, raising=False)
    tts._edge_mp3_bytes("Namaste, main Priya bol rahi hoon.", "hi-IN", "priya")
    assert seen["voice"] == "hi-IN-SwaraNeural"


def test_edge_mp3_bytes_picks_male_voice_for_male_speaker(monkeypatch):
    seen = {}

    class FakeCommunicate:
        def __init__(self, text, voice):
            seen["voice"] = voice

        async def stream(self):
            if False:
                yield {}

    monkeypatch.setattr("edge_tts.Communicate", FakeCommunicate, raising=False)
    tts._edge_mp3_bytes("Hello, this is Rahul.", "en-IN", "rahul")
    assert seen["voice"] == "en-IN-PrabhatNeural"
