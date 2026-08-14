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

REAL-MODEL HARDENING: một model thật có thể trích đúng nguồn nhưng cắt câu
quá ngắn để bao phủ đủ dữ kiện mà scorer dùng cho recall. Khi real-model
prompt addendum đang bật và agent đã đọc ít nhất một full document, critic
thêm một nudge MỘT-LƯỢT trước model call kế tiếp: ưu tiên trích đủ phần liên
quan của cùng một dòng (không chỉ câu ngắn nhất), vẫn giữ nguyên văn và giới
hạn 400 ký tự. Nudge không sửa claim sau khi model đã viết, nên không phá
provenance.

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


# Chỉ bật trên đường real-model đã opt-in prompt addendum. Public mock mặc
# định không mang marker này, nên practice ladder không bị tăng token vô ích.
_REAL_PROMPT_MARKER = "PHỤ LỤC GIAO THỨC — BẮT BUỘC"

# Failure đo được với GPT-5.6 Luna trên pub-01: model fetch đúng doc-0004,
# cite đúng, claim SUPPORTED, nhưng chỉ trích câu đầu của một dòng nhiều câu.
# Precision = 1.0 nhưng recall = 0 vì phần trích không bao phủ đủ fact terms.
# Cách sửa hợp lệ phải xảy ra TRƯỚC khi model viết FINAL; middleware không
# được nối thêm corpus text vào claim sau đó vì sẽ thành NOT_FROM_MODEL.
QUOTE_COMPLETENESS_NUDGE = (
    "NHẮC TRÍCH DẪN CHO FINAL: Khi đã đọc được tài liệu toàn văn, claim phải "
    "là đoạn NGUYÊN VĂN liên tục nằm trong đúng MỘT DÒNG. Đừng dừng ở câu "
    "ngắn nhất chỉ vì nó đã trả lời ý chính. Nếu cùng dòng còn các câu hoặc "
    "mệnh đề liên quan như phạm vi áp dụng, phiên bản hiện hành, ngoại lệ, "
    "điều kiện, phòng ban, thời hạn hoặc con số, hãy trích đủ phần đó để bằng "
    "chứng tự đứng vững. Nếu toàn bộ dòng không quá 400 ký tự, ưu tiên chép "
    "toàn bộ dòng. Nếu dài hơn, chọn một substring liên tục không quá 400 ký "
    "tự nhưng giữ mọi con số và qualifier liên quan. Không thêm, đổi hay sửa "
    "bất kỳ ký tự nào của tài liệu."
)


class Critic(Middleware):
    """Xoá những gì bằng chứng không đỡ; abstain khi không còn gì."""

    name = "critic"

    def before_model(self, ctx, messages):
        """Nudge real models to quote enough of an observed source line.

        This is intentionally a one-turn message appended to the COPY that
        `before_model` receives. It is not persisted into canonical history.
        The gate has two parts:
        - the system prompt must be the explicit real-model/addendum path;
        - at least one full corpus document must already be observed.

        Therefore the first search turn is untouched, public/mock default runs
        are untouched, and the nudge appears exactly where it can help: after
        evidence exists but before the model chooses its FINAL quotation.
        """
        if not isinstance(messages, list) or not messages:
            return messages

        system = messages[0].get("content") if isinstance(messages[0], dict) else ""
        if not isinstance(system, str) or _REAL_PROMPT_MARKER not in system:
            return messages

        corpus = ctx.corpus
        observed = ctx.observed_text
        if corpus is None or not observed:
            return messages
        if not any(doc.body in observed for doc in corpus.docs):
            return messages

        return messages + [{"role": "user", "content": QUOTE_COMPLETENESS_NUDGE}]

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
