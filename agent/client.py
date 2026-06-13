"""Provider-agnostic LLM client for the steward.

One interface — `LLMClient.generate(system, messages, tools)` — with Anthropic,
OpenAI, and a deterministic offline `mock` implementation behind it. The
steward never branches on provider: anything provider-specific (tool-call
shape, sampling-parameter quirks, thinking blocks) is handled here.

Neutral conversation model
--------------------------
A conversation is a list of `Message` objects in provider-neutral form:

    Message.user(text)                       # a user prompt (the briefing)
    response.assistant_message               # what the model said (text + tool calls)
    Message.from_tool_results([...])         # tool outcomes fed back

Each backend translates these to/from its own wire format. Assistant messages
carry an opaque `_raw` field holding the provider-native content so they can be
replayed verbatim within a turn — this is what preserves Anthropic "thinking"
blocks across the within-month tool loop (the API rejects modified thinking
blocks). The steward never inspects `_raw`; it just appends the returned
assistant message back into the list and calls `generate` again.

Why this matters for the project: across months, `agent/memory.py` rebuilds
context from stored artifacts (no `_raw`, so no thinking replay needed — each
month starts a fresh chain). Within a month, the live chain carries `_raw`.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("agent.client")


# --------------------------------------------------------------------------
# neutral types


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    """A single conversation turn in provider-neutral form."""
    role: str                                   # "user" | "assistant"
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    # provider-native content for faithful replay within a turn (opaque).
    _raw: Any = field(default=None, repr=False)

    @classmethod
    def user(cls, text: str) -> "Message":
        return cls(role="user", text=text)

    @classmethod
    def from_tool_results(cls, results: list[ToolResult]) -> "Message":
        return cls(role="user", tool_results=list(results))


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass
class ModelResponse:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    usage: Usage
    assistant_message: Message          # append this back into the conversation
    thinking: str = ""                  # summarized reasoning, if the backend exposes it


# A tool spec is a plain dict: {"name", "description", "input_schema"}.
ToolSpec = dict


# --------------------------------------------------------------------------
# pricing ($ per 1M tokens). Anthropic values are authoritative (claude-api
# skill, cached 2026-06). OpenAI values are best-effort and may be stale —
# override with price_in/price_out or treat the dollar estimate as indicative.

PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # OpenAI — indicative only; verify against current pricing.
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4o": (2.5, 10.0),
}

# Anthropic models that reject temperature/top_p/top_k (400 if sent) and use
# adaptive thinking instead of budget_tokens.
_ANTHROPIC_NO_SAMPLING = {
    "claude-opus-4-7", "claude-opus-4-8", "claude-fable-5", "claude-mythos-5",
}

# Transient error types worth retrying with backoff (in addition to the SDK's
# own automatic retries).
_RETRYABLE_NAMES = {
    "APIConnectionError", "APITimeoutError", "InternalServerError",
    "OverloadedError", "RateLimitError", "APIStatusError",
}


class LLMError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# base client


class LLMClient:
    """Common interface + usage/cost accounting. Subclass per provider."""

    def __init__(self, model: str, temperature: float | None = None,
                 max_tokens: int = 8192, effort: str = "high",
                 max_retries: int = 6,
                 price_in: float | None = None, price_out: float | None = None):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.effort = effort
        self.max_retries = max_retries
        self._price = None
        if price_in is not None and price_out is not None:
            self._price = (price_in, price_out)
        elif model in PRICING:
            self._price = PRICING[model]
        self.total_usage = Usage()
        self.calls = 0

    @property
    def backend(self) -> str:
        raise NotImplementedError

    def generate(self, system: str, messages: list[Message],
                 tools: list[ToolSpec] | None = None) -> ModelResponse:
        raise NotImplementedError

    # -- accounting -----------------------------------------------------------

    def _account(self, usage: Usage) -> None:
        self.total_usage = self.total_usage + usage
        self.calls += 1

    def estimate_cost(self) -> float | None:
        if not self._price:
            return None
        pin, pout = self._price
        # cache reads bill ~0.1x, writes ~1.25x; approximate when present.
        u = self.total_usage
        inp = u.input_tokens + 0.1 * u.cache_read_tokens + 1.25 * u.cache_write_tokens
        return (inp * pin + u.output_tokens * pout) / 1_000_000

    def cost_summary(self) -> dict:
        u = self.total_usage
        return {
            "backend": self.backend,
            "model": self.model,
            "calls": self.calls,
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cache_read_tokens": u.cache_read_tokens,
            "cache_write_tokens": u.cache_write_tokens,
            "estimated_usd": self.estimate_cost(),
        }

    # -- retry wrapper --------------------------------------------------------

    def _with_retry(self, fn):
        """Run fn() with exponential backoff on transient API errors. The SDKs
        also retry internally; this guards the whole call against flakiness."""
        delay = 2.0
        last = None
        for attempt in range(self.max_retries):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001 - classify by type name
                name = type(e).__name__
                status = getattr(e, "status_code", None)
                retryable = name in _RETRYABLE_NAMES and status not in (400, 401, 403, 404)
                if not retryable:
                    raise
                last = e
                if attempt == self.max_retries - 1:
                    break
                log.warning("transient API error (%s); retry %d/%d in %.0fs",
                            name, attempt + 1, self.max_retries, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
        raise LLMError(f"API call failed after {self.max_retries} attempts: {last}")


# --------------------------------------------------------------------------
# Anthropic


class AnthropicClient(LLMClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        import anthropic  # lazy: only needed for this backend
        self._sdk = anthropic
        self._client = anthropic.Anthropic(max_retries=self.max_retries)

    @property
    def backend(self) -> str:
        return "anthropic"

    def _accepts_temperature(self) -> bool:
        return self.model not in _ANTHROPIC_NO_SAMPLING

    def _to_messages(self, messages: list[Message]) -> list[dict]:
        out = []
        for m in messages:
            if m.role == "assistant":
                if m._raw is not None:
                    out.append({"role": "assistant", "content": m._raw})
                    continue
                content = []
                if m.text:
                    content.append({"type": "text", "text": m.text})
                for tc in m.tool_calls:
                    content.append({"type": "tool_use", "id": tc.id,
                                    "name": tc.name, "input": tc.arguments})
                out.append({"role": "assistant", "content": content})
            else:  # user
                if m.tool_results:
                    content = [{
                        "type": "tool_result",
                        "tool_use_id": tr.tool_call_id,
                        "content": tr.content,
                        "is_error": tr.is_error,
                    } for tr in m.tool_results]
                    out.append({"role": "user", "content": content})
                else:
                    out.append({"role": "user", "content": m.text})
        return out

    def generate(self, system, messages, tools=None) -> ModelResponse:
        params: dict = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=self._to_messages(messages),
        )
        if tools:
            params["tools"] = [{
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
            } for t in tools]
        if self.temperature is not None and self._accepts_temperature():
            params["temperature"] = self.temperature
        elif self.temperature is not None:
            log.warning("model %s rejects temperature; omitting it", self.model)
        # Adaptive thinking + effort are recommended on current models; degrade
        # gracefully if the installed SDK/model doesn't accept the kwargs.
        optional = dict(
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": self.effort},
        )

        def call(p):
            return self._with_retry(lambda: self._client.messages.create(**p))

        resp = self._create_with_fallback(call, params, optional)
        return self._parse(resp)

    def _create_with_fallback(self, call, params: dict, optional: dict):
        """Try with the optional kwargs; on a kwarg/param rejection, drop them."""
        try:
            return call({**params, **optional})
        except TypeError as e:
            log.info("SDK rejected optional kwargs (%s); retrying plain", e)
        except self._sdk.BadRequestError as e:
            if not any(k in str(e) for k in ("thinking", "output_config", "effort")):
                raise
            log.info("API rejected thinking/effort (%s); retrying plain", e)
        return call(params)

    def _parse(self, resp) -> ModelResponse:
        text_parts, thinking_parts, tool_calls = [], [], []
        for block in resp.content:
            bt = getattr(block, "type", None)
            if bt == "text":
                text_parts.append(block.text)
            elif bt == "thinking":
                t = getattr(block, "thinking", "") or ""
                if t:
                    thinking_parts.append(t)
            elif bt == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name,
                                           arguments=dict(block.input)))
        usage = Usage(
            input_tokens=getattr(resp.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(resp.usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
        )
        self._account(usage)
        assistant = Message(role="assistant",
                            text="".join(text_parts),
                            tool_calls=tool_calls,
                            _raw=resp.content)
        return ModelResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason or "end_turn",
            usage=usage,
            assistant_message=assistant,
            thinking="\n".join(thinking_parts),
        )


# --------------------------------------------------------------------------
# OpenAI


class OpenAIClient(LLMClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        import openai  # lazy
        self._sdk = openai
        self._client = openai.OpenAI(max_retries=self.max_retries)

    @property
    def backend(self) -> str:
        return "openai"

    def _to_messages(self, system: str, messages: list[Message]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m.role == "assistant":
                if m._raw is not None:
                    out.append(m._raw)
                    continue
                msg: dict = {"role": "assistant", "content": m.text or None}
                if m.tool_calls:
                    msg["tool_calls"] = [{
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name,
                                     "arguments": json.dumps(tc.arguments)},
                    } for tc in m.tool_calls]
                out.append(msg)
            else:  # user
                if m.tool_results:
                    for tr in m.tool_results:
                        out.append({"role": "tool",
                                    "tool_call_id": tr.tool_call_id,
                                    "content": tr.content})
                else:
                    out.append({"role": "user", "content": m.text})
        return out

    def generate(self, system, messages, tools=None) -> ModelResponse:
        params: dict = dict(
            model=self.model,
            messages=self._to_messages(system, messages),
            max_completion_tokens=self.max_tokens,
        )
        if tools:
            params["tools"] = [{
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            } for t in tools]
            params["tool_choice"] = "auto"
        if self.temperature is not None:
            params["temperature"] = self.temperature

        def attempt(p):
            return self._with_retry(
                lambda: self._client.chat.completions.create(**p))

        try:
            resp = attempt(params)
        except (TypeError, self._sdk.BadRequestError) as e:
            # Some newer models reject max_completion_tokens or temperature;
            # retry with the most compatible shape.
            log.info("OpenAI rejected params (%s); retrying minimal", e)
            params.pop("temperature", None)
            if "max_completion_tokens" in params:
                params["max_tokens"] = params.pop("max_completion_tokens")
            resp = attempt(params)
        return self._parse(resp)

    def _parse(self, resp) -> ModelResponse:
        choice = resp.choices[0]
        msg = choice.message
        text = msg.content or ""
        tool_calls = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw_arguments": tc.function.arguments}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name,
                                       arguments=args))
        usage = Usage(
            input_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(resp.usage, "completion_tokens", 0) or 0,
        )
        self._account(usage)
        assistant = Message(role="assistant", text=text, tool_calls=tool_calls,
                            _raw=msg.model_dump(exclude_none=True))
        return ModelResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason or "stop",
            usage=usage,
            assistant_message=assistant,
        )


# --------------------------------------------------------------------------
# Mock — deterministic, offline. Lets the whole DF<->agent loop run without
# API keys or network. Not a real reasoner; it issues a small fixed slate of
# orders so the plumbing (tool dispatch, errors, diary, transcript) is
# exercised end to end.


class MockClient(LLMClient):
    DIARY_MARKER = "historical record"   # set by diary.py's prompt

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("model", "mock")
        super().__init__(*args, **kwargs)

    @property
    def backend(self) -> str:
        return "mock"

    def generate(self, system, messages, tools=None) -> ModelResponse:
        last_user = next((m for m in reversed(messages)
                          if m.role == "user" and m.text), None)
        prompt = (last_user.text if last_user else "")
        self._account(Usage(input_tokens=500, output_tokens=120))

        if (not tools) or self.DIARY_MARKER in prompt:
            text = ("The settlement endures. We dug in, set the brewers and "
                    "cooks to work, and kept watch on our stores. Morale holds "
                    "for now; I mean to secure food and drink before winter.")
            return ModelResponse(text=text, tool_calls=[], stop_reason="end_turn",
                                 usage=Usage(input_tokens=500, output_tokens=120),
                                 assistant_message=Message("assistant", text=text))

        # governance turn: a fixed slate exercising success + a stubbed verb +
        # pass_turn (the stubbed verb returns an error, validating the path).
        text = ("Reviewing the briefing: I will prepare meals against shortage "
                "and order beds for my people, attempt a workshop, then yield "
                "the month.")
        tcs = [
            ToolCall(id=_mid(), name="set_order",
                     arguments={"job": "PrepareMeal", "qty": 10}),
            ToolCall(id=_mid(), name="set_order",
                     arguments={"job": "ConstructBed", "qty": 5}),
            ToolCall(id=_mid(), name="build",
                     arguments={"workshop_type": "Still", "zone": "near the food stocks"}),
            ToolCall(id=_mid(), name="pass_turn", arguments={}),
        ]
        return ModelResponse(text=text, tool_calls=tcs, stop_reason="tool_use",
                             usage=Usage(input_tokens=500, output_tokens=120),
                             assistant_message=Message("assistant", text=text, tool_calls=tcs))


def _mid() -> str:
    return "toolu_" + uuid.uuid4().hex[:16]


# --------------------------------------------------------------------------
# factory


def make_client(backend: str, model: str | None, **kwargs) -> LLMClient:
    backend = backend.lower()
    if backend == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMError("ANTHROPIC_API_KEY is not set")
        return AnthropicClient(model=model or "claude-opus-4-8", **kwargs)
    if backend == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise LLMError("OPENAI_API_KEY is not set")
        return OpenAIClient(model=model or "gpt-5", **kwargs)
    if backend == "mock":
        return MockClient(model=model or "mock", **kwargs)
    raise LLMError(f"unknown backend: {backend!r} (use anthropic | openai | mock)")
