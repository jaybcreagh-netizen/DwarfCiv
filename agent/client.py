"""Provider-agnostic model interface for the steward.

One interface — `LLMClient.generate(messages, tools) -> LLMResponse` — with
Anthropic (Messages API) and OpenAI (chat-completions) implementations behind
it, both driven through tool/function calling. `steward.py` never branches on
provider: if you find yourself writing `if backend == "anthropic"` upstream of
this module, push it down here instead.

The conversation is a neutral list of message dicts that this module owns the
schema for:

  {"role": "system",    "content": <str>}
  {"role": "user",      "content": <str>}
  {"role": "assistant", "content": <provider-native blob>,   # opaque, for replay
                        "text": <str>, "tool_calls": [ToolCall, ...]}
  {"role": "tool",      "tool_call_id": <str>, "name": <str>,
                        "content": <str>, "is_error": <bool>}

The assistant message carries a provider-native `content` blob (Anthropic
content blocks, or an OpenAI message dict) used verbatim when the history is
replayed to the *same* backend on the next call. A run never switches backend
mid-stream, so this is safe and keeps the steward agnostic — while preserving
Anthropic thinking blocks unchanged across the multi-action turn, as the API
requires.

API keys come from the environment (ANTHROPIC_API_KEY / OPENAI_API_KEY) and are
never logged. Token usage is accumulated so the steward can print a per-run
cost estimate.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("agent.client")


# --------------------------------------------------------------------------
# Normalized data structures


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    assistant_message: dict          # neutral assistant message, ready to append
    usage: Usage
    stop_reason: str | None = None


# --------------------------------------------------------------------------
# Pricing (USD per 1M tokens). Approximate; used only for a rough run estimate.
# Anthropic figures from the model catalog (2026-06); OpenAI figures are
# illustrative and may be stale — treat the printed cost as an estimate.

PRICING = {
    # Anthropic
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # OpenAI (illustrative)
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4.1": (2.0, 8.0),
    "o3": (2.0, 8.0),
}


def estimate_cost(model: str, usage: Usage) -> float | None:
    """Best-effort USD estimate; None if the model isn't in the price table."""
    rates = None
    for key, val in PRICING.items():
        if model.startswith(key):
            rates = val
            break
    if rates is None:
        return None
    in_rate, out_rate = rates
    # Cache reads are far cheaper; cache writes a bit more. Approximate both at
    # the base input rate — the goal is an order-of-magnitude check, not billing.
    billed_in = (usage.input_tokens + usage.cache_read_input_tokens
                 + usage.cache_creation_input_tokens)
    return (billed_in * in_rate + usage.output_tokens * out_rate) / 1_000_000


# --------------------------------------------------------------------------
# Retry helper — a run must survive a flaky API.


def _with_retries(fn, retryable: tuple, *, max_retries: int = 5,
                  base_delay: float = 2.0, max_delay: float = 60.0):
    last = None
    for attempt in range(max_retries):
        try:
            return fn()
        except retryable as e:  # noqa: PERF203
            last = e
            delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1),
                        max_delay)
            log.warning("transient API error (attempt %d/%d): %s — retrying in "
                        "%.1fs", attempt + 1, max_retries, type(e).__name__,
                        delay)
            time.sleep(delay)
    raise last


# --------------------------------------------------------------------------
# Interface + factory


class LLMClient:
    """Common interface. Subclasses implement `generate`."""

    def __init__(self, model: str, temperature: float | None = None):
        self.model = model
        self.temperature = temperature
        self.total_usage = Usage()
        self.calls = 0

    def generate(self, messages: list[dict], tools: list[dict] | None = None,
                 max_tokens: int = 16000) -> LLMResponse:
        raise NotImplementedError

    def _record(self, usage: Usage) -> None:
        self.total_usage.add(usage)
        self.calls += 1
        log.info("model call %d: in %d (+%d cached) / out %d tokens",
                 self.calls, usage.input_tokens, usage.cache_read_input_tokens,
                 usage.output_tokens)

    def cost_summary(self) -> str:
        u = self.total_usage
        cost = estimate_cost(self.model, u)
        cost_s = f"~${cost:.2f}" if cost is not None else "unknown (unpriced model)"
        return (f"{self.calls} model calls | "
                f"in {u.input_tokens:,} (+{u.cache_read_input_tokens:,} cached) / "
                f"out {u.output_tokens:,} tokens | est. cost {cost_s}")


def build_client(backend: str, model: str,
                 temperature: float | None = None) -> LLMClient:
    backend = backend.lower()
    if backend == "anthropic":
        return AnthropicClient(model, temperature)
    if backend == "openai":
        return OpenAIClient(model, temperature)
    raise ValueError(f"unknown backend {backend!r} (expected anthropic|openai)")


# --------------------------------------------------------------------------
# Anthropic backend


class AnthropicClient(LLMClient):
    def __init__(self, model: str, temperature: float | None = None):
        super().__init__(model, temperature)
        import anthropic  # lazy: only this backend needs the SDK
        self._sdk = anthropic
        self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self._retryable = (
            anthropic.RateLimitError,
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
            anthropic.InternalServerError,
        )

    def _to_api(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """Split out the system text and translate to Anthropic message form,
        merging consecutive tool results into a single user turn."""
        system_parts: list[str] = []
        api: list[dict] = []
        pending_results: list[dict] = []

        def flush_results():
            if pending_results:
                api.append({"role": "user", "content": list(pending_results)})
                pending_results.clear()

        for m in messages:
            role = m["role"]
            if role == "system":
                system_parts.append(m["content"])
                continue
            if role == "tool":
                pending_results.append({
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"],
                    "is_error": bool(m.get("is_error")),
                })
                continue
            flush_results()
            if role == "user":
                api.append({"role": "user", "content": m["content"]})
            elif role == "assistant":
                api.append({"role": "assistant", "content": m["content"]})
        flush_results()
        return "\n\n".join(system_parts), api

    def generate(self, messages: list[dict], tools: list[dict] | None = None,
                 max_tokens: int = 16000) -> LLMResponse:
        system, api_messages = self._to_api(messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": api_messages,
            # Adaptive thinking is the recommended setting on Opus 4.7/4.8 and
            # interleaves automatically between tool calls.
            "thinking": {"type": "adaptive"},
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [
                {"name": t["name"], "description": t["description"],
                 "input_schema": t["parameters"]}
                for t in tools
            ]
        # Opus 4.7/4.8 reject temperature; only send it when explicitly set.
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature

        resp = _with_retries(
            lambda: self._client.messages.create(**kwargs), self._retryable)

        text_parts, tool_calls = [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(block.id, block.name, dict(block.input)))

        usage = Usage(
            input_tokens=getattr(resp.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(resp.usage, "output_tokens", 0) or 0,
            cache_read_input_tokens=getattr(
                resp.usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(
                resp.usage, "cache_creation_input_tokens", 0) or 0,
        )
        self._record(usage)
        # Replay the content blocks verbatim (preserves thinking blocks).
        assistant_msg = {
            "role": "assistant",
            "content": resp.content,
            "text": "".join(text_parts),
            "tool_calls": tool_calls,
        }
        return LLMResponse("".join(text_parts), tool_calls, assistant_msg,
                           usage, resp.stop_reason)


# --------------------------------------------------------------------------
# OpenAI backend


class OpenAIClient(LLMClient):
    def __init__(self, model: str, temperature: float | None = None):
        super().__init__(model, temperature)
        import openai  # lazy
        self._sdk = openai
        self._client = openai.OpenAI()  # reads OPENAI_API_KEY
        self._retryable = (
            openai.RateLimitError,
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.InternalServerError,
        )

    def _to_api(self, messages: list[dict]) -> list[dict]:
        api: list[dict] = []
        for m in messages:
            role = m["role"]
            if role in ("system", "user"):
                api.append({"role": role, "content": m["content"]})
            elif role == "assistant":
                api.append(m["content"])  # provider-native message dict
            elif role == "tool":
                content = m["content"]
                if m.get("is_error"):
                    content = f"ERROR: {content}"
                api.append({"role": "tool", "tool_call_id": m["tool_call_id"],
                            "content": content})
        return api

    def generate(self, messages: list[dict], tools: list[dict] | None = None,
                 max_tokens: int = 16000) -> LLMResponse:
        import json
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_api(messages),
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = [
                {"type": "function",
                 "function": {"name": t["name"], "description": t["description"],
                              "parameters": t["parameters"]}}
                for t in tools
            ]
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature

        resp = _with_retries(
            lambda: self._client.chat.completions.create(**kwargs),
            self._retryable)

        choice = resp.choices[0]
        msg = choice.message
        text = msg.content or ""
        tool_calls = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw_arguments": tc.function.arguments}
            tool_calls.append(ToolCall(tc.id, tc.function.name, args))

        usage = Usage(
            input_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(resp.usage, "completion_tokens", 0) or 0,
        )
        self._record(usage)
        assistant_msg = {
            "role": "assistant",
            "content": msg.model_dump(exclude_none=True),
            "text": text,
            "tool_calls": tool_calls,
        }
        return LLMResponse(text, tool_calls, assistant_msg, usage,
                           choice.finish_reason)
