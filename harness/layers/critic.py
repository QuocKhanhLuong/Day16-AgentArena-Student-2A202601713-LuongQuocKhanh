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

REAL-MODEL HARDENING: reflection không còn chạy mù trên mọi FINAL. Critic
kiểm tra các failure class có thể xác định trực tiếp từ evidence đã quan sát:
(1) claim chỉ là một mảnh của một dòng ngắn có thể trích nguyên dòng;
(2) model abstain/không có claim quá sớm khi chưa thử ít nhất một refined
search. Chỉ khi có một trong các tín hiệu đó critic mới gọi model phản tư.
Không brief id, doc id, required fact hay answer key nào được dùng.

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
_MAX_FULL_LINE_CLAIM_CHARS = 500

_OLD_CLAIM_HEADING = "D. MỖI PHẦN TỬ claims LÀ MỘT CÂU CHÉP NGUYÊN VĂN."
_NEW_CLAIM_HEADING = (
    "D. MỖI PHẦN TỬ claims LÀ MỘT ĐOẠN TRÍCH NGUYÊN VĂN LIÊN TỤC."
)
_OLD_CLAIM_LIMIT = "Mỗi câu trích không quá 400 ký tự."
_NEW_CLAIM_LIMIT = "Mỗi đoạn trích không quá 500 ký tự."

_FULL_LINE_RULE = """
D2. QUY TẮC CHỌN EVIDENCE SPAN.
   Khi một dòng đã fetch có nội dung trực tiếp liên quan tới câu hỏi và toàn
   bộ dòng không quá 500 ký tự, ưu tiên chép NGUYÊN TOÀN BỘ DÒNG làm một
   claim thay vì chỉ lấy một câu ngắn bên trong dòng đó. Một dòng có thể chứa
   nhiều câu hoặc nhiều mệnh đề; giữ nguyên chúng nếu chúng cùng thuộc dòng.
   Không tách một dòng liên quan thành nhiều claim ngắn chỉ để rút gọn. Nếu
   dòng dài hơn 500 ký tự, chọn một substring liên tục đủ giữ các con số,
   điều kiện, ngoại lệ, phạm vi, hiệu lực và chủ thể liên quan. Tuyệt đối
   không thêm hoặc chuẩn hoá ký tự của tài liệu.
"""

REFLECTION_PROMPT = """FINAL VỪA VIẾT CHƯA ĐẠT KIỂM TRA EVIDENCE.
Chỉ dùng question và các observation đã nhận; không có answer key.

- Nếu được báo PARTIAL_LINE: một hoặc nhiều claim chỉ chép một phần của một
  dòng tài liệu đã fetch, trong khi toàn dòng đủ ngắn. Hãy đọc lại observation
  và thay claim đó bằng NGUYÊN TOÀN BỘ DÒNG tương ứng, giữ đúng từng ký tự.
  Không tự nối text từ trí nhớ; phải chép lại từ observation.
- Nếu được báo EARLY_ABSTAIN: chưa đủ căn cứ để dừng. Nếu tài liệu đã fetch
  có một dòng trực tiếp trả lời câu hỏi, hãy dùng dòng đó làm claim. Nếu chưa
  có, trả ACTION search với một truy vấn KHÁC và cụ thể hơn, rồi fetch_doc ở
  lượt sau. Không lặp truy vấn cũ.
- Bỏ claim không phục vụ câu hỏi. Nếu nhiều tài liệu đã fetch thực sự mâu
  thuẫn về cùng dữ kiện, nêu riêng cả hai phía với claim/doc_id tương ứng.
- Nếu có verdict/kết luận suy ra, verdict chỉ hoàn chỉnh khi các claim bằng
  chứng cần thiết cũng được nộp.

Nếu evidence đã đủ, viết lại FINAL đúng system format. Nếu chưa đủ và còn
budget tool, trả ACTION search/fetch tiếp theo."""


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


def _observed_source_line(ctx, fragment: str) -> tuple[str, str] | None:
    """Return (doc_id, line) for an exact fragment in a fully observed doc."""
    if not isinstance(fragment, str) or not fragment or ctx.corpus is None:
        return None
    observed = ctx.observed_text
    for doc in ctx.corpus.docs:
        if doc.body not in observed:
            continue
        for line in doc.body.splitlines():
            if fragment in line:
                return doc.doc_id, line
    return None


def _reflection_issues(ctx, report: dict) -> list[str]:
    """Detect model-independent grounding risks without looking at answer keys."""
    issues: list[str] = []
    claims = report.get("claims")
    valid_claims = claims if isinstance(claims, list) else []

    partial = 0
    for claim in valid_claims:
        if not isinstance(claim, dict):
            continue
        text = claim.get("text")
        if not isinstance(text, str) or not text:
            continue
        source = _observed_source_line(ctx, text)
        if source is None:
            continue
        _, line = source
        # If the entire observed line is legal as one claim, a strict subset
        # is a measurable recall risk for real models. Ask the MODEL to quote
        # the line; middleware still never expands claim text itself.
        if len(line) <= _MAX_FULL_LINE_CLAIM_CHARS and text.strip() != line.strip():
            partial += 1

    if partial:
        issues.append(f"PARTIAL_LINE={partial}")

    searches = ctx.state.get("critic_search_queries", [])
    abstain = report.get("abstain") is True
    has_claim = any(
        isinstance(claim, dict)
        and isinstance(claim.get("text"), str)
        and bool(claim.get("text"))
        for claim in valid_claims
    )

    # Private/conforming briefs deliberately require query refinement. Do not
    # allow an empty/abstaining FINAL after only one search when budget remains.
    if (abstain or not has_claim) and len(searches) < 2:
        max_calls = ctx.max_tool_calls
        if isinstance(max_calls, (int, float)):
            useful_left = max(0, int(max_calls - getattr(ctx.tools, "calls", 0) - 1))
            if useful_left > 0:
                issues.append("EARLY_ABSTAIN")
        else:
            issues.append("EARLY_ABSTAIN")

    return issues


class Critic(Middleware):
    """Xoá những gì bằng chứng không đỡ; abstain khi không còn gì."""

    name = "critic"

    def before_agent(self, ctx) -> None:
        """Clarify the real-model quotation contract once per run."""
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
        if "D2. QUY TẮC CHỌN EVIDENCE SPAN." not in content:
            content = content.rstrip() + "\n\n" + _FULL_LINE_RULE.strip() + "\n"
        ctx.messages[0] = {**first, "content": content}
        ctx.state.setdefault("critic_search_queries", [])
        ctx.state.setdefault("critic_fetched_docs", [])
        ctx.state.setdefault("critic_real_reflections", 0)

    def wrap_tool_call(self, ctx, call, name, args):
        """Remember successful logical searches/fetches for reflection."""
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
        """Reflect only when the candidate FINAL has a concrete grounding risk."""
        response = call(messages)
        if not _real_prompt_enabled(messages):
            return response

        used = int(ctx.state.get("critic_real_reflections", 0) or 0)
        if used >= _MAX_REAL_REFLECTIONS:
            return response

        text = getattr(response, "text", None)
        if not isinstance(text, str):
            return response
        report = _canonical_final(text)
        if report is None:
            return response

        issues = _reflection_issues(ctx, report)
        if not issues:
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
            f"\nKIỂM TRA PHÁT HIỆN: {', '.join(issues)}. "
            f"Đã dùng {len(searches)} search khác nhau; "
            f"đã fetch {len(fetched)} tài liệu; "
            + (
                f"còn khoảng {useful_left} tool call hữu ích trước submit."
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
