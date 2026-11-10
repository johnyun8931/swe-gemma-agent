"""Load evaluation records without exposing grading information to the agent."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_record(row: dict) -> dict:
    row = dict(row)
    for key in ("instance_id", "repo", "base_commit", "problem_statement", "version"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError(f"Missing/invalid {key} in {row.get('instance_id', 'task')}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", row["instance_id"]):
        raise ValueError("instance_id must be a safe filename component")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", row["repo"]):
        raise ValueError(f"Invalid repository: {row['repo']}")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", row["base_commit"]):
        raise ValueError(f"Invalid base commit: {row['base_commit']}")
    for key in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        value = row.get(key, [])
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ValueError(f"{key} must contain a list of test names")
        row[key] = value
    for key in ("patch", "test_patch", "hints_text"):
        if row.get(key) is None:
            row[key] = ""
    return row


def agent_fields(row: dict, hints: bool = False) -> dict:
    """Explicit allowlist: never pass evaluation scripts or answers to Task."""
    result = {key: row[key] for key in (
        "instance_id", "repo", "base_commit", "problem_statement"
    )}
    if hints:
        result["hints_text"] = row.get("hints_text", "")
    return result


def select_records(rows: list[dict], instance_ids: list[str] | None, limit: int | None) -> list[dict]:
    normalized = [normalize_record(row) for row in rows]
    by_id = {row["instance_id"]: row for row in normalized}
    if len(by_id) != len(normalized):
        raise ValueError("Dataset contains duplicate instance IDs")
    if instance_ids:
        if len(set(instance_ids)) != len(instance_ids):
            raise ValueError("Repeated instance IDs in selection")
        missing = set(instance_ids) - by_id.keys()
        if missing:
            raise ValueError(f"Unknown instance IDs: {', '.join(sorted(missing))}")
        normalized = [by_id[key] for key in instance_ids]
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive (use --all for the complete split)")
        normalized = normalized[:limit]
    if not normalized:
        raise ValueError("No tasks selected")
    return normalized


def cached_snapshot(dataset: str, revision: str | None) -> Path | None:
    from huggingface_hub.constants import HF_HUB_CACHE

    root = Path(HF_HUB_CACHE) / ("datasets--" + dataset.replace("/", "--"))
    ref = revision or "main"
    ref_file = root / "refs" / ref
    if ref_file.is_file():
        ref = ref_file.read_text().strip()
    candidate = root / "snapshots" / ref
    return candidate if candidate.is_dir() else None


def load_records(*, dataset: str, source: str, split: str, revision: str | None,
                 cache_dir: Path, offline: bool = False) -> tuple[list[dict], dict]:
    """Prefer existing Hub Parquet files; don't rebuild caches in the user's home."""
    path = Path(dataset).expanduser()
    snapshot = cached_snapshot(dataset, revision) if source == "hub" else None
    if source == "parquet" or snapshot is not None:
        path = snapshot or path
        files = [path] if path.is_file() else sorted(path.glob(f"data/{split}-*.parquet"))
        if not files and path.is_dir():
            files = sorted(path.glob(f"{split}*.parquet"))
        if not files:
            raise ValueError(f"No {split} Parquet files in {path}")
        import pyarrow.parquet as pq

        rows = []
        for file in files:
            rows.extend(pq.read_table(file).to_pylist())
        identity = {"source": source, "dataset": dataset, "split": split,
                    "revision": snapshot.name if snapshot else revision,
                    "files": {str(file.resolve()): file_hash(file) for file in files}}
        return rows, identity
    if source == "json":
        content = path.read_text(encoding="utf-8")
        rows = json.loads(content) if path.suffix == ".json" else [
            json.loads(line) for line in content.splitlines() if line.strip()]
        if not isinstance(rows, list):
            raise ValueError("JSON dataset must contain an array of task records")
        return rows, {"source": source, "dataset": str(path.resolve()), "split": split,
                      "sha256": file_hash(path)}
    from datasets import DatasetDict, load_dataset, load_from_disk

    if source == "disk":
        ds = load_from_disk(str(path))
        if isinstance(ds, DatasetDict):
            ds = ds[split]
    else:
        if offline:
            raise ValueError("No cached Hub snapshot found; use --source parquet or --source disk")
        ds = load_dataset(dataset, split=split, revision=revision, cache_dir=str(cache_dir))
    return list(ds), {"source": source, "dataset": dataset, "split": split,
                      "revision": revision, "fingerprint": ds._fingerprint}
