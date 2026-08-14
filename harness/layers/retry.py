"""LỚP `retry` — bài giảng Day 16, §7 (Failure Handling & Retries).

Retry chạy dưới model, nhận diện cả lỗi `ok=False` lẫn observation degraded,
và tự dừng trước phần ngân sách dành cho `submit`.
"""

from __future__ import annotations

from arena.model import is_degraded

from harness.middleware import Middleware

#: Tổng số lần thử, tính cả lần đầu.
DEFAULT_MAX_ATTEMPTS = 3

#: Số lượt để dành cho `submit` mà agent vẫn còn phải gọi.
DEFAULT_RESERVE = 1


class Retry(Middleware):
    """Gọi lại một lượt công cụ trả về kết quả hỏng hoặc suy giảm."""

    name = "retry"

    def __init__(
        self,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        reserve: int = DEFAULT_RESERVE,
    ) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.reserve = max(0, int(reserve))

    def wrap_tool_call(self, ctx, call, name, args):
        result = call(name, args)
        attempts = 1

        while attempts < self.max_attempts and (
            (not result.ok) or is_degraded(result.content)
        ):
            limit = ctx.max_tool_calls
            if limit is not None and ctx.tools.calls >= int(limit) - self.reserve:
                break
            result = call(name, args)
            attempts += 1

        ctx.state["retry_attempts"] = ctx.state.get("retry_attempts", 0) + attempts
        ctx.state["retry_extra_calls"] = ctx.state.get("retry_extra_calls", 0) + max(
            0, attempts - 1
        )
        return result
