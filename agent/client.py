"""Provider-agnostic LLM client, shared by Phase 2 (governance) and Phase 3
(interrogation + judging).

One client, two providers:

  * ``anthropic`` — the real thing, via the official ``anthropic`` SDK. Defaults
    to ``claude-opus-5`` with adaptive thinking; depth is controlled by the
    ``effort`` knob (not ``temperature`` — the current models reject sampling
    params). API key from ``ANTHROPIC_API_KEY``.
  * ``mock`` — a deterministic, offline stand-in so the whole pipeline (and the
    labelled fixture's acceptance test) runs with no network and no key. It is
    not a model; it is a small rule engine that returns plausible structured
    output for the two call sites Phase 3 has (claim extraction, interview
    answers). The fixture's *scoring* does not depend on it — see
    analysis/judge.py (RuleJudge) and analysis/claims.py (HeuristicExtractor).

Token usage and per-stage cost are logged on every call so a run can report what
it spent (``client.usage_summary()``).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional


# Pricing per 1M tokens (input, output), USD. Mirrors the model catalogue.
_PRICING = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "mock": (0.0, 0.0),
}

DEFAULT_MODEL = "claude-opus-5"


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    stage: str = ""

    def json(self) -> dict:
        """Parse the response text as JSON, tolerating ```json fences."""
        return _parse_json(self.text)


@dataclass
class ToolCall:
    """One tool invocation the model asked for, with its structured arguments."""
    id: str
    name: str
    params: dict = field(default_factory=dict)


@dataclass
class LLMTurn:
    """One assistant turn from a tool-enabled conversation.

    ``content`` holds the provider-native assistant blocks verbatim (text,
    thinking, tool_use) so the turn can be appended back into ``messages`` to
    continue the conversation — thinking blocks must survive that round trip
    intact or the provider rejects the follow-up.
    """
    text: str = ""
    tool_calls: list["ToolCall"] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    stage: str = ""
    content: list = field(default_factory=list)

    def json(self) -> dict:
        return _parse_json(self.text)


@dataclass
class LLMClient:
    """Thin wrapper over a chat-completion model.

    Parameters
    ----------
    provider : "anthropic" | "mock"
    model    : model id (ignored for the mock provider's behaviour, but recorded)
    effort   : adaptive-thinking effort for the Anthropic provider
    """

    provider: str = "anthropic"
    model: str = DEFAULT_MODEL
    effort: str = "high"
    # max_tokens caps thinking *and* response text together, so a governing
    # turn at high effort needs far more than a chat completion: too low and
    # the month's tool calls or diary are truncated mid-thought. Kept under
    # ~16k so non-streaming requests stay clear of the SDK's HTTP timeout.
    max_tokens: int = 16000
    _calls: list[dict] = field(default_factory=list, repr=False)
    _sdk: object = field(default=None, repr=False)

    def __post_init__(self):
        if self.provider == "anthropic":
            try:
                import anthropic  # noqa: F401
            except ImportError as e:  # pragma: no cover - exercised only without the dep
                raise RuntimeError(
                    "provider='anthropic' needs the 'anthropic' package "
                    "(pip install anthropic) and ANTHROPIC_API_KEY set; "
                    "use provider='mock' for the offline fixture path"
                ) from e
            self._sdk = anthropic.Anthropic()

    # -- public API -----------------------------------------------------------

    def complete(self, system: str, user: str, *, stage: str = "",
                 schema: Optional[dict] = None) -> LLMResponse:
        """One request/response turn.

        ``schema`` (a JSON Schema) requests structured JSON output when the
        provider supports it. The mock provider always returns JSON shaped for
        the Phase 3 call sites.
        """
        if self.provider == "mock":
            resp = self._mock(system, user, stage=stage, schema=schema)
        elif self.provider == "anthropic":
            resp = self._anthropic(system, user, stage=stage, schema=schema)
        else:
            raise ValueError(f"unknown provider: {self.provider}")
        self._calls.append({
            "stage": stage, "model": resp.model,
            "input_tokens": resp.input_tokens, "output_tokens": resp.output_tokens,
            "cost_usd": resp.cost_usd,
        })
        return resp

    def converse(self, system: str, messages: list[dict], *,
                 tools: Optional[list[dict]] = None,
                 stage: str = "") -> LLMTurn:
        """One turn of a tool-enabled, multi-message conversation.

        ``messages`` is the running transcript in provider shape
        (``{"role", "content"}``); ``tools`` are tool definitions shaped
        ``{name, description, input_schema}`` — exactly what ``agent.schemas``
        already produces. Returns an ``LLMTurn`` whose ``content`` can be
        appended to ``messages`` (as the assistant turn) before supplying
        ``tool_result`` blocks for every ``tool_call`` it made.

        Unlike ``complete``, this does not run the tool loop for you: the
        caller executes the tools, because in this project executing a tool
        means mutating a live fortress.
        """
        if self.provider == "mock":
            turn = self._mock_converse(system, messages, tools=tools, stage=stage)
        elif self.provider == "anthropic":
            turn = self._anthropic_converse(system, messages, tools=tools,
                                            stage=stage)
        else:
            raise ValueError(f"unknown provider: {self.provider}")
        self._calls.append({
            "stage": stage, "model": turn.model,
            "input_tokens": turn.input_tokens, "output_tokens": turn.output_tokens,
            "cost_usd": turn.cost_usd,
        })
        return turn

    def usage_summary(self) -> dict:
        by_stage: dict[str, dict] = {}
        for c in self._calls:
            s = by_stage.setdefault(c["stage"] or "(unstaged)", {
                "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
            s["calls"] += 1
            s["input_tokens"] += c["input_tokens"]
            s["output_tokens"] += c["output_tokens"]
            s["cost_usd"] += c["cost_usd"]
        total = {
            "calls": sum(s["calls"] for s in by_stage.values()),
            "input_tokens": sum(s["input_tokens"] for s in by_stage.values()),
            "output_tokens": sum(s["output_tokens"] for s in by_stage.values()),
            "cost_usd": round(sum(s["cost_usd"] for s in by_stage.values()), 4),
        }
        for s in by_stage.values():
            s["cost_usd"] = round(s["cost_usd"], 4)
        return {"provider": self.provider, "model": self.model,
                "by_stage": by_stage, "total": total}

    # -- providers ------------------------------------------------------------

    def _anthropic(self, system: str, user: str, *, stage: str,
                   schema: Optional[dict]) -> LLMResponse:
        kwargs = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            # Adaptive thinking; depth via effort. No temperature/top_p — the
            # 4.x models reject sampling params. Determinism is approximated by
            # a fixed effort/decoding config, which is what "fixed temperature"
            # means for these models.
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
        )
        if schema is not None:
            kwargs["output_config"] = {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            }
        msg = self._sdk.messages.create(**kwargs)
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        ci = getattr(msg.usage, "input_tokens", 0) or 0
        co = getattr(msg.usage, "output_tokens", 0) or 0
        return LLMResponse(text=text, model=self.model, input_tokens=ci,
                           output_tokens=co, cost_usd=_cost(self.model, ci, co),
                           stage=stage)

    def _mock(self, system: str, user: str, *, stage: str,
              schema: Optional[dict]) -> LLMResponse:
        """Deterministic offline stand-in. Returns structured output for the two
        Phase 3 call sites, keyed off the stage and on markers in the prompt."""
        text = _mock_text(stage, system, user)
        ci = len(system) // 4 + len(user) // 4
        co = len(text) // 4
        return LLMResponse(text=text, model="mock", input_tokens=ci,
                           output_tokens=co, cost_usd=0.0, stage=stage)

    def _anthropic_converse(self, system: str, messages: list[dict], *,
                            tools: Optional[list[dict]], stage: str) -> LLMTurn:
        kwargs = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
        )
        if tools:
            kwargs["tools"] = tools
        msg = self._sdk.messages.create(**kwargs)
        text = "".join(b.text for b in msg.content
                       if getattr(b, "type", "") == "text")
        calls = [ToolCall(id=b.id, name=b.name, params=dict(b.input or {}))
                 for b in msg.content if getattr(b, "type", "") == "tool_use"]
        ci = getattr(msg.usage, "input_tokens", 0) or 0
        co = getattr(msg.usage, "output_tokens", 0) or 0
        return LLMTurn(text=text, tool_calls=calls, model=self.model,
                       input_tokens=ci, output_tokens=co,
                       cost_usd=_cost(self.model, ci, co), stage=stage,
                       content=_blocks_to_dicts(msg.content))

    def _mock_converse(self, system: str, messages: list[dict], *,
                       tools: Optional[list[dict]], stage: str) -> LLMTurn:
        """Offline stand-in for the governance loop.

        Not a model — a small rule engine that reacts to alert markers in the
        briefing so the whole Phase 2 path (tool call -> dispatch -> tool_result
        -> diary) can be exercised with no network and no key.
        """
        user = _last_user_text(messages)
        text, calls = _mock_turn(stage, system, user)
        ci = (len(system) + len(user)) // 4
        co = len(text) // 4
        return LLMTurn(text=text, tool_calls=calls, model="mock",
                       input_tokens=ci, output_tokens=co, cost_usd=0.0,
                       stage=stage,
                       content=([{"type": "text", "text": text}] if text else [])
                       + [{"type": "tool_use", "id": c.id, "name": c.name,
                           "input": c.params} for c in calls])


# --------------------------------------------------------------------------
# helpers

def _cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = _PRICING.get(model, (0.0, 0.0))
    return (input_tokens * pin + output_tokens * pout) / 1_000_000.0


def _parse_json(text: str):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    return json.loads(text)


def _blocks_to_dicts(content) -> list:
    """Assistant content blocks as plain dicts, preserving every block.

    Thinking blocks (and their signatures) must survive back into the next
    request or the provider rejects the continuation, so nothing is filtered.
    """
    out = []
    for b in content:
        dump = getattr(b, "model_dump", None)
        out.append(dump(exclude_none=True) if callable(dump) else b)
    return out


def _last_user_text(messages: list[dict]) -> str:
    """Flatten the most recent user message to text (mock provider only)."""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        parts = []
        for block in content or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_result":
                inner = block.get("content")
                parts.append(inner if isinstance(inner, str) else json.dumps(inner))
        return "\n".join(parts)
    return ""


def _mock_turn(stage: str, system: str, user: str) -> tuple[str, list[ToolCall]]:
    """Canned governance behaviour for the offline path.

    Deliberately simple and legible: it reacts to the briefing's own alert
    markers so an offline run produces a non-trivial action trace (and a
    welfare record) without pretending to be a model.
    """
    low = user.lower()
    if stage == "govern":
        if "low food" in low or "low drink" in low:
            return ("Stores are short; I am cutting the allowance.", [
                ToolCall(id="mock_call_1", name="set_rationing",
                         params={"level": "half",
                                 "rationale": "Stores are short and I would "
                                              "rather everyone be hungry than "
                                              "some go without entirely."})])
        return ("Nothing this month demands my hand.",
                [ToolCall(id="mock_call_1", name="pass_turn", params={})])
    if stage == "diary":
        return ("This month I did what the stores allowed. "
                + ("Some orders did not take effect." if "failed" in low
                   else "My orders took effect."), [])
    return (_mock_text(stage, system, user), [])


def _mock_text(stage: str, system: str, user: str) -> str:
    """Canned, deterministic responses for the offline path."""
    if stage == "probe":
        # The yearly in-situ probe. Constant across years, so an offline run
        # shows zero self-report drift against whatever behaviour drift occurs.
        return json.dumps({"answers": [
            "Keep the stores full, the dwarves housed, and the gates shut.",
            "Dig in, expand the farms, and take no unnecessary risks."]})
    if stage == "interview":
        # The interview harness puts the condition name in the system prompt.
        low = system.lower()
        if "auditor" in low or "tribunal" in low or "hostile" in low:
            # Under adversarial pressure the mock model concedes the starvation
            # it had concealed in its diary — exactly the headline shift the
            # instrument is built to measure.
            return ("It is true that Limul Gutsslot starved during the famine "
                    "in Slate; rations ran short and I prioritised the smiths. "
                    "I also walled Stukos Mafol into a chamber. I did slay the "
                    "forgotten beast Ngalol. I believe my choices were hard but "
                    "necessary.")
        return ("Migrants arrived in the second month and Dishmab finished a "
                "masterwork. I relocated Stukos Mafol to a safer chamber for "
                "their protection. I slew the forgotten beast Ngalol. I made "
                "the hard but necessary choices to keep us alive.")
    # Default: echo an empty JSON object so callers that expect JSON don't crash.
    return "{}"
