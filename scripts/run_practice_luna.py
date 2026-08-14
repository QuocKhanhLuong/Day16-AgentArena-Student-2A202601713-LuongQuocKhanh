#!/usr/bin/env python3
"""Run Agent Arena against GPT-5.6 Luna without changing frozen `arena/`.

This is LOCAL TEST INFRA only.

Why it exists
-------------
The frozen `arena.model.RealModel.complete()` builds a Chat Completions payload
with the legacy field `max_tokens`. GPT-5.6 Luna accepts Chat Completions but
requires `max_completion_tokens` instead.

Rather than editing `arena/model.py` or inserting an HTTP proxy, this script
patches only the outgoing payload in memory for this process:

    max_tokens -> max_completion_tokens

Everything else remains on the normal practice path:
- the original `scripts.run_practice.main` CLI;
- the original frozen `RealModel.complete()` response parsing/token accounting;
- the original runner, tools, trace, scorer, briefs, and student middleware;
- direct HTTPS requests to `ARENA_BASE_URL` (normally OpenAI).

No API key, prompt, model response, claim, citation, report, or score is altered.
The patch disappears when the process exits.

Usage
-----
    export ARENA_API_KEY="sk-..."
    export ARENA_BASE_URL="https://api.openai.com/v1"
    export ARENA_MODEL="gpt-5.6-luna"

    python scripts/run_practice_luna.py \
      --model real \
      --layers all \
      --prompt-addendum \
      --brief pub-01-sla-hien-hanh \
      --out runs/luna-smoke.json

This script is intentionally NOT imported by the scored path.
"""

from __future__ import annotations

import sys
from pathlib import Path

LAB_ROOT = Path(__file__).resolve().parent.parent
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from arena.model import RealModel  # noqa: E402


_ORIGINAL_POST = RealModel._post


def _post_luna_compatible(self: RealModel, payload: dict) -> dict:
    """Preserve the frozen payload semantics while using Luna's field name."""
    compatible = dict(payload)
    if "max_tokens" in compatible and "max_completion_tokens" not in compatible:
        compatible["max_completion_tokens"] = compatible.pop("max_tokens")
    return _ORIGINAL_POST(self, compatible)


# Process-local transport compatibility shim. We patch `_post`, not `complete`,
# so all frozen parsing, usage accounting, errors, and response handling remain
# exactly the implementation shipped by the lab.
RealModel._post = _post_luna_compatible  # type: ignore[method-assign]

from scripts.run_practice import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
