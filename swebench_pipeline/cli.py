"""Command-line orchestration; expensive dependencies load only when needed."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import sys
import tempfile
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from . import __version__
from .artifacts import digest, ensure_manifest, export_predictions, save_attempt, tree_hash, write_json
from .data import agent_fields, file_hash, load_records, select_records
from .model_config import load_environment, resolve_model_settings


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def positive_float(value):
    number = float(value)
    if not number > 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def parser():
    import local_config as cfg

    load_environment()
    p = argparse.ArgumentParser(description="Run the agent with a local or cloud model and official SWE-bench grading.")
    p.add_argument("command", choices=("prepare", "generate", "grade", "run"))
    p.add_argument("--dataset", default="SWE-bench/SWE-bench")
    p.add_argument("--source", choices=("hub", "parquet", "disk", "json"), default="hub")
    p.add_argument("--revision", help="Hub revision; existing cached snapshot is preferred")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--split", default="dev", choices=("dev", "test", "train"))
    p.add_argument("--instance-ids", nargs="+")
    selection = p.add_mutually_exclusive_group()
    selection.add_argument("--limit", type=positive_int, default=None)
    selection.add_argument("--all", action="store_true", help="Select the complete split")
    p.add_argument("--run-id", required=True, help="Unique directory name; reuse only with --resume")
    p.add_argument("--output-dir", type=Path, default=cfg.WORKING_DIR / "swebench")
    p.add_argument("--agent-dir", type=Path, default=cfg.DATA_DIR / "sample_submission")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--retry-errors", action="store_true", help="With --resume, retry saved setup/agent errors")
    p.add_argument("--dry-run", action="store_true", help="Inspect selection/configuration without Docker or inference")
    p.add_argument("--image-mode", choices=("pull", "build"), default="pull")
    p.add_argument("--backend", choices=("local", "cloud"), default=os.environ.get("SWEBENCH_BACKEND") or "local",
                   help="Model endpoint settings to use (default: SWEBENCH_BACKEND or local)")
    p.add_argument("--model", help="Override LOCAL_MODEL/CLOUD_MODEL; local default from local_config.py")
    p.add_argument("--base-url", help="Override LOCAL_BASE_URL/CLOUD_BASE_URL (OpenAI-compatible API base)")
    p.add_argument("--health-url", help="Optional health endpoint; local defaults to /health, cloud skips this check")
    p.add_argument("--minutes", type=positive_float, default=10)
    p.add_argument("--tool-calls", type=positive_int, default=20)
    p.add_argument("--turns", type=positive_int, default=50)
    p.add_argument("--command-timeout", type=positive_int, default=300)
    p.add_argument("--grading-timeout", type=positive_int, default=1800)
    p.add_argument("--max-workers", type=positive_int, default=1, help="Official grading workers; inference is sequential")
    p.add_argument("--grading-python", default=sys.executable, help="Python executable for a separate grading environment")
    p.add_argument("--hints", action="store_true", help="Expose issue hints; disabled by default")
    p.add_argument("--graphs", choices=("off", "build", "import"), default="off")
    p.add_argument("--graph-cache", type=Path, default=cfg.WORKING_DIR / "swebench-graph-cache")
    p.add_argument("--memory", default="4g", help="Agent container memory limit")
    p.add_argument("--cpus", type=positive_int, default=2)
    p.add_argument("--min-free-gb", type=float, default=10)
    p.add_argument("--check-environment", action="store_true", help="Check workspace and patch round trip during prepare/run")
    p.add_argument("--gold-sanity", action="store_true", help="Grade gold patches separately; grade command, dev only")
    return p


def package_versions():
    result = {}
    locked = Path(__file__).resolve().parents[1] / "requirements-swebench.lock"
    names = {"swegemma", "adk-submission", "adk-eval-core", "google-adk", "litellm"}
    names.update(line.split("==")[0] for line in locked.read_text().splitlines()
                 if line and not line.startswith("#"))
    for name in sorted(names):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = "not installed"
    return result


def execute(args):
    from .agent import RUNNER_SHA256, copy_agent
    from .environment import check_harness, check_server, docker_client, environment_check, make_sandbox, prepare_image
    from .graphs import GRAPH_CONFIG, RUNTIME_REPRESENTATION, clear_runtime_caches, ensure_graphs, validate_graphs

    if Path(args.run_id).name != args.run_id or args.run_id in (".", ".."):
        raise ValueError("run-id must be a single directory name")
    if args.min_free_gb < 0:
        raise ValueError("min-free-gb cannot be negative")
    if args.gold_sanity and (args.command != "grade" or args.split != "dev"):
        raise ValueError("Use gold-sanity only with 'grade --split dev'")
    if args.command == "grade" and not args.resume:
        raise ValueError("Grading an existing run requires --resume")
    if args.retry_errors and not args.resume:
        raise ValueError("retry-errors requires --resume")
    settings = resolve_model_settings(args)
    args.model, args.base_url = settings.model, settings.base_url
    if args.command in ("generate", "run") and not args.dry_run:
        settings.require_credentials()
    args.output_dir = args.output_dir.expanduser().resolve()
    args.agent_dir = args.agent_dir.expanduser().resolve()
    args.graph_cache = args.graph_cache.expanduser().resolve()
    run_dir = args.output_dir / args.run_id
    rows, identity = load_records(dataset=args.dataset, source=args.source, split=args.split,
                                  revision=args.revision, cache_dir=args.output_dir / "dataset-cache",
                                  offline=args.offline)
    limit = args.limit if args.limit is not None else (None if args.all or args.instance_ids else 1)
    records = select_records(rows, args.instance_ids, limit)
    imported = {}
    if args.graphs == "import":
        for row in records:
            imported[row["instance_id"]] = validate_graphs(args.graph_cache, row)
    with tempfile.TemporaryDirectory(prefix="swegemma-agent-") as temporary:
        agent_copy = Path(temporary) / "agent"
        copy_agent(args.agent_dir, agent_copy, args.model, args.graphs != "off", backend=settings.backend)
        manifest = {
            "pipeline_version": __version__, "dataset": identity,
            "records_sha256": digest(records), "instance_ids": [row["instance_id"] for row in records],
            "agent_source": str(args.agent_dir), "agent_source_sha256": tree_hash(args.agent_dir),
            "agent_copy_sha256": tree_hash(agent_copy), "adapters": "disabled",
            "model": args.model, "base_url": args.base_url, "backend": settings.backend,
            "budgets": {"minutes": args.minutes, "tool_calls": args.tool_calls,
                        "turns": args.turns, "command_timeout": args.command_timeout},
            "hints": args.hints, "image_mode": args.image_mode,
            "graphs": {"mode": args.graphs, "configuration": GRAPH_CONFIG if args.graphs == "build" else imported,
                       "runtime_representation": RUNTIME_REPRESENTATION if args.graphs != "off" else None},
            "container": {"memory": args.memory, "cpus": args.cpus},
            "patch_policy": "swegemma-0.2.11-protected-files (tests and project/test configuration stripped)",
            "evaluation_policy": "official swebench 4.1.0 generated test specifications",
            "runner_sha256": RUNNER_SHA256, "packages": package_versions(),
            "pipeline_sha256": digest({p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob('*.py'))}),
        }
        if args.dry_run:
            print(json.dumps({"run_dir": str(run_dir), "manifest": manifest,
                              "tasks": [{k: row[k] for k in ("instance_id", "repo", "base_commit")} for row in records]}, indent=2))
            return 0
        check_harness()
        # Preflight before writing a manifest, so a missing daemon leaves no half-run.
        client = docker_client(args.min_free_gb, args.output_dir)
        try:
            if args.command in ("generate", "run"):
                if settings.health_url:
                    check_server(settings.health_url, api_key=settings.api_key, backend=settings.backend)
                from .agent import adapted_runner
                adapted_runner()
            ensure_manifest(run_dir, manifest, args.resume)
            target = run_dir / "agent"
            if target.exists():
                if tree_hash(target) != manifest["agent_copy_sha256"]:
                    raise ValueError("The run's copied agent changed; use a new run ID")
            else:
                shutil.copytree(agent_copy, target)
            evaluation_path = run_dir / "evaluation_tasks.json"
            if evaluation_path.exists() and digest(json.loads(evaluation_path.read_text())) != manifest["records_sha256"]:
                raise ValueError("Saved evaluation records changed; use a new run ID")
            write_json(evaluation_path, records)
            (run_dir / "agent_tasks.jsonl").write_text("".join(
                json.dumps(agent_fields(row, args.hints)) + "\n" for row in records), encoding="utf-8")

            if args.command == "grade":
                prepared_path = run_dir / "prepared.json"
                if prepared_path.exists():
                    for entry in json.loads(prepared_path.read_text()).values():
                        if client.images.get(entry["image"]).id != entry["image_id"]:
                            raise ValueError("A prepared image changed before grading; use a new run ID")
                from .grading import grade
                summary = grade(run_dir, records, manifest, python=args.grading_python,
                                max_workers=args.max_workers, timeout=args.grading_timeout, gold=args.gold_sanity)
                print(json.dumps(summary, indent=2))
                return int(bool(summary.get("grading_errors")) or
                           (args.gold_sanity and summary["resolved"] != summary["selected"]))

            prepared_path = run_dir / "prepared.json"
            prepared = json.loads(prepared_path.read_text()) if prepared_path.exists() else {}
            models = None
            if args.command in ("generate", "run"):
                from .agent import model_registry
                models = model_registry(args.model, args.base_url, api_key=settings.api_key)
            setup_failures = 0
            for index, row in enumerate(records, 1):
                key = row["instance_id"]
                attempt = run_dir / "attempts" / f"{key}.json"
                if args.command in ("generate", "run") and attempt.exists():
                    metrics = json.loads(attempt.read_text())["metrics"]
                    if not (args.retry_errors and (metrics.get("setup_error") or metrics.get("agent_error"))):
                        print(f"[{index}/{len(records)}] Resume: {key} already attempted", flush=True)
                        continue
                print(f"[{index}/{len(records)}] {key}", flush=True)
                manager = None
                started = time.monotonic()
                inference_started = None
                try:
                    image_started = time.monotonic()
                    with contextlib.chdir(run_dir):
                        image, image_id = prepare_image(client, row, args.image_mode)
                    if key in prepared and prepared[key]["image_id"] != image_id:
                        raise ValueError(f"Image changed for {key}; use a new run ID")
                    manager = make_sandbox(row, image_id, mem_limit=args.memory, cpus=args.cpus,
                                           timeout=args.command_timeout)
                    entry = {"image": image, "image_id": image_id,
                             "image_preparation_seconds": time.monotonic() - image_started}
                    if args.check_environment:
                        entry["environment_check"] = environment_check(manager)
                    if args.graphs != "off":
                        graph_started = time.monotonic()
                        entry["graphs"] = ensure_graphs(args.graph_cache, run_dir, row, args.graphs, manager)
                        entry["graph_preparation_seconds"] = time.monotonic() - graph_started
                        old = prepared.get(key, {}).get("graphs")
                        if old and old != entry["graphs"]:
                            raise ValueError("Graph artifacts changed; use a new run ID")
                    prepared[key] = entry
                    write_json(prepared_path, prepared)
                    if args.command == "prepare":
                        continue
                    from .agent import generate_patch
                    inference_started = time.monotonic()
                    patch, metrics = asyncio.run(generate_patch(row, manager, run_dir, manifest, models, index))
                    metrics.update(inference_seconds=time.monotonic() - inference_started,
                                   total_seconds=time.monotonic() - started)
                    save_attempt(run_dir, row, patch, args.model, metrics)
                    print(f"  patch_chars={len(patch)} tools={metrics['tool_calls']} error={metrics['agent_error']}", flush=True)
                except Exception as exc:
                    setup_failures += 1
                    error = f"{type(exc).__name__}: {exc}"
                    print(f"  ERROR: {error}", file=sys.stderr, flush=True)
                    if args.command == "prepare":
                        write_json(run_dir / "preparation-errors" / f"{key}.json", {"error": error})
                    else:
                        save_attempt(run_dir, row, "", args.model, {
                            "setup_error" if inference_started is None else "agent_error": error,
                            "total_seconds": time.monotonic() - started,
                            "inference_seconds": time.monotonic() - inference_started if inference_started else 0,
                        })
                finally:
                    try:
                        if manager:
                            manager.cleanup_all()
                    finally:
                        if args.graphs != "off":
                            clear_runtime_caches()
                if args.command != "prepare":
                    export_predictions(run_dir, manifest["instance_ids"])
            if args.command == "prepare":
                print(f"Prepared {len(records) - setup_failures}/{len(records)} tasks in {run_dir}")
                return int(bool(setup_failures))
            from .grading import grade, summarize
            summary = summarize(run_dir, records)
            if args.command == "run":
                summary = grade(run_dir, records, manifest, python=args.grading_python,
                                max_workers=args.max_workers, timeout=args.grading_timeout)
            print(json.dumps(summary, indent=2))
            return int(bool(summary["setup_errors"] or summary["agent_errors"] or summary["grading_errors"]))
        finally:
            client.close()


def main(argv=None):
    try:
        return execute(parser().parse_args(argv))
    except KeyboardInterrupt:
        print("Interrupted. Completed task attempts are saved; resume with the same options and --resume.", file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OSError, ImportError, PackageNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
