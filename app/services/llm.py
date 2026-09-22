"""
LLM client with provider fallback.

Providers (order from LLM_PROVIDERS):
- openrouter: OpenAI-compatible, tries each model in OPENROUTER_MODELS
- sarvam:     Sarvam chat completions (sarvam-105b)

All calls are synchronous (run in threads from async code) and use a
shared pooled HTTP client.
"""

import json
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

_client = httpx.Client(timeout=httpx.Timeout(15, connect=5),
                       limits=httpx.Limits(max_connections=50, max_keepalive_connections=20))


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    latency_ms: int


_hedge_pool = ThreadPoolExecutor(max_workers=64, thread_name_prefix="llm")
HEDGE_AFTER_SECONDS = 0.8
MAX_CAP_TRIMS = 2

# Free-tier accounts get a small, shifting prompt cap ("Prompt tokens limit exceeded: 4759 > 4130").
# Other OpenAI-compatible gateways phrase the same failure as a context-length error.
_CAP_RE = re.compile(r"Prompt tokens limit exceeded: (\d+) > (\d+)")
_CAP_HINTS = ("prompt tokens limit", "context length", "maximum context", "context_length_exceeded")
# Sections of the system prompt that can go before anything else, in order (least important first).
_DROPPABLE_SECTIONS = ("# Knowledge", "# Earlier conversations", "# Company brief")
# Everything from here on (Caller record, Earlier conversations, brief, Output format) survives a raw cut.
_KEEP_FROM = "\n# Today"


def _is_cap_error(text: str) -> bool:
    lowered = (text or "").lower()
    return bool(_CAP_RE.search(text or "")) or any(hint in lowered for hint in _CAP_HINTS)


def _drop_section(content: str, heading: str) -> str:
    """Replace one '# heading' section (up to the next '# ' heading) with a one-line placeholder."""
    start = content.find("\n" + heading)
    if start < 0:
        return content
    end = content.find("\n# ", start + 1)
    placeholder = "\n" + heading + "\n- (omitted to fit the model's prompt limit)\n"
    return content[:start] + placeholder + (content[end:] if end >= 0 else "")


def _fit_to_cap(messages: list[dict], error_text: str, passes: int = 0) -> list[dict] | None:
    """
    Trimmed copy of `messages` that should fit the provider's prompt cap, or None when `error_text`
    is not a prompt-cap / context-length error.

    Shrinks the longest message (the system prompt): first structurally (Knowledge, then Earlier
    conversations, then Company brief), and only then by cutting text in front of the Caller record,
    so the lead context, GOAL and Output format survive. `passes` = trims already applied to these
    messages; each pass cuts harder because the char ratio under-estimates Devanagari/JSON tokens.
    """
    if not _is_cap_error(error_text):
        return None
    m = _CAP_RE.search(error_text)
    ratio = int(m.group(2)) / int(m.group(1)) * 0.85 if m else 0.7
    ratio *= 0.7 ** passes
    longest = max(range(len(messages)), key=lambda i: len(messages[i].get("content") or ""))
    trimmed = [dict(msg) for msg in messages]
    content = trimmed[longest].get("content") or ""
    target = max(200, int(len(content) * ratio))
    for heading in _DROPPABLE_SECTIONS:
        if len(content) <= target:
            break
        content = _drop_section(content, heading)
    if len(content) > target:
        note = "\n(... trimmed to fit the model's prompt limit)\n"
        idx = content.rfind(_KEEP_FROM)
        if idx > 0:
            head, tail = content[:idx], content[idx:]
        else:
            idx = content.rfind("# Output")
            head, tail = (content[:idx], content[idx:]) if idx > 0 else (content, "")
        keep = max(200, target - len(tail) - len(note))
        content = head[:keep] + note + tail
    trimmed[longest]["content"] = content
    return trimmed


_ACCOUNT_HINTS = ("no credits", "insufficient_quota", "exceed your available credits", "invalid api key", "unauthorized")


OPENROUTER_DEAD_KEY = "openrouter_dead_until"
OPENROUTER_DEAD_SECONDS = 600


def _openrouter_dead() -> bool:
    """OpenRouter answered 401/402 recently: no credits / bad key. Not worth a request per turn."""
    from app.core import store
    until = store.get_json(OPENROUTER_DEAD_KEY)
    return bool(until) and float(until) > time.time()


def _mark_openrouter_dead(reason: str) -> None:
    from app.core import store
    store.set_json(OPENROUTER_DEAD_KEY, time.time() + OPENROUTER_DEAD_SECONDS, ttl=OPENROUTER_DEAD_SECONDS)
    log.warning("OpenRouter unusable for %ss (%s); live turns go to Sarvam without tools until then", OPENROUTER_DEAD_SECONDS, reason[:120])
    from app.services.heal_service import report
    report("llm_dead", f"OpenRouter: {reason[:300]}", data={"reason": reason[:300]})


PREFLIGHT_KEY = "llm_preflight"
PREFLIGHT_SECONDS = 300


def providers_ready(force: bool = False) -> tuple[bool, str]:
    """
    Can the agent talk right now? One tiny completion, cached five minutes, checked before an automated
    call is placed: dialling a customer into an agent whose providers are down is worse than calling
    later. (ok, detail)
    """
    from app.core import store
    cached = None if force else store.get_json(PREFLIGHT_KEY)
    if cached and float(cached.get("until", 0)) > time.time():
        return bool(cached["ok"]), cached.get("detail", "")
    ok, detail = False, ""
    try:
        r = complete([{"role": "user", "content": "Reply with the single word OK."}], max_tokens=5, temperature=0, timeout=8)
        ok, detail = True, f"{r.provider}/{r.model} answered in {r.latency_ms}ms"
    except Exception as e:  # noqa: BLE001 - every provider failed: that is the finding
        detail = str(e)[:300]
    # A failure is re-checked sooner so a recovered provider is picked up within a minute.
    store.set_json(PREFLIGHT_KEY, {"ok": ok, "detail": detail, "until": time.time() + (PREFLIGHT_SECONDS if ok else 60)}, ttl=PREFLIGHT_SECONDS)
    if ok:
        store.delete("llm_outage_postponed")  # the panel warning clears once the agent can talk again
    return ok, detail


def tools_via_openrouter() -> bool:
    """Native tool-calling is only available through OpenRouter, and only while the account is usable."""
    return bool(settings.openrouter_api_key) and not _openrouter_dead()


def _is_account_error(text: str) -> bool:
    """A 401/402-class failure: the whole provider account is out, not just this model."""
    lowered = (text or "").lower()
    return lowered.startswith(("401", "402")) or " 401" in lowered or " 402" in lowered or any(h in lowered for h in _ACCOUNT_HINTS)


def _fallback_models() -> list[str]:
    return [m.strip() for m in settings.openrouter_fallback_models.split(",") if m.strip()]


def _compact(messages: list[dict]) -> list[dict]:
    """
    Prompt for the last-resort free tier: system prompt without Knowledge / Earlier conversations /
    Company brief and cut to FALLBACK_PROMPT_CHAR_BUDGET (Caller record, GOAL and Output survive),
    plus only the last 6 conversation turns.
    """
    budget = max(1500, settings.fallback_prompt_char_budget)
    out = [dict(m) for m in messages]
    system = [i for i, m in enumerate(out) if m.get("role") == "system"]
    if system:
        i = system[0]
        content = out[i].get("content") or ""
        for heading in _DROPPABLE_SECTIONS:
            content = _drop_section(content, heading)
        if len(content) > budget:
            idx = content.rfind(_KEEP_FROM)
            if idx <= 0:
                idx = content.rfind("# Output")
            head, tail = (content[:idx], content[idx:]) if idx > 0 else (content, "")
            keep = max(300, budget - len(tail))
            content = head[:keep] + "\n(... trimmed for a small model)\n" + tail
        out[i]["content"] = content
        rest = out[:i] + out[i + 1:]
    else:
        rest = out
    turns = [m for m in rest if m.get("role") != "system"]
    if len(turns) > 6:
        rest = turns[-6:]
    return ([out[system[0]]] if system else []) + rest


def _openrouter_model(model: str, messages: list[dict], json_mode: bool, max_tokens: int, temperature: float, shrunk: int = 0) -> LLMResult:
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        # Voice calls need fast answers: no hidden "thinking"
        "reasoning": {"enabled": False},
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {settings.openrouter_api_key}",
               "HTTP-Referer": settings.public_base_url or "http://localhost", "X-Title": settings.app_name}
    started = time.perf_counter()
    res = _client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=body)
    if res.status_code == 400 and json_mode and "response_format" in res.text:
        body.pop("response_format")
        res = _client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=body)
    data = res.json()
    if "choices" not in data:
        message = (data.get("error") or {}).get("message", str(data)[:200])
        # Shrink the system prompt to fit the cap and retry (up to MAX_CAP_TRIMS times) rather than fail.
        trimmed = _fit_to_cap(messages, message, passes=shrunk) if shrunk < MAX_CAP_TRIMS else None
        if trimmed is not None:
            log.warning("OpenRouter %s prompt over cap (%s), retrying with trimmed system prompt (pass %d)", model, message[:80], shrunk + 1)
            return _openrouter_model(model, trimmed, json_mode, max_tokens, temperature, shrunk=shrunk + 1)
        raise LLMError(message)
    text = (data["choices"][0]["message"].get("content") or "").strip()
    if not text:
        raise LLMError("empty content")
    return LLMResult(text, "openrouter", model, int((time.perf_counter() - started) * 1000))


def _openrouter(messages: list[dict], json_mode: bool, max_tokens: int, temperature: float, timeout: float | None = None) -> LLMResult:
    """
    Hedged requests across OPENROUTER_MODELS: start the next model if the current ones
    haven't answered within HEDGE_AFTER_SECONDS (or failed), return the first valid answer,
    and give up after LLM_TIMEOUT_SECONDS of wall-clock time.

    A plain HTTP read timeout is not enough: OpenRouter keeps queued requests alive with
    whitespace, so a slow free model can hold a connection for over a minute.

    A prompt-cap failure is retried with a trimmed prompt inside the same model's future
    (see _openrouter_model); the deadline is extended by the time that failed attempt took so
    the retry gets its own budget instead of being discarded as "no model answered in time".
    """
    if not settings.openrouter_api_key:
        raise LLMError("OPENROUTER_API_KEY not set")
    models = [m.strip() for m in settings.openrouter_models.split(",") if m.strip()]
    timeout = timeout or settings.llm_timeout_seconds
    deadline = time.monotonic() + timeout
    pending, errors, launched = set(), [], 0

    def launch():
        nonlocal launched
        model = models[launched]
        launched += 1
        future = _hedge_pool.submit(_openrouter_model, model, messages, json_mode, max_tokens, temperature)
        future.model = model
        future.started = time.monotonic()
        pending.add(future)

    if models:
        launch()
    while pending or launched < len(models):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if not pending:
            launch()
            continue
        done, _ = wait(pending, timeout=min(HEDGE_AFTER_SECONDS, remaining), return_when=FIRST_COMPLETED)
        for future in done:
            pending.discard(future)
            try:
                return future.result()
            except Exception as e:
                errors.append(f"{future.model}: {e}")
                log.warning("OpenRouter %s failed: %s", future.model, e)
                if _is_cap_error(str(e)):
                    # The trimmed re-POSTs already ran inside this future; give the next model the same grace.
                    deadline += time.monotonic() - future.started
                elif _is_account_error(str(e)):
                    # Account-level (401/402): every remaining paid model fails the same way.
                    launched = len(models)
        if launched < len(models) and (not done or not pending):
            launch()
    # Last-resort tier: free models with a compact prompt, tried one at a time.
    compact = None
    for model in _fallback_models():
        if model in models or time.monotonic() >= deadline + timeout:
            continue
        compact = compact or _compact(messages)
        try:
            log.warning("OpenRouter primaries failed (%s); trying fallback %s", "; ".join(errors)[:120], model)
            return _openrouter_model(model, compact, json_mode, max_tokens, temperature)
        except Exception as e:
            errors.append(f"{model}: {e}")
    raise LLMError("; ".join(errors) or f"no model answered within {timeout}s")


def _sarvam(messages: list[dict], json_mode: bool, max_tokens: int, temperature: float) -> LLMResult:
    if not settings.sarvam_api_key:
        raise LLMError("SARVAM_API_KEY not set")
    started = time.perf_counter()
    res = _client.post(
        "https://api.sarvam.ai/v1/chat/completions",
        headers={"api-subscription-key": settings.sarvam_api_key},
        json={"model": settings.sarvam_llm_model, "messages": messages, "temperature": temperature,
              "max_tokens": max_tokens, "reasoning_effort": settings.sarvam_reasoning_effort or None},
    )
    data = res.json()
    if res.status_code >= 400 or "choices" not in data:
        raise LLMError(f"Sarvam {res.status_code}: {str(data)[:200]}")
    text = re.sub(r"<think>.*?</think>", "", data["choices"][0]["message"].get("content") or "", flags=re.S).strip()
    if not text:
        raise LLMError("Sarvam returned empty content")
    return LLMResult(text, "sarvam", settings.sarvam_llm_model, int((time.perf_counter() - started) * 1000))


PROVIDERS = {"openrouter": _openrouter, "sarvam": _sarvam}


CHARS_PER_TOKEN = 3.6   # mixed Hinglish/Devanagari prompt: measured against provider counts, refine from telemetry


def estimate_tokens(text: str) -> int:
    return int(len(text or "") / CHARS_PER_TOKEN + 0.5)


def turn_usage(messages: list[dict], tools: list[dict] | None, out_chars: int, raw: dict | None) -> dict:
    """Token usage for one request: the provider's count when its stream reported one, else a character estimate."""
    if raw and (raw.get("prompt_tokens") or raw.get("completion_tokens")):
        return {"input_tokens": int(raw.get("prompt_tokens") or 0), "output_tokens": int(raw.get("completion_tokens") or 0),
                "estimated": False}
    prompt = "".join(str(m.get("content") or "") for m in messages) + (json.dumps(tools) if tools else "")
    return {"input_tokens": estimate_tokens(prompt), "output_tokens": estimate_tokens("x" * out_chars), "estimated": True}


def _stream_sse(url: str, headers: dict, body: dict, first_token_timeout: float):
    """
    OpenAI-compatible streaming chat. Yields content deltas; raises LLMError if no token arrives in
    time, or if the whole stream runs past 3x that budget (a stream that starts and then stalls).

    The wall-clock check runs on every line, including keepalive/comment lines
    (": OPENROUTER PROCESSING"), which otherwise reset httpx's read timeout forever.
    """
    started = time.monotonic()
    deadline = started + first_token_timeout * 3
    with _client.stream("POST", url, headers=headers, json={**body, "stream": True},
                        timeout=httpx.Timeout(first_token_timeout, connect=3, read=first_token_timeout)) as res:
        if res.status_code >= 400:
            res.read()
            raise LLMError(f"{res.status_code}: {res.text[:200]}")
        got = False
        for line in res.iter_lines():
            now = time.monotonic()
            if not got and now - started > first_token_timeout:
                raise LLMError("no content before timeout")
            if now > deadline:
                raise LLMError(f"stream stalled past {first_token_timeout * 3:.1f}s")
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            data = json.loads(payload)
            if isinstance(data.get("usage"), dict):
                yield {"usage_raw": data["usage"]}   # OpenAI-style final frame (stream_options.include_usage)
            if "error" in data:
                # Gateways deliver some failures (rate limit, moderation, prompt cap) as an error frame with HTTP 200.
                err = data["error"]
                raise LLMError(str((err.get("message") if isinstance(err, dict) else err) or err or "stream error")[:200])
            choices = data.get("choices") or []
            delta_obj = choices[0].get("delta") or {} if choices else {}
            content = delta_obj.get("content")
            tool_calls = delta_obj.get("tool_calls")

            if content:
                got = True
                yield content
            if tool_calls:
                got = True
                yield {"tool_calls": tool_calls}
            # agent.process_stream appends the hang-up marker to a farewell cut by max_tokens; it can only
            # do that if the finish reason is forwarded. Not counted as content: a stream that ends with
            # only a finish frame still falls through to the next provider.
            fr = choices[0].get("finish_reason") if choices else None
            if fr:
                yield {"finish_reason": fr}
        if not got:
            raise LLMError("empty stream")


def _stream_attempts(tools: list[dict] | None) -> list[tuple[str, str]]:
    """(provider, model) pairs to try, in order.

    Every OpenRouter model is tried, not just the first: a retired model id or a free-tier 429 on
    one model used to end the call outright, even with working models configured behind it.
    """
    attempts: list[tuple[str, str]] = []
    # Ids the Integrations health check found retired: a stale .env must not cost a wasted request per reply.
    from app.core import store
    retired = set(store.get_json("openrouter_unknown_models", []) or [])
    for name in [p.strip() for p in settings.llm_providers.split(",") if p.strip()]:
        if name == "sarvam" and settings.sarvam_api_key and not tools:
            # Sarvam streaming does not emit standard tool_calls deltas.
            attempts.append(("sarvam", settings.sarvam_llm_model))
        elif name == "openrouter" and settings.openrouter_api_key:
            attempts += [("openrouter", m.strip()) for m in settings.openrouter_models.split(",") if m.strip() and m.strip() not in retired]
    if settings.openrouter_api_key:
        primary = {m for n, m in attempts}
        attempts += [("openrouter-fallback", m) for m in _fallback_models() if m not in primary]
    return attempts


# Swapped in for the tool instructions when a tools-enabled call has to fall back to a provider without tools.
SPOKEN_ONLY_FALLBACK = ("\n\n# Tools unavailable right now\n"
                        "Answer out loud in 1-2 short sentences of plain speech. Do not call any tool. "
                        "If a meeting or callback time was agreed, repeat the day and time back to confirm it.\n")


def _without_tools(messages: list[dict]) -> list[dict]:
    """Copy of `messages` with the god-mode tool instructions replaced by spoken-only guidance."""
    stripped = [dict(msg) for msg in messages]
    for msg in stripped:
        content = msg.get("content")
        if msg.get("role") == "system" and isinstance(content, str) and "# GOD MODE ACTIVE" in content:
            msg["content"] = content.split("\n\n# GOD MODE ACTIVE", 1)[0] + SPOKEN_ONLY_FALLBACK
    return stripped


def stream(messages: list[dict], max_tokens: int = 160, temperature: float = 0.4, tools: list[dict] | None = None,
           deadline: float | None = None):
    """
    Streaming completion for live calls, in LLM_PROVIDERS order. Falls back to the next provider only
    if the current one fails before producing any text (a half-spoken reply is never restarted).

    A prompt-cap / context-length failure retries the same model with a trimmed system prompt
    (up to MAX_CAP_TRIMS times) before moving on: every free-tier OpenRouter model shares the same cap,
    so failing over alone just repeats the error. When every tool-capable attempt fails, the turn is
    retried once without tools (Sarvam included) so the caller still gets a spoken answer.
    """
    errors = []
    dead: set[str] = set()  # providers that answered 401/402: skip their remaining models
    if tools and _openrouter_dead() and settings.sarvam_api_key:
        # Tools need OpenRouter; with the account out of credits every turn walked the whole chain (retired
        # id, 402, free-tier timeout) and answered 7s late. Speak from Sarvam straight away instead.
        yield from stream(_without_tools(messages), max_tokens=max_tokens, temperature=temperature, tools=None, deadline=deadline)
        return
    compact = None
    # One budget for the whole turn, shared with the no-tools retry below.
    deadline = deadline or (time.monotonic() + settings.llm_stream_budget_seconds)
    attempts = _stream_attempts(tools)
    for index, (name, model) in enumerate(attempts):
        provider = "openrouter" if name == "openrouter-fallback" else name
        if provider in dead and name != "openrouter-fallback":
            continue
        remaining = deadline - time.monotonic()
        if remaining < 1.0:
            errors.append(f"{name}/{model}: skipped, turn budget of {settings.llm_stream_budget_seconds}s spent")
            break
        if name == "sarvam":
            url, headers = "https://api.sarvam.ai/v1/chat/completions", {"api-subscription-key": settings.sarvam_api_key}
            # reasoning_effort null = no hidden thinking: first sentence in ~1s instead of ~3s
            body = {"model": model, "reasoning_effort": settings.sarvam_reasoning_effort or None}
        else:
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {"Authorization": f"Bearer {settings.openrouter_api_key}", "X-Title": settings.app_name}
            body = {"model": model, "reasoning": {"enabled": False}, "stream_options": {"include_usage": True}}
            if tools:
                body["tools"] = tools
        if name == "openrouter-fallback":
            # Free tier: no tools, compact prompt so the free-model prompt cap is never hit.
            body.pop("tools", None)
            compact = compact or _compact(_without_tools(messages) if tools else messages)
            if not errors:
                continue  # primaries never ran (none configured); nothing to fall back from
            log.warning("LLM primaries failed (%s); trying fallback %s", " | ".join(errors)[:160], model)
        attempt_messages, trims = (compact if name == "openrouter-fallback" else messages), 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining < 1.0:
                errors.append(f"{name}/{model}: turn budget of {settings.llm_stream_budget_seconds}s spent")
                break
            body.update(messages=attempt_messages, max_tokens=max_tokens, temperature=temperature)
            produced = False
            out_chars, raw_usage = 0, None
            try:
                # A model that has not started speaking in a second and a half is not going to be the
                # fast one. Only the last attempt gets the full budget: before this, a silent primary
                # spent 4.5 seconds of a live call before the next model was even tried.
                budget = settings.llm_timeout_seconds if index == len(attempts) - 1 else settings.llm_stream_first_token_seconds
                for delta in _stream_sse(url, headers, body, min(budget, remaining)):
                    if isinstance(delta, dict) and "usage_raw" in delta:
                        raw_usage = delta["usage_raw"]
                        continue
                    produced = True
                    if isinstance(delta, str):
                        out_chars += len(delta)
                    yield delta
                yield {"usage": turn_usage(attempt_messages, body.get("tools"), out_chars, raw_usage)}
                return
            except Exception as e:
                if produced:
                    log.warning("LLM stream %s/%s broke mid-reply: %s", name, model, e)
                    return
                trimmed = _fit_to_cap(attempt_messages, str(e), passes=trims) if trims < MAX_CAP_TRIMS else None
                if trimmed is not None:
                    trims += 1
                    attempt_messages = trimmed
                    log.warning("LLM stream %s/%s prompt over cap (%s), retrying with trimmed system prompt (pass %d)",
                                name, model, str(e)[:80], trims)
                    continue
                errors.append(f"{name}/{model}: {e}")
                log.warning("LLM stream %s/%s failed: %s", name, model, e)
                if _is_account_error(str(e)):
                    dead.add(provider)
                    if provider == "openrouter":
                        _mark_openrouter_dead(str(e))
                        if tools and settings.sarvam_api_key:
                            # Out of credits mid-turn: the free-tier walk (timeouts included) cost ~4s before a
                            # spoken answer. Sarvam without tools answers in about a second; take it now.
                            log.warning("OpenRouter account error on a tools turn; answering from Sarvam without tools")
                            yield from stream(_without_tools(messages), max_tokens=max_tokens, temperature=temperature,
                                              tools=None, deadline=deadline)
                            return
                break
    if tools:
        # Team/admin calls otherwise depend on OpenRouter alone; answer in speech rather than hang up.
        log.warning("All tool-capable LLM streams failed (%s); retrying without tools", " | ".join(errors))
        yield from stream(_without_tools(messages), max_tokens=max_tokens, temperature=temperature, tools=None, deadline=deadline)
        return
    raise LLMError("All streaming LLM providers failed — " + " | ".join(errors))


def provider_order(providers: str | None = None) -> list[str]:
    """The configured order, with a dead OpenRouter (401/402 minutes ago) moved last: every summary and
    classification would otherwise pay the failed round trip and its fallback-model retries first."""
    order = [p.strip() for p in (providers or settings.llm_providers).split(",") if p.strip()]
    if "openrouter" in order and len(order) > 1 and _openrouter_dead():
        order = [p for p in order if p != "openrouter"] + ["openrouter"]
    return order


def complete(messages: list[dict], json_mode: bool = False, max_tokens: int = 500, temperature: float = 0.3,
             providers: str | None = None, timeout: float | None = None) -> LLMResult:
    """providers: comma-separated order for this request (default LLM_PROVIDERS). timeout: OpenRouter wall-clock budget."""
    errors = []
    for name in provider_order(providers):
        provider = PROVIDERS.get(name)
        if provider is None:
            continue
        try:
            return provider(messages, json_mode, max_tokens, temperature, **({"timeout": timeout} if name == "openrouter" and timeout else {}))
        except Exception as e:
            errors.append(f"{name}: {e}")
    raise LLMError("All LLM providers failed — " + " | ".join(errors))


def parse_json(text: str) -> dict:
    """
    Tolerant JSON extraction (strips fences / prose around the object).
    """
    if "<arg_key>" in text:
        # Some models (e.g. sarvam-105b) answer in a tool-call markup instead of JSON
        data = {}
        for key, value in re.findall(r"<arg_key>\s*(.*?)\s*</arg_key>\s*<arg_value>\s*(.*?)\s*(?:</arg_value>|$)", text, flags=re.S):
            lowered = value.lower()
            if lowered in ("true", "false"):
                data[key] = lowered == "true"
            elif value.startswith(("{", "[")):
                try:
                    data[key] = json.loads(value)
                except json.JSONDecodeError:
                    data[key] = {} if value.startswith("{") else value
            else:
                data[key] = value
        if data:
            return data
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if match:
            return json.loads(match.group(0))
        raise


def embed(texts: list[str], timeout: float = 30, task: str = "document",
          provider: str | None = None) -> list[list[float]] | None:
    """
    Embeddings from the first configured provider that answers; None when none do (RAG then uses
    keyword search only). `task` is "document" while ingesting and "query" while searching: Gemini
    embeds the two differently and ranks better when told which it is given. `provider` pins the
    call to one provider, which the knowledge base needs: vectors from two different models share
    no geometry, so a query embedded elsewhere than the passages would rank noise.
    """
    answered = embed_with_provider(texts, timeout, task, provider)
    return answered[1] if answered else None


def embeddings_configured() -> bool:
    """Whether any embedding provider has a key. Without one there is nothing to retry."""
    for name in [p.strip() for p in settings.embedding_providers.split(",") if p.strip()]:
        if (name == "gemini" and settings.gemini_api_key) or (name == "openrouter" and settings.openrouter_api_key):
            return True
    return False


def embed_with_provider(texts: list[str], timeout: float = 30, task: str = "document",
                        provider: str | None = None) -> tuple[str, list[list[float]]] | None:
    """As `embed`, but also says which provider answered, so the caller can pin later calls to it."""
    if not texts:
        return None
    order = [provider] if provider else [p.strip() for p in settings.embedding_providers.split(",") if p.strip()]
    for name in order:
        if name == "gemini" and settings.gemini_api_key:
            vectors = _embed_gemini(texts, timeout, task)
        elif name == "openrouter":
            vectors = _embed_openrouter(texts, timeout)
        else:
            continue
        if vectors is not None:
            return name, vectors
    return None


def _embed_gemini(texts: list[str], timeout: float, task: str) -> list[list[float]] | None:
    """Google's free embedding tier. One request carries the whole batch."""
    model = f"models/{settings.gemini_embedding_model}"
    body = {"requests": [{
        "model": model,
        "content": {"parts": [{"text": t}]},
        "taskType": "RETRIEVAL_QUERY" if task == "query" else "RETRIEVAL_DOCUMENT",
        "outputDimensionality": settings.gemini_embedding_dimensions,
    } for t in texts]}
    try:
        res = _client.post(
            f"https://generativelanguage.googleapis.com/v1beta/{model}:batchEmbedContents",
            headers={"x-goog-api-key": settings.gemini_api_key},
            json=body,
            timeout=timeout,
        )
        if res.status_code >= 400:
            log.warning("Gemini embeddings unavailable: %s: %s", res.status_code, res.text[:200])
            return None
        vectors = [e["values"] for e in res.json().get("embeddings", [])]
        return vectors if len(vectors) == len(texts) else None
    except Exception as e:
        log.warning("Gemini embeddings unavailable: %s", e)
        return None


def _embed_openrouter(texts: list[str], timeout: float) -> list[list[float]] | None:
    if not settings.openrouter_api_key or _openrouter_dead():
        return None
    try:
        res = _client.post(
            "https://openrouter.ai/api/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json={"model": settings.openrouter_embedding_model, "input": texts,
                  "dimensions": settings.openrouter_embedding_dimensions},
            timeout=timeout,
        )
        if res.status_code >= 400:
            msg = f"{res.status_code}: {res.text[:200]}"
            # A 402 saying the prompt was too long is about this one request's size, not the
            # account's credit. Marking the provider dead for it cost live calls their tools.
            oversized = "tokens limit exceeded" in res.text
            if res.status_code in (401, 402) and not oversized:
                _mark_openrouter_dead(msg)
            log.warning("Embeddings unavailable: %s", msg)
            return None
        data = res.json()
        vectors = [item["embedding"] for item in sorted(data["data"], key=lambda d: d.get("index", 0))]
        return vectors if len(vectors) == len(texts) else None
    except Exception as e:
        log.warning("Embeddings unavailable: %s", e)
        return None
