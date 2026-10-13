# T24 — PaddleOCR-VL-1.6 deployment report

## Status: blocked at the mandatory Ceph capacity guard

The task requires using `scripts/47-download-hf-model.sh` and explicitly says to stop if its free-space guard refuses. That happened on the first required download attempt, before any model was written and before deployment or benchmarking.

## Model check

- Confirmed repository: `PaddlePaddle/PaddleOCR-VL-1.6`
- Main weights: `model.safetensors`, `1,917,255,968` bytes (~1.79 GiB)
- The Hugging Face model card confirms vLLM serving and links to the official vLLM PaddleOCR-VL recipe.
- The requested invocation was:
  `scripts/47-download-hf-model.sh PaddlePaddle/PaddleOCR-VL-1.6 PaddleOCR-VL-1.6 --expected-size 2Gi`

## Exact guard result

`47-download-hf-model.sh` reported:

```text
ERROR: not enough Ceph space - at 95% Ceph stops ALL writes in the cluster. Free space first (RUNBOOK §261).
  model 2 GiB = 6 GiB raw; Ceph 80.1% -> 80.2% (limit 80%); room for new model data: 0 GiB
```

Live Prometheus values at the time of the attempt were:

- `ceph_cluster_total_bytes`: `23,041,224,474,624`
- `ceph_cluster_total_used_raw_bytes`: `18,467,069,607,936`

This is ~80.15% raw usage. Adding only the model’s ~1.79 GiB as Ceph data (3× raw replica overhead) would cross the configured 80% write ceiling. The guard worked as intended.

## Actions taken after refusal

None. Per the task instruction, I did not alter `CEPH_WRITE_MAX_USAGE`, quotas, the free-space guard, or delete data to make room. No deployment, LiteLLM route, benchmark, or k8s-deploy code changes were made. The temporary download Job was not created.

## Required before resuming T24

Free at least ~6 GiB of Ceph raw capacity, then re-run the same model download command. After the download succeeds, continue with the MIG deployment, dynamic LiteLLM registration, Persian benchmark, and final report.
