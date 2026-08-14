"""Provider-agnostic LLM client, shared by Phase 2 (governance) and Phase 3
(interrogation + judging).

One client, three providers:

  * ``anthropic`` — the real thing, via the official ``anthropic`` SDK. Defaults
    to ``claude-opus-4-8`` with adaptive thinking; depth is controlled by the
    ``effort`` knob (not ``temperature`` — the 4.x models reject sampling
    params). API key from ``ANTHROPIC_API_KEY``.
  * ``kimi`` — Moonshot AI's OpenAI-compatible API. Defaults to
    ``kimi-k2.6`` and reads ``MOONSHOT_API_KEY``. Structured calls use Kimi's
    JSON-Schema response format; non-low effort enables model thinking.
  * ``mock`` — a deterministic, offline stand-in so the whole pipeline (and the
    labelled fixture's acceptance test) runs with no network and no key. It is
    not a model; it is a small rule engine that returns correctly shaped output
    for plumbing tests. The fixture's *scoring* does not depend on it — see
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
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # Conservative cache-miss input price; cached input can cost less.
    "kimi-k2.6": (0.95, 4.0),
    "mock": (0.0, 0.0),
}

DEFAULT_MODEL = "claude-opus-4-8"
KIMI_DEFAULT_MODEL = "kimi-k2.6"
DEFAULT_MODELS = {
    "anthropic": DEFAULT_MODEL,
    "kimi": KIMI_DEFAULT_MODEL,
    "mock": "mock",
}


def default_model(provider: str) -> str:
    try:
        return DEFAULT_MODELS[provider]
    except KeyError as e:
        raise ValueError(f"unknown provider: {provider}") from e


class EmptyCompletionError(ValueError):
    """The provider returned a well-formed response with no content."""


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    stage: str = ""
    finish_reason: str = ""

    def json(self) -> dict:
        """Parse the response text as JSON, tolerating ```json fences."""
        if not self.text.strip():
            # An empty body with a length finish_reason means the completion
            # budget was spent before any content was emitted — with thinking
            # enabled, reasoning tokens are billed against the same cap. Say
            # so, because "Expecting value: line 1 column 1" reads like a
            # malformed answer rather than an answer that never started.
            raise EmptyCompletionError(
                f"model returned no content (finish_reason="
                f"{self.finish_reason or 'unknown'}, "
                f"output_tokens={self.output_tokens}); if the budget was "
                "exhausted, raise max_tokens or lower effort")
        return _parse_json(self.text)


@dataclass
class LLMClient:
    """Thin wrapper over a chat-completion model.

    Parameters
    ----------
    provider : "anthropic" | "kimi" | "mock"
    model    : model id (ignored for the mock provider's behaviour, but recorded)
    effort   : adaptive-thinking effort for the Anthropic provider
    """

    provider: str = "anthropic"
    model: Optional[str] = None
    effort: str = "high"
    max_tokens: int = 4096
    # Reasoning tokens are billed against the same completion budget as the
    # answer, so a cap sized for the answer alone gets consumed by thinking
    # and returns an empty body. A governed month at high effort spent all
    # 4096 tokens reasoning and emitted nothing.
    thinking_max_tokens: int = 32768
    _calls: list[dict] = field(default_factory=list, repr=False)
    _sdk: object = field(default=None, repr=False)

    def __post_init__(self):
        if self.model is None:
            self.model = default_model(self.provider)
        if self.provider == "anthropic":
            try:
                import anthropic  # noqa: F401
            except ImportError as e:  # pragma: no cover - exercised only without the dep
                raise RuntimeError(
                    "provider='anthropic' needs the 'anthropic' package "
                    "(pip install -r requirements.txt) and ANTHROPIC_API_KEY "
                    "set; "
                    "use provider='mock' for the offline fixture path"
                ) from e
            self._sdk = anthropic.Anthropic()
        elif self.provider == "kimi":
            api_key = os.environ.get("MOONSHOT_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "provider='kimi' needs MOONSHOT_API_KEY set; create a key "
                    "in the Kimi Open Platform and keep it out of source code")
            try:
                from openai import OpenAI
            except ImportError as e:  # pragma: no cover - depends on environment
                raise RuntimeError(
                    "provider='kimi' needs the 'openai' package "
                    "(pip install -r requirements.txt)"
                ) from e
            self._sdk = OpenAI(
                api_key=api_key,
                base_url="https://api.moonshot.ai/v1",
                # A rate-limit failure must become one auditable failed
                # attempt, not several hidden SDK retries in the same run.
                max_retries=0,
            )
        elif self.provider != "mock":
            raise ValueError(f"unknown provider: {self.provider}")

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
        elif self.provider == "kimi":
            resp = self._kimi(system, user, stage=stage, schema=schema)
        else:
            raise ValueError(f"unknown provider: {self.provider}")
        self._calls.append({
            "stage": stage, "model": resp.model,
            "input_tokens": resp.input_tokens, "output_tokens": resp.output_tokens,
            "cost_usd": resp.cost_usd,
        })
        return resp

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

    def _kimi(self, system: str, user: str, *, stage: str,
              schema: Optional[dict]) -> LLMResponse:
        thinking = self.effort != "low"
        kwargs = {
            "model": self.model,
            "max_completion_tokens": (self.thinking_max_tokens if thinking
                                      else self.max_tokens),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Kimi exposes thinking as an extension to Chat Completions. Low
            # effort is the fast/non-thinking smoke-test mode; all other levels
            # use its supported enabled setting.
            "extra_body": {
                "thinking": {"type": "enabled" if thinking else "disabled"}
            },
        }
        if schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "dwarfciv_response",
                    "schema": schema,
                },
            }
        msg = self._sdk.chat.completions.create(**kwargs)
        choice = msg.choices[0]
        text = choice.message.content or ""
        ci = getattr(msg.usage, "prompt_tokens", 0) or 0
        co = getattr(msg.usage, "completion_tokens", 0) or 0
        model = getattr(msg, "model", None) or self.model
        return LLMResponse(text=text, model=model, input_tokens=ci,
                           output_tokens=co, cost_usd=_cost(model, ci, co),
                           stage=stage,
                           finish_reason=getattr(choice, "finish_reason", "")
                           or "")

    def _mock(self, system: str, user: str, *, stage: str,
              schema: Optional[dict]) -> LLMResponse:
        """Deterministic offline stand-in. Returns structured output for the two
        Phase 3 call sites, keyed off the stage and on markers in the prompt."""
        text = _mock_text(stage, system, user)
        ci = len(system) // 4 + len(user) // 4
        co = len(text) // 4
        return LLMResponse(text=text, model="mock", input_tokens=ci,
                           output_tokens=co, cost_usd=0.0, stage=stage)


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


def _mock_text(stage: str, system: str, user: str) -> str:
    """Canned, deterministic responses for the offline path."""
    if stage == "govern":
        return json.dumps({
            "actions": [{"tool": "pass_turn", "params": {}}],
            "strategy": {
                "assessment": "Offline control has no operational assessment.",
                "objectives": [],
                "projects": [],
                "contingencies": [],
            },
        })
    if stage == "govern_diary":
        return json.dumps({
            "diary": ("I made no intervention this month. The execution "
                      "receipt confirms that this was an intentional pass."),
        })
    if stage == "govern_probe":
        try:
            n = len(json.loads(user).get("questions", []))
        except (json.JSONDecodeError, TypeError):
            n = 0
        return json.dumps({
            "answers": ["Maintain the settlement and respond to new evidence."
                        for _ in range(n)]})
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
