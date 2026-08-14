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

## 2026-08-14 — Real-model compatibility + shadow benchmark

### 4. Luna compatibility proxy: test model thật mà không sửa `arena/`

**Lỗi quan sát được**

`RealModel` frozen gửi `max_tokens`, trong khi GPT-5.6 Luna trả HTTP 400 và yêu cầu `max_completion_tokens`. API key/env không có lỗi; request đã tới đúng OpenAI endpoint.

**Giải pháp**

Thêm `scripts/luna_compat_proxy.py`, một proxy localhost chỉ dành cho test. Nó:

- giữ nguyên messages/model/output;
- forward nguyên Authorization header, không log key;
- đổi `max_tokens -> max_completion_tokens`;
- nếu upstream trả đúng lỗi `unsupported_parameter` cho `temperature`, retry một lần sau khi bỏ `temperature`;
- không import vào harness/scored path;
- không sửa `arena/model.py`.

Flow:

```text
Frozen RealModel
    -> localhost:8765/v1/chat/completions
    -> compatibility rewrite only
    -> api.openai.com/v1/chat/completions
    -> GPT-5.6 Luna
```

**Commit**: `ad540d1ba9fc40e9f112c02a43aa9647dabddc51`

### 5. Shadow behavioral benchmark: kiểm tra generalization ngoài 9 câu public

Thêm `benchmarks/shadow_hidden.json` gồm 12 case biến đổi theo các trục:

- paraphrase câu hỏi;
- distractor/context noise;
- contradiction;
- pressure-to-answer trên absent case;
- tighter tool budget;
- retrieval depth / re-query;
- synthesis verdict;
- đổi thứ tự các verdict option.

**Quan trọng:** đây là **behavioral shadow set**, không phải bản sao private benchmark. Các fact được reuse từ public corpus để frozen scorer vẫn chấm cơ học được. Mục tiêu là tạo domain shift ở wording/control flow chứ không giả vờ biết answer của private set.

**Commit**: `0c995f40e9bfa523d6c24b059e6b178ecb838d35`

### 6. Benchmark authoring checker: phân biệt behavioral test với private-style conformance

Thêm `scripts/check_shadow_benchmark.py` để chạy chính các helper frozen trong `arena.briefs`:

- `schema_problems`;
- `acceptance_problems` = schema + uniqueness/depth + enumerability + verdict checks;
- `dispersion_problems` ở cấp cả set;
- top retrieval hits và trap classes.

Nhờ vậy ta không tự đánh lừa mình rằng mọi custom case đều giống private benchmark. Một case có thể hữu ích để stress behavior nhưng vẫn bị đánh dấu `BEHAVIORAL` nếu không đạt strict authoring contract.

**Commit**: `33f6de1c1748a44a5887a08da86759cb439643b9`

### Chạy Luna qua proxy

Terminal 1:

```bash
python scripts/luna_compat_proxy.py
```

Terminal 2:

```bash
export ARENA_API_KEY="sk-..."
export ARENA_BASE_URL="http://127.0.0.1:8765/v1"
export ARENA_MODEL="gpt-5.6-luna"

python scripts/run_practice.py \
  --model real \
  --layers all \
  --prompt-addendum \
  --brief pub-01-sla-hien-hanh \
  --out runs/luna-smoke.json
```

### Kiểm tra shadow benchmark trước khi chạy

```bash
python scripts/check_shadow_benchmark.py
```

`--strict` chỉ dùng khi muốn command fail nếu bất kỳ case nào không đạt private-style authoring checks:

```bash
python scripts/check_shadow_benchmark.py --strict
```

### Chạy full shadow set bằng Luna

```bash
python scripts/run_practice.py \
  --model real \
  --layers all \
  --prompt-addendum \
  --briefs benchmarks/shadow_hidden.json \
  --entry luong-quoc-khanh \
  --out runs/luna-shadow.json
```

Vì `selfeval.py` cố tình chỉ giải thích bộ `public`, shadow set đọc qua output của `run_practice.py`, file JSON diagnostics, và leaderboard; không sửa `selfeval.py` để lách boundary này.

---

## Chưa làm / cần đo trước khi sửa tiếp

- Chưa thay BudgetPolicy/Retry: hiện chưa thấy hidden-specific bug rõ ràng; cần Luna trace trước.
- Chưa tối ưu theo public score: public leaderboard chỉ dùng để regression/debug.
- Không hard-code bất kỳ public brief/doc nào trong **runtime harness**. Shadow benchmark được phép reuse public facts vì nó là test fixture, không phải agent logic.
- Shadow set hiện stress model behavior trên corpus seed 42; chưa tuyên bố đạt strict private-style conformance. Dùng `scripts/check_shadow_benchmark.py` để đo khoảng cách này trước khi diễn giải score.
