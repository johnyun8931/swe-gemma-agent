"""Invoke the pinned official evaluator in a fresh grading directory."""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

from .artifacts import digest, export_predictions, write_json


def evaluation_log_dir(grade_dir: Path) -> Path:
    """Use the pinned evaluator's contract rather than a path from newer docs."""
    from swebench.harness.constants import RUN_EVALUATION_LOG_DIR

    return grade_dir / RUN_EVALUATION_LOG_DIR


def summarize(run_dir: Path, records: list[dict], grade_dir: Path | None = None) -> dict:
    predictions = export_predictions(run_dir, [row["instance_id"] for row in records])
    by_id = {row["instance_id"]: row for row in predictions}
    summary = {"selected": len(records), "predictions": len(predictions),
               "empty_patches": sum(not row["model_patch"].strip() for row in predictions),
               "setup_errors": 0, "agent_errors": 0, "grading_errors": 0,
               "resolved": 0, "unresolved": 0, "graded": 0,
               "tool_calls": 0, "llm_calls": 0, "inference_seconds": 0.0,
               "image_preparation_seconds": 0.0, "graph_preparation_seconds": 0.0}
    prepared_path = run_dir / "prepared.json"
    prepared = json.loads(prepared_path.read_text()) if prepared_path.exists() else {}
    for row in records:
        for key in ("image_preparation_seconds", "graph_preparation_seconds"):
            summary[key] += prepared.get(row["instance_id"], {}).get(key, 0.0)
        path = run_dir / "attempts" / f"{row['instance_id']}.json"
        if path.exists():
            metrics = json.loads(path.read_text())["metrics"]
            summary["setup_errors"] += bool(metrics.get("setup_error"))
            summary["agent_errors"] += bool(metrics.get("agent_error"))
            for key in ("tool_calls", "llm_calls", "inference_seconds"):
                summary[key] += metrics.get(key, 0)
        if grade_dir and row["instance_id"] in by_id:
            pred = by_id[row["instance_id"]]
            report_files = list(evaluation_log_dir(grade_dir).glob(
                f"*/{pred['model_name_or_path'].replace('/', '__')}/{row['instance_id']}/report.json"))
            if report_files:
                report = json.loads(report_files[0].read_text()).get(row["instance_id"], {})
                if "resolved" not in report:
                    summary["grading_errors"] += 1
                else:
                    summary["graded"] += 1
                    summary["resolved"] += bool(report["resolved"])
                    summary["unresolved"] += not bool(report["resolved"])
            elif pred["model_patch"].strip():
                summary["grading_errors"] += 1
    summary["resolution_rate"] = summary["resolved"] / len(records)
    summary["resolution_rate_denominator"] = "all selected instances, including errors and empty patches"
    summary["grading_status"] = "completed" if grade_dir else "not_run"
    write_json((grade_dir or run_dir) / "summary.json", summary)
    return summary


def grade(run_dir: Path, records: list[dict], manifest: dict, *, python: str,
          max_workers: int, timeout: int, gold: bool = False) -> dict:
    predictions = export_predictions(run_dir, manifest["instance_ids"])
    if not gold and len(predictions) != len(records):
        raise ValueError("Generation is incomplete; generate remaining tasks before grading")
    if gold and manifest["dataset"]["split"] != "dev":
        raise ValueError("Gold sanity checks are restricted to development instances")
    if gold:
        predictions = [{"instance_id": row["instance_id"], "model_name_or_path": "gold-sanity",
                        "model_patch": row["patch"]} for row in records]
    # Every grading invocation gets a new ID, including grading modified predictions.
    evaluation_id = ("gold-" if gold else "eval-") + digest(predictions)[:12] + "-" + uuid.uuid4().hex[:8]
    grade_dir = run_dir / ("gold-sanity" if gold else "grading") / evaluation_id
    grade_dir.mkdir(parents=True)
    predictions_path = grade_dir / "predictions.jsonl"
    predictions_path.write_text("".join(json.dumps(row) + "\n" for row in predictions), encoding="utf-8")
    command = [python, "-m", "swebench.harness.run_evaluation",
               "--dataset_name", str((run_dir / "evaluation_tasks.json").resolve()),
               "--split", manifest["dataset"]["split"],
               "--predictions_path", str(predictions_path.resolve()),
               "--max_workers", str(max_workers), "--timeout", str(timeout),
               "--run_id", evaluation_id, "--namespace",
               "swebench" if manifest["image_mode"] == "pull" else "none",
               "--cache_level", "instance", "--clean", "False"]
    version_check = subprocess.run([python, "-c", "from importlib.metadata import version; "
                    "assert version('swebench') == '4.1.0', 'Install swebench==4.1.0 in grading environment'"],
                    capture_output=True, text=True)
    if version_check.returncode:
        raise RuntimeError(f"Grading Python {python} needs swebench==4.1.0. Install requirements-swebench.lock there.")
    write_json(grade_dir / "invocation.json", {"command": command, "run_id": evaluation_id})
    print(f"Official evaluation: {evaluation_id}\nLogs: {grade_dir}", flush=True)
    with (grade_dir / "harness.log").open("w") as log:
        result = subprocess.run(command, cwd=grade_dir, stdout=log, stderr=subprocess.STDOUT)
    write_json(grade_dir / "exit_status.json", {"returncode": result.returncode})
    if gold:
        reports = list(evaluation_log_dir(grade_dir).glob("*/*/*/report.json"))
        resolved = sum(bool(json.loads(path.read_text()).get(row["instance_id"], {}).get("resolved"))
                       for path in reports for row in records)
        summary = {"gold_sanity": True, "selected": len(records), "resolved": resolved,
                   "harness_returncode": result.returncode, "grading_dir": str(grade_dir)}
        write_json(grade_dir / "summary.json", summary)
    else:
        summary = summarize(run_dir, records, grade_dir)
        summary.update(harness_returncode=result.returncode, grading_dir=str(grade_dir))
        write_json(run_dir / "summary.json", summary)
    if result.returncode:
        raise RuntimeError(f"Official evaluator exited {result.returncode}; inspect {grade_dir / 'harness.log'}")
    return summary
