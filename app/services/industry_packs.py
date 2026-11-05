"""Industry packs: a named preset that (a) enables the subset of the existing 20 CUSTOMER_TOOLS relevant
to a business vertical and (b) appends a short playbook snippet steering the agent's conversational focus.

A pack does not add new capability - it only re-points which of the existing builtin tools are enabled
and nudges the persona's `instructions` text (the same field the playbook writer fills in
app/services/persona_writer.py). "Test drive booking" for automotive, for example, is really just
`set_meeting` worded for a test drive, not a new booking subsystem.
"""

from app.services import agents, tool_registry
from app.services.agent_tools import CUSTOMER_TOOLS

ALL_CUSTOMER_TOOL_NAMES = [t["function"]["name"] for t in CUSTOMER_TOOLS]

INDUSTRY_PACKS = {
    "real_estate": {
        "key": "real_estate",
        "label": "Real Estate",
        "description": "For brokers/agencies qualifying buyers and booking site visits.",
        "enabled_tools": [
            "search_knowledge", "schedule_callback", "set_meeting", "reschedule_meeting", "cancel_meeting",
            "get_my_details", "update_lead_requirement", "send_brochure", "share_location",
            "handoff_to_human", "create_support_ticket", "set_language_preference", "opt_out",
        ],
        # Reasoning: site visits map to set_meeting/reschedule/cancel; brochures and location are the
        # two documents/assets a property agent actually sends; order/payment/ticket-status tools and
        "playbook_snippet": (
            "This is a real estate conversation: ask about budget, preferred location/locality and "
            "timeline before anything else. Once you know those, offer to book a site visit rather than "
            "giving a hard sell over chat/call. Keep property details grounded in the knowledge base only."
        ),
    },
    "automotive": {
        "key": "automotive",
        "label": "Automotive",
        "description": "For dealerships qualifying buyers and booking test drives/service visits.",
        "enabled_tools": [
            "search_knowledge", "schedule_callback", "set_meeting", "reschedule_meeting", "cancel_meeting",
            "get_my_details", "update_lead_requirement", "send_brochure", "share_location",
            "check_my_order_or_deal_status", "send_payment_link", "handoff_to_human",
            "create_support_ticket", "set_language_preference", "opt_out",
        ],
        # Reasoning: adds order/deal-status and payment-link on top of the real-estate set, since a
        # dealership tracks a booking/delivery pipeline and sometimes takes a token payment; "test drive"
        # is set_meeting with automotive wording, not a new booking tool.
        "playbook_snippet": (
            "This is an automotive dealership conversation: ask about the model they're interested in, "
            "budget, and whether they have a trade-in vehicle. Offer to book a test drive (use the "
            "meeting tool) once interest is clear, and quote only prices/specs from the knowledge base."
        ),
    },
    "clinics": {
        "key": "clinics",
        "label": "Clinics / Healthcare",
        "description": "For clinics booking appointments and triaging routine vs urgent requests.",
        "enabled_tools": [
            "search_knowledge", "schedule_callback", "set_meeting", "reschedule_meeting", "cancel_meeting",
            "get_my_details", "update_lead_requirement", "handoff_to_human", "escalate_urgent",
            "create_support_ticket", "set_language_preference", "opt_out",
        ],
        # Reasoning: drops brochure/location/payment/order-status (not how a clinic books patients) and
        # adds escalate_urgent, since a symptom described as urgent should reach a human fast rather than
        # staying in create_support_ticket's normal queue.
        "playbook_snippet": (
            "This is a clinic/healthcare conversation: ask about the symptom or reason for the visit, "
            "any preferred doctor, and how urgent it feels. Offer to book an appointment (use the meeting "
            "tool) for routine requests, and escalate immediately for anything that sounds time-critical. "
            "Never give medical advice or diagnosis - only the clinic's own knowledge base and scheduling."
        ),
    },
}


class IndustryPackNotFound(LookupError):
    pass


def list_packs() -> list[dict]:
    return [{"key": p["key"], "label": p["label"], "description": p["description"],
              "enabled_tools": p["enabled_tools"], "playbook_snippet": p["playbook_snippet"]}
             for p in INDUSTRY_PACKS.values()]


def apply_industry_pack(agent_id: int, pack_key: str) -> dict:
    pack = INDUSTRY_PACKS.get(pack_key)
    if not pack:
        raise IndustryPackNotFound(pack_key)

    tool_registry.ensure_builtin_defaults(agent_id)
    enabled_set = set(pack["enabled_tools"])

    from app.core.database import get_db
    from app.models.channel import ToolDefinition

    with get_db() as db:
        rows = db.query(ToolDefinition).filter(
            ToolDefinition.agent_id == agent_id, ToolDefinition.type == "builtin",
            ToolDefinition.name.in_(ALL_CUSTOMER_TOOL_NAMES),
        ).all()
        for row in rows:
            row.enabled = row.name in enabled_set

    profile = agents.get_profile(agent_id)
    existing = (profile.get("instructions") or "").strip()
    # Strip a previously-applied pack's own snippet so re-applying (this pack or a different one) swaps
    # the vertical steering text rather than piling up every pack ever applied.
    prev_pack = profile.get("industry_pack")
    if prev_pack and prev_pack in INDUSTRY_PACKS:
        prev_snippet = INDUSTRY_PACKS[prev_pack]["playbook_snippet"]
        existing = existing.replace(f"\n\n{prev_snippet}", "").replace(prev_snippet, "").strip()
    snippet = pack["playbook_snippet"]
    merged = f"{existing}\n\n{snippet}".strip() if existing else snippet
    agents.update_profile(agent_id, {"instructions": merged, "industry_pack": pack_key})

    return {"pack": pack_key, "enabled_tools": pack["enabled_tools"], "playbook_snippet": snippet}
