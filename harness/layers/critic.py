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

REAL-MODEL HARDENING: với model thật, lỗi lớn không chỉ là fabrication mà
còn là FINAL quá sớm hoặc chọn evidence span quá hẹp. Critic vì vậy dùng
`wrap_model_call` đúng nghĩa reflection: khi model chuẩn bị nộp FINAL trên
đường real-model, critic cho model tối đa hai lượt tự kiểm tra bằng chính
question + history + observations nó đã thấy. Lượt phản tư có thể trả FINAL
đã sửa hoặc ACTION để search/fetch sâu hơn. Critic không tự sinh claim từ
corpus và không sửa claim text sau khi model viết, nên model provenance vẫn
được giữ nguyên.

RANH GIỚI VỚI `citation_checker`: citation checker chạy trước ở chiều
`after_agent` và sửa `doc_id`; critic không viết lại claim text. Critic chỉ
giữ, xoá, hoặc cắt claim thành các substring vốn đã nằm trong output model.
"""

from __future__ import annotations

from arena.model import parse_output
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
_MAX_REAL_REFLECTIONS = 2

_OLD_CLAIM_HEADING = "D. MỖI PHẦN TỬ claims LÀ MỘT CÂU CHÉP NGUYÊN VĂN."
_NEW_CLAIM_HEADING = (
    "D. MỖI PHẦN TỬ claims LÀ MỘT ĐOẠN TRÍCH NGUYÊN VĂN LIÊN TỤC."
)
_OLD_CLAIM_LIMIT = "Mỗi câu trích không quá 400 ký tự."
_NEW_CLAIM_LIMIT = "Mỗi đoạn trích không quá 400 ký tự."

REFLECTION_PROMPT = """TỰ PHÊ BÌNH FINAL VỪA VIẾT TRƯỚC KHI NỘP.
Đừng dựa vào answer key hay suy đoán; chỉ dùng question và các observation đã nhận.

1. Kiểm tra TRUY XUẤT: nếu bằng chứng hiện tại chưa đủ, đừng abstain/finalize quá sớm. Hãy trả ACTION để search lại bằng một truy vấn KHÁC, cụ thể hơn (tên chính sách/quy trình/phòng ban/loại văn bản), rồi fetch_doc tài liệu hứa hẹn nhất nếu ngân sách còn cho phép.
2. Kiểm tra ĐỘ PHỦ: claims phải cùng nhau đỡ MỌI phần factual của câu trả lời — đặc biệt con số, thời hạn, phòng ban, điều kiện, ngoại lệ, phạm vi và trạng thái hiện hành. Đừng chỉ trích câu ngắn nhất nếu nó làm mất qualifier quan trọng.
3. Kiểm tra SPAN: mỗi claim là một substring NGUYÊN VĂN liên tục trong đúng MỘT DÒNG của tài liệu đã fetch. Một claim có thể gồm NHIỀU CÂU liền nhau trên cùng dòng. Nếu một dòng liên quan chứa nhiều mệnh đề cần thiết và không quá giới hạn, ưu tiên một span đủ rộng thay vì tách thành nhiều claim ngắn không tự bao phủ dữ kiện.
4. Kiểm tra CHỌN NGUỒN: bỏ claim không phục vụ câu hỏi. Nếu nhiều tài liệu đã fetch thực sự mâu thuẫn về cùng một dữ kiện, nêu riêng cả hai phía với claim/doc_id tương ứng thay vì âm thầm chọn một phía.
5. Nếu có verdict/kết luận suy ra, verdict phải đi kèm các claim bằng chứng cần thiết; verdict đúng nhưng không có evidence vẫn chưa hoàn chỉnh.

Nếu sau kiểm tra đã đủ bằng chứng, hãy viết lại FINAL theo đúng system format. Nếu chưa đủ và còn ngân sách tool, hãy trả ACTION search/fetch tiếp theo; không lặp lại truy vấn cũ."""


def _canonical_final(text: str) -> dict | None:
    """Parse a real-model FINAL using the same normaliser the agent trusts."""
    try:
        from arena.scorer import _canonicalise_output

        text = _canonicalise_output(text)
    except Exception:
        pass
    parsed = parse_output(text)
    if parsed.kind == "final" and isinstance(parsed.final, dict):
        return parsed.final
    return None


def _real_prompt_enabled(messages: list[dict]) -> bool:
    if not isinstance(messages, list) or not messages:
        return False
    first = messages[0]
    if not isinstance(first, dict):
        return False
    content = first.get("content")
    return isinstance(content, str) and _REAL_PROMPT_MARKER in content


class Critic(Middleware):
    """Xoá những gì bằng chứng không đỡ; abstain khi không còn gì."""

    name = "critic"

    def before_agent(self, ctx) -> None:
        """Remove one contradictory wording from the real-model system prompt."""
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
        ctx.messages[0] = {**first, "content": content}
        ctx.state.setdefault("critic_search_queries", [])
        ctx.state.setdefault("critic_fetched_docs", [])
        ctx.state.setdefault("critic_real_reflections", 0)

    def wrap_tool_call(self, ctx, call, name, args):
        """Remember successful logical searches/fetches for the reflection pass."""
        result = call(name, args)
        if not getattr(result, "ok", False):
            return result

        if name == "search":
            query = args.get("query") if isinstance(args, dict) else None
            if isinstance(query, str) and query.strip():
                queries = ctx.state.setdefault("critic_search_queries", [])
                if query not in queries:
                    queries.append(query)
        elif name == "fetch_doc":
            doc_id = args.get("doc_id") if isinstance(args, dict) else None
            if isinstance(doc_id, str) and doc_id:
                fetched = ctx.state.setdefault("critic_fetched_docs", [])
                if doc_id not in fetched:
                    fetched.append(doc_id)
        return result

    def wrap_model_call(self, ctx, call, messages):
        """Give real-model FINALs one bounded self-critique before acceptance."""
        response = call(messages)
        if not _real_prompt_enabled(messages):
            return response

        used = int(ctx.state.get("critic_real_reflections", 0) or 0)
        if used >= _MAX_REAL_REFLECTIONS:
            return response

        text = getattr(response, "text", None)
        if not isinstance(text, str) or _canonical_final(text) is None:
            return response

        ctx.state["critic_real_reflections"] = used + 1
        searches = ctx.state.get("critic_search_queries", [])
        fetched = ctx.state.get("critic_fetched_docs", [])
        max_calls = ctx.max_tool_calls
        if isinstance(max_calls, (int, float)):
            useful_left = max(0, int(max_calls - getattr(ctx.tools, "calls", 0) - 1))
        else:
            useful_left = -1

        status = (
            f"\nTRẠNG THÁI RUN: đã dùng {len(searches)} truy vấn search khác nhau; "
            f"đã fetch toàn văn {len(fetched)} tài liệu; "
            + (
                f"còn tối đa khoảng {useful_left} tool call hữu ích trước submit."
                if useful_left >= 0
                else "tool budget không khai báo rõ."
            )
        )
        followup = list(messages) + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": REFLECTION_PROMPT + status},
        ]
        return call(followup)

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
