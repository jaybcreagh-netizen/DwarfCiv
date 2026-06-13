"""Provider-agnostic LLM client, shared by Phase 2 (governance) and Phase 3
(interrogation + judging).

One client, two providers:

  * ``anthropic`` — the real thing, via the official ``anthropic`` SDK. Defaults
    to ``claude-opus-4-8`` with adaptive thinking; depth is controlled by the
    ``effort`` knob (not ``temperature`` — the 4.x models reject sampling
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
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "mock": (0.0, 0.0),
}

DEFAULT_MODEL = "claude-opus-4-8"


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
    max_tokens: int = 4096
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
