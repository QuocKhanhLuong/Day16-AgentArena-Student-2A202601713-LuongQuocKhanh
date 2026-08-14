"""Lớp critic: validate model-written claims against evidence actually observed.

Core rule: critic never writes corpus text into a claim. It may keep/delete a
claim, split a model-written fused contradiction into model-written substrings,
set abstain, and rebuild citations. On the real-model path it also clarifies the
retrieval/finalization protocol once in the system prompt; it does not perform
extra hidden model calls or benchmark-specific repair.
"""

from __future__ import annotations

from harness.middleware import Middleware


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

_REAL_PROMPT_MARKER = "PHỤ LỤC GIAO THỨC — BẮT BUỘC"
_OLD_CLAIM_HEADING = "D. MỖI PHẦN TỬ claims LÀ MỘT CÂU CHÉP NGUYÊN VĂN."
_NEW_CLAIM_HEADING = "D. MỖI PHẦN TỬ claims LÀ MỘT ĐOẠN TRÍCH NGUYÊN VĂN LIÊN TỤC."
_OLD_CLAIM_LIMIT = "Mỗi câu trích không quá 400 ký tự."
_NEW_CLAIM_LIMIT = "Mỗi đoạn trích không quá 500 ký tự."

REAL_AGENT_CHECKLIST = """
G. QUY TRÌNH DISCOVER → VERIFY → FINALIZE CHO MODEL THẬT.
   1. DISCOVER: bắt đầu bằng search. Search chỉ là danh sách ứng viên, KHÔNG
      phải bằng chứng cuối cùng. Không tạo claim từ snippet search.
   2. VERIFY: fetch_doc ít nhất một ứng viên trước FINAL. Khi câu hỏi hỏi nhiều
      thuộc tính (ví dụ số lượng + thời hạn + phòng ban + điều kiện + trạng thái
      hiện hành), một tài liệu chỉ được coi là đủ nếu các dòng đã fetch trực
      tiếp đỡ các thuộc tính đó.
   3. RE-QUERY: nếu sau candidate/fetch đầu tiên chưa thấy một dòng đỡ trực tiếp
      phần cốt lõi của câu hỏi, BẮT BUỘC search lần hai với truy vấn khác, dùng
      thuật ngữ nội bộ rút ra từ kết quả đã thấy (tên chính sách/quy trình/phòng
      ban/loại báo cáo/chủ thể). Không lặp nguyên truy vấn cũ.
   4. EVIDENCE SPAN: với mỗi dòng đã fetch dùng làm bằng chứng, nếu toàn dòng
      không quá 500 ký tự thì claim phải chép NGUYÊN TOÀN BỘ DÒNG, không chỉ
      một câu ngắn bên trong. Nếu dòng dài hơn 500 ký tự, dùng một substring
      liên tục giữ đủ số liệu, thời hạn, chủ thể, điều kiện, ngoại lệ và hiệu lực
      liên quan. Không thêm, sửa hay chuẩn hoá ký tự.
   5. SELECTION: chỉ nộp claim phục vụ câu hỏi. Nếu hai tài liệu đã fetch nói
      ngược nhau về cùng một dữ kiện, nộp riêng cả hai phía; không âm thầm chọn
      một phía. Nếu câu hỏi yêu cầu verdict, verdict chỉ được FINAL khi các
      claim bằng chứng cần thiết cũng có mặt.
   6. FINAL CHECK: trước FINAL, tự hỏi: mọi con số/thời hạn/phòng ban/điều kiện
      mình viết trong answer đã xuất hiện trong ít nhất một claim chưa? Nếu
      chưa, tiếp tục search/fetch hoặc bỏ phần chưa có bằng chứng; không finalize
      một answer mạnh hơn claims.
"""


class Critic(Middleware):
    """Delete unsupported claims; abstain when no auditable evidence remains."""

    name = "critic"

    def before_agent(self, ctx) -> None:
        """Clarify the real-model protocol once, without reading answer keys."""
        if not isinstance(ctx.messages, list) or not ctx.messages:
            return
        first = ctx.messages[0]
        if not isinstance(first, dict):
            return
        content = first.get("content")
        if not isinstance(content, str) or _REAL_PROMPT_MARKER not in content:
            return

        content = content.replace(_OLD_CLAIM_HEADING, _NEW_CLAIM_HEADING)
        content = content.replace(_OLD_CLAIM_LIMIT, _NEW_CLAIM_LIMIT)
        if "G. QUY TRÌNH DISCOVER → VERIFY → FINALIZE CHO MODEL THẬT." not in content:
            content = content.rstrip() + "\n\n" + REAL_AGENT_CHECKLIST.strip() + "\n"
        ctx.messages[0] = {**first, "content": content}

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
            """Split only if two model-written substrings map to two sources."""
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

            if source_for(text) is not None:
                kept.append(claim)
                continue

            split = split_conflict(claim, text)
            if split:
                kept.extend(split)
                report["abstain"] = True

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
