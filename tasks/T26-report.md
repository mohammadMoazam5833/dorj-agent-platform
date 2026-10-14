# T26 — Dorj Agent images readable by both models

## Implementation

- `/llmapi/{v1/,}chat/completions` now prepares image-bearing requests before streaming to LiteLLM.
- qwen3 (`NOVA_IMAGE_MODELS`) receives the image only. Every image is converted to RGB JPEG q=92 and upscaled to a 2400 px target long side, capped at 2x, matching the measured Dorj Chat normalization. No extracted OCR text is added for qwen3.
- glm and any other configured non-image model receives qwen3 extraction text instead of image bytes. The extraction prompt and “OCR” block match Dorj Chat; qwen3 is the OCR engine (`NOVA_IMAGE_TEXT_MODEL=qwen3`). PaddleOCR-VL is not called.
- OCR uses the internal LiteLLM service key, never the user key. Concurrency is 4 and timeout 60 s. The in-memory cache is SHA-256 over original image bytes, LRU 2000 entries, 24 h TTL, and stores successful results only.
- qwen image-normalization failures leave the original image in place. Text-only-model extraction failures receive `[تصویر قابل خواندن نبود]`. OCR never fails the caller request.
- Multiple image positions are handled safely; qwen sends normalized images in their original positions and glm replacements retain image labels/order.
- Prometheus: `nova_ocr_operations_total{event=hit|miss|error}` and `nova_ocr_seconds`. Logs contain no image bytes, URLs, or extracted image content.
- OpenHands accepts image input for `NOVA_VISION_MODELS=qwen3,glm`, matching `litellm_proxy/...` suffixes. The workspace deployment passes that setting through.

## Validation

- `control` unit suite: **26 passed**. Covers qwen normalization/2x upscale/no OCR text, glm image replacement, cache hit, multiple image ordering/labels, timeout fallback, streaming passthrough, and byte-identical non-image bodies.
- `python -m py_compile`, `bash -n scripts/140-deploy-nova.sh`, and `node --check e2e/agent-ocr.spec.js` pass.
- Manifest rendering was smoke-tested with the three new variables; no image tags were changed and no deployment was performed.

## Live e2e

`e2e/agent-ocr.spec.js` uses the owner-specified `/workdir/faranegar/e2e/assets/table-people-fa.png` and JSON truth. It tests glm first, then qwen3, and requires the same 50/56 cell pass rate; the checked-in table has 72 cells, so the threshold is 65/72. The owner should run it after deploying this commit.
