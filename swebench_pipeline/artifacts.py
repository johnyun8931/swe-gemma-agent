"""Atomic run artifacts and strict resume compatibility."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .data import file_hash


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def tree_hash(path: Path) -> str:
    if not (path / "agent.yaml").is_file():
        raise ValueError(f"Agent directory has no agent.yaml: {path}")
    return digest({str(p.relative_to(path)): file_hash(p) for p in sorted(path.rglob("*"))
                   if p.is_file() and "__pycache__" not in p.parts})


def ensure_manifest(run_dir: Path, manifest: dict, resume: bool) -> None:
    target = run_dir / "manifest.json"
    if target.exists():
        existing = json.loads(target.read_text())
        if existing != manifest:
            changed = sorted(key for key in existing.keys() | manifest.keys()
                             if existing.get(key) != manifest.get(key))
            raise ValueError(f"Run configuration changed ({', '.join(changed)}); use a new --run-id")
        if not resume:
            raise ValueError("Run already exists; use --resume or a new --run-id")
    else:
        if run_dir.exists() and any(run_dir.iterdir()):
            raise ValueError(f"Run directory is nonempty and has no manifest: {run_dir}")
        write_json(target, manifest)


def save_attempt(run_dir: Path, record: dict, patch: str, model: str, metrics: dict) -> None:
    """One atomic record is the source of truth for both predictions and metrics."""
    write_json(run_dir / "attempts" / f"{record['instance_id']}.json", {
        "prediction": {"instance_id": record["instance_id"],
                       "model_name_or_path": model, "model_patch": patch},
        "metrics": metrics,
    })


def export_predictions(run_dir: Path, instance_ids: list[str]) -> list[dict]:
    predictions = []
    for instance_id in instance_ids:
        attempt = run_dir / "attempts" / f"{instance_id}.json"
        if attempt.exists():
            predictions.append(json.loads(attempt.read_text())["prediction"])
    path = run_dir / "predictions.jsonl"
    temporary = path.with_suffix(".jsonl.tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in predictions),
                         encoding="utf-8")
    temporary.replace(path)
    return predictions
