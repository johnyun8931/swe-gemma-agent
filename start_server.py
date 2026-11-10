#!/usr/bin/env python
"""Start the llama.cpp server for the model selected in local_config.py.

Usage:
    .venv/bin/python start_server.py            # uses MODEL_KEY from local_config.py
    .venv/bin/python start_server.py e2b        # override the model for this run

It will:
  1. resolve the model from the registry in local_config.py,
  2. download the GGUF from Hugging Face if it isn't already local,
  3. locate the prebuilt llama-server binary,
  4. launch the server in the foreground (Ctrl+C stops it).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys

import local_config as cfg


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def main() -> int:
    key = sys.argv[1] if len(sys.argv) > 1 else cfg.MODEL_KEY
    model = cfg.get_model_config(key)
    alias = model['served_alias']
    gguf = cfg.gguf_path(key)

    print(f'Model      : {key}  ->  {alias}')
    print(f'GGUF path  : {gguf}')
    print(f'Endpoint   : {cfg.base_url()}')

    if _port_in_use(cfg.HOST, cfg.PORT):
        print(
            f'\nERROR: {cfg.HOST}:{cfg.PORT} is already in use. '
            f'Stop the existing server first (e.g. pkill -f llama-server).'
        )
        return 1

    # 1. Download the GGUF if needed.
    if not gguf.is_file():
        print(f'\nGGUF not found locally; downloading {model["hf_repo"]} ...')
        try:
            from huggingface_hub import hf_hub_download
        except ImportError:
            print('ERROR: huggingface_hub is not installed in this environment.')
            return 1
        cfg.model_dir(key).mkdir(parents=True, exist_ok=True)
        downloaded = hf_hub_download(
            repo_id=model['hf_repo'],
            filename=model['gguf_file'],
            local_dir=str(cfg.model_dir(key)),
        )
        print(f'Downloaded: {downloaded}')
    else:
        print(f'GGUF found ({gguf.stat().st_size / 1e9:.2f} GB).')

    # 2. Locate the llama-server binary.
    llama_server = cfg.find_llama_server()
    if llama_server is None:
        print(
            f'\nERROR: no llama-server binary found under {cfg.LLAMA_DIR}. '
            f'See README.md ("llama.cpp (GGUF serving)") to download it.'
        )
        return 1
    print(f'Binary     : {llama_server}')

    # 3. Launch (foreground; shared libs sit next to the binary).
    env = dict(os.environ)
    env['LD_LIBRARY_PATH'] = f'{llama_server.parent}:' + env.get('LD_LIBRARY_PATH', '')
    command = [
        str(llama_server),
        '--model', str(gguf),
        '--host', cfg.HOST,
        '--port', str(cfg.PORT),
        '--alias', alias,
        '--n-gpu-layers', str(cfg.N_GPU_LAYERS),
        '--ctx-size', str(cfg.MAX_MODEL_LEN),
        '--jinja',
        '--flash-attn', 'on',
        '--parallel', '1',
    ]
    print('\nLaunching (Ctrl+C to stop):')
    print('  ' + ' '.join(command) + '\n')
    try:
        return subprocess.call(command, env=env)
    except KeyboardInterrupt:
        print('\nStopped.')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
