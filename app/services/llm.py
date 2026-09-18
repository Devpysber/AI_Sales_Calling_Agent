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


def _openrouter_model(model: str, messages: list[dict], json_mode: bool, max_tokens: int, temperature: float, shrunk: bool = False) -> LLMResult:
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
        # Free-tier accounts get a small, shifting prompt cap ("Prompt tokens limit exceeded: 4759 > 4130").
        # Shrink the longest message (the system prompt with knowledge) to fit and retry once rather than fail.
        m = re.search(r"Prompt tokens limit exceeded: (\d+) > (\d+)", message)
        if m and not shrunk:
            ratio = int(m.group(2)) / int(m.group(1)) * 0.85
            longest = max(range(len(messages)), key=lambda i: len(messages[i].get("content") or ""))
            trimmed = [dict(msg) for msg in messages]
            content = trimmed[longest]["content"]
            # Keep the output-format instructions at the end intact; drop the tail of the knowledge sections.
            idx = content.rfind("# Output")
            head, tail = (content[:idx], content[idx:]) if idx > 0 else (content, "")
            keep = max(200, int(len(content) * ratio) - len(tail))
            trimmed[longest]["content"] = head[:keep] + "\n(... trimmed to fit the model's prompt limit)\n\n" + tail
            log.warning("OpenRouter %s prompt over free cap, retrying with system prompt trimmed to %.0f%%", model, ratio * 100)
            return _openrouter_model(model, trimmed, json_mode, max_tokens, temperature, shrunk=True)
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
        pending.add(future)

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
        if launched < len(models) and (not done or not pending):
            launch()
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


def _stream_sse(url: str, headers: dict, body: dict, first_token_timeout: float):
    """OpenAI-compatible streaming chat. Yields content deltas; raises LLMError if no token arrives in time."""
    started = time.monotonic()
    with _client.stream("POST", url, headers=headers, json={**body, "stream": True},
                        timeout=httpx.Timeout(first_token_timeout, connect=3, read=first_token_timeout)) as res:
        if res.status_code >= 400:
            res.read()
            raise LLMError(f"{res.status_code}: {res.text[:200]}")
        got = False
        for line in res.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            data = json.loads(payload)
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
                
            if not got and time.monotonic() - started > first_token_timeout:
                raise LLMError("no content before timeout")
        if not got:
            raise LLMError("empty stream")


def _stream_attempts(tools: list[dict] | None) -> list[tuple[str, str]]:
    """(provider, model) pairs to try, in order.

    Every OpenRouter model is tried, not just the first: a retired model id or a free-tier 429 on
    one model used to end the call outright, even with working models configured behind it.
    """
    attempts: list[tuple[str, str]] = []
    for name in [p.strip() for p in settings.llm_providers.split(",") if p.strip()]:
        if name == "sarvam" and settings.sarvam_api_key and not tools:
            # Sarvam streaming does not emit standard tool_calls deltas.
            attempts.append(("sarvam", settings.sarvam_llm_model))
        elif name == "openrouter" and settings.openrouter_api_key:
            attempts += [("openrouter", m.strip()) for m in settings.openrouter_models.split(",") if m.strip()]
    return attempts


def stream(messages: list[dict], max_tokens: int = 160, temperature: float = 0.4, tools: list[dict] | None = None):
    """
    Streaming completion for live calls, in LLM_PROVIDERS order. Falls back to the next provider only
    if the current one fails before producing any text (a half-spoken reply is never restarted).
    """
    errors = []
    for name, model in _stream_attempts(tools):
        if name == "sarvam":
            url, headers = "https://api.sarvam.ai/v1/chat/completions", {"api-subscription-key": settings.sarvam_api_key}
            # reasoning_effort null = no hidden thinking: first sentence in ~1s instead of ~3s
            body = {"model": model, "reasoning_effort": settings.sarvam_reasoning_effort or None}
        else:
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {"Authorization": f"Bearer {settings.openrouter_api_key}", "X-Title": settings.app_name}
            body = {"model": model, "reasoning": {"enabled": False}}
            if tools:
                body["tools"] = tools
        body.update(messages=messages, max_tokens=max_tokens, temperature=temperature)
        produced = False
        try:
            for delta in _stream_sse(url, headers, body, settings.llm_timeout_seconds):
                produced = True
                yield delta
            return
        except Exception as e:
            if produced:
                log.warning("LLM stream %s/%s broke mid-reply: %s", name, model, e)
                return
            errors.append(f"{name}/{model}: {e}")
            log.warning("LLM stream %s/%s failed: %s", name, model, e)
    raise LLMError("All streaming LLM providers failed — " + " | ".join(errors))


def complete(messages: list[dict], json_mode: bool = False, max_tokens: int = 500, temperature: float = 0.3,
             providers: str | None = None, timeout: float | None = None) -> LLMResult:
    """providers: comma-separated order for this request (default LLM_PROVIDERS). timeout: OpenRouter wall-clock budget."""
    errors = []
    for name in [p.strip() for p in (providers or settings.llm_providers).split(",") if p.strip()]:
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
            elif value.startswith("{"):
                try:
                    data[key] = json.loads(value)
                except json.JSONDecodeError:
                    data[key] = {}
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


def embed(texts: list[str], timeout: float = 30) -> list[list[float]] | None:
    """
    Embeddings via OpenRouter; None when unavailable (RAG then uses keyword search only).
    """
    if not settings.openrouter_api_key or not texts:
        return None
    try:
        res = _client.post(
            "https://openrouter.ai/api/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json={"model": settings.openrouter_embedding_model, "input": texts},
            timeout=timeout,
        )
        data = res.json()
        vectors = [item["embedding"] for item in sorted(data["data"], key=lambda d: d.get("index", 0))]
        return vectors if len(vectors) == len(texts) else None
    except Exception as e:
        log.warning("Embeddings unavailable: %s", e)
        return None
