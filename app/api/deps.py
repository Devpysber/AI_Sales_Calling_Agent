from fastapi import HTTPException, Path

from app.services import agents


def workspace(agent_id: int = Path(..., ge=1)) -> int:
    """Path dependency for /api/agents/{agent_id}/...: 404 unless the agent exists."""
    if not agents.exists(agent_id):
        raise HTTPException(404, "Agent not found.")
    return agent_id
