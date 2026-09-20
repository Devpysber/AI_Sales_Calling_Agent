"""
Rehearse a call against the real agent (same prompt, knowledge and LLM as the playground) from a script
of customer lines, and print the transcript with what the agent understood on each turn.

    python scripts/sim_call.py --agent 1 --lang hi-IN --name Rahul -- "haan bolo" "price kya hai" "bye"
    python scripts/sim_call.py --agent 1 --file scenario.json      # {"lang": "hi-IN", "name": "...", "lines": [...]}

Exit code is 0; the transcript is JSON on stdout so a reviewer can judge it.
"""
import argparse
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import logging
logging.disable(logging.WARNING)

from app.services import agent  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--agent", type=int, default=1)
    p.add_argument("--lang", default="hi-IN")
    p.add_argument("--name", default="Rahul")
    p.add_argument("--purpose", default=None, help="inbound | confirm_meeting | follow_up | None (outbound)")
    p.add_argument("--file")
    p.add_argument("lines", nargs="*")
    a = p.parse_args()
    lines, lang, name = a.lines, a.lang, a.name
    if a.file:
        spec = json.load(open(a.file, encoding="utf-8"))
        lines, lang, name = spec["lines"], spec.get("lang", lang), spec.get("name", name)
    lead = {"name": name, "language": lang}
    if a.purpose:
        lead["call_purpose"] = a.purpose
    history = [{"role": "assistant", "text": agent.greeting(a.agent, lead, lang)}]
    out = {"greeting": history[0]["text"], "turns": []}
    for line in lines:
        t = time.time()
        try:
            r = agent.respond(a.agent, history, line, lead)
        except Exception as e:  # noqa: BLE001
            out["turns"].append({"customer": line, "error": f"{type(e).__name__}: {e}"})
            break
        history += [{"role": "customer", "text": line}, {"role": "assistant", "text": r["reply"]}]
        out["turns"].append({"customer": line, "agent": r["reply"], "intent": r.get("intent"), "end_call": r.get("end_call"),
                             "qualification": r.get("qualification"), "crm_update": r.get("crm_update"),
                             "seconds": round(time.time() - t, 2)})
        if r.get("end_call"):
            break
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
