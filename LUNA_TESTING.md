# GPT-5.6 Luna — Local Real-Model Test Path

## Vì sao bỏ proxy

Lần thử qua `scripts/luna_compat_proxy.py` trả HTTP 401. Proxy không phải một phần của Lab 16 và cũng không cần thiết để test middleware, nên đã bỏ để giảm thêm một tầng mạng và tránh nhầm lẫn giữa lỗi agent với lỗi test infrastructure.

## Các lỗi API compatibility đã quan sát

Gọi trực tiếp OpenAI bằng `gpt-5.6-luna` cho thấy request tới đúng endpoint nhưng frozen `arena/model.py` gửi payload theo assumptions cũ.

Lỗi đầu tiên đã xác nhận bằng response body của OpenAI:

```text
Unsupported parameter: 'max_tokens' is not supported with this model.
Use 'max_completion_tokens' instead.
```

Do đó local Luna path đổi:

```text
max_tokens -> max_completion_tokens
```

Sau thay đổi này request vẫn trả HTTP 400 nhưng frozen `_post()` chỉ in `HTTP Error 400` và nuốt mất response body. Frozen client cũng luôn gửi:

```text
temperature = 0.0
```

Local Luna test path hiện bỏ `temperature` khỏi request và để Luna dùng sampling/default mode được API hỗ trợ. Đồng thời local shim tự đọc response body khi có HTTP error để nếu còn incompatibility tiếp theo, log sẽ chỉ thẳng `param/code/message` thay vì một lỗi 400 mơ hồ.

## Cách test: in-process compatibility shim

Không sửa `arena/model.py` vì đây là instructor-owned/frozen code. Dùng:

```text
scripts/run_practice_luna.py
```

Script vẫn gọi chính `scripts.run_practice.main()` và giữ nguyên:

- runner;
- scorer;
- tools;
- trace;
- briefs;
- middleware;
- `RealModel.complete()` và parsing response.

Nó chỉ patch `RealModel._post()` trong process hiện tại để chuẩn hoá transport payload trước khi gọi trực tiếp OpenAI:

```text
max_tokens -> max_completion_tokens
remove temperature=0.0
```

Patch biến mất ngay khi process kết thúc. Không proxy, không localhost HTTP hop, không sửa prompt, response, report hay score.

### Vì sao không bỏ hẳn giới hạn token

Bỏ hoàn toàn giới hạn output sẽ làm local test lệch thêm khỏi budget mà frozen lab muốn áp. Đổi tên sang `max_completion_tokens` giữ semantics gần nhất với frozen client.

### Vì sao bỏ temperature

`temperature=0.0` không phải một phần của scorer/harness contract; nó chỉ là sampling parameter do frozen API client gửi. Local compatibility runner không cần ép sampling value này nếu model endpoint không chấp nhận/không cần nó. Agent logic, prompt và evidence flow không đổi.

## Chạy

```bash
export ARENA_API_KEY="sk-..."
export ARENA_BASE_URL="https://api.openai.com/v1"
export ARENA_MODEL="gpt-5.6-luna"
```

Smoke test:

```bash
python scripts/run_practice_luna.py \
  --model real \
  --layers all \
  --prompt-addendum \
  --brief pub-01-sla-hien-hanh \
  --entry luong-quoc-khanh \
  --out runs/luna-smoke.json
```

Sau đó:

```bash
python scripts/selfeval.py --run runs/luna-smoke.json
```

Nếu còn lỗi API, `run_practice_luna.py` hiện sẽ in cả JSON error body từ OpenAI. Gửi nguyên dòng `Luna API HTTP ...` để xác định chính xác incompatibility tiếp theo.

Nếu smoke pass, test lần lượt contradiction / depth / synthesis:

```bash
python scripts/run_practice_luna.py --model real --layers all --prompt-addendum \
  --brief pub-04-lam-viec-tu-xa --out runs/luna-04.json

python scripts/run_practice_luna.py --model real --layers all --prompt-addendum \
  --brief pub-08-an-toan-boc-do --out runs/luna-08.json

python scripts/run_practice_luna.py --model real --layers all --prompt-addendum \
  --brief pub-09-so-vu-voi-doi-tac-moi --out runs/luna-09.json
```

Shadow set:

```bash
python scripts/run_practice_luna.py \
  --model real \
  --layers all \
  --prompt-addendum \
  --briefs benchmarks/shadow_hidden.json \
  --entry luong-quoc-khanh \
  --out runs/luna-shadow.json
```

## Boundary

Đây chỉ là local test infrastructure. Vòng coach chấm vẫn dùng frozen runner/model path của họ. Không commit API key. Không import shim này từ `harness/` hoặc `arena/`.
