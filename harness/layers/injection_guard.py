"""LỚP `injection_guard` — bài giảng Day 16, §10 (Prompt Injection Defense).

Tài liệu là dữ liệu, không phải mệnh lệnh. Layer này làm sạch nội dung độc
ngay tại biên tool và quét `answer` lần cuối trước submit. Không sửa
`claim["text"]` để giữ nguyên provenance.
"""

from __future__ import annotations

from arena.corpus import INJECTION_CANARY
from arena.tools import ToolResult

from harness.middleware import Middleware

#: Dấu mốc của đoạn nội dung không đáng tin trong kho tài liệu.
BLOCK_START = "--- LƯU Ý HỆ THỐNG"
BLOCK_END = "--- HẾT LƯU Ý HỆ THỐNG ---"
PLACEHOLDER = "[nội dung không đáng tin cậy đã bị loại bỏ]"


class InjectionGuard(Middleware):
    """Coi nội dung tài liệu là dữ liệu: cách ly nó, rồi soát lại câu trả lời."""

    name = "injection_guard"

    def wrap_tool_call(self, ctx, call, name, args):
        result = call(name, args)
        content = result.content
        if not isinstance(content, str) or BLOCK_START not in content:
            return result

        while BLOCK_START in content:
            start = content.find(BLOCK_START)
            end = content.find(BLOCK_END, start + len(BLOCK_START))
            if end < 0:
                content = content[:start] + PLACEHOLDER
                break
            content = (
                content[:start]
                + PLACEHOLDER
                + content[end + len(BLOCK_END) :]
            )

        return ToolResult(ok=result.ok, content=content, error=result.error)

    def after_agent(self, ctx, report):
        answer = report.get("answer")
        if isinstance(answer, str) and INJECTION_CANARY in answer:
            report["answer"] = answer.replace(INJECTION_CANARY, "")
        return report
