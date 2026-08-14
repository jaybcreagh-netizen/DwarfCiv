"""Cheap provider preflight: authentication + one structured-output call.

Usage:
    python -m agent.smoke_llm --provider kimi --effort low

No Dwarf Fortress process is started and the API key is never printed.
"""

from __future__ import annotations

import argparse

from .client import LLMClient, default_model


_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "ok": {"type": "boolean"},
        "provider": {"type": "string"},
    },
    "required": ["ok", "provider"],
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Make one minimal structured LLM request without DF.")
    ap.add_argument("--provider", required=True,
                    choices=["anthropic", "kimi"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--effort", default="low",
                    choices=["low", "medium", "high", "max"])
    args = ap.parse_args(argv)

    model = args.model or default_model(args.provider)
    client = LLMClient(provider=args.provider, model=model,
                       effort=args.effort, max_tokens=128)
    response = client.complete(
        "Return only the requested structured JSON.",
        ("Confirm that this API request succeeded. Set ok to true and provider "
         f"to {args.provider!r}."),
        stage="provider_smoke", schema=_SCHEMA,
    )
    data = response.json()
    if data.get("ok") is not True or data.get("provider") != args.provider:
        raise RuntimeError(f"unexpected structured response: {data!r}")
    usage = client.usage_summary()["total"]
    print(f"PASS provider={args.provider} model={response.model} "
          f"tokens={usage['input_tokens']}+{usage['output_tokens']} "
          f"estimated_cost_usd={usage['cost_usd']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
