"""Chat-channel agent: a tool-calling loop over agent.build_messages(), for WhatsApp (and future channels).

Mirrors the ReAct-style pattern voice calls already use in agent.respond_stream / agent._json_tool_turn,
but restricted to CUSTOMER_TOOLS and executed with role="customer" — a much smaller, chat-safe slice of
the tool catalogue, with the real lead_id/agent_id/conversation_id always injected from the conversation
context, never read from the model's own JSON arguments.
"""

import json
import logging
import time
from datetime import datetime, timedelta

from app.services import agent as agent_svc
from app.services import llm
from app.services import tool_registry
from app.services.agent_tools import execute_tool
from app.services.whatsapp_cloud_service import CHAT_GUIDANCE
from app.services.instagram_service import CHAT_GUIDANCE as INSTAGRAM_CHAT_GUIDANCE

CHAT_GUIDANCE_BY_CHANNEL = {"whatsapp": CHAT_GUIDANCE, "instagram": INSTAGRAM_CHAT_GUIDANCE}

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4
# Builtin CUSTOMER_TOOLS are all read/write (nothing moves money), so only risk="money" gates today —
# gating "write" too is left to a business's own custom tools (set risk="write" there) per the PRD note
# that write *may* warrant it; the 6 defaults keep their existing no-gate behaviour (Day-3 tests rely on it).
_CONFIRM_RISKS = ("money",)
CONFIRM_TTL_SECONDS = 600


def _log_tool_run(agent_id: int, conversation_id: int | None, name: str, args: dict, result: str, status: str,
                   duration_ms: int, message_id: int | None = None) -> None:
    from app.core.database import get_db
    from app.models.channel import ToolRun
    try:
        with get_db() as db:
            db.add(ToolRun(agent_id=agent_id, conversation_id=conversation_id, message_id=message_id, tool_name=name,
                            arguments_json=json.dumps(args, ensure_ascii=False, default=str),
                            result=str(result)[:4000], status=status, duration_ms=duration_ms))
    except Exception:
        log.exception("tool_runs logging failed for %s", name)


def _pending_confirm(agent_id: int, conversation_id: int | None, name: str) -> bool:
    """True if this tool/conversation already has a pending confirmation ToolRun (so this call is the
    customer's confirmation, not the first ask) — clears it so the next ask starts fresh."""
    from app.core.database import get_db
    from app.models.channel import ToolRun
    cutoff = datetime.utcnow() - timedelta(seconds=CONFIRM_TTL_SECONDS)
    with get_db() as db:
        row = (db.query(ToolRun)
               .filter(ToolRun.agent_id == agent_id, ToolRun.conversation_id == conversation_id,
                       ToolRun.tool_name == name, ToolRun.status == "pending_confirm",
                       ToolRun.created_at >= cutoff)
               .order_by(ToolRun.id.desc()).first())
        if row:
            row.status = "confirmed"
            return True
    return False


def _run_tool(name: str, args: dict, agent_id: int, conversation_id: int | None, lead_id) -> tuple[str, str]:
    # The real lead_id/agent_id/conversation_id always win: anything the model put in its own JSON for
    # these keys is dropped before the tool ever runs, so a crafted lead_id cannot reach another lead.
    safe_args = {k: v for k, v in (args or {}).items() if k not in ("lead_id", "agent_id", "conversation_id")}
    safe_args["lead_id"] = lead_id
    safe_args["conversation_id"] = conversation_id
    started = time.perf_counter()
    status = "ok"

    definition = tool_registry.get_definition(agent_id, name)
    if not definition or not definition.get("enabled"):
        result = f"Access Denied or Unknown tool: {name}"
        status = "denied"
        _log_tool_run(agent_id, conversation_id, name, safe_args, result, status, 0)
        return result, status

    # Confirmation gate: risk="money"/"write" tools don't execute on the first ask — the agent asks the
    # customer to confirm in natural language, and only runs on the next turn once confirmed.
    if definition.get("risk") in _CONFIRM_RISKS and not _pending_confirm(agent_id, conversation_id, name):
        result = ("Before I do that, please confirm: should I go ahead with "
                   f"{name.replace('_', ' ')} ({json.dumps(safe_args, ensure_ascii=False, default=str)})? Reply yes to proceed.")
        duration_ms = int((time.perf_counter() - started) * 1000)
        _log_tool_run(agent_id, conversation_id, name, safe_args, result, "pending_confirm", duration_ms)
        return result, "pending_confirm"

    try:
        if definition["type"] == "http":
            context = {"lead_id": lead_id}
            outcome = tool_registry.run_http_tool(definition, safe_args, context)
            result = outcome["result"] if outcome["ok"] else (outcome["error"] or "Tool failed.")
            status = "ok" if outcome["ok"] else "error"
        else:
            result = execute_tool(name, json.dumps(safe_args, ensure_ascii=False, default=str), agent_id, role="customer")
            if isinstance(result, str) and result.lower().startswith(("failed", "access denied", "tool ", "error")):
                status = "error"
    except Exception as e:  # noqa: BLE001
        result = f"Tool {name} failed: {e}"
        status = "error"
    duration_ms = int((time.perf_counter() - started) * 1000)
    _log_tool_run(agent_id, conversation_id, name, safe_args, result, status, duration_ms)
    return result, status


def _collect_tool_calls(messages: list[dict], tools: list[dict], max_tokens: int = 300) -> tuple[list[dict], str]:
    """One native-tool-calling round: (tool_calls, text)."""
    tool_calls: list[dict] = []
    text_parts: list[str] = []
    for delta in llm.stream(messages, max_tokens=max_tokens, temperature=0.4, tools=tools):
        if isinstance(delta, dict) and "tool_calls" in delta:
            for tc in delta["tool_calls"]:
                idx = tc.get("index", 0)
                while len(tool_calls) <= idx:
                    tool_calls.append({"id": "", "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    tool_calls[idx]["id"] = tc["id"]
                if tc.get("function"):
                    f = tc["function"]
                    if f.get("name"):
                        tool_calls[idx]["function"]["name"] = f["name"]
                    if f.get("arguments"):
                        tool_calls[idx]["function"]["arguments"] += f["arguments"]
        elif isinstance(delta, str):
            text_parts.append(delta)
    return tool_calls, "".join(text_parts).strip()


def _native_loop(messages: list[dict], tools: list[dict], agent_id: int, conversation_id: int | None, lead_id) -> dict:
    tools_used: list[dict] = []
    for round_n in range(MAX_TOOL_ROUNDS):
        tool_calls, text = _collect_tool_calls(messages, tools)
        if not tool_calls:
            return {"reply": text, "end_call": False, "tools_used": tools_used}
        for i, tc in enumerate(tool_calls):
            tc["id"] = tc["id"] or f"call_{round_n}_{i}"
        messages.append({"role": "assistant", "tool_calls": [
            {"id": tc["id"], "type": "function", "function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}}
            for tc in tool_calls]})
        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except Exception:
                args = {}
            result, status = _run_tool(name, args if isinstance(args, dict) else {}, agent_id, conversation_id, lead_id)
            tools_used.append({"name": name, "status": status})
            # Custom HTTP tools are already capped (tool_registry.run_http_tool, 4000 chars); this guards
            # the builtin ones too, so a future verbose one can't inflate every remaining round's prompt.
            messages.append({"role": "tool", "tool_call_id": tc["id"], "name": name, "content": result[:2000]})
    log.warning("Chat tool loop hit %s rounds for agent %s", MAX_TOOL_ROUNDS, agent_id)
    messages.append({"role": "user", "content": "Reply to the customer now in one short chat message, no more tool calls."})
    result = llm.complete(llm._without_tools(messages), max_tokens=250, temperature=0.4)
    return {"reply": (result.text or "").strip(), "end_call": False, "tools_used": tools_used}


def _json_fallback(messages: list[dict], tools: list[dict], agent_id: int, conversation_id: int | None, lead_id) -> dict:
    """The same tool loop, driven through a JSON turn when native tool-calling isn't available."""
    catalogue = "\n".join(
        f'- {t["function"]["name"]}: {t["function"]["description"]} Arguments: '
        f'{json.dumps(t["function"].get("parameters", {}).get("properties", {}))}' for t in tools)
    instruction = (
        "\n\n# Tools (answer as JSON only)\n"
        "You can run one tool per turn. Respond with ONLY this JSON object, nothing else:\n"
        '{"reply": "what you say now, in the customer\'s language (empty string if a tool must run first)", '
        '"tool": {"name": "<tool name>", "arguments": {...}} or null}\n'
        "Available tools:\n" + catalogue + "\n"
        "Never invent a result; if a tool is needed, run it. When the tool result comes back you will be "
        "asked again: then give the reply and set tool to null."
    )
    messages[0]["content"] += instruction
    tools_used: list[dict] = []
    seen = None
    for _round in range(MAX_TOOL_ROUNDS):
        result = llm.complete(messages, json_mode=True, max_tokens=300, temperature=0.3)
        data = agent_svc._parse_turn(result.text)
        t = data.get("tool")
        tool = t if isinstance(t, dict) else None
        reply = str(data.get("reply") or "").strip()
        if not tool:
            return {"reply": reply, "end_call": bool(data.get("end_call")), "tools_used": tools_used}
        name = str(tool.get("name") or "")
        args = tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {}
        key = (name, json.dumps(args, sort_keys=True, default=str))
        if key == seen:
            messages.append({"role": "user", "content": "That tool already ran; its result is above. Reply now with tool null."})
            continue
        seen = key
        outcome, status = _run_tool(name, args, agent_id, conversation_id, lead_id)
        tools_used.append({"name": name, "status": status})
        messages.append({"role": "assistant", "content": json.dumps({"reply": reply, "tool": {"name": name, "arguments": args}}, ensure_ascii=False)})
        messages.append({"role": "user", "content": f"[Result of {name}: {outcome[:1500]}]\nNow reply to the customer in one short "
                                                     "chat message and set tool to null."})
    log.warning("Chat JSON tool loop hit %s rounds for agent %s", MAX_TOOL_ROUNDS, agent_id)
    return {"reply": "", "end_call": False, "tools_used": tools_used}


def respond(agent_id: int, conversation_id: int, history: list[dict], customer_text: str, lead: dict,
            channel: str = "whatsapp") -> dict:
    """Next chat-mode turn, with a customer-safe tool-calling loop.

    `history` excludes `customer_text`, same contract as agent.respond(). Returns {"reply", "end_call",
    "tools_used"} — the subset of agent.respond()'s shape whatsapp_service's call site actually reads.
    """
    messages, _ = agent_svc.build_messages(agent_id, history, customer_text, lead, use_embeddings=True)
    messages[0]["content"] += "\n\n" + CHAT_GUIDANCE_BY_CHANNEL.get(channel, CHAT_GUIDANCE)
    lead_id = lead.get("id")
    tools = tool_registry.tools_for_agent(agent_id, channel)
    if llm.tools_via_openrouter():
        return _native_loop(messages, tools, agent_id, conversation_id, lead_id)
    return _json_fallback(messages, tools, agent_id, conversation_id, lead_id)
