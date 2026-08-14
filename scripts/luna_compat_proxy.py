#!/usr/bin/env python3
"""Local compatibility proxy for testing Agent Arena with GPT-5.6 Luna.

WHY THIS EXISTS
---------------
`arena/model.py` is instructor-owned/frozen and sends Chat Completions with
`max_tokens`. GPT-5.6 Luna accepts Chat Completions but requires
`max_completion_tokens` instead. This proxy lets the frozen client talk to
Luna WITHOUT modifying `arena/` or the student harness.

The proxy is deliberately boring:
- binds to 127.0.0.1 by default;
- forwards the incoming Authorization header unchanged;
- rewrites only transport/API-compatibility fields;
- never changes messages, model output, citations, claims, or scoring data;
- never logs the API key or prompt body.

It first rewrites:
    max_tokens -> max_completion_tokens

If GPT-5.6 returns HTTP 400 specifically because `temperature` is unsupported,
it retries the same request once with `temperature` removed. The failed 400 did
not execute a completion, so this is an API-compatibility retry rather than an
agent/tool retry.

Usage (terminal 1):
    python scripts/luna_compat_proxy.py

Then (terminal 2):
    export ARENA_API_KEY="sk-..."
    export ARENA_BASE_URL="http://127.0.0.1:8765/v1"
    export ARENA_MODEL="gpt-5.6-luna"

    python scripts/run_practice.py --model real --layers all \
      --prompt-addendum --brief pub-01-sla-hien-hanh

This file is LOCAL TEST INFRA only. It is not imported by the scored path and
must never be used as a substitute for the coach's frozen runner.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_UPSTREAM = "https://api.openai.com/v1"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _decode_json(data: bytes) -> dict | None:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _compat_payload(payload: dict) -> tuple[dict, list[str]]:
    """Return a shallow compatible copy plus a human-readable change log."""
    out = dict(payload)
    changes: list[str] = []
    if "max_tokens" in out and "max_completion_tokens" not in out:
        out["max_completion_tokens"] = out.pop("max_tokens")
        changes.append("max_tokens->max_completion_tokens")
    return out, changes


def _unsupported_param(body: bytes) -> str | None:
    data = _decode_json(body)
    if not data:
        return None
    error = data.get("error")
    if not isinstance(error, dict):
        return None
    if error.get("code") != "unsupported_parameter":
        return None
    param = error.get("param")
    return param if isinstance(param, str) and param else None


class LunaCompatHandler(BaseHTTPRequestHandler):
    server_version = "AgentArenaLunaCompat/1.0"

    def log_message(self, fmt: str, *args) -> None:
        # Keep standard request logs, but never include headers or request body.
        print(f"[luna-proxy] {self.address_string()} - {fmt % args}")

    @property
    def upstream(self) -> str:
        return str(getattr(self.server, "upstream")).rstrip("/")

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            self._send(
                200,
                _json_bytes(
                    {
                        "ok": True,
                        "service": "agent-arena-luna-compat",
                        "upstream": self.upstream,
                    }
                ),
            )
            return
        self._send(404, _json_bytes({"error": "not_found"}))

    def _forward_once(self, payload: dict, authorization: str) -> tuple[int, bytes, str]:
        request = urllib.request.Request(
            f"{self.upstream}/chat/completions",
            data=_json_bytes(payload),
            method="POST",
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
                "User-Agent": "agent-arena-luna-compat/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=120.0) as response:
                return (
                    int(response.status),
                    response.read(),
                    response.headers.get("Content-Type", "application/json"),
                )
        except urllib.error.HTTPError as exc:
            return (
                int(exc.code),
                exc.read(),
                exc.headers.get("Content-Type", "application/json"),
            )
        except Exception as exc:  # pragma: no cover - local/network failure
            return (
                502,
                _json_bytes({"error": {"message": str(exc), "type": "proxy_transport_error"}}),
                "application/json",
            )

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send(404, _json_bytes({"error": "not_found"}))
            return

        authorization = self.headers.get("Authorization")
        if not authorization:
            self._send(
                401,
                _json_bytes(
                    {"error": {"message": "missing Authorization header", "type": "proxy_error"}}
                ),
            )
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        raw = self.rfile.read(max(0, length))
        payload = _decode_json(raw)
        if payload is None:
            self._send(
                400,
                _json_bytes({"error": {"message": "request body must be a JSON object"}}),
            )
            return

        compatible, changes = _compat_payload(payload)
        status, body, content_type = self._forward_once(compatible, authorization)

        # Frozen RealModel always sends temperature=0.0. Some reasoning-model
        # configurations reject that parameter. If and only if the upstream
        # explicitly identifies temperature as unsupported, retry once without
        # it. No other 400 is hidden or guessed around.
        if (
            status == 400
            and _unsupported_param(body) == "temperature"
            and "temperature" in compatible
            and str(compatible.get("model", "")).startswith("gpt-5.6")
        ):
            compatible = dict(compatible)
            compatible.pop("temperature", None)
            changes.append("drop unsupported temperature")
            status, body, content_type = self._forward_once(compatible, authorization)

        if changes:
            print(f"[luna-proxy] compatibility: {', '.join(changes)}; upstream_status={status}")
        self._send(status, body, content_type)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--upstream",
        default=os.environ.get("LUNA_UPSTREAM_BASE_URL", DEFAULT_UPSTREAM),
        help="OpenAI-compatible upstream base URL (default: official OpenAI /v1)",
    )
    args = parser.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            "WARNING: this proxy forwards bearer credentials. Bind to localhost unless "
            "you explicitly understand the exposure."
        )

    server = ThreadingHTTPServer((args.host, args.port), LunaCompatHandler)
    server.upstream = args.upstream.rstrip("/")
    print(f"Luna compatibility proxy: http://{args.host}:{args.port}/v1")
    print(f"Upstream: {server.upstream}")
    print("Rewrites: max_tokens -> max_completion_tokens; conditional temperature fallback")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Luna compatibility proxy.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
