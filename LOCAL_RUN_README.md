# Running the Gemma 4 Developer Agent locally

This folder contains a local port of the Kaggle starter notebook:

- **`gemma-4-developer-agent-local.ipynb`** — the ported notebook (run this one).
- `getting-started-gemma-4-developer-agent.ipynb` — the original Kaggle notebook (left untouched for reference).
- `gemma-4-developer-agent/` — the competition data (tasks, snapshots, graphs, embeddings, wheels, sample_submission).
- `gemma-4-developer-agent-wheelhouse/` — all `.whl` files for an offline install (includes `swegemma`, `adk_submission`, `adk_eval_core`, `vllm`, `torch`, `google-adk`, ...).

## Hard environment requirements

The vLLM wheel in the wheelhouse is `vllm-0.19.1-cp38-abi3-manylinux_2_31_x86_64.whl`:

- **OS:** Linux x86_64 (native Linux **or** WSL2 on Windows). vLLM does not run on native Windows.
- **GPU:** an NVIDIA CUDA GPU. The CPU-only steps (cells 0–3) run anywhere, but cells 4–6 need the GPU.
- **Python:** 3.12 (the wheelhouse ships cp312 binary wheels). Create a clean venv with 3.12.

A Windows laptop with no GPU (like the machine this was prepared on) can only run cells 0–3.

## Steps on the GPU machine

1. Copy this whole project folder over, keeping the three subfolders side by side:
   `gemma-4-developer-agent/`, `gemma-4-developer-agent-wheelhouse/`, and the notebook.

2. Create and activate a Python 3.12 virtual environment, then install Jupyter:
   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   pip install jupyter   # only jupyter from the internet; everything else is offline
   ```

3. Download the model weights for your chosen `MODEL_NAME` into a local folder (HF format:
   `config.json`, `*.safetensors`, tokenizer files). Point `MODEL_PATH` in the notebook's
   CONFIG cell at that folder. Start with the smallest model: `gemma-4-e2b-it-qat-w4a16-ct`.

4. Open `gemma-4-developer-agent-local.ipynb`, edit the **CONFIG cell (section 0)** if the
   auto-detected paths are wrong, then run top to bottom.

## What each cell does

| Cell | Needs GPU? | Purpose |
|------|-----------|---------|
| 0 Config | no | Set local paths + model choice. |
| 1 Install | no | Offline `pip install` from the wheelhouse. |
| 2 Data | no | Load tasks, copy & retarget `sample_submission` to your model. |
| 3 Graph | no | Smoke-test the code graph / embeddings (good first check). |
| 4 vLLM server | **yes** | Launch vLLM on `127.0.0.1:8000`, register with ADK. |
| 5 Evaluate | **yes** | Run agent (Phase 1) + verification (Phase 2) with `sandbox='subprocess'`. |
| 6 Package | no | Zip the submission and validate it. |

## Tuning knobs (CONFIG cell)

- `MODEL_NAME` — pick the smallest that fits your VRAM first.
- `GPU_MEMORY_UTILIZATION` — lower (e.g. `0.80`) if vLLM OOMs at startup.
- `MAX_MODEL_LEN` — lower (e.g. `8192`) to shrink the KV cache on small GPUs.
- `NUM_SAMPLE_TASKS` — keep at `1` for the first end-to-end run.

## Honest caveats / likely first-run issues

These depend on the actual machine and can't be verified until you're on the GPU box:

- **Model availability.** "Gemma 4" and the `*-qat-w4a16-ct` names are competition-internal.
  You must have real weights for the chosen `MODEL_NAME` to download in step 3. If you only
  have a different real Gemma (e.g. Gemma 2/3), vLLM can still serve it, but the agent YAML
  and `ALLOWED_MODEL_NAMES` check in cell 4 expect one of the four competition names — you'd
  need to adjust those.
- **CUDA / driver match.** The prebuilt `vllm`/`torch` wheels assume a compatible CUDA driver.
  If startup fails with a CUDA error, the driver version is the first thing to check.
- **VRAM.** Even the smallest quantized model plus a 32k-token KV cache needs a fair amount of
  VRAM. Reduce `MAX_MODEL_LEN` and `GPU_MEMORY_UTILIZATION` if you hit OOM.
- **12B model** needs the `--hf-overrides` flag (already wired in `MODEL_VLLM_EXTRA_ARGS`).
