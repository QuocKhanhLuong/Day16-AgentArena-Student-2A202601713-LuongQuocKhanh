# GPT-5.6 Luna — Local Real-Model Test Path

## Vì sao bỏ proxy

Lần thử qua `scripts/luna_compat_proxy.py` trả HTTP 401. Proxy không phải một phần của Lab 16 và cũng không cần thiết để test middleware, nên đã bỏ để giảm thêm một tầng mạng và tránh nhầm lẫn giữa lỗi agent với lỗi test infrastructure.

## Lỗi API thực sự đã xác nhận

Gọi trực tiếp OpenAI bằng `gpt-5.6-luna` cho thấy request tới đúng endpoint nhưng bị HTTP 400 vì frozen `arena/model.py` gửi trường legacy:

```text
max_tokens
```

Luna yêu cầu:

```text
max_completion_tokens
```

Điều này chứng minh API key/base URL/model ID đều đã đi tới OpenAI; lỗi là compatibility của payload.

## Cách test mới: in-process compatibility shim

Không sửa `arena/model.py` vì đây là instructor-owned/frozen code. Thay vào đó dùng:

```text
scripts/run_practice_luna.py
```

Script này vẫn gọi chính `scripts.run_practice.main()` và giữ nguyên runner, scorer, tools, trace, briefs, middleware và `RealModel.complete()`.

Nó chỉ patch `RealModel._post()` trong process hiện tại để đổi đúng một field ở payload trước khi request đi ra mạng:

```text
max_tokens -> max_completion_tokens
```

Patch biến mất ngay khi process kết thúc. Không proxy, không localhost HTTP hop, không sửa prompt, không sửa response, không sửa report, không sửa score.

### Vì sao đổi tên thay vì bỏ hẳn giới hạn token

Bỏ hoàn toàn `max_tokens` sẽ làm Luna dùng giới hạn output mặc định của API và khiến local test lệch thêm khỏi budget mà frozen lab muốn áp. Đổi sang `max_completion_tokens` giữ ý nghĩa của giới hạn gần nhất có thể với frozen client, đồng thời tương thích Luna.

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
