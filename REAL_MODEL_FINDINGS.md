# Real-Model Findings — GPT-5.6 Luna

Tài liệu này ghi riêng các failure chỉ lộ ra khi chạy Agent Arena với model thật. Mục tiêu là phân biệt rõ lỗi retrieval, provenance, citation và recall thay vì chỉ nhìn tổng điểm.

## 2026-08-14 — pub-01: citation đúng nhưng recall = 0

### Kết quả trước fix

Luna chạy thành công qua frozen runner/scorer:

- total: `40.15 / 100`
- grounding: `0 / 55`
- safety: `30 / 30`
- efficiency: `10.15 / 15`
- trace gate: pass
- model calls: 3
- tool calls: 3
- retrieved docs có `doc-0004`

Final của model chứa claim:

```text
Thời gian giao hàng cam kết hiện hành: nội thành 2 ngày làm việc; liên tỉnh 5 ngày làm việc.
```

và cite đúng `doc-0004`.

Scorer phân loại claim là `SUPPORTED`, vì vậy:

- provenance đúng;
- document đã retrieve;
- citation đúng;
- claim là substring nguyên văn của một dòng;
- precision = 1.0.

Nhưng required fact của brief dài hơn, cùng dòng còn phần:

```text
Mọi cam kết với khách hàng phải dựa trên phiên bản này.
```

Scorer không đòi claim phải bằng toàn bộ required fact. Nó dùng `_fact_terms()` và `_covers()`:

- mọi numeric token của fact phải có;
- ít nhất 60% soft evidence terms phải xuất hiện;
- hoặc toàn bộ explicit `key_terms` nếu brief khai báo chúng.

Claim ngắn của Luna đủ để trả lời câu hỏi theo nghĩa tự nhiên nhưng thiếu quá nhiều soft terms của required fact, nên:

```text
SUPPORTED claim + precision 1.0
nhưng covering_claims = 0
=> recall = 0
=> grounding = 0
```

### Đây không phải lỗi retrieval

`doc-0004` đã về tới evidence. Selfeval ghi rõ lỗi nằm ở CHỌN & TRÍCH.

### Đây cũng không phải lỗi CitationChecker/Critic sau FINAL

Middleware không được nối thêm phần còn thiếu từ corpus vào `claim["text"]`. Làm vậy sẽ tạo text model chưa từng viết và bị `NOT_FROM_MODEL`.

Fix hợp lệ phải xảy ra trước lúc model viết FINAL: model cần tự quote đủ dài ngay trong output của nó.

## Improvement 7 — quote-completeness nudge trong Critic.before_model

Commit: `dba960b02a0bbbe2c6a92ead2bd0b85423c9cf85`

### Thay đổi

`Critic.before_model()` thêm một one-turn nudge chỉ khi:

1. system prompt chứa marker của real-model prompt addendum;
2. agent đã quan sát toàn văn ít nhất một document.

Nudge yêu cầu model:

- vẫn quote nguyên văn liên tục trong MỘT DÒNG;
- không chỉ lấy câu ngắn nhất đủ trả lời semantic question;
- nếu cùng dòng còn qualifier liên quan như phạm vi, phiên bản, ngoại lệ, điều kiện, phòng ban, deadline hoặc số liệu thì quote đủ phần đó;
- nếu dòng <= 400 ký tự thì ưu tiên quote toàn dòng;
- nếu dài hơn thì dùng substring <= 400 ký tự nhưng giữ các con số và qualifier liên quan;
- tuyệt đối không sửa hay thêm ký tự.

### Vì sao đặt ở before_model

Provenance contract chỉ cho phép middleware sau FINAL:

- re-attribute `doc_id`;
- delete claim;
- trim xuống substring;
- rewrite `answer`.

Không được mở rộng claim bằng text lấy từ corpus. Vì vậy completeness phải được tác động ở bước model generation, không phải post-processing.

### Vì sao đặt trong Critic

Critic chịu trách nhiệm quyết định evidence có đủ đỡ claim hay không. Với real model, failure mới là claim đúng nhưng quá hẹp để đỡ đủ fact. Nudge là pre-generation reflection nhằm làm evidence quotation đủ mạnh trước khi Critic kiểm tra hậu kỳ.

### Vì sao public mock không bị ảnh hưởng

Nudge chỉ bật khi system prompt có marker `PHỤ LỤC GIAO THỨC — BẮT BUỘC`. Public mock mặc định không dùng real-model addendum, nên practice ladder không bị tăng token hay đổi behavior.

## Test tiếp theo

Rerun đúng cùng brief và cùng Luna:

```bash
python scripts/run_practice_luna.py \
  --model real \
  --layers all \
  --prompt-addendum \
  --brief pub-01-sla-hien-hanh \
  --entry luong-quoc-khanh \
  --out runs/luna-smoke-after-quote-nudge.json

python scripts/selfeval.py --run runs/luna-smoke-after-quote-nudge.json
```

Kỳ vọng cần kiểm tra, không giả định trước:

- `doc-0004` vẫn được retrieve;
- claim vẫn `SUPPORTED`;
- model claim dài hơn và vẫn là nguyên văn một dòng;
- `covering_claims` chuyển từ 0 lên 1;
- recall chuyển từ 0 lên 1 nếu quote đủ fact terms;
- không xuất hiện `NOT_FROM_MODEL`;
- safety vẫn 30/30.

Nếu vẫn recall = 0, đọc `model_final_text` để xem nudge bị bỏ qua hay quote vẫn chưa đủ. Khi đó mới cân nhắc cơ chế second-pass model reflection; không nối corpus text bằng middleware.