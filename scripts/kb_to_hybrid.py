"""Reshape the WedEazzy knowledge base into passages that survive rag.chunk_text().

Rules it targets (app/services/rag.py): paragraphs split on blank lines, merged while
the running chunk stays under CHUNK_CHARS=400, long paragraphs cut on sentence boundary.
So every passage here is: self-contained (breadcrumb repeated), under 380 chars, and at
least MIN_CHARS so two passages can never merge into one diluted chunk.
"""
import re, sys, unicodedata

SRC = sys.argv[1]
OUT = sys.argv[2]
# Sections that tell the agent how to behave — intent labels, objection scripts, tool rules, call
# examples, editorial notes about missing data. They belong in the persona prompt, not in a corpus
# of facts the agent may state, and they outrank real answers because they are written in the same
# words a caller uses. Pass --all to keep them.
KEEP_PLAYBOOK = "--all" in sys.argv[3:]
PLAYBOOK = re.compile(r"^(1|18|19|20|22|23|24|25|26|27|28|29|30|31|32|33|34|35|36|37)\.|^DATA (GAPS|CONFLICTS)")
MAX_CHARS = 380
MIN_CHARS = 215

# BM25 runs on an English corpus; these widen the surface a caller's words can hit.
SYNONYMS = {
    "pricing": "price cost charges fees rate how much amount",
    "price": "cost charges fees rate how much",
    "refund": "money back reversal return",
    "cancellation": "cancel call off withdraw",
    "payment": "pay paid billing invoice transaction",
    "booking": "book reserve confirm",
    "enquiry": "inquiry lead request contact",
    "vendor": "supplier business listing partner",
    "venue": "hall banquet lawn garden marriage garden",
    "photographer": "photography photo shoot album",
    "makeup": "bridal makeup artist beauty",
    "mehndi": "mehendi henna",
    "caterer": "catering food menu plate",
    "decorator": "decoration decor stage flowers",
    "planner": "planning coordinator",
    "pandit": "priest purohit pooja ceremony",
    "location": "city area place where",
    "verification": "verified genuine trust check",
    "plan": "subscription package premium featured",
    "campaign": "ads advertising grow promotion leads",
    "support": "help contact complaint grievance",
    "privacy": "data personal information consent",
    "terms": "conditions agreement policy",
    "escalation": "transfer human agent handover",
    "timing": "hours time when open",
    "document": "papers paperwork kyc proof",
}
STOP = set("a an and are as at be by for from has have i in is it its of on or our that the this to we what with you your the not no all any each per".split())
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9\-]+")


def keywords(text: str, breadcrumb: str) -> str:
    seen, out = set(), []
    for tok in TOKEN.findall(f"{breadcrumb} {text}"):
        low = tok.lower()
        if low in STOP or len(low) < 4 or low in seen:
            continue
        seen.add(low)
        if low in SYNONYMS:
            for w in SYNONYMS[low].split():
                if w not in seen:
                    seen.add(w)
                    out.append(w)
    return " ".join(out)


def parse(src: str):
    """Yield (breadcrumb, title, body_lines) blocks in document order."""
    stack, block, blocks = [], [], []
    title = ""

    def flush():
        nonlocal block, title
        if block:
            blocks.append((" / ".join(stack), title, block))
        block, title = [], ""

    for raw in src.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush()
            depth = len(m.group(1))
            del stack[depth - 1:]
            stack.append(m.group(2).strip())
            continue
        if stack and not KEEP_PLAYBOOK and PLAYBOOK.match(stack[0]):
            continue
        # "**8. PRICING / 8.1 Customer charges — Browsing**" opens a labelled block
        m = re.match(r"^\*\*(.+?)\*\*\s*$", line)
        if re.fullmatch(r"[-*_]{3,}", line.strip()):  # horizontal rule, not content
            flush()
            continue
        if re.fullmatch(r"[-*_]{3,}", line.strip()):  # horizontal rule, not content
            flush()
            continue
        if m and " — " in m.group(1) and not block:
            title = m.group(1).rsplit(" — ", 1)[1].strip()
            continue
        block.append(line)
    flush()
    return blocks


def split_body(lines, budget):
    """Pack body lines into parts that each fit `budget` characters."""
    parts, cur, n = [], [], 0
    for line in lines:
        while len(line) > budget:  # a single over-long line: cut on sentence, else on space
            cut = line.rfind(". ", 0, budget)
            cut = cut + 1 if cut > budget // 2 else line.rfind(" ", 0, budget)
            cut = cut if cut > budget // 2 else budget
            if cur:
                parts.append(cur)
                cur, n = [], 0
            parts.append([line[:cut].strip()])
            line = line[cut:].strip()
        if cur and n + len(line) + 1 > budget:
            parts.append(cur)
            cur, n = [], 0
        cur.append(line)
        n += len(line) + 1
    if cur:
        parts.append(cur)
    return parts


def merge_short(out):
    """Pre-merge neighbours the chunker would merge anyway, so a stored chunk is exactly
    the passage an author reads here. Only within one breadcrumb."""
    NL = chr(10)
    passages = [p for p in out if p.strip()]
    head = []
    merged = []
    for p in passages:
        if merged:
            prev = merged[-1]
            key = lambda x: x.split(NL, 1)[0].split(" (part ")[0]
            if key(prev) == key(p) and len(prev) + len(p) + 1 <= MAX_CHARS:
                merged[-1] = prev + NL + (p.split(NL, 1)[1] if NL in p else p)
                continue
        merged.append(p)
    result = []
    for p in head + merged:
        result.append(p)
        result.append("")
    return result


def main():
    src = open(SRC, encoding="utf-8").read()
    blocks = parse(src)
    out, count = [], 0
    for breadcrumb, title, body in blocks:
        head = f"{breadcrumb}" + (f" — {title}" if title else "")
        head = re.sub(r"\s+", " ", head).strip(" /")
        budget = MAX_CHARS - len(head) - 40
        if budget < 60:
            head = head[-160:]
            budget = MAX_CHARS - len(head) - 40
        parts = split_body(body, budget)
        for i, part in enumerate(parts):
            label = head if len(parts) == 1 else f"{head} (part {i + 1} of {len(parts)})"
            text = "\n".join(part)
            passage = f"{label}\n{text}"
            kw = keywords(text, label)
            if kw:
                need = max(0, MIN_CHARS - len(passage) - 11)
                tail = kw if len(passage) + 11 + len(kw) <= MAX_CHARS else kw[:MAX_CHARS - len(passage) - 11].rsplit(" ", 1)[0]
                if need or tail:
                    passage = f"{passage}\nKeywords: {tail}".rstrip()
            out.append(passage)
            out.append("")
            count += 1
    out = merge_short(out)
    count = len([p for p in out if p.strip()])
    text = "\n".join(out)
    open(OUT, "w", encoding="utf-8").write(text)
    lens = [len(p) for p in text.split("\n\n") if p.strip()]
    print(f"passages={count} chars={len(text)} max={max(lens)} min={min(lens)} avg={sum(lens)//len(lens)}")
    print(f"over_{MAX_CHARS}={sum(1 for l in lens if l > MAX_CHARS)} under_{MIN_CHARS}={sum(1 for l in lens if l < MIN_CHARS)}")


main()
