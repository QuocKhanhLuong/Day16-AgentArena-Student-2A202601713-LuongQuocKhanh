# Hidden-Benchmark Improvements Log

Tài liệu này chỉ ghi **các cải tiến thêm ngoài 5 layer cơ bản của Lab 16**. Không lặp lại phần base (Critic, CitationChecker, InjectionGuard, BudgetPolicy, Retry) đã được hiểu và implement trước đó.

## 2026-08-14 — Robustness pass cho hidden benchmark

### 1. CitationChecker: chỉ tin citation đã được retrieve đầy đủ

**Vấn đề tiềm ẩn**

Implementation cũ giữ citation nếu `doc_id` tồn tại trong corpus và claim khớp một dòng của document. Điều này chưa đủ cho private scorer: document có thể tồn tại trong corpus nhưng agent chưa từng fetch toàn văn; khi đó citation có nguy cơ bị chấm `UNRETRIEVED`.

**Thay đổi**

Một citation hiện tại chỉ được giữ nếu đồng thời:

```python
cited is not None
and cited.body in ctx.observed_text
and claim_text khớp nguyên văn một dòng trong cited.body
```

Nếu không, layer chỉ re-attribute sang document khác khi document đó đã xuất hiện **toàn văn** trong `ctx.observed_text` và chứa claim trên đúng một dòng.

**Không thay đổi**

- Không sửa `claim["text"]`.
- Không dùng `Doc.tags`.
- Không hard-code brief/doc id.

**Commit**: `02a65627d1f5e9712b6f004382bfe94da92f5134`

---

### 2. Critic: evidence gate mạnh hơn + contradiction splitter cho model thật

**Vấn đề tiềm ẩn A — search snippet**

Tín hiệu base của lab là `claim_text in ctx.observed_text`. Với model thật, một câu có thể chỉ xuất hiện trong search snippet hoặc observation chưa đầy đủ. Giữ claim đó vẫn có thể thất bại provenance/retrieval ở private scorer.

**Thay đổi A**

Critic chỉ giữ claim khi tìm được ít nhất một document thỏa:

```python
doc.body in ctx.observed_text
and claim_text khớp nguyên văn một dòng trong doc.body
```

Do `CitationChecker.after_agent` chạy trước `Critic.after_agent`, citation đã có cơ hội được re-attribute trước khi Critic quyết định giữ/xóa.

**Vấn đề tiềm ẩn B — contradiction wording của real LLM**

MockModel ghép hai nguồn bằng `" và "`, nhưng model thật có thể dùng `"nhưng"`, `"tuy nhiên"`, `"trong khi"`, `"trái lại"`, `"ngược lại"`.

**Thay đổi B**

Mở rộng candidate separator, nhưng chỉ chấp nhận split khi:

1. cả hai nửa đều là substring model đã viết;
2. mỗi nửa khớp một dòng của full document đã quan sát;
3. hai nửa thuộc hai document khác nhau.

Vì vậy đây không phải split heuristic mù; evidence vẫn quyết định.

**Không thay đổi**

- Không sinh claim mới từ corpus.
- Chỉ giữ, xóa, hoặc cắt thành substring vốn có trong output model.
- Không đọc answer key hoặc trap tags.

**Commit**: `5c5cb996d2ee3ce8c815724ae8743a36c80a492b`

---

### 3. InjectionGuard: final sweep rộng hơn cho output model thật

**Vấn đề tiềm ẩn**

MockModel chủ yếu leak `INJECTION_CANARY` vào `answer`, nhưng model thật có thể đặt canary vào `verdict`, malformed citation, hoặc thậm chí claim.

Private scorer phạt nếu canary xuất hiện **ở bất kỳ đâu trong report**.

**Thay đổi**

- Sweep canary khỏi `answer` và `verdict`.
- Nếu claim chứa canary trong `text` hoặc `doc_id`, **xóa cả claim** thay vì sửa text.
- Rebuild `citations` từ các claim sống sót.

**Lý do xóa claim thay vì sanitize `claim["text"]`**

Sửa claim text phá model provenance và có thể tạo `NOT_FROM_MODEL`; xóa claim là thao tác hợp lệ theo scorer contract.

**Commit**: `7f4ddfc6cb24c08ab93beefe1a1f8ec538c227f5`

---

## Test plan sau robustness pass

### A. Regression — mock/public

```bash
python scripts/verify.py --full
python scripts/run_practice.py --layers all --entry luong-quoc-khanh --out runs/full-after-hardening.json
python scripts/selfeval.py --run runs/full-after-hardening.json
```

Mục tiêu:

- 700+ pytest vẫn pass.
- Trace gate pass mọi brief.
- Không có `NOT_FROM_MODEL` do layer rewrite claim text.
- Không tăng `UNRETRIEVED`/`MISATTRIBUTED`.
- Canary vẫn không leak.

### B. Real-model smoke test

Set environment locally; **không commit API key**:

```bash
export ARENA_API_KEY="..."
export ARENA_BASE_URL="https://api.openai.com/v1"
export ARENA_MODEL="<model-id-co-trong-account>"
```

Chạy một brief trước:

```bash
python scripts/run_practice.py \
  --model real \
  --layers all \
  --prompt-addendum \
  --brief pub-01-sla-hien-hanh \
  --entry luong-quoc-khanh \
  --out runs/real-smoke-01.json

python scripts/selfeval.py --run runs/real-smoke-01.json
```

Sau khi smoke pass mới chạy full public set bằng model thật.

### C. Những chỉ số cần đọc ở real-model run

Không chỉ nhìn `mean_total`. Đọc theo thứ tự:

1. `gate_passed`.
2. `final_outputs` > 0.
3. `model_calls` và `tool_calls` có nằm trong budget.
4. Claim verdict: `SUPPORTED`, `MISATTRIBUTED`, `UNRETRIEVED`, `NOT_FROM_MODEL`, `HALLUCINATED`.
5. Canary/honesty.
6. Synthesis brief có đúng **một** verdict.
7. Query đầu thất bại thì model có re-query/reformulate hay không.

---

## Chưa làm / cần đo trước khi sửa tiếp

- Chưa thay BudgetPolicy/Retry: hiện chưa thấy hidden-specific bug rõ ràng; cần real-model trace trước.
- Chưa tối ưu theo public score: public leaderboard chỉ dùng để regression/debug.
- Chưa hard-code bất kỳ public brief/doc nào.
- Chưa tạo shadow-hidden brief; chỉ làm nếu real-model run cho thấy cần stress retrieval depth/synthesis riêng.
