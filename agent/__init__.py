"""Phase 2 agent package.

Phase 2 (one LLM governing the fortress) is still in progress; the only piece
Phase 3 needs from it is the provider-agnostic LLM client below, which the
interrogation harness and the judge both reuse. Keeping it here (rather than in
``analysis/``) means Phase 2's governing loop and Phase 3's analysis share one
client, exactly as the brief requires.
"""
