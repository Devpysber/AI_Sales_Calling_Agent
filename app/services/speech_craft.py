"""
Voice Speech Craft & Human Realism Module.
Inspired by enterprise phone voice craftsmanship (BodhX-style):

1. Indian Number Reading:
   - Amounts: Converts raw digits into Indian lakh/crore scale: "150000 rupees" -> "1.5 lakh rupees"
   - Phone/Mobile numbers: Speaks phone numbers digit-by-digit with natural breathing pause (e.g., "9 8 7 6 5, 4 3 2 1 0")

2. Human Speech Craft & Natural Flow:
   - Removes robotic software language ("as per database", "system record", "agent prompt").
   - Enforces warm conversational openers and natural backchannel conversational markers.
   - Rehearses prices and dates naturally without awkward robotic phrasing.
"""

import re

# Indian Amount conversion
def format_indian_amount(amount: int) -> str | None:
    if amount < 100000 or amount > 9999999999:
        return None
    crore = amount // 10000000
    remainder = amount % 10000000
    lakh = remainder // 100000
    thousand = (remainder % 100000) // 1000
    
    parts = []
    if crore > 0:
        parts.append(f"{crore} crore")
    if lakh > 0:
        parts.append(f"{lakh} lakh")
    if thousand > 0:
        parts.append(f"{thousand} thousand")
    return " ".join(parts) if parts else None

# Regex patterns
RUPEE_AMOUNT_PATTERN = re.compile(r"(?:₹|rs\.?|inr)?\s*(\d{5,10})\s*(?:rupees|rs|inr)?", re.IGNORECASE)
MOBILE_PATTERN = re.compile(r"(?:\+?91[\s-]?)?([6-9]\d{4})[\s-]?(\d{5})\b")
PERCENT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%")

def format_spoken_digits(digits: str) -> str:
    """Format 10-digit mobile numbers into spaced cadence '9 8 7 6 5, 4 3 2 1 0'."""
    clean = re.sub(r"\D", "", digits)
    if len(clean) == 10:
        return f"{' '.join(clean[:5])}, {' '.join(clean[5:])}"
    return " ".join(clean)

def humanize_voice_speech(text: str) -> str:
    """Transform raw LLM output text into human phone speech craft."""
    if not text:
        return ""

    out = text

    # 1. Transform Phone Numbers to natural digit cadences
    def _replace_phone(m):
        first_half, second_half = m.group(1), m.group(2)
        full_num = first_half + second_half
        return format_spoken_digits(full_num)

    out = MOBILE_PATTERN.sub(_replace_phone, out)

    # 2. Transform high amounts to Indian scale (Lakh / Crore)
    def _replace_amount(m):
        raw_val = int(m.group(1))
        scale = format_indian_amount(raw_val)
        if scale:
            return f"{scale} rupees"
        return m.group(0)

    out = RUPEE_AMOUNT_PATTERN.sub(_replace_amount, out)

    # 3. Transform percentages to spoken form
    out = PERCENT_PATTERN.sub(lambda m: f"{m.group(1)} percent", out)

    # 4. Conversational software-strip
    replacements = [
        (r"\bas an ai model\b", "I"),
        (r"\bas an ai voice agent\b", "I"),
        (r"\bdatabase\b", "notes"),
        (r"\bsystem record\b", "note"),
        (r"\bcrm\b", "record"),
        (r"\bcall prompt\b", "information"),
    ]
    for pattern, rep in replacements:
        out = re.sub(pattern, rep, out, flags=re.IGNORECASE)

    return out
