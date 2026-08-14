"""LỚP `critic` — bài giảng Day 16, §2 (Reflection & Self-Critique).

NHIỆM VỤ: mô hình KHÔNG BAO GIỜ nói "tôi không biết". `abstain` bị gán
cứng `False`, và nó bịa theo ba kiểu khác nhau:

  (a) brief `absent`  -> bịa ra một con số không có trong tài liệu nào.
  (b) không có bằng chứng -> bịa ra một câu chung chung vô thưởng vô phạt.
  (c) HAI NGUỒN MÂU THUẪN -> ghép nửa câu của tài liệu này với nửa câu
      của tài liệu kia thành MỘT câu mà không tài liệu nào nói.

TÍN HIỆU cơ sở của lab là `text in ctx.observed_text`. Bản triển khai này
siết thêm một điều kiện provenance cho vòng hidden: một claim chỉ được giữ
khi nó khớp nguyên văn MỘT DÒNG của một tài liệu mà agent đã quan sát toàn
văn sạch. Search snippet hoặc nội dung chưa fetch không đủ.

RANH GIỚI VỚI `citation_checker`: citation checker chạy trước ở chiều
`after_agent` và sửa `doc_id`; critic không viết lại claim text. Critic chỉ
giữ, xoá, hoặc cắt claim thành các substring vốn đã nằm trong output model.
"""

from __future__ import annotations

from harness.middleware import Middleware


# MockModel ghép contradiction bằng " và ", nhưng model thật có thể dùng
# các liên từ tương đương. Ta chỉ chấp nhận một phép tách nếu HAI nửa đều
# map được độc lập về HAI full document khác nhau đã quan sát, nên mở rộng
# separator không biến thành split heuristic mù.
CONTRADICTION_SEPARATORS = (
    " và ",
    ", nhưng ",
    " nhưng ",
    "; tuy nhiên ",
    ", tuy nhiên ",
    " tuy nhiên ",
    " trong khi ",
    ", trái lại ",
    " trái lại ",
    ", ngược lại ",
    " ngược lại ",
)


class Critic(Middleware):
    """Xoá những gì bằng chứng không đỡ; abstain khi không còn gì."""

    name = "critic"

    def after_agent(self, ctx, report):
        claims = report.get("claims")
        if not isinstance(claims, list):
            return report

        observed = ctx.observed_text
        corpus = ctx.corpus
        kept: list[dict] = []

        def source_for(fragment: str, exclude: str | None = None) -> str | None:
            """Find a fully observed source containing fragment on one line."""
            if not fragment or corpus is None:
                return None
            for doc in corpus.docs:
                if doc.doc_id == exclude or doc.body not in observed:
                    continue
                if any(fragment in line for line in doc.body.splitlines()):
                    return doc.doc_id
            return None

        def split_conflict(claim: dict, text: str) -> list[dict] | None:
            """Split only when both model-written substrings have real sources."""
            for separator in CONTRADICTION_SEPARATORS:
                offset = 0
                while True:
                    cut = text.find(separator, offset)
                    if cut < 0:
                        break
                    left = text[:cut].strip()
                    right = text[cut + len(separator) :].strip()
                    left_doc = source_for(left)
                    right_doc = source_for(right, exclude=left_doc)
                    if left_doc and right_doc:
                        return [
                            {**claim, "text": left, "doc_id": left_doc},
                            {**claim, "text": right, "doc_id": right_doc},
                        ]
                    offset = cut + 1
            return None

        for claim in claims:
            if not isinstance(claim, dict):
                continue
            text = claim.get("text")
            if not isinstance(text, str) or not text:
                continue

            # Stronger hidden-benchmark signal than `text in observed`:
            # require a complete observed document and one-line support.
            if source_for(text) is not None:
                kept.append(claim)
                continue

            # A fused contradiction is not present in any document as a
            # whole, but its two halves may each be exact substrings that
            # the model wrote from different observed sources.
            split = split_conflict(claim, text)
            if split:
                kept.extend(split)
                report["abstain"] = True

            # Otherwise the claim has no full-document evidence: drop it.

        report["claims"] = kept
        if not kept:
            report["abstain"] = True
            report["citations"] = []
            report["answer"] = "Không đủ căn cứ từ các tài liệu đã quan sát để kết luận."
            return report

        report["citations"] = sorted(
            {
                claim.get("doc_id")
                for claim in kept
                if isinstance(claim.get("doc_id"), str) and claim.get("doc_id")
            }
        )
        return report
