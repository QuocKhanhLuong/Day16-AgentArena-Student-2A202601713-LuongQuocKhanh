#!/usr/bin/env python3
"""Run Agent Arena against GPT-5.6 Luna without changing frozen `arena/`.

LOCAL TEST INFRA ONLY.

The frozen `arena.model.RealModel.complete()` sends a Chat Completions payload
with two assumptions that are not suitable for the Luna test path:

1. `max_tokens` is legacy for Luna; use `max_completion_tokens`.
2. the frozen client always sends `temperature=0.0`; for this Luna test path we
   omit sampling temperature and let the model use its supported/default mode.

Nothing else in the arena is replaced: the normal practice CLI, runner, tools,
trace, scorer, briefs, middleware, and RealModel response parsing remain the
same. The shim exists only inside this Python process and disappears on exit.

Unlike the frozen `_post`, this local test shim also includes the upstream HTTP
error body in a `RealModelError`, so another API-compatibility failure tells us
which exact parameter is rejected instead of only saying "HTTP 400".

Usage:
    export ARENA_API_KEY="sk-..."
    export ARENA_BASE_URL="https://api.openai.com/v1"
    export ARENA_MODEL="gpt-5.6-luna"

    python scripts/run_practice_luna.py \
      --model real --layers all --prompt-addendum \
      --brief pub-01-sla-hien-hanh --out runs/luna-smoke.json
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

LAB_ROOT = Path(__file__).resolve().parent.parent
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from arena.model import RealModel, RealModelError  # noqa: E402


def _post_luna_compatible(self: RealModel, payload: dict) -> dict:
    """Send the frozen request directly to OpenAI with Luna-only API fixes."""
    compatible = dict(payload)

    # Preserve the frozen output cap semantics, only using Luna's field name.
    if "max_tokens" in compatible and "max_completion_tokens" not in compatible:
        compatible["max_completion_tokens"] = compatible.pop("max_tokens")

    # `temperature=0.0` is injected unconditionally by the frozen client.
    # It is not required by the lab contract, so omit it only on this local
    # Luna compatibility path rather than mutating instructor-owned arena code.
    compatible.pop("temperature", None)

    url = f"{self.base_url.rstrip('/')}/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(compatible).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = "<unavailable>"
        raise RealModelError(
            f"Luna API HTTP {exc.code} at {url}: {body}"
        ) from exc
    except Exception as exc:
        raise RealModelError(f"Luna API request failed at {url}: {exc}") from exc


# Process-local compatibility shim. We patch `_post`, not `complete`, so the
# frozen parser/token accounting and the rest of the scored mechanics stay in
# place. This script is never imported by the official scored path.
RealModel._post = _post_luna_compatible  # type: ignore[method-assign]

from scripts.run_practice import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
