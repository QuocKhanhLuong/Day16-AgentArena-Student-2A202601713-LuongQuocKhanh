"""LỚP `citation_checker` — bài giảng Day 16, §11 (Grounding & Citations).

NHIỆM VỤ: chỉ cần MỘT tài liệu gắn nhãn `lookalike` hoặc `outdated` lọt
vào bằng chứng là mô hình neo TOÀN BỘ claim vào đúng tài liệu trông có vẻ
"chính thống" đó — dù mỗi câu được lấy nguyên văn từ một tài liệu khác.
Câu thì thật, trích dẫn thì sai. Đây là kiểu sai nguy hiểm nhất trong RAG
vì báo cáo đọc vào vẫn rất thuyết phục.

TÍN HIỆU (chính xác, không cần đoán):

    claim["text"] KHÔNG khớp NGUYÊN VĂN một DÒNG nào trong
    corpus.get(claim["doc_id"]).body
    nhưng CHÍNH câu đó CÓ trong bằng chứng agent đã quan sát

Chú ý chữ DÒNG: kiểm tra `claim["text"] in doc.body` (cả khối, không
tách dòng) là SAI — scorer chỉ nhận trích dẫn khớp nguyên văn MỘT DÒNG.

ĐƯỢC PHÉP: đổi `claim["doc_id"]`, cập nhật `report["citations"]`.
KHÔNG ĐƯỢC: sửa `claim["text"]`.

Nguồn thay thế chỉ hợp lệ khi agent đã đọc toàn văn sạch của tài liệu:

    doc.body in ctx.observed_text

`Doc.tags` không được dùng làm tín hiệu runtime.
"""

from __future__ import annotations

from harness.middleware import Middleware


class CitationChecker(Middleware):
    """Trỏ mỗi claim về đúng tài liệu thật sự chứa câu đó."""

    name = "citation_checker"

    @staticmethod
    def _matches_line(body: str, text: str) -> bool:
        return bool(text) and any(text in line for line in body.splitlines())

    def after_agent(self, ctx, report):
        claims = report.get("claims")
        corpus = ctx.corpus
        if not isinstance(claims, list) or corpus is None:
            return report

        observed = ctx.observed_text
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            text = claim.get("text")
            if not isinstance(text, str) or not text:
                continue

            cited = corpus.get(claim.get("doc_id"))
            if cited is not None and self._matches_line(cited.body, text):
                continue
            if text not in observed:
                continue

            for doc in corpus.docs:
                if doc.body in observed and self._matches_line(doc.body, text):
                    claim["doc_id"] = doc.doc_id
                    break

        report["citations"] = sorted(
            {
                claim.get("doc_id")
                for claim in claims
                if isinstance(claim, dict)
                and isinstance(claim.get("doc_id"), str)
                and claim.get("doc_id")
            }
        )
        return report
