from fastapi import HTTPException, Path, Request

from app.services import agents

def require_admin(request: Request):
    user = getattr(request.state, "user", "")
    if user != "admin" and user != "api":
        raise HTTPException(403, "Administrator access required.")

def workspace(request: Request, agent_id: int = Path(..., ge=1)) -> int:
    """Path dependency for /api/agents/{agent_id}/...: 404 unless the agent exists."""
    if not agents.exists(agent_id):
        raise HTTPException(404, "Agent not found.")
        
    user = getattr(request.state, "user", "")
    if user != "admin" and user != "api":
        if user == "team":
            payload = getattr(request.state, "token_payload", {})
            # A member always reaches the workspaces they made: with no passcode set (the default) the
            # unlock form was the only way in and it rejects every password, so their own agent was a
            # dead end. Someone else's workspace still needs its passcode.
            if agent_id not in payload.get("unlocked", []) and not agents.made_by(agent_id, payload.get("team_id")):
                raise HTTPException(403, "LOCKED")
        else:
            raise HTTPException(403, "You do not have access to this agent workspace.")
            
    return agent_id
