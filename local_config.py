"""Shared local-run configuration.

Edit MODEL_KEY to switch models. start_server.py and working/_eval_test.py both import
from here so they never drift apart.
"""
from __future__ import annotations

import glob
from pathlib import Path

# =============================================================================
# PICK YOUR MODEL HERE
# =============================================================================
# Must be one of the keys in MODEL_REGISTRY below.
#   'e4b' -> Gemma 4 E4B q4_0 GGUF (~5.1 GB, needs ~4.3 GB VRAM at 16k ctx)
#   'e2b' -> Gemma 4 E2B q4_0 GGUF (~3.4 GB, smaller/faster)
MODEL_KEY = 'e4b'

# =============================================================================
# SERVING KNOBS (llama.cpp)
# =============================================================================
HOST = '127.0.0.1'
PORT = 8000
# llama.cpp --ctx-size. 16384 fits 8 GB VRAM with flash-attn (~4.3 GB used for E4B).
MAX_MODEL_LEN = 16384
# 999 offloads all layers to the GPU; lower it if you OOM.
N_GPU_LAYERS = 999
# Seconds to wait for the server to become healthy on startup.
STARTUP_TIMEOUT = 600

# =============================================================================
# MODEL REGISTRY  (key -> how to fetch + how to name it to the harness)
# =============================================================================
# served_alias MUST be one of the four competition names the harness accepts:
#   gemma-4-e2b-it-qat-w4a16-ct, gemma-4-e4b-it-qat-w4a16-ct,
#   gemma-4-12b-it-qat-w4a16-ct, gemma-4-31b-it-qat-w4a16-ct
MODEL_REGISTRY = {
    'e4b': {
        'served_alias': 'gemma-4-e4b-it-qat-w4a16-ct',
        'hf_repo': 'google/gemma-4-E4B-it-qat-q4_0-gguf',
        'gguf_file': 'gemma-4-E4B_q4_0-it.gguf',
    },
    'e2b': {
        'served_alias': 'gemma-4-e2b-it-qat-w4a16-ct',
        'hf_repo': 'google/gemma-4-E2B-it-qat-q4_0-gguf',
        'gguf_file': 'gemma-4-E2B_q4_0-it.gguf',
    },
}

# =============================================================================
# PATHS (derived)
# =============================================================================
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / 'gemma-4-developer-agent'
WORKING_DIR = PROJECT_ROOT / 'working'
MODELS_DIR = PROJECT_ROOT / 'models'
LLAMA_DIR = PROJECT_ROOT / 'llama.cpp'


def get_model_config(key: str | None = None) -> dict:
    """Return the registry entry for MODEL_KEY (or the given key)."""
    key = key or MODEL_KEY
    if key not in MODEL_REGISTRY:
        raise KeyError(
            f'Unknown MODEL_KEY {key!r}. Choose one of: {sorted(MODEL_REGISTRY)}'
        )
    return MODEL_REGISTRY[key]


def model_dir(key: str | None = None) -> Path:
    """Local folder that holds this model's GGUF weights."""
    cfg = get_model_config(key)
    return MODELS_DIR / cfg['served_alias']


def gguf_path(key: str | None = None) -> Path:
    """Full path to the GGUF file for this model."""
    cfg = get_model_config(key)
    return model_dir(key) / cfg['gguf_file']


def base_url() -> str:
    return f'http://{HOST}:{PORT}/v1'


def health_url() -> str:
    return f'http://{HOST}:{PORT}/health'


def find_llama_server() -> Path | None:
    """Locate the llama-server binary under llama.cpp/ (any build number)."""
    matches = sorted(glob.glob(str(LLAMA_DIR / 'llama-*' / 'llama-server')))
    return Path(matches[-1]) if matches else None
