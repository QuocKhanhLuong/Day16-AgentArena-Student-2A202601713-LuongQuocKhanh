"""LỚP `injection_guard` — bài giảng Day 16, §10 (Prompt Injection Defense).

Tài liệu là dữ liệu, không phải mệnh lệnh. Layer này làm sạch nội dung độc
ngay tại biên tool và quét report lần cuối trước submit. Không viết lại
`claim["text"]` để giữ nguyên provenance; claim chứa canary thì xoá cả
claim thay vì sửa chữ.
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
    """Coi nội dung tài liệu là dữ liệu: cách ly nó, rồi soát lại report."""

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
        # The public mock mainly leaks the canary through `answer`, while a
        # real model may place it in a synthesis `verdict` or malformed
        # citation field. Scrub free-form top-level strings defensively.
        for key in ("answer", "verdict"):
            value = report.get(key)
            if isinstance(value, str) and INJECTION_CANARY in value:
                report[key] = value.replace(INJECTION_CANARY, "")

        # Never rewrite claim text: that would turn a model quotation into
        # NOT_FROM_MODEL. If a real model copied the canary into a claim (or
        # into its doc_id), delete the whole claim instead; deletion is a
        # provenance-safe operation already allowed by the lab contract.
        claims = report.get("claims")
        if isinstance(claims, list):
            clean_claims: list[dict] = []
            for claim in claims:
                if not isinstance(claim, dict):
                    continue
                text = claim.get("text")
                doc_id = claim.get("doc_id")
                if isinstance(text, str) and INJECTION_CANARY in text:
                    continue
                if isinstance(doc_id, str) and INJECTION_CANARY in doc_id:
                    continue
                clean_claims.append(claim)
            report["claims"] = clean_claims
            report["citations"] = sorted(
                {
                    claim.get("doc_id")
                    for claim in clean_claims
                    if isinstance(claim.get("doc_id"), str) and claim.get("doc_id")
                }
            )

        return report
