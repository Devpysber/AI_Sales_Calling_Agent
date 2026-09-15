"""
CRM: leads CRUD, search, bulk import/export, and AI-driven updates.
Every instance is bound to one agent so leads never mix between agents.
"""

import io
import re
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import func, or_, select

from app.core.database import get_db
from app.models.lead import Lead
from app.services import events

IST = timezone(timedelta(hours=5, minutes=30))

EDITABLE = {"name", "company", "phone", "email", "city", "language", "source", "tags", "notes", "do_not_call",
            "status", "call_status", "retry_count", "qualification", "summary", "requirements", "objections",
            "follow_up_date", "meeting_at", "callback_at"}

SORTABLE = {"id": Lead.id, "name": Lead.name, "company": Lead.company, "status": Lead.status,
            "last_contacted_at": Lead.last_contacted_at, "created_at": Lead.created_at,
            "qualification": Lead.qualification, "meeting_at": Lead.meeting_at}

IMPORT_ALIASES = {
    "name": "name", "fullname": "name", "customername": "name", "contactname": "name", "leadname": "name",
    "company": "company", "companyname": "company", "organization": "company", "organisation": "company", "business": "company",
    "phone": "phone", "phonenumber": "phone", "mobile": "phone", "mobilenumber": "phone", "contact": "phone",
    "contactnumber": "phone", "number": "phone", "whatsapp": "phone", "cell": "phone",
    "email": "email", "emailaddress": "email", "mail": "email",
    "city": "city", "location": "city",
    "language": "language", "lang": "language", "preferredlanguage": "language", "calllanguage": "language",
    "speaks": "language", "languagepreference": "language",
    "source": "source", "leadsource": "source",
    "tags": "tags", "tag": "tags",
    "notes": "notes", "note": "notes", "remarks": "notes", "comments": "notes",
    "status": "status", "stage": "status", "leadstage": "status", "leadstatus": "status",
}

LANGUAGE_NAMES = {"english": "en-IN", "hindi": "hi-IN", "bengali": "bn-IN", "tamil": "ta-IN", "telugu": "te-IN",
                  "kannada": "kn-IN", "malayalam": "ml-IN", "marathi": "mr-IN", "gujarati": "gu-IN",
                  "punjabi": "pa-IN", "odia": "od-IN", "en": "en-IN", "hi": "hi-IN"}


def _clean(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def normalize_phone(phone) -> str | None:
    text = _clean(phone)
    if not text:
        return None
    if text.endswith(".0"):
        text = text[:-2]
    digits = "".join(c for c in text if c.isdigit())
    if digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) == 10:
        return "+91" + digits
    if digits.startswith("91") and len(digits) != 12 and len(digits) <= 13:
        return None  # an Indian number with a digit missing or extra: calls would fail
    if 11 <= len(digits) <= 15:
        return "+" + digits
    return None


def normalize_language(value) -> str:
    text = (_clean(value) or "en-IN")
    if len(text) == 5 and text[2] == "-":
        return text[:2].lower() + "-IN"
    return LANGUAGE_NAMES.get(text.lower(), "en-IN")


def _now_utc():
    return datetime.utcnow()


class CRMService:
    """Leads of one agent. agent_id=None is unscoped: only for webhooks that already hold a trusted lead id."""

    def __init__(self, agent_id: int | None = None):
        self.agent_id = agent_id

    def _scoped(self, query):
        return query.where(Lead.agent_id == self.agent_id) if self.agent_id is not None else query

    def _load(self, db, lead_id: int) -> Lead | None:
        lead = db.get(Lead, lead_id)
        if lead is None or (self.agent_id is not None and lead.agent_id != self.agent_id):
            return None
        return lead

    # ---------------- read ----------------

    def _view(self, query, view: str | None):
        """Saved views on the Leads page."""
        active = ("Queued", "Ringing", "In Progress")
        if view == "website":
            return query.where(Lead.source.like("website%"))
        if view == "never_called":
            return query.where(Lead.last_contacted_at.is_(None), Lead.do_not_call.is_(False))
        if view == "hot_uncalled":
            return query.where(Lead.qualification == "Hot", Lead.do_not_call.is_(False),
                               or_(Lead.last_contacted_at.is_(None), Lead.last_contacted_at < _now_utc() - timedelta(days=2)))
        if view == "callbacks":
            return query.where(Lead.callback_at.is_not(None), Lead.callback_at != "")
        if view == "meetings":
            return query.where(Lead.meeting_at.is_not(None), Lead.meeting_at != "")
        if view == "attention":  # needs a person: failed repeatedly, or unreachable number
            return query.where(or_(Lead.retry_count >= 3, (Lead.phone.like("+91%") & (func.length(Lead.phone) != 13))),
                               Lead.call_status.notin_(active))
        if view == "new_callers":
            return query.where(Lead.source == "inbound call")
        if view == "dnc":
            return query.where(Lead.do_not_call.is_(True))
        return query

    def view_counts(self) -> dict:
        with get_db() as db:
            return {v: db.scalar(select(func.count()).select_from(self._view(self._scoped(select(Lead)), v).subquery())) or 0
                    for v in ("website", "new_callers", "never_called", "hot_uncalled", "callbacks", "meetings", "attention", "dnc")}

    def list_leads(self, search=None, status=None, call_status=None, qualification=None, sort="id", order="desc",
                   page=1, page_size=25, view=None, source=None) -> dict:
        with get_db() as db:
            query = self._view(self._scoped(select(Lead)), view)
            if source:
                query = query.where(Lead.source == source)
            if search:
                like = f"%{search.strip()}%"
                query = query.where(or_(Lead.name.ilike(like), Lead.company.ilike(like), Lead.phone.ilike(like),
                                        Lead.email.ilike(like), Lead.city.ilike(like), Lead.tags.ilike(like)))
            if status:
                query = query.where(Lead.status == status)
            if call_status:
                query = query.where(Lead.call_status == call_status)
            if qualification:
                query = query.where(Lead.qualification == qualification)
            total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
            column = SORTABLE.get(sort, Lead.id)
            query = query.order_by(column.desc().nulls_last() if order == "desc" else column.asc().nulls_last())
            rows = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
            return {"items": [r.to_dict() for r in rows], "total": total, "page": page, "page_size": page_size}

    def board(self, statuses: list[str], search=None, qualification=None, per_column: int = 50) -> dict:
        """Leads grouped by status for the pipeline board: the most recently active per column plus totals."""
        with get_db() as db:
            base = self._scoped(select(Lead))
            if search:
                like = f"%{search.strip()}%"
                base = base.where(or_(Lead.name.ilike(like), Lead.company.ilike(like), Lead.phone.ilike(like),
                                      Lead.city.ilike(like), Lead.tags.ilike(like)))
            if qualification:
                base = base.where(Lead.qualification == qualification)
            columns = {}
            for status in statuses:
                query = base.where(Lead.status == status)
                total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
                rows = db.scalars(query.order_by(func.coalesce(Lead.last_contacted_at, Lead.updated_at).desc())
                                  .limit(per_column)).all()
                columns[status] = {"total": total, "items": [r.to_dict() for r in rows]}
            return columns

    def bulk_update(self, lead_ids: list[int], data: dict, add_tags: list[str] | None = None, actor: str = "admin") -> int:
        updated = 0
        for lead_id in lead_ids:
            values = dict(data)
            if add_tags:
                current = (self.get(lead_id) or {}).get("tags") or []
                values["tags"] = list(dict.fromkeys([*current, *add_tags]))
            try:
                self.update(lead_id, values, actor="system")
                updated += 1
            except LookupError:
                continue
        if updated:
            fields = [*data, *(["tags"] if add_tags else [])]
            events.record("lead.updated", f"Bulk updated {updated} lead(s)", ", ".join(fields), agent_id=self.agent_id,
                          actor=actor, data={"ids": lead_ids, **data, **({"add_tags": add_tags} if add_tags else {})})
        return updated

    def get(self, lead_id: int) -> dict | None:
        with get_db() as db:
            lead = self._load(db, lead_id)
            return lead.to_dict() if lead else None

    def find_by_phone(self, phone) -> dict | None:
        normalized = normalize_phone(phone)
        if not normalized:
            return None
        with get_db() as db:
            lead = db.scalars(self._scoped(select(Lead)).where(Lead.phone == normalized).order_by(Lead.id.desc())).first()
            return lead.to_dict() if lead else None

    def pending_for_dial(self, limit: int) -> list[dict]:
        with get_db() as db:
            query = self._scoped(select(Lead)).where(
                Lead.do_not_call.is_(False),
                or_(Lead.call_status == "Pending", (Lead.status == "New") & Lead.call_status.is_(None)),
                ~(Lead.phone.like("+91%") & (func.length(Lead.phone) != 13)),
                or_(Lead.callback_at.is_(None), Lead.callback_at == ""),  # scheduled calls belong to the callback job
            ).order_by(Lead.id).limit(limit)
            return [l.to_dict() for l in db.scalars(query)]

    def retry_candidates(self, max_retries: int, min_gap_minutes: int, limit: int) -> list[dict]:
        cutoff = _now_utc() - timedelta(minutes=min_gap_minutes)
        with get_db() as db:
            query = self._scoped(select(Lead)).where(
                Lead.do_not_call.is_(False),
                Lead.call_status.in_(("No Answer", "Busy", "Failed")),
                Lead.retry_count < max_retries,
                ~(Lead.phone.like("+91%") & (func.length(Lead.phone) != 13)),
                or_(Lead.last_contacted_at.is_(None), Lead.last_contacted_at < cutoff),
            ).order_by(Lead.last_contacted_at).limit(limit)
            return [l.to_dict() for l in db.scalars(query)]

    def due_callbacks(self, now_ist: str, limit: int) -> list[dict]:
        """Leads whose promised callback time ("YYYY-MM-DD HH:MM" IST, sortable as text) has arrived."""
        with get_db() as db:
            query = self._scoped(select(Lead)).where(
                Lead.do_not_call.is_(False), Lead.callback_at.is_not(None), Lead.callback_at != "", Lead.callback_at <= now_ist,
            ).order_by(Lead.callback_at).limit(limit)
            return [l.to_dict() for l in db.scalars(query)]

    def queued(self, limit: int, dialable_only: bool = True) -> list[dict]:
        """The call queue in order: queue_position first (set by the user), then when queued."""
        with get_db() as db:
            query = self._scoped(select(Lead)).where(Lead.call_status == "Pending", Lead.do_not_call.is_(False))
            if dialable_only:
                query = query.where(
                    ~(Lead.phone.like("+91%") & (func.length(Lead.phone) != 13)),
                    or_(Lead.callback_at.is_(None), Lead.callback_at == ""),
                    # never ring someone again minutes after a call
                    or_(Lead.last_contacted_at.is_(None), Lead.last_contacted_at < _now_utc() - timedelta(minutes=10)),
                )
            query = query.order_by(Lead.queue_position.is_(None), Lead.queue_position, Lead.updated_at).limit(limit)
            return [l.to_dict() for l in db.scalars(query)]

    def reorder_queue(self, lead_ids: list[int]) -> int:
        """Set the queue order exactly as given (position 1..n); leads not listed keep their place after them."""
        with get_db() as db:
            leads = {l.id: l for l in db.scalars(self._scoped(select(Lead)).where(Lead.id.in_(lead_ids), Lead.call_status == "Pending"))}
            for position, lead_id in enumerate(lead_ids, start=1):
                if lead_id in leads:
                    leads[lead_id].queue_position = position
            return len(leads)

    def dequeue(self, lead_ids: list[int]) -> int:
        with get_db() as db:
            leads = list(db.scalars(self._scoped(select(Lead)).where(Lead.id.in_(lead_ids), Lead.call_status == "Pending")))
            for lead in leads:
                lead.call_status, lead.queue_position = None, None
            return len(leads)

    def queue_size(self) -> int:
        with get_db() as db:
            return db.scalar(self._scoped(select(func.count(Lead.id))).where(Lead.call_status == "Pending", Lead.do_not_call.is_(False))) or 0

    def nurture_candidates(self, after_days: int, limit: int) -> list[dict]:
        """Warm leads with no contact for after_days and nothing already scheduled."""
        cutoff = _now_utc() - timedelta(days=after_days)
        with get_db() as db:
            query = self._scoped(select(Lead)).where(
                Lead.do_not_call.is_(False), Lead.status.in_(("Interested", "Follow Up")),
                or_(Lead.meeting_at.is_(None), Lead.meeting_at == ""), or_(Lead.callback_at.is_(None), Lead.callback_at == ""),
                or_(Lead.call_status.is_(None), Lead.call_status.notin_(("Queued", "Ringing", "In Progress"))),
                or_(Lead.last_contacted_at.is_(None), Lead.last_contacted_at < cutoff),
            ).order_by(Lead.last_contacted_at).limit(limit)
            return [l.to_dict() for l in db.scalars(query)]

    def meetings_on(self, date_str: str) -> list[dict]:
        with get_db() as db:
            return [l.to_dict() for l in db.scalars(self._scoped(select(Lead)).where(Lead.meeting_at.like(f"{date_str}%")))]

    def stats(self) -> dict:
        with get_db() as db:
            def count(*conds):
                return db.scalar(self._scoped(select(func.count(Lead.id))).where(*conds)) or 0

            def group(column):
                return {(k or "Unknown"): v for k, v in db.execute(self._scoped(select(column, func.count())).group_by(column)).all()}
            return {
                "total": count(),
                "by_status": group(Lead.status),
                "by_call_status": group(Lead.call_status),
                "by_qualification": group(Lead.qualification),
                "pending": count(Lead.do_not_call.is_(False),
                                 or_(Lead.call_status == "Pending", (Lead.status == "New") & Lead.call_status.is_(None))),
                "meetings": count(Lead.meeting_at.is_not(None), Lead.meeting_at != ""),
            }

    # ---------------- write ----------------

    @staticmethod
    def _apply(lead: Lead, data: dict) -> dict:
        changes = {}
        for key, value in data.items():
            if key not in EDITABLE:
                continue
            if key == "phone":
                value = normalize_phone(value)
                if not value:
                    raise ValueError("Invalid phone number.")
            elif key == "language":
                value = normalize_language(value)
            elif key == "tags" and isinstance(value, list):
                value = ",".join(t.strip() for t in value if t.strip())
            elif key == "retry_count":
                value = int(value or 0)
            elif key == "do_not_call":
                value = bool(value)
            elif isinstance(value, str):
                value = value.strip() or None
            if getattr(lead, key) != value:
                changes[key] = {"from": getattr(lead, key), "to": value}
                setattr(lead, key, value)
        return changes

    def create(self, data: dict, actor: str = "admin") -> dict:
        if self.agent_id is None:
            raise ValueError("A lead must belong to an agent.")
        if not normalize_phone(data.get("phone")):
            raise ValueError("A valid phone number is required (10-digit Indian or +country code).")
        with get_db() as db:
            lead = Lead(agent_id=self.agent_id, status="New", language="en-IN", retry_count=0)
            self._apply(lead, {k: v for k, v in data.items() if v not in (None, "")})
            db.add(lead)
            db.flush()
            result = lead.to_dict()
        events.record("lead.created", f"Lead added: {result['name'] or result['phone']}", agent_id=self.agent_id,
                      lead_id=result["id"], actor=actor)
        return result

    def update(self, lead_id: int, data: dict, actor: str = "admin", event_type: str = "lead.updated",
               title: str | None = None, touch: bool = False) -> dict:
        with get_db() as db:
            lead = self._load(db, lead_id)
            if lead is None:
                raise LookupError(f"Lead {lead_id} not found.")
            changes = self._apply(lead, data)
            if touch:
                lead.last_contacted_at = _now_utc()
            result = lead.to_dict()
        if changes and actor != "system":
            events.record(event_type, title or f"Updated {', '.join(changes)}", agent_id=result["agent_id"], lead_id=lead_id,
                          actor=actor, data={k: v["to"] for k, v in changes.items()})
        return result

    def delete(self, lead_ids: list[int], actor: str = "admin") -> int:
        with get_db() as db:
            query = db.query(Lead).filter(Lead.id.in_(lead_ids))
            if self.agent_id is not None:
                query = query.filter(Lead.agent_id == self.agent_id)
            count = query.delete(synchronize_session=False)
        if count:
            events.record("lead.deleted", f"Deleted {count} lead(s)", agent_id=self.agent_id, actor=actor, data={"ids": lead_ids})
        return count

    # ---------------- import / export ----------------

    @staticmethod
    def read_table(filename: str, content: bytes) -> pd.DataFrame:
        name = filename.lower()
        if name.endswith((".xlsx", ".xls")):
            return pd.read_excel(io.BytesIO(content), dtype=str)
        for encoding in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                return pd.read_csv(io.BytesIO(content), dtype=str, encoding=encoding, sep=None, engine="python")
            except UnicodeDecodeError:
                continue
        raise ValueError("Could not decode the CSV file.")

    @staticmethod
    def map_columns(columns) -> dict:
        mapping = {}
        for col in columns:
            key = "".join(ch for ch in str(col).lower() if ch.isalnum())
            if key in IMPORT_ALIASES and IMPORT_ALIASES[key] not in mapping.values():
                mapping[str(col)] = IMPORT_ALIASES[key]
        return mapping

    IMPORT_STATUSES = ("New", "Contacted", "Interested", "Follow Up", "Meeting Booked", "Closed Won", "Closed Lost", "Not Interested")

    def _row_values(self, row: dict, mapping: dict, defaults: dict) -> tuple[dict | None, str | None]:
        """Cleaned lead fields for one spreadsheet row, or an error explaining why it can't be imported."""
        values = {field: _clean(row.get(col)) for col, field in mapping.items() if field}
        phone = normalize_phone(values.get("phone"))
        if not phone:
            return None, f"Invalid phone: {values.get('phone') or '(empty)'}"
        email = values.get("email")
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            email = None  # keep the lead, drop the bad address
        status = next((s for s in self.IMPORT_STATUSES if s.lower() == (values.get("status") or "").strip().lower()), "New")
        tags = [t.strip() for t in f"{values.get('tags') or ''},{defaults.get('tags') or ''}".split(",") if t.strip()]
        return {
            "name": values.get("name"), "company": values.get("company"), "phone": phone, "email": email,
            "city": values.get("city"), "language": normalize_language(values.get("language") or defaults.get("language")),
            "source": values.get("source") or defaults.get("source") or "import",
            "tags": ",".join(dict.fromkeys(tags)) or None, "notes": values.get("notes"), "status": status,
        }, None

    def analyze_rows(self, df: pd.DataFrame, mapping: dict | None = None) -> dict:
        """Dry run for the preview: how many rows are new, duplicates (in the CRM or repeated in the file) or invalid."""
        mapping = mapping or self.map_columns(df.columns)
        if "phone" not in mapping.values():
            return {"ready": 0, "duplicates": 0, "invalid": len(df), "row_status": []}
        with get_db() as db:
            existing = set(db.scalars(self._scoped(select(Lead.phone))))
        seen, ready, duplicates, invalid, row_status = set(), 0, 0, 0, []
        for i, row in enumerate(df.to_dict("records")):
            values, error = self._row_values(row, mapping, {})
            if error:
                invalid += 1
                state = {"state": "invalid", "detail": error}
            elif values["phone"] in existing or values["phone"] in seen:
                duplicates += 1
                state = {"state": "duplicate", "detail": "Already in this agent's leads" if values["phone"] in existing else "Repeated in this file"}
            else:
                ready += 1
                state = {"state": "ready", "detail": values["phone"]}
            if values:
                seen.add(values["phone"])
            if i < 8:
                row_status.append(state)
        return {"ready": ready, "duplicates": duplicates, "invalid": invalid, "row_status": row_status}

    def import_rows(self, df: pd.DataFrame, mapping: dict | None = None, skip_duplicates: bool = True,
                    defaults: dict | None = None, actor: str = "admin", on_duplicate: str | None = None,
                    queue_for_calls: bool = False) -> dict:
        """on_duplicate: skip (default) or update (fill in the existing lead's empty fields, add tags and notes)."""
        if self.agent_id is None:
            raise ValueError("Leads must be imported into an agent.")
        mapping = mapping or self.map_columns(df.columns)
        if "phone" not in mapping.values():
            raise ValueError("Map one column to Phone.")
        defaults = dict(defaults or {})
        batch = datetime.now(IST).strftime("import-%m%d-%H%M")  # tag to find this import's leads later
        defaults["tags"] = ",".join(t for t in (defaults.get("tags"), batch) if t)
        on_duplicate = on_duplicate or ("skip" if skip_duplicates else "update")
        created, updated, skipped, errors = 0, 0, 0, []

        with get_db() as db:
            leads_by_phone = {l.phone: l for l in db.scalars(self._scoped(select(Lead)))}  # duplicates are per agent
            for i, row in enumerate(df.to_dict("records"), start=2):
                values, error = self._row_values(row, mapping, defaults)
                if error:
                    errors.append({"row": i, "error": error})
                    continue
                current = leads_by_phone.get(values["phone"])
                if current is not None:
                    if on_duplicate != "update":
                        skipped += 1
                        continue
                    for field in ("name", "company", "email", "city", "source"):
                        if values.get(field) and not getattr(current, field):
                            setattr(current, field, values[field])
                    if values.get("tags"):
                        merged = [t for t in f"{current.tags or ''},{values['tags']}".split(",") if t.strip()]
                        current.tags = ",".join(dict.fromkeys(merged))
                    if values.get("notes") and values["notes"] not in (current.notes or ""):
                        current.notes = "\n".join(x for x in (current.notes, values["notes"]) if x)
                    updated += 1
                    continue
                lead = Lead(agent_id=self.agent_id, retry_count=0, **values,
                            call_status="Pending" if queue_for_calls else None)
                db.add(lead)
                leads_by_phone[values["phone"]] = lead
                created += 1

        events.record("lead.imported", f"Imported {created} lead(s)",
                      f"{updated} updated · {skipped} duplicates skipped · {len(errors)} invalid rows", agent_id=self.agent_id,
                      actor=actor, data={"created": created, "updated": updated, "skipped": skipped, "errors": len(errors)})
        return {"created": created, "updated": updated, "skipped_duplicates": skipped, "errors": errors[:500], "mapping": mapping,
                "batch_tag": batch}

    def export_csv(self) -> str:
        with get_db() as db:
            rows = [l.to_dict() for l in db.scalars(self._scoped(select(Lead)).order_by(Lead.id))]
        for r in rows:
            r["tags"] = ",".join(r["tags"])
        return pd.DataFrame(rows).to_csv(index=False)

    def import_legacy_excel(self, path: str):
        from pathlib import Path
        if self.agent_id is None or not Path(path).exists():
            return
        with get_db() as db:
            if db.scalar(select(func.count(Lead.id))):
                return
        df = pd.read_excel(path, dtype=str)
        result = self.import_rows(df, actor="system")
        events.record("lead.imported", f"Migrated {result['created']} lead(s) from {path}", agent_id=self.agent_id, actor="system")
