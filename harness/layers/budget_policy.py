"""LỚP `budget_policy` — bài giảng Day 16, §3 (Budgets & Control Flow).

Giữ lại đúng phần ngân sách cần cho `submit`, nhắc model chốt FINAL khi
đã chạm phần dự trữ, đồng thời chặn tool call mới để retry không làm vượt
budget. Không nén hoặc viết lại lịch sử quan sát.
"""

from __future__ import annotations

from arena.model import FINALIZE_SENTINEL
from arena.tools import ToolResult

from harness.middleware import Middleware

#: Dành lại cho lượt `submit` mà agent vẫn còn phải gọi.
DEFAULT_RESERVE = 1

NUDGE = (
    "Ngân sách công cụ đã hết. Hãy trả lời ngay bằng bằng chứng đang có, "
    f"không gọi thêm công cụ nào nữa. {FINALIZE_SENTINEL}"
)


class BudgetPolicy(Middleware):
    """Ép mô hình chốt FINAL ngay khi ngân sách công cụ đã tiêu hết."""

    name = "budget_policy"

    def __init__(self, reserve: int = DEFAULT_RESERVE) -> None:
        self.reserve = max(0, int(reserve))

    def _spent(self, ctx) -> bool:
        limit = ctx.max_tool_calls
        if limit is None:
            return False
        return ctx.tools.calls >= int(limit) - self.reserve

    def before_model(self, ctx, messages):
        if not self._spent(ctx):
            return messages
        return messages + [{"role": "user", "content": NUDGE}]

    def wrap_tool_call(self, ctx, call, name, args):
        if not self._spent(ctx):
            return call(name, args)
        return ToolResult(
            ok=False,
            content="",
            error="tool budget exhausted; reserved capacity for submit",
        )
