"""
Real-time phone conversation over a Plivo bidirectional audio stream.

    caller audio (μ-law 8 kHz) --> Sarvam streaming STT (VAD + transcript)
                                        |
                                 end of caller turn
                                        |
                             LLM (agent.respond, thread)
                                        |
                  Sarvam TTS per sentence, in parallel (PCM 8 kHz)
                                        |
    caller <-- playAudio (μ-law) <------+

Turn taking:
- Sarvam emits START_SPEECH / END_SPEECH and a transcript right after END_SPEECH.
  The agent answers TURN_END_GRACE_MS after a transcript unless the caller starts
  speaking again (their words are then merged into one turn).
- Barge-in: if the caller starts speaking while the agent is talking, queued
  audio is cleared (clearAudio) and the in-flight reply is cancelled.
- Silence: after the agent finishes speaking, SILENCE_PROMPT_SECONDS of quiet
  triggers "could you repeat"; after MAX_SILENT_PROMPTS the call ends politely.

Call state still lives in the shared session store, so /api/plivo/hangup and
the end-of-call summary work exactly as in gather mode.
"""

import asyncio
import base64
import contextlib
import json
import random
import re
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

import numpy as np
import websockets
from fastapi import WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.core.logging import get_logger
from app.services import agent, agents, call_session, events, rag, tts
from app.services.call_service import IST, CallService, session_agent

log = get_logger(__name__)

# Calls streaming on this process, by session id: live supervision (monitor, guide, take over) attaches here.
LIVE: dict[str, "CallStream"] = {}

SILENCE_PROMPT_SECONDS = 8.0
MAX_SILENT_PROMPTS = 2
PLAY_CHUNK_BYTES = 1600          # 200 ms of 8 kHz μ-law per playAudio message
SENTENCE_SPLIT = re.compile(r"(?<=[।.!?])\s+")
SENTENCE_END = re.compile(r"[।!?]|\.(?=\s)")
CLAUSE_END = re.compile(r"[,;:]\s")
LANGUAGE_SWITCH_CONFIDENCE = 0.6   # Sarvam language_probability needed to follow the caller into another language
ECHO_TAIL_SECONDS = 0.6            # audio still in flight at Plivo after the last chunk was queued
ECHO_OVERLAP = 0.6                 # share of a transcript's words that must come from the agent's own speech
ECHO_MEMORY = 6                    # how many recent agent utterances to compare against
# A goodbye from the agent. Bare "धन्यवाद" is deliberately absent: it opens ordinary mid-call sentences
# ("धन्यवाद जी, आपकी email id बता दीजिए"), so only closings that end a conversation are listed.
FAREWELL = re.compile(
    r"take care|have a (great|good|nice) day|see you (at|then|soon|there)|goodbye|bye for now|"
    r"thanks for your time|thank you for (calling|your time)|"
    r"\bbye\b|bye bye|good night|all the best|namaste|खयाल रखना|ख्याल रखना|ध्यान रखिए|ध्यान रखें|नमस्ते|शुक्रिया|धन्यवाद|थैंक यू|thank you\W*$|thanks\W*$|"
    r"समय के लिए धन्यवाद|बात करने के लिए धन्यवाद|शुभ दिन|अच्छा दिन|फिर मिलते|अलविदा|ध्यान रखना|दिन शुभ हो", re.I)
# A reply that still asks the caller for something is not a farewell, whatever its last words are.
REQUEST_WORDS = re.compile(r"email|e-mail|number|नंबर|बताइए|बताइये|बता दीजिए|बता दो|\bkab\b|कब|kitne|कितने|which|when|what|कौन", re.I)
# Explicit goodbye tokens: these end the call wherever they appear in the caller's sentence.
CALLER_GOODBYE = re.compile(
    r"\b(bye|goodbye|ok bye|okay bye|alvida|cut the call|hang up|rakhta hoon|rakhti hoon|rakh do|milte hain|"
    r"theek hai bye|thik hai bye|tata|ta ta)\b|अलविदा|बाय|बाई|टाटा|रखता हूँ|रखती हूँ|रखता हूं|रखती हूं|रख दो|फ़ोन रखो|फोन रखो|कॉल काटो|फिर मिलते", re.I)
# Acknowledgements that only count as a closing when they are the caller's whole short utterance:
# "ठीक है, कर दीजिए" is agreement, "ठीक है" alone after a goodbye is a goodbye.
# "No no thank you" is also a closing: repeated refusal words are allowed before the closing phrase.
CALLER_CLOSING = re.compile(
    r"^\W*(?:(?:no|nahi|nahin|nope|ok|okay|ji|haan|acha|accha|नहीं|ना|नो)\W*)*(?:(?:thanks|thank you|thank u|no thanks|that'?s all|nothing else|"
    r"take care|theek hai|thik hai|thik|chalo|chalo theek hai|chalo thik hai|"
    r"धन्यवाद|शुक्रिया|ठीक है|बस इतना|और कुछ नहीं|चलो)(?: ji| जी| bhai| sir| madam)?\W*)+$", re.I)
# "don't hang up" / "phone mat rakho" contains a goodbye token but means the opposite.
KEEP_LINE = re.compile(r"(don'?t|do not|never|mat|नहीं|मत)\W+(?:\w+\W+)?(hang up|cut|rakh|रख|काट|kaat)|(rakh|रख|काट|kaat)\w*\W+(mat|नहीं|मत)\b", re.I)
# Short acknowledgements a listener makes while the agent talks ("haan", "ji", "hmm"): never a barge-in.
BACKCHANNEL = re.compile(r"^(hmm+|h+m+|haan( ji)?|haa|han|ji( haan)?|ha|ok(ay)?|accha|acha|achha|theek( hai)?|thik( hai)?|"
                         r"right|yes|yeah|yep|sure|hello|हाँ|हां|जी( हाँ| हां)?|हम्म+|ठीक( है)?|अच्छा|ओके|सही)[.!]?$", re.I)
# Voicemail greetings and carrier announcements, in the languages Indian networks play them in.
VOICEMAIL = re.compile(
    r"leave (a|your) message|after the (tone|beep)|voice ?mail|not reachable|switched off|not answering|"
    r"out of coverage|cannot be reached|is busy|not available|कृपया (बाद में|थोड़ी देर)|संपर्क नहीं|उपलब्ध नहीं|"
    r"बंद है|संदेश (छोड़|रिकॉर्ड)|पहुंच से बाहर|पहुँच से बाहर|कवरेज क्षेत्र", re.I)
VOICEMAIL_WINDOW_SECONDS = 15.0    # only the opening of a call can be a recording
# The caller asks for a moment ("ek minute", "hold on", "रुकिए"): the silence loop waits instead of prompting.
HOLD = re.compile(r"(ek|one|एक) (minute|second|sec|min|मिनट|सेकंड)|hold on|hold kar|रुकिए|रुको|\bruko\b|rukiye|ek min\b|"
                  r"थोड़ा रुक|just a (sec|second|moment)|one moment|\bwait\b", re.I)
HOLD_SECONDS = 45.0
HOLD_ACK_AFTER = 20.0
# A transcript that ends on one of these is not a finished sentence: wait longer before replying.
CONNECTOR_END = re.compile(r"(^|\s)(aur|और|lekin|लेकिन|toh|तो|matlab|मतलब|and|but|so|like|around|about|ke|ka|ki|"
                           r"के|का|की|main|mera|मेरा|मेरी|मैं|par|पर|ya|या|or|se|से|ko|को|the|a|an|"
                           r"is|are|was|for|to|in|of|with|my|our|your|मुझे)\W*$", re.I)
NUMBER_FRAGMENT = re.compile(r"(^|\s)(\d+|one|two|three|four|five|six|seven|eight|nine|ten|ek|do|teen|char|paanch|"
                             r"एक|दो|तीन|चार|पांच|पाँच|lakh|लाख|hazaar|हज़ार|hazar|crore|करोड़|thousand|hundred)\W*$", re.I)
SHORT_ANSWER = re.compile(r"^\W*(haan|nahi|nahin|yes|no|ok|okay|ji|हाँ|हां|नहीं|जी|ठीक है|theek hai)\W*$", re.I)
UNFINISHED_GRACE_MS = 700
# Lines the stream speaks on its own (the plivo module's PROMPTS cover the shared ones).
STREAM_PROMPTS = {
    "still_there": {"en": "Hello? Can you hear me?", "hi": "हैलो? आवाज़ आ रही है?"},
    "hold_ack": {"en": "Sure, I'm still on the line.", "hi": "जी, मैं line पर हूँ।"},
    "filler": {"en": "One moment.", "hi": "जी, एक second।"},
}
# A reply whose first audio has not started this long after the caller stopped gets a short cached
# acknowledgement first. On a slow network (providers 5-10s away) the line was dead silent until the
# answer came, and callers took that for a dropped call and hung up.
FILLER_AFTER_SECONDS = 2.5
# Seconds before the persona's max_call_minutes budget at which the agent is told to wrap up, so the
# goodbye is spoken before Plivo's hard time_limit (budget + PlivoService.HARD_LIMIT_GRACE_SECONDS) cuts the line.
WRAP_UP_LEAD_SECONDS = 45
WRAP_UP_GUIDANCE = ("You are almost out of time for this call. In one or two short sentences, sum up what was agreed "
                    "or offer a callback, thank the caller and say goodbye. End your reply with <END>.")
STEER_GUIDANCE = ("About three minutes / most of the talk budget is used. Do not end abruptly. Decide silently: is this "
                  "lead qualified (need, budget, timeline, next step known)? If yes, move to the call to action in one "
                  "short sentence. If exactly one critical field is missing, ask only that one question. If they are "
                  "still actively discussing, continue naturally but say less: acknowledge in a few words, then one "
                  "question. Every reply under 120 characters.")
BUDGET_GUIDANCE = ("The talk budget for this call is spent. Do not hang up mid-topic. If the lead is qualified: propose or "
                   "confirm ONE concrete visit or callback slot, confirm it back, and end the call with <END>. If not "
                   "qualified: ask the single most important missing question, then offer a callback or WhatsApp details "
                   "and close. No new topics, no pitch, no recap. Every reply under 100 characters.")
QUIET_GUIDANCE = ("The caller has gone quiet. In a few words check they are still there and repeat your last "
                  "question, shorter. Do not start a new topic.")
POST_FAREWELL_GUIDANCE = ("You already said goodbye. If the caller is only checking the line or acknowledging (\"hello?\", "
                          "\"are you there\", \"haan ji\"), answer in two or three words and say goodbye again with <END>. "
                          "Only if they raise a genuine new question or request, answer it briefly and continue. Never "
                          "restart the introduction or the pitch.")
FAREWELL_SILENCE_SECONDS = 3.0     # after the agent said goodbye, this much quiet ends the call without another word
STT_RETRY_BASE = 0.3               # reconnect backoff for Sarvam STT: 0.3s doubling to STT_RETRY_CAP, with jitter
STT_RETRY_CAP = 5.0
STT_MAX_FAILURES = 5               # consecutive failed connects before the caller is handed over
TTS_STALL_SECONDS = 6.0            # no audio and no "final" after the text was flushed: the socket is stuck
REPLY_DEADLINE_SECONDS = 45.0      # a whole turn (LLM + speech) can never take longer than this
SPOKEN_ONLY = ("Answer out loud in 1-2 short sentences of plain speech. Do not call any tool. "
               "If a meeting or callback time was agreed, repeat the day and time back to confirm it.")

# ---------------- G.711 μ-law ----------------


def _build_mulaw_decode() -> np.ndarray:
    u = ~np.arange(256, dtype=np.uint8)
    sign, exponent, mantissa = u & 0x80, (u >> 4) & 0x07, u & 0x0F
    magnitude = ((mantissa.astype(np.int32) << 3) + 0x84) << exponent.astype(np.int32)
    return np.where(sign != 0, 0x84 - magnitude, magnitude - 0x84).astype(np.int16)


_MULAW_DECODE = _build_mulaw_decode()


def mulaw_to_pcm16(data: bytes) -> bytes:
    return _MULAW_DECODE[np.frombuffer(data, dtype=np.uint8)].astype("<i2").tobytes()


def pcm16_to_mulaw(data: bytes) -> bytes:
    x = np.frombuffer(data[: len(data) // 2 * 2], dtype="<i2").astype(np.int32)
    sign = np.where(x < 0, 0x80, 0)
    x = np.minimum(np.abs(x), 32635) + 0x84
    exponent = np.clip(np.floor(np.log2(x)).astype(np.int32) - 7, 0, 7)
    mantissa = (x >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa) & 0xFF).astype(np.uint8).tobytes()


# Caller asks to switch language ("हिन्दी में बोलो", "speak in English", "ગુજરાતીમાં"): name -> code.
LANGUAGE_REQUESTS = [
    (re.compile(r"hindi|हिन्दी|हिंदी|હિન્દી|હિંદી", re.I), "hi-IN"),
    (re.compile(r"english|इंग्लिश|अंग्रेज़ी|अंग्रेजी|ઇંગ્લિશ|અંગ્રેજી", re.I), "en-IN"),
    (re.compile(r"gujarati|गुजराती|ગુજરાતી", re.I), "gu-IN"),
    (re.compile(r"marathi|मराठी", re.I), "mr-IN"),
    (re.compile(r"tamil|तमिल|தமிழ்", re.I), "ta-IN"),
    (re.compile(r"telugu|तेलुगु|తెలుగు", re.I), "te-IN"),
    (re.compile(r"bengali|bangla|बंगाली|বাংলা", re.I), "bn-IN"),
    (re.compile(r"kannada|कन्नड़|ಕನ್ನಡ", re.I), "kn-IN"),
    (re.compile(r"malayalam|मलयालम|മലയാളം", re.I), "ml-IN"),
    (re.compile(r"punjabi|पंजाबी|ਪੰਜਾਬੀ", re.I), "pa-IN"),
]


def requested_language(text: str) -> str | None:
    """Language the caller asks the agent to speak. With two names ("not Gujarati, in Hindi") the last one wins."""
    hits = sorted((m.start(), code) for pattern, code in LANGUAGE_REQUESTS for m in pattern.finditer(text))
    return hits[-1][1] if hits else None


NAME_PATTERNS = [
    re.compile(r"(?:my name is|i am|i'm|this is|name's)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"),
    re.compile(r"(?:मेरा नाम|मेरा नाम है)\s+([\u0900-\u097F]+(?:\s+[\u0900-\u097F]+)?)"),
    re.compile(r"(?:मैं)\s+([\u0900-\u097F]{2,}(?:\s+[\u0900-\u097F]{2,})?)\s+(?:बोल रहा हूँ|बोल रही हूँ|बोल रहा हूं|बोल रही हूं)(?!\s*(?:से|से बोल))"),
]
NOT_NAMES = {"है", "हूँ", "हूं", "interested", "busy", "fine", "good", "calling", "looking", "से", "यहाँ", "यहां", "अभी", "sir", "madam", "ji", "जी"}
# "मैं भोपाल से बोल रहा हूँ" names a place, not a person.
_PLACE_CUE = re.compile(r"\b(?:से|from)\s+(?:बोल|call|calling|speaking)|(?:से बोल रह)")


def spoken_name(text: str) -> str | None:
    """A name the caller states about themselves ("my name is Neha", "मेरा नाम नेहा है"); None when unsure."""
    if _PLACE_CUE.search(text):
        return None
    for pattern in NAME_PATTERNS:
        m = pattern.search(text)
        if m:
            name = " ".join(w for w in m.group(1).split() if w.lower() not in NOT_NAMES and w not in NOT_NAMES).strip()
            if 2 <= len(name) <= 40 and not any(ch.isdigit() for ch in name):
                return name
    return None


EMAIL = re.compile(r"[\w+-]+(?:\s*(?:\.|\bdot\b|\bunderscore\b)\s*[\w+-]+)*"  # "ashish dot sharma", "ashish underscore sharma"
                   r"\s*(?:@|\bat the rate\b|\bat\b)\s*[\w-]+\s*(?:\.|\bdot\b)\s*[a-z]{2,}(?:\s*(?:\.|\bdot\b)\s*[a-z]{2,})?", re.I)
# The caller is replacing an email already on record ("actually it's ...", "nahi, ... hai").
EMAIL_CORRECTION = re.compile(r"\b(actually|correct|correction|wrong|galat|nahi|nahin|not|no,)\b|नहीं|गलत|सही", re.I)


def spoken_email(text: str) -> str | None:
    """An email said on a call ("neha at the rate gmail dot com" or typed-style), normalised; None when unsure."""
    m = EMAIL.search(text)
    if not m:
        return None
    before = text[:m.start()].rstrip()
    if re.search(r"[\u0900-\u097F]\s*$", before) or re.search(r"[\u0900-\u097F]", m.group(0)):
        return None  # part of the address was heard in Devanagari: ask them to spell it, never guess
    email = re.sub(r"\s*(?:\bat the rate\b|\bat\b)\s*", "@", m.group(0), count=1, flags=re.I)
    email = re.sub(r"\s*\bdot\b\s*", ".", email, flags=re.I)
    email = re.sub(r"\s*\bunderscore\b\s*", "_", email, flags=re.I).replace(" ", "").lower()
    return email if re.fullmatch(r"[\w.+-]+@[\w-]+(\.[a-z]{2,})+", email) else None


def echo_words(text: str) -> set[str]:
    """Words of an utterance, lowercased and stripped of punctuation, for echo comparison."""
    return {w for w in re.split(r"[^\wऀ-ॿঀ-෿]+", text.lower()) if len(w) > 2}


def looks_like_echo(text: str, spoken: list[str]) -> bool:
    """
    True when a transcript is mostly the agent's own words coming back through the caller's line.

    Phone echo re-transcribes what we just played, so the caller appears to say our own sentence.
    Compared by word overlap, not equality: recognition mangles the echo ("CarsIndias" -> "Cars India").
    """
    words = echo_words(text)
    if not words:
        return False
    for said in spoken:
        mine = echo_words(said)
        if mine and len(words & mine) / len(words) >= ECHO_OVERLAP:
            return True
    return False


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in SENTENCE_SPLIT.split(text.strip()) if p.strip()]
    return parts or [text.strip()]


def is_farewell(reply: str) -> bool:
    """The agent's reply ends the conversation: its last sentence says goodbye and asks for nothing there."""
    if not reply or "?" in reply:
        return False
    last = split_sentences(reply)[-1]
    if REQUEST_WORDS.search(last):
        return False
    return bool(FAREWELL.search(last))


def is_caller_closing(text: str) -> bool:
    """The caller is saying goodbye: an explicit goodbye anywhere, or a short acknowledgement that is all they said."""
    text = (text or "").strip()
    if not text or KEEP_LINE.search(text):
        return False
    if CALLER_GOODBYE.search(text):
        return True
    return len(text.split()) <= 6 and bool(CALLER_CLOSING.match(text))


# Deterministic customer requests answered without an LLM turn: zero prompt tokens, one short cached TTS line.
# Do-not-call: an explicit "stop calling / remove my number". A "call later" is a callback, so words that
# schedule (baad mein, kal, shaam, tomorrow, evening…) keep the turn with the model.
DNC_REQUEST = re.compile(r"(call|phone|कॉल|फोन)\s*(mat|मत)\s*(kar|karo|karna|karein|kijiye|कर|करो|करना|करें|कीजिए)|"
                         r"(don'?t|do not|stop|never)\s+(call|calling|ring)|remove (my|this) number|unsubscribe|"
                         r"number\s*(hata|hatao|hata do|delete|nikal|निकाल|हटा)|dobara (call|phone) (mat|nahi|na)\b|"
                         r"दोबारा (कॉल|फोन) (मत|नहीं|ना)", re.I)
DNC_NOT_NOW = re.compile(r"\b(baad|bad me|later|kal|tomorrow|shaam|subah|evening|morning|afternoon|after|abhi nahi|"
                         r"busy|meeting|drive|driving|time|baje|o'?clock)\b|बाद|कल|शाम|सुबह|अभी नहीं|बजे|व्यस्त", re.I)
DNC_LINE = {"en": "Sorry for the trouble. I'm removing your number now; you won't be called again. Have a good day.",
            "hi": "तकलीफ़ के लिए माफ़ी। आपका number अभी हटा रहा हूँ, दोबारा call नहीं आएगा। धन्यवाद।"}
# "Send me the details on WhatsApp / SMS": a template text with the site link goes out and one line confirms it.
DETAILS_REQUEST = re.compile(r"(whatsapp|sms|message|msg|text|मैसेज|मेसेज|व्हाट्सएप|व्हाट्सऐप)", re.I)
DETAILS_WORDS = re.compile(r"(detail|details|link|brochure|info|information|price|rate|list|catalog|website|site|"
                           r"डिटेल|जानकारी|लिंक|भेज|bhej|send|kar do|karo|कर दो)", re.I)
DETAILS_LINE = {"en": "Sure, I've sent the details to this number. Anything else I can help with?",
                "hi": "जी, details इसी number पर भेज दी हैं। और कुछ बताऊँ?"}


def is_post_farewell_noise(text: str) -> bool:
    """After the agent's goodbye, a greeting or acknowledgement ("hello", "haan ji", "ok sir") means the
    caller has nothing more: the call should end, not restart."""
    words = re.findall(r"[\wऀ-ॿ']+", (text or "").lower())
    if not words or len(words) > 4:
        return False
    return all(BACKCHANNEL.match(w) or w in ("sir", "madam", "ji", "bhai", "haanji", "yes", "hello", "hi", "हेलो", "हैलो") for w in words)


def turn_grace_ms(text: str) -> int:
    """
    How long to wait after a transcript before answering. A sentence that stops on a connector, a bare
    number or without any punctuation is probably not finished ("my budget is around... ten lakh"), so
    the agent waits longer; a question or a one-word answer is complete and gets the short grace.
    """
    base = settings.turn_end_grace_ms
    text = (text or "").strip()
    if not text:
        return base
    if text.endswith("?") or SHORT_ANSWER.match(text):
        return base
    if CONNECTOR_END.search(text) or NUMBER_FRAGMENT.search(text) or not re.search(r"[।.!?]$", text):
        return max(base, UNFINISHED_GRACE_MS)
    return base


# ---------------- Sarvam streaming STT ----------------


def digits(value: str | None) -> str:
    """Just the digits of a phone number, for comparing numbers written in different formats."""
    return "".join(c for c in str(value or "") if c.isdigit())


def merge_call_context(session_lead: dict, fresh: dict | None) -> dict:
    """
    Refresh the lead from the CRM without losing this call's context. The CRM row carries no `call_goal`, so using
    it raw drops the instruction that tells the agent which details to ask a new caller for.
    """
    if not fresh:
        return session_lead
    carried = {k: v for k, v in (session_lead or {}).items()
               if k in ("collect", "call_purpose", "call_goal") and v is not None}
    lead = {**fresh, **carried}
    if lead.get("collect") is not None and lead.get("call_purpose") == "inbound":
        # Recomputed against the fresh row, so details already saved during this call drop off the ask list.
        lead["call_goal"] = agent.call_goal(lead, "inbound_new")
    return lead


class SilenceGate:
    """
    Decides which 20 ms frames are worth sending to paid speech-to-text.
    Voiced frames pass, plus PREROLL frames before speech (soft word onsets) and TAIL frames after it
    (Sarvam needs trailing silence to emit END_SPEECH). Long silences - most of the time the agent is
    talking - are not sent at all. The noise floor adapts to the line.
    """

    PREROLL = 10      # 200 ms
    TAIL = 60         # 1.2 s

    def __init__(self):
        self.noise = 200.0
        self.preroll: list[bytes] = []
        self.tail_left = 0

    def process(self, pcm: bytes) -> list[bytes]:
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
        voiced = rms > max(350.0, self.noise * 2.5)
        if not voiced:
            self.noise = 0.95 * self.noise + 0.05 * rms
        if voiced:
            out = self.preroll + [pcm]
            self.preroll = []
            self.tail_left = self.TAIL
            return out
        if self.tail_left > 0:
            self.tail_left -= 1
            return [pcm]
        self.preroll = (self.preroll + [pcm])[-self.PREROLL:]
        return []


class SarvamSTT:
    """One streaming recognition socket; reconnects transparently if Sarvam closes it."""

    def __init__(self, language: str):
        self.language = language if language in tts.LANGUAGES else "unknown"
        self.ws = None
        self._lock = asyncio.Lock()

    @property
    def url(self) -> str:
        query = urlencode({"language-code": self.language, "model": settings.sarvam_stt_model, "mode": "transcribe",
                           "sample_rate": "8000", "input_audio_codec": "pcm_s16le", "vad_signals": "true"})
        return f"wss://api.sarvam.ai/speech-to-text/ws?{query}"

    async def connect(self):
        async with self._lock:
            if self.ws is None or self.ws.state.name != "OPEN":
                self.ws = await websockets.connect(self.url, additional_headers={"Api-Subscription-Key": settings.sarvam_api_key},
                                                   open_timeout=5, ping_interval=20, max_queue=256)
        return self.ws

    async def send_pcm(self, pcm: bytes):
        ws = self.ws
        if ws is None:
            return
        try:
            await ws.send(json.dumps({"audio": {"data": base64.b64encode(pcm).decode(), "sample_rate": "8000",
                                                "encoding": "audio/wav"}}))
        except websockets.ConnectionClosed:
            pass

    async def close(self):
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.close()


class SarvamTTS:
    """
    Sarvam streaming TTS socket (text in, 8 kHz mu-law audio out), opened ahead of time so the
    first reply of a turn does not pay the connect cost.
    """

    def __init__(self, language: str, speaker: str | None):
        self.language = language if language in tts.LANGUAGES else "en-IN"
        self.speaker = speaker
        self.ws = None
        self._connecting: asyncio.Task | None = None

    @property
    def url(self) -> str:
        return f"wss://api.sarvam.ai/text-to-speech/ws?{urlencode({'model': settings.sarvam_tts_model, 'send_completion_event': 'true'})}"

    async def _open(self):
        ws = await websockets.connect(self.url, additional_headers={"Api-Subscription-Key": settings.sarvam_api_key},
                                      open_timeout=5, ping_interval=20, max_queue=512)
        # pace/loudness slightly under 1: the default delivery is brisk and announcer-like on a phone line.
        config = {"target_language_code": self.language, "speech_sample_rate": "8000", "output_audio_codec": "mulaw",
                  "pace": 0.95, "loudness": 1.0,
                  "min_buffer_size": 30, "max_chunk_length": 150}
        if self.speaker:
            config["speaker"] = self.speaker
        await ws.send(json.dumps({"type": "config", "data": config}))
        return ws

    def warm(self):
        if (self.ws is None or self.ws.state.name != "OPEN") and (self._connecting is None or self._connecting.done()):
            self._connecting = asyncio.create_task(self._open())

    async def get(self):
        if self.ws is not None and self.ws.state.name == "OPEN":
            return self.ws
        self.warm()
        try:
            self.ws = await self._connecting
        finally:
            self._connecting = None
        return self.ws

    async def reset(self):
        """Drop everything in flight (barge-in) and open a fresh socket in the background."""
        old, self.ws = self.ws, None
        if old is not None:
            with contextlib.suppress(Exception):
                await old.close()
        self.warm()

    async def ping(self):
        if self.ws is not None and self.ws.state.name == "OPEN":
            with contextlib.suppress(Exception):
                await self.ws.send(json.dumps({"type": "ping"}))

    async def close(self):
        if self._connecting and not self._connecting.done():
            self._connecting.cancel()
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.close()


class _LocalSocket:
    """
    Socket-shaped wrapper over local IndicF5 so reply() works unchanged. Text is buffered into
    sentences (the first one may be cut at a clause to get audio out sooner) and each chunk is
    synthesized on the GPU as soon as it is complete, in order.
    """

    FIRST_CLAUSE_CHARS = 25

    def __init__(self, language: str, speaker: str | None):
        self.language, self.speaker = language, speaker
        self.state = type("State", (), {"name": "OPEN"})()
        self.buffer = ""
        self.first = True
        self.jobs: asyncio.Queue = asyncio.Queue()
        self.out: asyncio.Queue = asyncio.Queue()
        self.worker = asyncio.create_task(self._work())

    def _cut(self, final: bool) -> list[str]:
        chunks = []
        while True:
            m = SENTENCE_END.search(self.buffer)
            if not m and self.first and len(self.buffer) >= self.FIRST_CLAUSE_CHARS:
                m = CLAUSE_END.search(self.buffer, self.FIRST_CLAUSE_CHARS - 10)
            if not m:
                break
            chunks.append(self.buffer[:m.end()])
            self.buffer = self.buffer[m.end():]
            self.first = False
        if final and self.buffer.strip():
            chunks.append(self.buffer)
            self.buffer = ""
        return [c.strip() for c in chunks if re.search(r"[^\W\d_]", c)]

    async def send(self, raw: str):
        msg = json.loads(raw)
        if msg.get("type") == "text":
            self.buffer += msg["data"]["text"]
            for chunk in self._cut(final=False):
                await self.jobs.put(chunk)
        elif msg.get("type") == "flush":
            for chunk in self._cut(final=True):
                await self.jobs.put(chunk)
            await self.jobs.put(None)

    async def _work(self):
        try:
            while True:
                chunk = await self.jobs.get()
                if chunk is None:
                    await self.out.put(json.dumps({"type": "event", "data": {"event_type": "final"}}))
                    self.first = True
                    continue
                language = tts.detect_language(chunk, self.language)
                pcm = await asyncio.to_thread(tts.synthesize_pcm, chunk, language, self.speaker)
                mulaw = await asyncio.to_thread(pcm16_to_mulaw, pcm)
                await self.out.put(json.dumps({"type": "audio", "data": {"audio": base64.b64encode(mulaw).decode()}}))
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001 - surfaced to reply()
            await self.out.put(json.dumps({"type": "error", "data": str(e)}))

    async def recv(self) -> str:
        return await self.out.get()

    async def close(self):
        self.state.name = "CLOSED"
        self.worker.cancel()


class LocalTTS(SarvamTTS):
    """Same interface as SarvamTTS, backed by the local IndicF5 model."""

    def warm(self):
        if self.ws is None or self.ws.state.name != "OPEN":
            self.ws = _LocalSocket(self.language, self.speaker)

    async def get(self):
        self.warm()
        return self.ws

    async def ping(self):
        pass


class EdgeTTS(SarvamTTS):
    """
    Fallback TTS backed by Microsoft Edge TTS (free, no API key).
    Uses the same _LocalSocket interface — text is buffered into sentences,
    synthesized via tts.synthesize_pcm (which uses Edge TTS), converted to
    8 kHz mu-law and sent to Plivo, exactly as LocalTTS does.
    """

    def warm(self):
        if self.ws is None or self.ws.state.name != "OPEN":
            self.ws = _LocalSocket(self.language, self.speaker)

    async def get(self):
        self.warm()
        return self.ws

    async def reset(self):
        old, self.ws = self.ws, None
        if old is not None:
            with contextlib.suppress(Exception):
                await old.close()
        self.warm()

    async def ping(self):
        pass

    async def close(self):
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.close()


def make_tts(language: str, speaker: str | None):
    engine = settings.tts_engine.lower()
    if engine == "indicf5":
        return LocalTTS(language, speaker)
    # Use Sarvam streaming WebSocket only when the API key is present.
    # If not set, go straight to Edge TTS (free neural voices, no WebSocket).
    if settings.sarvam_api_key:
        return SarvamTTS(language, speaker)
    log.warning("SARVAM_API_KEY not set – phone calls will use Edge TTS fallback")
    return EdgeTTS(language, speaker)



# Words the plain-speech rewrites read together with the noun that follows them.
DETERMINERS = {"the", "our", "your", "my", "their", "a", "an", "this", "that", "these", "those", "support"}


class ReplyFilter:
    """
    Cleans streamed LLM text for speech: drops <tags> and tool-call markup, detects the end-of-call
    marker, and rewrites software words into what a person would say before any of it is spoken.
    """

    def __init__(self):
        self.pending = ""
        self.end_call = False
        self.transfer = False
        self.muted = False
        self.tail = ""   # an unfinished word held back so a rewrite never runs on half a word

    def feed(self, delta: str) -> str:
        self.pending += delta
        out = ""
        while self.pending:
            if self.muted:
                # Tool-call arguments often mention end_call/meeting fields: never end the call from them.
                self.pending = self.pending[-16:]
                return out
            lt = self.pending.find("<")
            if lt < 0:
                out, self.pending = out + self.pending, ""
                break
            out += self.pending[:lt]
            gt = self.pending.find(">", lt)
            if gt < 0:
                self.pending = self.pending[lt:]
                if len(self.pending) > 40:  # a stray "<", not a tag
                    out, self.pending = out + self.pending, ""
                break
            tag = self.pending[lt:gt + 1].lower()
            self.pending = self.pending[gt + 1:]
            if tag == "<end>":
                self.end_call = True
            if tag == "<transfer>":
                self.transfer = True
            if "tool" in tag:
                self.muted = True
        return self.speakable(out)

    def speakable(self, text: str) -> str:
        """
        Rewrite whole words only. Deltas arrive mid-word ("data" + "base"), so the trailing fragment is
        carried to the next call: rewriting it now would miss the word it is about to become.

        A trailing determiner is held back with it, because the rewrites read the pair together — "the
        database" becomes "my notes", and speaking "the" early would strand it in front of the result.
        """
        text = self.tail + text
        cut = max(text.rfind(" "), text.rfind("\n")) + 1
        head, tail = text[:cut], text[cut:]
        held = head.rstrip().rsplit(" ", 1)[-1].lower().strip(",.;:!?")
        if held in DETERMINERS:
            keep = len(head.rstrip()) - len(held)
            head, tail = text[:keep], text[keep:]
        self.tail = tail
        return agent.plain_speech(head)

    def flush(self) -> str:
        rest, self.pending = ("" if self.muted else self.pending), ""
        remainder, self.tail = self.tail + rest, ""
        return agent.plain_speech(remainder)


# ---------------- the call ----------------


class CallStream:
    def __init__(self, ws: WebSocket, session_id: str):
        self.ws = ws
        self.session_id = session_id
        self.session = call_session.get(session_id)
        self.agent_id = session_agent(self.session) if self.session else None
        self.persona = agents.get_profile(self.agent_id) if self.agent_id else {}
        self.stream_id: str | None = None
        self.call_uuid: str | None = None
        # Auto-detect: the caller may answer in another language than the lead's; replies follow what they speak.
        self.stt = SarvamSTT("unknown")
        self.gate = SilenceGate() if settings.stt_silence_gate else None
        self.tts = make_tts(self.session.get("language", "en-IN") if self.session else "en-IN", self.persona.get("voice_speaker"))

        self.agent_speaking = False        # audio queued/playing at Plivo
        self.caller_speaking = False
        self.heard: list[str] = []         # transcripts of the caller's current turn
        self.reply_task: asyncio.Task | None = None
        self.commit_task: asyncio.Task | None = None
        self.compacted_at = 0              # history length when the rolling summary was last rebuilt
        self.mark = 0
        self.hangup_on_mark: str | None = None
        self.transfer_on_mark: str | None = None
        self.transferred = False
        self.quiet_since = time.monotonic()
        self.silent_prompts = 0
        self.closed = False
        self.spoken_recent: list[str] = []   # what the agent actually played, to recognise its own echo
        self.pending_bargein = False         # speech detected while we speak, not yet confirmed to be human
        self.backchannel: str | None = None  # a short "haan"/"ok" heard over our last words: answered once we are quiet
        self.agent_quiet_at = 0.0            # when the last audio finished playing at Plivo
        self.usage = {"tts_chars": 0, "stt_seconds": 0.0, "llm_requests": 0, **((self.session or {}).get("usage") or {})}
        self.speech_ended_at: float | None = None
        self.started_at = time.monotonic()      # when Plivo's stream started: voicemail detection only applies early on
        self.last_reply_farewell = False        # the agent's last line was a goodbye: brief silence ends the call quietly
        self.hold_until = 0.0                   # the caller asked for a moment: no silence prompts until then
        self.hold_acked = False
        self.language_votes: list[str] = []     # consecutive auto-detected languages, for switch hysteresis
        self.stt_failures = 0                   # consecutive failed Sarvam STT connects

        # Live supervision
        self.mode = "ai"                   # "ai" answers the caller; "human" = supervisor speaks, AI stays silent
        self.guidance: str | None = None   # one-shot instruction for the next AI reply
        self.wrap_up_asked = False          # the time-budget wrap-up nudge has been given
        self.close_asked = False   # the cost-budget close nudge (STEER_GUIDANCE) has been given
        self.budget_asked = False   # BUDGET_GUIDANCE (budget spent) has been given
        self.cost_guidance: str | None = None   # persistent steer once the budget is nearly spent
        self.direction: str | None = None  # standing instruction for every AI reply until cleared
        self.monitors: dict[asyncio.Queue, dict] = {}
        from app.services.live_bridge import CallBridge
        self.bridge = CallBridge(self, self.session.get("call_id") if self.session else None)

    # ----- plumbing -----

    def meter(self, key: str, amount: float = 1):
        """Billable usage for cost tracking; saved on the call record when it ends."""
        self.usage[key] = self.usage.get(key, 0) + amount

    def meter_llm(self, delta: dict):
        """A {"usage": {...}} delta from the LLM stream: tokens in and out of this request, onto the call record."""
        u = delta.get("usage") or {}
        self.meter("llm_input_tokens", int(u.get("input_tokens") or 0))
        self.meter("llm_output_tokens", int(u.get("output_tokens") or 0))
        if u.get("estimated"):
            self.usage["llm_tokens_estimated"] = True

    def lang_key(self) -> str:
        return "hi" if (self.session.get("language") or "").startswith("hi") else "en"

    async def send(self, message: dict):
        if not self.closed:
            with contextlib.suppress(Exception):
                await self.ws.send_text(json.dumps(message))

    async def play_pcm(self, pcm: bytes):
        await self.send_mulaw(await asyncio.to_thread(pcm16_to_mulaw, pcm), pcm)

    async def send_mulaw(self, mulaw: bytes, pcm: bytes | None = None):
        """Queue audio for the caller and mirror it to supervisors who are listening."""
        self.agent_speaking = True
        for i in range(0, len(mulaw), PLAY_CHUNK_BYTES):
            await self.send({"event": "playAudio", "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000,
                                                             "payload": base64.b64encode(mulaw[i:i + PLAY_CHUNK_BYTES]).decode()}})
        self.publish_audio("agent", pcm if pcm is not None else mulaw, is_mulaw=pcm is None)

    # ----- live supervision -----

    def state(self) -> dict:
        return {"type": "state", "mode": self.mode, "agent_speaking": self.agent_speaking, "caller_speaking": self.caller_speaking,
                "thinking": bool(self.reply_task and not self.reply_task.done()), "direction": self.direction,
                "guidance": self.guidance}

    def publish(self, event: dict):
        for queue in list(self.monitors):
            if queue.qsize() < 400:
                queue.put_nowait(event)
        self.bridge.emit_nowait(event)  # supervisors connected to other replicas

    def publish_state(self):
        self.publish(self.state())

    def publish_audio(self, track: str, audio: bytes, is_mulaw: bool = False):
        listeners = [q for q, opts in self.monitors.items() if opts.get("listen")]
        if not listeners and not self.bridge.remote_listening:
            return
        pcm = mulaw_to_pcm16(audio) if is_mulaw else audio
        event = {"type": "audio", "track": track, "pcm": base64.b64encode(pcm).decode()}
        for queue in listeners:
            if queue.qsize() < 400:
                queue.put_nowait(event)
        self.bridge.emit_nowait(event)

    def turn(self, role: str, text: str, by: str | None = None):
        call_session.add_turn(self.session, role, text)
        if by:
            self.session["history"][-1]["by"] = by
        self.publish({"type": "turn", "role": role, "text": text, "by": by})

    async def control(self, action: str, text: str = "", now: bool = False, by: str = "admin") -> dict:
        """Supervisor commands. Scripted lines cost no LLM tokens; guidance steers the next AI reply."""
        text = (text or "").strip()
        if action == "takeover":
            self.mode = "human"
            await self.interrupt(force=True)
            self.flush_heard()
        elif action == "release":
            self.mode = "ai"
            self.flush_heard()
            self.quiet_since = time.monotonic()
        elif action == "guide":
            if not text:
                raise ValueError("Write an instruction for the agent.")
            self.guidance = text
            if now:
                await self.interrupt(force=True)
                self.reply_task = asyncio.create_task(self.reply(None, time.monotonic()))
        elif action == "direction":
            self.direction = text or None
        elif action == "say":
            if not text:
                raise ValueError("Write what the agent should say.")
            await self.interrupt(force=True)
            language = tts.detect_language(text, self.session.get("language") or "en-IN")
            pcm = await asyncio.to_thread(tts.synthesize_pcm, text, language, self.persona.get("voice_speaker"))
            self.meter("tts_chars", len(text))
            await self.play_pcm(pcm)
            self.turn("assistant", text, by=by)
            self.save_session()
            await self.checkpoint()
        elif action == "transfer":
            # A supervisor asking for this by hand overrides the "transfer when a caller asks" preference.
            if not self.has_human_line():
                raise ValueError("Set a transfer number for this agent first (Inbound & transfer page).")
            from app.api.plivo import TRANSFER_LINES
            await self.interrupt(force=True)
            self.mode = "human"
            line = TRANSFER_LINES[self.lang_key()]
            self.turn("assistant", line, by=by)
            await self.say_fixed(line)
            self.transfer_on_mark = f"m{self.mark}"
        elif action == "stop_speaking":
            await self.interrupt(force=True)
        elif action == "end":
            from app.api.plivo import PROMPTS
            await self.interrupt(force=True)
            self.mode = "human"
            await self.say_fixed(PROMPTS["goodbye"][self.lang_key()], hangup=True)
        else:
            raise ValueError(f"Unknown action {action!r}")
        self.publish_state()
        return self.state()

    def flush_heard(self):
        """
        Move any buffered caller words into the transcript instead of leaving them for the AI to answer later.

        Used around a supervisor takeover: the cancelled reply put its caller text back in self.heard, and
        answering it minutes later, after the person has dealt with it, would make no sense.
        """
        if self.commit_task and not self.commit_task.done():
            self.commit_task.cancel()
        if self.heard:
            self.turn("customer", " ".join(self.heard))
            self.save_session()
        self.heard.clear()
        self.pending_bargein = False

    def customer_spoke(self) -> bool:
        return any(t["role"] != "assistant" for t in self.session.get("history", []))

    async def human_audio(self, pcm: bytes):
        """Supervisor's microphone (16-bit PCM, 8 kHz) straight to the caller, only while they have the call."""
        if self.mode == "human" and pcm:
            await self.send({"event": "playAudio", "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000,
                                                             "payload": base64.b64encode(pcm16_to_mulaw(pcm)).decode()}})

    async def checkpoint(self, hangup: bool = False, transfer: bool = False) -> str:
        self.mark += 1
        name = f"m{self.mark}"
        if hangup:
            self.hangup_on_mark = name
        if transfer:
            self.transfer_on_mark = name
        await self.send({"event": "checkpoint", "streamId": self.stream_id, "name": name})
        return name

    def note_spoken(self, text: str):
        """Remember an utterance the caller's line will echo back at us for the next second or so."""
        if text.strip():
            self.spoken_recent = (self.spoken_recent + [text])[-ECHO_MEMORY:]

    def is_echo(self, text: str) -> bool:
        """Drop a transcript that arrives while (or just after) we speak and repeats our own words."""
        if not (self.agent_speaking or time.monotonic() - self.agent_quiet_at < ECHO_TAIL_SECONDS):
            return False
        return looks_like_echo(text, self.spoken_recent)

    async def clear_audio(self):
        self.agent_speaking = False
        self.backchannel = None
        self.agent_quiet_at = time.monotonic()
        # The reply that was just cut off may have armed a hangup/transfer on its checkpoint. A late
        # playedStream for that mark must not end the call in the middle of the next answer.
        self.hangup_on_mark = None
        self.transfer_on_mark = None
        await self.send({"event": "clearAudio", "streamId": self.stream_id})

    async def say_fixed(self, text: str, hangup: bool = False):
        self.note_spoken(text)
        language = tts.detect_language(text, self.session.get("language") or "en-IN")
        pcm = await asyncio.to_thread(tts.cached_pcm, text, language, self.persona.get("voice_speaker"), self.usage)
        await self.play_pcm(pcm)
        await self.checkpoint(hangup=hangup)

    def can_transfer(self) -> bool:
        # Same rule the prompt is built from, so a stray <TRANSFER> can never dial a line the operator turned off.
        return agent.can_transfer(self.persona)

    def has_human_line(self) -> bool:
        """
        A number exists to hand a caller to, whatever the on-request toggle says.

        "Transfer when a caller asks" is a preference about how calls are handled. When every LLM
        provider has failed there is no agent left to handle the call, so the only alternatives are a
        person or a dead line: the preference does not apply.

        A colleague who rings the agent from the transfer line itself is the exception: dialling them
        back on the number they are speaking from reaches their own busy line, so there is no person
        to reach and the caller is better told the team will ring them.
        """
        targets = [digits(part) for part in str(self.persona.get("transfer_number") or "").split(",")]
        targets = [t for t in targets if t]
        if not targets:
            return False
        caller = digits((self.session.get("lead") or {}).get("phone") or self.session.get("customer_phone") or "")
        return any(t for t in targets if t != caller)

    async def transfer_call(self):
        """
        Hand the caller to the agent's human number. Plivo replaces the stream with a <Dial>, ending this socket.

        Gated on a number existing, not on the on-request preference: every caller who reaches here was
        already told they are being put through, by the model, a supervisor or the failure path. Refusing
        now would leave them holding a silent line.
        """
        if self.transferred or not self.call_uuid or not self.has_human_line():
            return
        self.transferred = True
        from app.services.plivo_service import PlivoService
        try:
            await asyncio.to_thread(PlivoService().transfer, self.call_uuid, self.session_id, self.session.get("call_id"))
            self.save_session(transferred=True)
            if self.session.get("call_id"):
                calls = CallService(self.agent_id)
                number = self.persona.get("transfer_number")
                await asyncio.to_thread(calls.mark_transferred, self.session["call_id"],
                                        f"Transferred to {calls.transfer_label(number)}", "transfer", number)
            log.info("Transferred session %s to %s", self.session_id[:8], self.persona.get("transfer_number"))
        except Exception as e:  # noqa: BLE001 - keep the AI on the line if Plivo refuses
            self.transferred = False
            log.error("Transfer failed for %s: %s", self.session_id[:8], e)

    async def finalize_if_unreported(self, delay: float = 15.0):
        await asyncio.sleep(delay)
        call_id = self.session.get("call_id")
        if not call_id:
            return
        with contextlib.suppress(Exception):
            # on_hangup is idempotent: a callback that did arrive already closed the call and this is a no-op.
            await asyncio.to_thread(CallService().on_hangup, int(call_id), "completed", 0,
                                    "Stream ended; no hangup callback", self.call_uuid)

    async def wait_for_end(self):
        """
        Stay alive until plivo_loop acts on an armed hangup/transfer checkpoint, or the caller hangs up.

        A helper loop that returns tears run() down, and run()'s cleanup hangs up at once: the goodbye
        or hand-off line it just queued would be cut off, and an armed transfer would never run.
        """
        while not self.closed:
            await asyncio.sleep(0.5)

    async def hangup(self):
        if self.call_uuid:
            from app.services.plivo_service import PlivoService
            with contextlib.suppress(Exception):
                await asyncio.to_thread(PlivoService().hangup, self.call_uuid)

    def compact_history_soon(self):
        """Fold the turns that have fallen out of the prompt window into one summary line.

        Runs between turns in a worker thread: a long call keeps paying for the last
        MAX_HISTORY_TURNS plus one paragraph instead of a growing transcript. The caller never
        waits for it, and a failure only means the next turn reuses the previous summary.
        """
        turns = len(self.session["history"])
        if turns < agent.COMPACT_AFTER_TURNS or turns - self.compacted_at < agent.COMPACT_EVERY_TURNS:
            return
        self.compacted_at = turns
        history, prior = list(self.session["history"]), self.session.get("summary")

        def run():
            try:
                return agent.compact_history(history, prior)
            except Exception as e:  # noqa: BLE001 - the summary is an optimisation, not state we need
                log.warning("History compaction failed for %s: %s", self.session_id[:8], e)
                return None

        async def store():
            summary = await asyncio.to_thread(run)
            if summary:
                self.save_session(summary=summary)
                log.info("Compacted %s turns into a summary, session=%s", len(history), self.session_id[:8])

        asyncio.create_task(store())

    def save_session(self, **fields):
        fresh = call_session.get(self.session_id) or self.session
        fresh.update(fields)
        fresh["history"] = self.session["history"]
        fresh["latencies"] = self.session.get("latencies", [])
        fresh["language"] = self.session.get("language")
        fresh["usage"] = self.usage
        call_session.save(fresh)
        self.session = fresh

    def switch_agent(self, agent_id: int) -> dict:
        """
        Hand the live call to another of our agents (a caller known to several desks said which one they
        want). Persona, knowledge base, CRM lead and the call record all move; the transcript so far stays.
        """
        from app.core.database import get_db
        from app.models.call import Call
        old = self.agent_id
        self.agent_id = agent_id
        self.persona = agents.get_profile(agent_id)
        crm = CallService(agent_id).crm
        phone = (self.session.get("lead") or {}).get("phone") or self.session.get("from_number")
        lead = crm.find_by_phone(phone) if phone else None
        # The call carries on in the language already being spoken; remember it on this desk's lead so the
        # next call from this number opens in it directly.
        language = self.session.get("language")
        if lead and language and not lead.get("language"):
            with contextlib.suppress(Exception):
                lead = crm.update(lead["id"], {"language": language}, actor="ai") or lead
        context = agent.inbound_context(self.persona, lead, phone or "")
        self.session["agent_id"] = agent_id
        self.session["lead_id"] = lead and lead["id"]
        self.session["lead"] = context
        if self.session.get("call_id"):
            with get_db() as db:
                call = db.get(Call, self.session["call_id"])
                if call:
                    call.agent_id, call.lead_id = agent_id, lead and lead["id"]
        self.save_session()
        events.record("call.rerouted", f"Call moved to {self.persona['agent_name']} ({self.persona['company_name']})",
                      f"The caller chose this desk; agent {old} greeted them.", agent_id=agent_id,
                      lead_id=lead and lead["id"], call_id=self.session.get("call_id"))
        log.info("Session %s switched from agent %s to %s", self.session_id[:8], old, agent_id)
        return context

    # ----- lifecycle -----

    async def run(self):
        if not self.session:
            await self.ws.close()
            return
        LIVE[self.session_id] = self
        self.bridge.start()
        self.tts.warm()
        try:
            await self.stt.connect()
        except Exception as e:
            log.error("Sarvam STT connect failed for %s: %s", self.session_id[:8], e)
        tasks = [asyncio.create_task(self.plivo_loop()), asyncio.create_task(self.stt_loop()),
                 asyncio.create_task(self.silence_loop())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self.closed = True
            LIVE.pop(self.session_id, None)
            self.publish({"type": "ended"})
            await self.bridge.stop()
            for t in tasks + [self.reply_task, self.commit_task]:
                if t and not t.done():
                    t.cancel()
            for t in tasks:
                if t.done() and not t.cancelled() and t.exception():
                    log.error("Stream task failed for session %s: %r", self.session_id[:8], t.exception())
            await self.stt.close()
            await self.tts.close()
            # A loop that died leaves the caller on a silent open line until Plivo's time limit unless
            # the call is ended here. After a transfer Plivo owns the call, so it is left alone.
            if not self.transferred:
                await self.hangup()
                # Plivo's hangup callback normally lands within seconds and finalises the record (status,
                # duration, summary). When it is lost (public URL changed, tunnel restart) the call sat
                # "In Progress" until a sweeper marked it Failed 20 minutes later, transcript unsummarised.
                asyncio.get_running_loop().create_task(self.finalize_if_unreported())
            with contextlib.suppress(Exception):
                await self.ws.close()
            log.info("Stream closed for session %s", self.session_id[:8])

    async def plivo_loop(self):
        try:
            while True:
                msg = json.loads(await self.ws.receive_text())
                event = msg.get("event")
                if event == "media":
                    payload = (msg.get("media") or {}).get("payload")
                    if payload:
                        pcm = mulaw_to_pcm16(base64.b64decode(payload))
                        self.publish_audio("caller", pcm)
                        for frame in (self.gate.process(pcm) if self.gate else [pcm]):
                            await self.stt.send_pcm(frame)
                            self.usage["stt_seconds"] += len(frame) / 16000  # 8 kHz, 16-bit
                elif event == "start":
                    start = msg.get("start") or {}
                    self.stream_id = start.get("streamId") or msg.get("streamId")
                    self.call_uuid = start.get("callId") or msg.get("callId")
                    self.started_at = time.monotonic()
                    log.info("Stream started session=%s call=%s", self.session_id[:8], self.call_uuid)
                    asyncio.create_task(self.greet())
                elif event == "playedStream":
                    if msg.get("name") == f"m{self.mark}":
                        self.agent_speaking = False
                        self.agent_quiet_at = time.monotonic()
                        self.quiet_since = time.monotonic()
                        self.pending_bargein = False
                        self.publish_state()
                        if (self.backchannel and self.mode == "ai" and not self.heard
                                and not (self.reply_task and not self.reply_task.done())):
                            # "haan" said over our final word was the answer to the question we just asked.
                            self.heard.append(self.backchannel)
                            self.commit_task = asyncio.create_task(self.commit_turn())
                        self.backchannel = None
                    if msg.get("name") and msg.get("name") == self.hangup_on_mark:
                        await self.hangup()
                        return
                    if msg.get("name") and msg.get("name") == self.transfer_on_mark:
                        self.transfer_on_mark = None
                        await self.transfer_call()
                        if self.transferred:
                            return
                        # Plivo refused the transfer: the caller was just told "connecting you". Say so
                        # and end on a spoken line rather than dropping them from run()'s cleanup.
                        from app.api.plivo import PROMPTS
                        await self.say_recorded(PROMPTS["error"][self.lang_key()], hangup=True)
                        continue
                elif event == "stop":
                    return
        except (WebSocketDisconnect, RuntimeError):
            return
        except (ValueError, KeyError, TypeError) as e:
            # A malformed frame: reported, and run()'s cleanup ends the call instead of leaving it open.
            log.error("Bad Plivo frame on session %s: %s", self.session_id[:8], e)
            raise

    async def stt_loop(self):
        while not self.closed:
            try:
                ws = await self.stt.connect()
                self.stt_failures = 0
                async for raw in ws:
                    await self.on_stt(json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.stt_failures += 1
                if self.stt_failures >= STT_MAX_FAILURES:
                    # A bad key, exhausted quota or dead DNS: hammering the API every 300 ms for the rest of
                    # the call helps nobody, and the caller is sitting on a line that cannot hear them.
                    log.error("Sarvam STT unavailable after %s attempts on session %s: %s",
                              self.stt_failures, self.session_id[:8], e)
                    await self.fail_turn(f"speech recognition unavailable: {e}")
                    await self.wait_for_end()
                    return
                delay = min(STT_RETRY_CAP, STT_RETRY_BASE * 2 ** (self.stt_failures - 1)) * random.uniform(0.7, 1.3)
                log.warning("Sarvam STT stream dropped (%s), reconnecting in %.1fs", e, delay)
                await asyncio.sleep(delay)

    async def silence_loop(self):
        ticks = 0
        while True:
            await asyncio.sleep(0.5)
            ticks += 1
            if ticks % 30 == 0:
                await self.tts.ping()
            replying = bool(self.reply_task and not self.reply_task.done())
            committing = bool(self.commit_task and not self.commit_task.done())
            idle = self.mode == "ai" and not (self.agent_speaking or self.caller_speaking or committing or replying)
            # Time budget: nudge the model to wrap up shortly before the persona's limit, and if it is
            # still talking at the limit say goodbye ourselves, before Plivo's hard cut drops the line.
            elapsed = time.monotonic() - self.started_at
            budget = self.persona.get("max_call_minutes")
            soft_limit = max(60, int(budget) * 60) if budget else float("inf")  # mirrors PlivoService.soft_time_limit
            # Cost budget: steer at ~75% / a minute before target, prioritise completion at the budget, hard
            # wrap-up only at the persona limit.
            spent = int(self.usage.get("tts_chars") or 0)
            char_budget = int(settings.tts_chars_per_call or 0)
            target = float(settings.call_target_minutes or 0) * 60
            over_budget = (char_budget and spent >= char_budget) or (target and elapsed >= target)
            near_budget = (char_budget and spent >= 0.75 * char_budget) or (target and elapsed >= target - 60)
            if self.mode == "ai" and not self.close_asked and near_budget:
                self.close_asked = True
                self.cost_guidance = STEER_GUIDANCE
                log.info("Cost budget nearly spent on session %s (%s chars, %ss)", self.session_id[:8], spent, int(elapsed))
            if self.mode == "ai" and not self.budget_asked and over_budget:
                self.budget_asked = True
                self.cost_guidance = BUDGET_GUIDANCE
                log.info("Cost budget spent on session %s (%s chars, %ss)", self.session_id[:8], spent, int(elapsed))
            if self.mode == "ai" and not self.wrap_up_asked and elapsed >= soft_limit - WRAP_UP_LEAD_SECONDS:
                self.wrap_up_asked = True
                self.guidance = WRAP_UP_GUIDANCE
                if idle:
                    self.reply_task = asyncio.create_task(self.run_reply(None, time.monotonic()))
                    replying = True
                    idle = False
            elif self.mode == "ai" and elapsed >= soft_limit and idle and not self.heard:
                from app.api.plivo import PROMPTS
                log.info("Call time budget reached on session %s, saying goodbye", self.session_id[:8])
                await self.say_recorded(PROMPTS["goodbye"][self.lang_key()], hangup=True)
                await self.wait_for_end()
                return
            # A barge-in that never produced a transcript (noise, a cough, a false VAD trigger) cancels
            # the reply, which puts the caller's words back in self.heard with nothing left to commit
            # them. Without this the buffer keeps silence_loop "busy" forever and the call goes dead.
            if self.mode == "ai" and self.heard and not committing and not replying and not self.caller_speaking:
                self.commit_task = asyncio.create_task(self.commit_turn())
                committing = True
            busy = self.mode != "ai" or self.agent_speaking or self.caller_speaking or committing or replying
            if busy:
                self.quiet_since = time.monotonic()
                continue
            quiet_for = time.monotonic() - self.quiet_since
            try:
                if self.last_reply_farewell and quiet_for >= FAREWELL_SILENCE_SECONDS:
                    # The agent already said goodbye (the model just forgot the end marker) and the caller
                    # has nothing more: end the call without "Sorry, I didn't catch that".
                    log.info("Silence after farewell, hanging up session %s", self.session_id[:8])
                    await self.hangup()
                    return
                if time.monotonic() < self.hold_until:
                    # The caller asked for a moment ("ek minute"): no prompting, just one reassurance.
                    if not self.hold_acked and quiet_for >= HOLD_ACK_AFTER:
                        self.hold_acked = True
                        await self.say_recorded(STREAM_PROMPTS["hold_ack"][self.lang_key()])
                        self.quiet_since = time.monotonic()
                    continue
                if quiet_for < SILENCE_PROMPT_SECONDS:
                    continue
                self.silent_prompts += 1
                from app.api.plivo import PROMPTS
                caller_spoke = self.customer_spoke()
                if self.silent_prompts > (MAX_SILENT_PROMPTS if caller_spoke else 1):
                    await self.say_recorded(PROMPTS["goodbye"][self.lang_key()], hangup=True)
                    await self.wait_for_end()
                    return
                if caller_spoke:
                    # A person would check the line and repeat their question, not ask the caller to
                    # repeat something they never said. One LLM call, but it sounds like a person.
                    self.guidance = QUIET_GUIDANCE
                    self.reply_task = asyncio.create_task(self.run_reply(None, time.monotonic()))
                else:
                    await self.say_recorded(STREAM_PROMPTS["still_there"][self.lang_key()])
                self.quiet_since = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - a dead silence loop leaves the call open forever
                log.error("Silence handling failed on session %s: %s", self.session_id[:8], e)
                await self.hangup()
                return

    async def say_recorded(self, text: str, hangup: bool = False):
        """A fixed line the transcript (and the model) must know about, unlike a scripted greeting replay."""
        await self.say_fixed(text, hangup=hangup)
        self.turn("assistant", text)
        self.save_session()

    # ----- conversation -----

    async def greet(self):
        history = self.session["history"]
        # Resumed after an unanswered transfer: speak the latest agent line, not the original greeting
        text = history[-1]["text"] if history and history[-1]["role"] == "assistant" and len(history) > 1 else \
            next((t["text"] for t in history if t["role"] == "assistant"), None)
        if not text:
            # May translate through the LLM: never on the event loop, where it would freeze every live call.
            text = await asyncio.to_thread(agent.greeting, self.agent_id, self.session.get("lead") or {},
                                           self.session.get("language") or "en-IN")
            self.turn("assistant", text)
            self.save_session()
        try:
            await self.say_fixed(text)
        except Exception as e:
            log.error("Greeting TTS failed: %s", e)

    def mark_greeting_interrupted(self):
        """The caller spoke over the greeting: tell the model its opening line was cut short."""
        history = self.session.get("history") or []
        if history and history[-1]["role"] == "assistant" and not history[-1]["text"].endswith("(interrupted)"):
            history[-1]["text"] = history[-1]["text"].rstrip() + " — (interrupted)"
            self.save_session()

    async def on_stt(self, msg: dict):
        kind, data = msg.get("type"), msg.get("data") or {}
        if kind == "events":
            signal = data.get("signal_type")
            if signal == "START_SPEECH":
                self.caller_speaking = True
                self.silent_prompts = 0
                if self.commit_task and not self.commit_task.done():
                    self.commit_task.cancel()
                if self.mode == "ai" and (self.agent_speaking or (self.reply_task and not self.reply_task.done())):
                    if self.agent_speaking:
                        # Could be our own audio echoing back. Hold the barge-in until a transcript
                        # proves a human is talking, otherwise the agent cuts itself off mid-sentence.
                        self.pending_bargein = True
                    else:
                        await self.interrupt()
                self.publish_state()
            elif signal == "END_SPEECH":
                self.caller_speaking = False
                self.speech_ended_at = time.monotonic()
                self.publish_state()
        elif kind == "data":
            text = (data.get("transcript") or "").strip()
            if text and self.is_echo(text):
                # Our own voice looping back through the caller's line: answering it makes the agent
                # talk to itself and repeat the question it just asked.
                log.info("Dropped echo on session %s: %s", self.session_id[:8], text)
                self.publish({"type": "echo", "text": text})
                self.pending_bargein = False
                return
            if (text and self.mode == "ai" and not self.heard and not self.customer_spoke()
                    and time.monotonic() - self.started_at < VOICEMAIL_WINDOW_SECONDS and VOICEMAIL.search(text)):
                # A voicemail greeting or carrier announcement: a person hangs up in two seconds, and so
                # do we, without pitching to a recording. The turn is marked so the call ends as a
                # no-answer (retry logic kept, no summary) rather than a conversation.
                log.info("Voicemail/announcement on session %s, hanging up: %s", self.session_id[:8], text)
                self.pending_bargein = False
                await self.interrupt(force=True)
                self.turn("customer", f"[voicemail/announcement: {text}]")
                self.save_session(voicemail=True)
                await self.hangup()
                return
            if text and self.pending_bargein:
                self.pending_bargein = False
                if self.agent_speaking and len(text.split()) <= 2 and BACKCHANNEL.match(text):
                    # "haan", "ji", "hmm", "ok": the caller is listening along, not taking the floor.
                    # Cutting the agent off and answering "haan" as a new turn is what made it stutter.
                    # Kept aside: if it turns out to be the last thing said before we go quiet, it was
                    # the answer to our question and playedStream commits it.
                    log.info("Backchannel on session %s: %s", self.session_id[:8], text)
                    self.publish({"type": "heard", "text": text})
                    self.backchannel = text
                    return
                if not await self.interrupt(text=text):
                    return  # the hand-off or goodbye being played is left to finish
                if not self.customer_spoke():
                    self.mark_greeting_interrupted()
            spoken = data.get("language_code")
            if (text and spoken in tts.LANGUAGES and spoken != self.session.get("language")
                    and (data.get("language_probability") or 0) >= LANGUAGE_SWITCH_CONFIDENCE and len(text.split()) >= 2):
                # Hysteresis: one English sentence from a Hindi caller ("ok what is the price") must not
                # rebuild the TTS socket and flip the whole call. Two transcripts in a row, or one long
                # and confident one, do.
                self.language_votes = (self.language_votes + [spoken])[-2:]
                confident = (data.get("language_probability") or 0) >= 0.85 and len(text.split()) >= 4
                if confident or self.language_votes == [spoken, spoken]:
                    await self.switch_language(spoken, explicit=False)
            elif text:
                self.language_votes.clear()
            if text and self.mode != "ai":
                # Supervisor has the call: log what the caller said, the AI does not answer
                self.turn("customer", text)
                self.save_session()
            elif text:
                self.publish({"type": "heard", "text": text})
                self.heard.append(text)
                # Retrieve for what we have heard so far while the caller finishes their sentence, so
                # the embedding is cached by the time the reply is actually built.
                if agent.needs_knowledge(" ".join(self.heard)):
                    rag.prefetch(self.agent_id, agent.retrieval_query(self.session["history"], " ".join(self.heard)))
                if self.commit_task and not self.commit_task.done():
                    self.commit_task.cancel()
                self.commit_task = asyncio.create_task(self.commit_turn())
        elif kind == "error":
            log.warning("Sarvam STT error: %s", data)

    def escalate_failure(self, error: str):
        """
        A caller we could not keep talking to: record what broke, tell the team and book a callback.

        Runs on every mid-call breakdown, whether or not the caller was handed to a person, so the
        failure is visible in the history instead of looking like an ordinary transfer.
        """
        lead_id = self.session.get("lead_id")
        lead = self.session.get("lead") or {}
        who = lead.get("name") or lead.get("phone") or "A caller"
        with contextlib.suppress(Exception):
            events.record("call.failed", f"Call with {who} ended early", f"agent error: {error[:200]}",
                          agent_id=self.agent_id, lead_id=lead_id, call_id=self.session.get("call_id"), actor="system")
        if lead_id:
            with contextlib.suppress(Exception):
                soon = (datetime.now(IST) + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M")
                CallService(self.agent_id).crm.update(lead_id, {"callback_at": soon}, actor="system",
                                                      event_type="lead.updated", title="Callback after a dropped call")
        # The admin alone hears what broke; the team sees the caller through the booked callback, never
        # an error report, and the caller heard only the hand-over / callback line.
        with contextlib.suppress(Exception):
            from app.services.notification_service import notify_admin
            notify_admin(f"Agent fault on a call with {who}",
                         f"The call with {who} ({lead.get('phone') or 'unknown number'}) could not continue and was "
                         f"handed over / booked for a callback in 10 minutes.\n\nWhat went wrong: {error[:300]}\n\n"
                         f"Agent #{self.agent_id}, session {self.session_id[:8]}.",
                         lead_id=lead_id, agent_id=self.agent_id)

    async def interrupt(self, force: bool = False, text: str | None = None) -> bool:
        """
        Caller (or supervisor) talks over the agent: stop audio and drop the reply in flight.

        Returns False when the barge-in was ignored because the audio being played is the hand-off line
        ("connecting you now"): clearing it would also clear the checkpoint that runs the transfer, and
        the caller would be pulled back into the conversation. A caller who talks over the goodbye with
        a closing of their own is hung up on right away instead of answered again.
        """
        current = f"m{self.mark}"
        if not force and self.agent_speaking and self.transfer_on_mark == current:
            log.info("Barge-in during hand-off ignored on session %s", self.session_id[:8])
            return False
        if not force and self.agent_speaking and self.hangup_on_mark == current and text and is_caller_closing(text):
            log.info("Caller closed during the goodbye, hanging up session %s", self.session_id[:8])
            await self.clear_audio()
            await self.hangup()
            return False
        cancelled = bool(self.reply_task and not self.reply_task.done())
        if cancelled:
            self.reply_task.cancel()
        if self.agent_speaking:
            await self.clear_audio()
        if cancelled or self.agent_speaking or force:
            # A reply cancelled before its first audio has already pushed text (maybe a flush) into the
            # socket; reusing it would speak the abandoned words ahead of the next answer.
            await self.tts.reset()
            log.info("Barge-in on session %s", self.session_id[:8])
        return True

    async def switch_language(self, language: str, explicit: bool = True):
        """
        Follow the caller's language: recognition, voice and prompts. The lead record is only updated when
        the caller asked for the language (explicit=True): an auto-detected switch reflects what they said
        last, not what they prefer, and would otherwise be rewritten on every flip.
        """
        if language == self.session.get("language") or language not in tts.LANGUAGES:
            return
        log.info("Language switch %s -> %s on session %s", self.session.get("language"), language, self.session_id[:8])
        self.session["language"] = language
        self.language_votes.clear()
        await self.tts.close()
        self.tts = make_tts(language, self.persona.get("voice_speaker"))
        self.tts.warm()
        self.save_session()
        if explicit and self.session.get("lead_id"):
            with contextlib.suppress(Exception):
                await asyncio.to_thread(CallService(self.agent_id).crm.update, self.session["lead_id"], {"language": language}, "ai",
                                        "lead.updated", f"Language switched to {tts.LANGUAGES[language]} on request")

    async def capture_caller_details(self, text: str):
        """Save details the caller states about themselves the moment they say them (any lead, new or known):
        name and email fill empty fields right away and are used from the next reply; the summary adds the rest."""
        lead = self.session.get("lead") or {}
        if not self.session.get("lead_id"):
            return
        updates = {}
        if not lead.get("name") and (name := spoken_name(text)):
            updates["name"] = name
        elif lead.get("name") and re.search(r"मेरा नाम|my name is", text, re.I) and (name := spoken_name(text)):
            updates["name"] = name  # they corrected or stated their name explicitly: that wins over a guess
        if not lead.get("email") and (email := spoken_email(text)):
            updates["email"] = email
        elif lead.get("email") and EMAIL_CORRECTION.search(text) and (email := spoken_email(text)) and email != lead["email"]:
            updates["email"] = email  # "actually it's ashish123@gmail.com": the correction replaces the record
        if not updates:
            return
        lead.update(updates)
        if lead.get("collect") is not None and lead.get("call_purpose") == "inbound":
            lead["call_goal"] = agent.call_goal(lead, "inbound_new")
        self.session["lead"] = lead
        self.save_session()
        with contextlib.suppress(Exception):
            await asyncio.to_thread(CallService(self.agent_id).crm.update, self.session["lead_id"], updates, "ai",
                                    "lead.updated", "Caller shared " + ", ".join(f"{k}: {v}" for k, v in updates.items()))
        self.publish({"type": "caller", **updates})

    async def commit_turn(self):
        await asyncio.sleep(turn_grace_ms(" ".join(self.heard)) / 1000)
        if self.caller_speaking or not self.heard:
            return
        if self.reply_task and not self.reply_task.done():
            # Cancel first and let the cancellation land: the cut-off reply puts its own caller text back
            # in self.heard, so the merged turn below answers everything. Switching the voice underneath
            # a streaming reply would instead surface as a socket error and trip the failure path.
            self.reply_task.cancel()
            await asyncio.wait([self.reply_task])
            await self.tts.reset()
        text = " ".join(self.heard)
        if self.last_reply_farewell and (is_caller_closing(text) or is_post_farewell_noise(text)):
            # We already said goodbye; "hello" / "ok thank you" back is the caller signing off, not a new
            # question. Record it and hang up instead of starting another reply.
            log.info("Caller signed off after farewell, hanging up session %s", self.session_id[:8])
            self.heard = []
            self.turn("customer", text)
            self.save_session(silent_prompts=0)
            await self.hangup()
            return
        elif self.last_reply_farewell:
            # Something more than a sign-off after our goodbye ("wait, one more question", "are you there?"):
            # let the model answer, but from the post-farewell state, not the top of the script.
            self.guidance = " ".join(g for g in (self.guidance, POST_FAREWELL_GUIDANCE) if g)
        if HOLD.search(text):
            self.hold_until = time.monotonic() + HOLD_SECONDS
            self.hold_acked = False
        # self.heard keeps the words until the reply starts: a barge-in during these awaits cancels this
        # task, and the next commit picks them up instead of losing them.
        await self.capture_caller_details(text)
        wanted = requested_language(text)
        if wanted:
            await self.switch_language(wanted)
        if self.caller_speaking:
            return
        self.heard = []
        if await self.quick_action(text):
            return
        self.reply_task = asyncio.create_task(self.run_reply(text, self.speech_ended_at or time.monotonic()))

    async def quick_action(self, text: str) -> bool:
        """
        Customer requests with one right answer, handled without the model: "stop calling me" and "send me the
        details on WhatsApp". Saves a full prompt round and keeps the spoken line short. Team/admin check-ins and
        the desk-choice turn are left to the model. Returns True when the turn was answered here.
        """
        lead = self.session.get("lead") or {}
        purpose = lead.get("call_purpose") or ""
        if purpose in ("team", "admin", "inbound_choose") or not text:
            return False
        lead_id = self.session.get("lead_id")
        key = self.lang_key()
        if DNC_REQUEST.search(text) and not DNC_NOT_NOW.search(text):
            log.info("Do-not-call request on session %s: %s", self.session_id[:8], text)
            self.turn("customer", text)
            if lead_id:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(CallService(self.agent_id).crm.update, lead_id,
                                            {"do_not_call": True, "status": "Do Not Call"}, "ai")
            self.save_session()
            await self.say_recorded(DNC_LINE[key], hangup=True)
            await self.wait_for_end()
            return True
        phone = lead.get("phone") or self.session.get("from_number")
        site = (self.persona.get("website_url") or "").strip()
        if (DETAILS_REQUEST.search(text) and DETAILS_WORDS.search(text) and phone and site
                and not self.session.get("details_sent")):
            self.turn("customer", text)
            body = f"{self.persona['company_name']}: details as discussed - {site}"
            try:
                from app.services.plivo_service import PlivoService
                await asyncio.to_thread(PlivoService().send_sms, phone, body)
            except Exception as e:  # noqa: BLE001 - the model answers instead; never claim a text that did not go
                log.warning("Details SMS failed on session %s: %s", self.session_id[:8], e)
                self.heard = [text]
                self.session["history"].pop()
                return False
            self.session["details_sent"] = True
            self.save_session()
            events.record("lead.details_sent", "Details sent by SMS", body, agent_id=self.agent_id, lead_id=lead_id,
                          call_id=self.session.get("call_id"), actor="ai")
            await self.say_recorded(DETAILS_LINE[key])
            return True
        return False

    async def run_reply(self, text: str | None, speech_ended_at: float):
        """reply() under a hard deadline, so a stuck provider can never hold the line open until Plivo's limit."""
        try:
            await asyncio.wait_for(self.reply(text, speech_ended_at), REPLY_DEADLINE_SECONDS)
        except asyncio.TimeoutError:
            log.error("Reply exceeded %ss on session %s", REPLY_DEADLINE_SECONDS, self.session_id[:8])
            if text is not None and self.heard[:1] == [text]:
                self.heard.pop(0)  # put back by the cancelled reply: recorded here instead of answered again
                self.turn("customer", text)
            await self.tts.reset()
            await self.fail_turn("reply timed out")

    async def fail_turn(self, error: str):
        """
        Nothing left to keep the conversation going (every LLM provider refused, speech recognition is gone,
        a reply hung). Hanging up on a caller mid-conversation is the worst outcome, so hand them to a
        person when a number exists, and promise a callback otherwise; neither line mentions a fault.

        Only once per call: if the team did not pick up and the caller was returned to the agent, or the
        session already failed before, a second hand-off would loop them between "let me put you through"
        and "what do you need?" until the time limit. The breakdown is recorded the first time, so a day
        of failing calls never looks like a day of transfers.
        """
        from app.api.plivo import FALLBACK_LINES, PROMPTS
        failures = int(self.session.get("failures") or 0) + 1
        self.save_session(failures=failures)
        history = self.session.get("history") or []
        returned = any(t.get("role") == "assistant" and t.get("text") in FALLBACK_LINES.values() for t in history[-4:])
        if failures == 1:
            await asyncio.to_thread(self.escalate_failure, error)
        if failures == 1 and not returned and self.has_human_line():
            line = PROMPTS["handover"][self.lang_key()]
            self.turn("assistant", line)
            await self.say_fixed(line)
            await self.checkpoint(transfer=True)
        else:
            # Recorded like any other spoken line: a transcript that ends on the caller's question makes
            # the call look like the agent never answered at all.
            line = PROMPTS["error"][self.lang_key()]
            self.turn("assistant", line)
            await self.say_fixed(line, hangup=True)
        self.save_session()

    async def reply(self, text: str | None, speech_ended_at: float):
        """
        LLM text streams straight into streaming TTS; audio chunks go to the caller as they arrive.
        text=None: the supervisor asked the agent to speak now (no new caller words).
        """
        if self.mode != "ai":
            return
        log.info("Customer said (%s): %s", self.session_id[:8], text)
        language = self.session.get("language") or "en-IN"
        supervised = bool(self.direction or self.guidance)
        guidance = " ".join(g for g in (self.direction, self.guidance, self.cost_guidance) if g) or None
        self.guidance = None
        self.publish_state()
        prompt_text = text if text is not None else "(The customer is listening. Continue the call now, following the supervisor instruction.)"
        lead = self.session.get("lead") or {}
        if text and lead.get("call_purpose") == "inbound_choose":
            # The caller was asked which of our desks this call is about: once they say, the rest of the call
            # runs as that agent (its persona, knowledge base and CRM record).
            chosen = await asyncio.to_thread(agent.choose_agent, text, lead.get("choices") or [])
            if chosen:
                lead = await asyncio.to_thread(self.switch_agent, chosen)
                guidance = " ".join(g for g in (guidance, f"The caller chose {self.persona['company_name']}. Acknowledge in a few "
                                                          "words and continue as that agent; whole reply under 120 characters.") if g)
        if self.session.get("lead_id"):
            with contextlib.suppress(Exception):
                fresh = await asyncio.to_thread(CallService(self.agent_id).crm.get, self.session["lead_id"])
                lead = merge_call_context(lead, fresh)

        loop = asyncio.get_running_loop()
        deltas: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()
        history = list(self.session["history"])

        def pump():
            try:
                for delta in agent.respond_stream(self.agent_id, history, prompt_text, lead, guidance, language,
                                                  summary=self.session.get('summary')):
                    if stop.is_set():
                        return
                    loop.call_soon_threadsafe(deltas.put_nowait, delta)
                loop.call_soon_threadsafe(deltas.put_nowait, None)
            except Exception as e:  # noqa: BLE001 - surfaced to the event loop
                loop.call_soon_threadsafe(deltas.put_nowait, e)

        threading.Thread(target=pump, daemon=True, name="llm-stream").start()
        self.meter("llm_requests")
        cleaner = ReplyFilter()
        spoken: list[str] = []
        first_audio_ms = None
        feeder = None
        unsent = ""            # text the feeder holds back until a whole word is available
        llm_done = False       # the feeder consumed the whole LLM stream
        played_upto = 0        # len(spoken) when the last audio chunk arrived: text the caller may have heard
        last_audio_at = time.monotonic()
        recorded = False       # the caller's line is in the transcript
        tts_fault: Exception | None = None

        def on_cancel():
            """A barge-in: leave the transcript honest about what was already said."""
            stop.set()
            if feeder:
                feeder.cancel()
            if recorded:
                return
            partial = " ".join("".join(spoken).split()) if first_audio_ms is not None else ""
            if partial:
                # The caller heard at least the start of this: the model must know it was cut off
                # mid-sentence, otherwise it restarts the same sentence or skips the point. What it
                # heard from the caller goes in front of it.
                if text is not None:
                    self.turn("customer", text)
                self.turn("assistant", partial + " — (interrupted)")
                self.save_session()
            elif text is not None:
                self.heard.insert(0, text)  # caller kept talking before we said anything: answer everything together

        try:
            tts_ws = await self.tts.get()

            async def feed():
                nonlocal unsent, llm_done, last_audio_at

                async def push(chunk: str, final: bool = False):
                    nonlocal unsent
                    unsent += chunk
                    # Send whole words only: a lone vowel sign or "?" is rejected by Sarvam and would be lost
                    cut = len(unsent) if final else max(unsent.rfind(" "), unsent.rfind("\n")) + 1
                    ready = unsent[:cut]
                    if ready.strip() and re.search(r"[^\W\d_]|[ऀ-෿]", ready):
                        spoken.append(ready)
                        self.note_spoken(ready)
                        self.meter("tts_chars", len(ready))
                        unsent = unsent[cut:]
                        await tts_ws.send(json.dumps({"type": "text", "data": {"text": ready}}))

                while (item := await deltas.get()) is not None:
                    if isinstance(item, Exception):
                        raise item
                    if isinstance(item, dict):
                        self.meter_llm(item)
                        continue
                    await push(cleaner.feed(item))
                llm_done = True
                await push(cleaner.flush(), final=True)
                if "".join(spoken).strip():
                    await tts_ws.send(json.dumps({"type": "flush"}))
                    last_audio_at = max(last_audio_at, time.monotonic())  # the stall clock starts at the flush

            feeder = asyncio.create_task(feed())
            filler_sent = False
            while True:
                if (first_audio_ms is None and not filler_sent and self.mode == "ai"
                        and time.monotonic() - speech_ended_at >= FILLER_AFTER_SECONDS):
                    filler_sent = True
                    line = STREAM_PROMPTS["filler"][self.lang_key()]
                    try:
                        pcm = await asyncio.to_thread(tts.cached_pcm, line, self.session.get("language") or "en-IN",
                                                      self.persona.get("voice_speaker"), self.usage)
                        self.note_spoken(line)  # its echo must not come back as a caller turn
                        await self.play_pcm(pcm)
                        log.info("Slow reply on session %s: played filler", self.session_id[:8])
                    except Exception as e:  # noqa: BLE001 - the filler is a nicety, never a reason to fail the turn
                        log.warning("Filler audio failed on session %s: %s", self.session_id[:8], e)
                if feeder.done():
                    if feeder.exception():
                        raise feeder.exception()
                    if not "".join(spoken).strip():
                        break  # only markup: no audio will come
                    if time.monotonic() - last_audio_at > TTS_STALL_SECONDS:
                        # The flush was dropped or the completion event never comes: without this the
                        # turn never finishes and silence_loop treats the call as busy forever.
                        raise RuntimeError("Sarvam TTS: no audio or completion after the text was flushed")
                try:
                    raw = await asyncio.wait_for(tts_ws.recv(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "audio":
                    audio = base64.b64decode(msg["data"]["audio"])
                    if first_audio_ms is None:
                        first_audio_ms = int((time.monotonic() - speech_ended_at) * 1000)
                    last_audio_at = time.monotonic()
                    played_upto = len(spoken)
                    await self.send_mulaw(audio)
                elif msg.get("type") == "event" and (msg.get("data") or {}).get("event_type") == "final":
                    break
                elif msg.get("type") == "error":
                    raise RuntimeError(f"Sarvam TTS: {msg.get('data')}")
        except asyncio.CancelledError:
            on_cancel()
            raise
        except Exception as e:
            if feeder:
                feeder.cancel()
            from app.services.llm import LLMError
            if (isinstance(e, (websockets.ConnectionClosed, websockets.InvalidHandshake, OSError, TimeoutError))
                    or (isinstance(e, RuntimeError) and not isinstance(e, LLMError) and str(e).startswith("Sarvam TTS"))):
                # The voice socket hiccuped, not the model: the words exist, so finish saying them below
                # with one-shot synthesis instead of booking a callback and dropping the caller.
                tts_fault = e
            else:
                stop.set()
                log.error("Reply failed for session %s: %s", self.session_id[:8], e)
                await self.tts.reset()
                if text is not None:
                    self.turn("customer", text)
                self.save_session()
                await self.fail_turn(str(e))
                return

        try:
            if tts_fault is not None:
                log.warning("TTS socket failed mid-reply on session %s (%s), finishing the turn without it",
                            self.session_id[:8], tts_fault)
                await self.tts.reset()
                rest = "".join(spoken[played_upto:]) + unsent
                try:
                    if not llm_done:
                        while (item := await asyncio.wait_for(deltas.get(), 20)) is not None:
                            if isinstance(item, Exception):
                                raise item
                            if isinstance(item, dict):
                                self.meter_llm(item)
                                continue
                            rest += cleaner.feed(item)
                        rest += cleaner.flush()
                except Exception as e:  # noqa: BLE001 - the model failed too: this is the real breakdown
                    stop.set()
                    log.error("Reply failed for session %s: %s", self.session_id[:8], e)
                    if text is not None:
                        self.turn("customer", text)
                    self.save_session()
                    await self.fail_turn(str(e))
                    return
                rest = " ".join(rest.split())
                del spoken[played_upto:]
                if rest:
                    spoken.append(rest)
                    self.note_spoken(rest)
                    self.meter("tts_chars", len(rest))
                    await self.play_pcm(await asyncio.to_thread(tts.synthesize_pcm, rest, tts.detect_language(rest, language),
                                                                self.persona.get("voice_speaker")))

            reply = " ".join("".join(spoken).split())
            if not reply and not cleaner.end_call:
                # The model emitted a tool call instead of speech (typically when a meeting is agreed).
                # Ask once more, streaming and short, with an explicit spoken-only instruction.
                with contextlib.suppress(Exception):
                    retry = ReplyFilter()
                    spoken_only = ((guidance + " ") if guidance else "") + SPOKEN_ONLY
                    self.meter("llm_requests")
                    def spoken_retry() -> str:
                        parts = []
                        for d in agent.respond_stream(self.agent_id, history, prompt_text, lead, spoken_only, language,
                                                      summary=self.session.get("summary")):
                            (self.meter_llm(d) if isinstance(d, dict) else parts.append(d))
                        return "".join(parts)
                    raw = await asyncio.to_thread(spoken_retry)
                    reply = " ".join((retry.feed(raw) + retry.flush()).split())
                    cleaner.end_call = retry.end_call
                    if reply:
                        spoken.append(reply)
                        self.note_spoken(reply)
                        language = tts.detect_language(reply, self.session.get("language") or "en-IN")
                        await self.play_pcm(await asyncio.to_thread(tts.synthesize_pcm, reply, language, self.persona.get("voice_speaker")))
                        self.meter("tts_chars", len(reply))
            if not reply:
                # Still nothing to say (never hang up). "I didn't catch that" is only honest when they said
                # nothing: after a caller has spoken, the failure is ours, so invite them to carry on instead.
                from app.api.plivo import PROMPTS
                if text and is_caller_closing(text):
                    # They said bye and the model produced nothing: answer the goodbye, do not ask them to go on.
                    cleaner.end_call = True
                fallback = "goodbye" if cleaner.end_call else ("continue" if (text or "").strip() else "repeat")
                reply = PROMPTS[fallback][self.lang_key()]
                spoken.append(reply)
                await self.say_fixed(reply)
            self.note_spoken(reply)
            if cleaner.end_call and reply.rstrip().endswith("?") and not (text and is_caller_closing(text)):
                # The model put its end marker on a question ("anything else I can help with?"): hanging up
                # there cuts the caller off mid-conversation. Let them answer; the next turn can close.
                log.info("End marker on a question ignored, session=%s", self.session_id[:8])
                cleaner.end_call = False
            farewell = is_farewell(reply)
            caller_bye = bool(text and CALLER_GOODBYE.search(text) and not KEEP_LINE.search(text))
            if not cleaner.end_call and text and ((farewell and is_caller_closing(text)) or (caller_bye and "?" not in reply)):
                # Both sides said goodbye, or the caller said bye and we did not ask anything: hang up even
                # without the end marker.
                log.info("Ending call on mutual farewell without end marker, session=%s", self.session_id[:8])
                cleaner.end_call = True
            # A goodbye without a closing from the caller: if they now stay quiet, silence_loop ends the
            # call without asking them to repeat themselves.
            self.last_reply_farewell = farewell and not cleaner.end_call
            if text is not None:
                self.turn("customer", text)
            self.turn("assistant", reply, by="ai-guided" if supervised else None)
            recorded = True
            if first_audio_ms is not None:
                self.session["latencies"] = (self.session.get("latencies") or []) + [first_audio_ms]
            self.save_session(silent_prompts=0)
            log.info("Turn: first audio %sms after caller stopped, session=%s", first_audio_ms, self.session_id[:8])
            self.compact_history_soon()
            if cleaner.transfer and self.can_transfer():
                await self.checkpoint(transfer=True)
            else:
                await self.checkpoint(hangup=cleaner.end_call)
        except asyncio.CancelledError:
            # A barge-in during the retry, the fallback line or the checkpoint: the caller's words are
            # still kept, either in the transcript or in the next turn.
            on_cancel()
            raise
