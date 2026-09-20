"""
Activity log writer / reader, scoped per agent.
"""

import json

from sqlalchemy import select

from app.core.database import get_db
from app.core.logging import get_logger
from app.models.call import Call
from app.models.event import Event
from app.models.lead import Lead

log = get_logger(__name__)


def record(type: str, title: str, detail: str | None = None, *, agent_id: int | None = None, lead_id: int | None = None,
           call_id: int | None = None, actor: str = "system", data: dict | None = None):
    try:
        with get_db() as db:
            if agent_id is None and lead_id:
                agent_id = db.scalar(select(Lead.agent_id).where(Lead.id == lead_id))
            if agent_id is None and call_id:
                agent_id = db.scalar(select(Call.agent_id).where(Call.id == call_id))
            db.add(Event(agent_id=agent_id, type=type, title=title[:255], detail=detail, lead_id=lead_id, call_id=call_id,
                         actor=actor, data=json.dumps(data, ensure_ascii=False, default=str) if data else None))
    except Exception:
        log.exception("Failed to record event %s", type)


def list_events(agent_id: int | None, lead_id: int | None = None, call_id: int | None = None,
                type_prefix: str | None = None, limit: int = 50, before_id: int | None = None,
                agent_ids: list[int] | None = None) -> list[dict]:
    if agent_ids is not None and not agent_ids:
        return []
    with get_db() as db:
        query = select(Event, Lead.name).outerjoin(Lead, Lead.id == Event.lead_id).order_by(Event.id.desc()).limit(limit)
        if agent_id is not None:
            query = query.where(Event.agent_id == agent_id)
        elif agent_ids is not None:
            query = query.where(Event.agent_id.in_(agent_ids))
        if lead_id:
            query = query.where(Event.lead_id == lead_id)
        if call_id:
            query = query.where(Event.call_id == call_id)
        if type_prefix:
            query = query.where(Event.type.startswith(type_prefix))
        if before_id:
            query = query.where(Event.id < before_id)
        return [{**event.to_dict(), "lead_name": name} for event, name in db.execute(query).all()]
