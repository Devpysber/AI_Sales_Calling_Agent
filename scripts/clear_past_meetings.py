"""
Clear meeting_at values that are already in the past.

Before validation existed, the summariser could write a meeting time earlier than the call itself,
which then showed up as "Prepare for the meeting" on leads whose meeting had long gone.
New writes are validated in call_service._valid_meeting; this cleans what is already stored.

    python scripts/clear_past_meetings.py            # show what would change
    python scripts/clear_past_meetings.py --apply    # write it
"""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal
from app.models.lead import Lead

now = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M")


def main(apply: bool):
    with SessionLocal() as db:
        stale = [l for l in db.query(Lead).filter(Lead.meeting_at.is_not(None), Lead.meeting_at != "")
                 if str(l.meeting_at)[:16] < now]
        for lead in stale:
            print(f"{lead.id}\t{lead.name or lead.phone}\t{lead.meeting_at}")
            if apply:
                lead.meeting_at = None
        if apply:
            db.commit()
        print(f"\n{len(stale)} lead(s) with a past meeting. Now is {now} IST.")
        print("Cleared." if apply else "Dry run: pass --apply to clear them.")


if __name__ == "__main__":
    main("--apply" in sys.argv)
