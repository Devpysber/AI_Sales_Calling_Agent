"""Admin API for an agent's tool_definitions: builtin (seeded, disable-only) and custom HTTP tools
(no-code: a business supplies name/description/parameters/URL/method/auth)."""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import workspace
from app.core.database import get_db
from app.models.channel import ToolDefinition, ToolRun
from app.services import industry_packs, tool_registry
from app.services.tool_registry import template_host_is_fixed

router = APIRouter(prefix="/api/agents/{agent_id}/tools", tags=["tools"])
packs_router = APIRouter(prefix="/api/agents/{agent_id}/industry-packs", tags=["tools"])


class ToolIn(BaseModel):
    name: str
    description: str = ""
    parameters: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})
    url: str
    method: str = "GET"
    auth_type: str = "none"  # none | bearer | api_key
    auth_header: str = "X-API-Key"
    secret: str | None = None
    timeout_seconds: int = Field(10, ge=1, le=30)
    risk: str = "read"  # read | write | money
    channels: list[str] | None = None


class ToolPatch(BaseModel):
    description: str | None = None
    parameters: dict | None = None
    url: str | None = None
    method: str | None = None
    auth_type: str | None = None
    auth_header: str | None = None
    secret: str | None = None
    timeout_seconds: int | None = Field(None, ge=1, le=30)
    risk: str | None = None
    channels: list[str] | None = None
    enabled: bool | None = None


@router.get("")
def list_tools(agent_id: int = Depends(workspace)):
    tool_registry.ensure_builtin_defaults(agent_id)
    with get_db() as db:
        rows = db.query(ToolDefinition).filter(ToolDefinition.agent_id == agent_id).order_by(ToolDefinition.id).all()
        return [r.to_dict() for r in rows]


@router.post("")
def create_tool(body: ToolIn, agent_id: int = Depends(workspace)):
    if not template_host_is_fixed(body.url):
        raise HTTPException(400, "The tool URL's host/scheme may not contain a {placeholder} — only the "
                                  "path, query, or body may be templated from the tool's arguments.")
    config = {"url": body.url, "method": body.method.upper(), "auth_type": body.auth_type,
              "auth_header": body.auth_header}
    with get_db() as db:
        row = ToolDefinition(
            agent_id=agent_id, name=body.name, description=body.description, type="http",
            parameters_json=json.dumps(body.parameters, ensure_ascii=False),
            config_json=json.dumps(config, ensure_ascii=False),
            risk=body.risk, timeout_seconds=body.timeout_seconds,
            channels_json=json.dumps(body.channels, ensure_ascii=False) if body.channels else None,
            needs_confirmation=body.risk in ("write", "money"), enabled=True,
        )
        db.add(row)
        db.flush()
        tool_id = row.id
    if body.secret:
        tool_registry.set_tool_secret(agent_id, tool_id, body.secret)
    return tool_registry.get_definition(agent_id, body.name)


@router.patch("/{tool_id}")
def patch_tool(tool_id: int, body: ToolPatch, agent_id: int = Depends(workspace)):
    with get_db() as db:
        row = db.query(ToolDefinition).filter(ToolDefinition.agent_id == agent_id, ToolDefinition.id == tool_id).first()
        if not row:
            raise HTTPException(404, "Tool not found.")
        if body.description is not None:
            row.description = body.description
        if body.parameters is not None:
            row.parameters_json = json.dumps(body.parameters, ensure_ascii=False)
        if body.channels is not None:
            row.channels_json = json.dumps(body.channels, ensure_ascii=False) if body.channels else None
        if body.timeout_seconds is not None:
            row.timeout_seconds = body.timeout_seconds
        if body.risk is not None:
            row.risk = body.risk
            row.needs_confirmation = body.risk in ("write", "money")
        if body.enabled is not None:
            row.enabled = body.enabled
        if row.type == "http":
            config = json.loads(row.config_json) if row.config_json else {}
            if body.url is not None:
                if not template_host_is_fixed(body.url):
                    raise HTTPException(400, "The tool URL's host/scheme may not contain a {placeholder} "
                                              "— only the path, query, or body may be templated from the "
                                              "tool's arguments.")
                config["url"] = body.url
            if body.method is not None:
                config["method"] = body.method.upper()
            if body.auth_type is not None:
                config["auth_type"] = body.auth_type
            if body.auth_header is not None:
                config["auth_header"] = body.auth_header
            row.config_json = json.dumps(config, ensure_ascii=False)
        name = row.name
    if body.secret is not None:
        tool_registry.set_tool_secret(agent_id, tool_id, body.secret)
    return tool_registry.get_definition(agent_id, name)


@router.delete("/{tool_id}")
def delete_tool(tool_id: int, agent_id: int = Depends(workspace)):
    with get_db() as db:
        row = db.query(ToolDefinition).filter(ToolDefinition.agent_id == agent_id, ToolDefinition.id == tool_id).first()
        if not row:
            raise HTTPException(404, "Tool not found.")
        if row.type != "http":
            raise HTTPException(400, "Builtin tools can only be disabled, not deleted.")
        db.delete(row)
    return {"ok": True}


class ToolTest(BaseModel):
    arguments: dict = Field(default_factory=dict)


@router.post("/{tool_id}/test")
def test_tool(tool_id: int, body: ToolTest, agent_id: int = Depends(workspace)):
    definition = None
    with get_db() as db:
        row = db.query(ToolDefinition).filter(ToolDefinition.agent_id == agent_id, ToolDefinition.id == tool_id).first()
        if not row:
            raise HTTPException(404, "Tool not found.")
        definition = row.to_dict()
    if definition["type"] != "http":
        raise HTTPException(400, "Only custom HTTP tools can be test-run.")
    context = {"lead_phone": "+10000000000", "lead_name": "Test Lead"}
    return tool_registry.run_http_tool(definition, body.arguments, context)


@router.get("/{tool_id}/runs")
def list_tool_runs(tool_id: int, agent_id: int = Depends(workspace)):
    with get_db() as db:
        row = db.query(ToolDefinition).filter(ToolDefinition.agent_id == agent_id, ToolDefinition.id == tool_id).first()
        if not row:
            raise HTTPException(404, "Tool not found.")
        tool_name = row.name
        runs = (
            db.query(ToolRun)
            .filter(ToolRun.agent_id == agent_id, ToolRun.tool_name == tool_name)
            .order_by(ToolRun.created_at.desc())
            .limit(50)
            .all()
        )
        return [r.to_dict() for r in runs]


@packs_router.get("")
def list_industry_packs(agent_id: int = Depends(workspace)):
    return industry_packs.list_packs()


@packs_router.post("/{key}/apply")
def apply_industry_pack(key: str, agent_id: int = Depends(workspace)):
    try:
        return industry_packs.apply_industry_pack(agent_id, key)
    except industry_packs.IndustryPackNotFound:
        raise HTTPException(404, "Unknown industry pack.")
