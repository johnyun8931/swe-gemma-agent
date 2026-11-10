"""Focused checks for data integrity, resume, graphs, and the real ADK loop."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from swebench_pipeline.artifacts import ensure_manifest, export_predictions, save_attempt, write_json
from swebench_pipeline.data import agent_fields, load_records, normalize_record, select_records


def record(instance_id="owner__repo-1"):
    return {"instance_id": instance_id, "repo": "owner/repo", "base_commit": "a" * 40,
            "version": "1.0", "problem_statement": "Repair value()",
            "patch": "SECRET_GOLD", "test_patch": "SECRET_TEST", "eval_script": "SECRET_SCRIPT",
            "hints_text": "SECRET_HINT", "FAIL_TO_PASS": '["test_value"]', "PASS_TO_PASS": []}


class DataTests(unittest.TestCase):
    def test_normalization_selection_and_redaction(self):
        row = normalize_record(record())
        self.assertEqual(row["FAIL_TO_PASS"], ["test_value"])
        visible = agent_fields(row)
        self.assertNotIn("SECRET", json.dumps(visible))
        self.assertEqual(agent_fields(row, True)["hints_text"], "SECRET_HINT")
        selected = select_records([row, record("owner__repo-2")], ["owner__repo-2"], None)
        self.assertEqual(selected[0]["instance_id"], "owner__repo-2")
        with self.assertRaisesRegex(ValueError, "Unknown"):
            select_records([row], ["missing"], None)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            select_records([row, row], None, 1)
        with self.assertRaisesRegex(ValueError, "No tasks"):
            select_records([], None, None)

    def test_all_local_formats(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        from datasets import Dataset, DatasetDict

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "records.json", [record()])
            pq.write_table(pa.Table.from_pylist([record()]), root / "dev-00000.parquet")
            DatasetDict(dev=Dataset.from_list([record()])).save_to_disk(root / "disk")
            for source, path in (("json", root / "records.json"), ("parquet", root), ("disk", root / "disk")):
                rows, identity = load_records(dataset=str(path), source=source, split="dev",
                                              revision=None, cache_dir=root / "cache", offline=True)
                self.assertEqual(rows[0]["instance_id"], "owner__repo-1")
                self.assertEqual(identity["source"], source)


class ArtifactTests(unittest.TestCase):
    def test_resume_checks_and_atomic_attempt_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            ensure_manifest(root, {"model": "one"}, False)
            ensure_manifest(root, {"model": "one"}, True)
            with self.assertRaisesRegex(ValueError, "configuration changed"):
                ensure_manifest(root, {"model": "two"}, True)
            with self.assertRaisesRegex(ValueError, "already exists"):
                ensure_manifest(root, {"model": "one"}, False)
            save_attempt(root, record(), "", "one", {"agent_error": "empty"})
            save_attempt(root, record(), "diff\n", "one", {})
            predictions = export_predictions(root, ["owner__repo-1"])
            self.assertEqual(len(predictions), 1)
            self.assertEqual(json.loads((root / "predictions.jsonl").read_text())["model_patch"], "diff\n")

    def test_graph_disabled_agent_copy_preserves_original(self):
        from swebench_pipeline.agent import GRAPH_TOOLS, copy_agent
        import local_config as cfg

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agent"
            copy_agent(cfg.DATA_DIR / "sample_submission", target, "local-model", False)
            configs = "\n".join(p.read_text() for p in target.rglob("*.yaml"))
            self.assertIn("local-model", configs)
            self.assertNotIn("adapter:", configs)
            self.assertFalse(any(tool in configs for tool in GRAPH_TOOLS))
            self.assertIn("run_command", (target / "sub_agents/code_analyzer.yaml").read_text())
            self.assertIn("search_similar_code", (cfg.DATA_DIR / "sample_submission/agent.yaml").read_text())

    def test_summary_keeps_errors_in_denominator(self):
        from swebench_pipeline.grading import summarize

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [record(), record("owner__repo-2")]
            save_attempt(root, rows[0], "diff", "model", {"tool_calls": 3})
            save_attempt(root, rows[1], "", "model", {"setup_error": "bad environment"})
            write_json(root / "prepared.json", {rows[0]["instance_id"]: {"graph_preparation_seconds": 2.5}})
            grade_dir = root / "grading" / "one"
            write_json(grade_dir / "logs/run_evaluation/eval/model/owner__repo-1/report.json",
                       {"owner__repo-1": {"resolved": True}})
            summary = summarize(root, rows, grade_dir)
            self.assertEqual(summary["resolution_rate"], 0.5)
            self.assertEqual(summary["setup_errors"], 1)
            self.assertEqual(summary["empty_patches"], 1)
            self.assertEqual(summary["graph_preparation_seconds"], 2.5)


class GraphTests(unittest.TestCase):
    def test_built_artifacts_work_with_original_retrieval(self):
        import networkx as nx
        import numpy as np
        from swebench_pipeline.graphs import GRAPH_CONFIG, build_graph, graph_key, validate_graphs
        from swebench_pipeline.data import file_hash
        from swegemma import graph as sg

        row = record()
        graph, vectors, skipped = build_graph({"pkg.py": "def helper():\n    return 1\n\ndef caller():\n    return helper()\n"}, row)
        self.assertTrue(graph.has_edge("pkg.caller", "pkg.helper"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = graph_key(row)
            gpath, epath = root / (key + ".json"), root / (key + ".npz")
            write_json(gpath, nx.node_link_data(graph, edges="edges"))
            np.savez_compressed(epath, **vectors)
            write_json(root / (key + ".meta.json"), {
                "repo": row["repo"], "base_commit": row["base_commit"], "configuration": GRAPH_CONFIG,
                "sha256": {".json": file_hash(gpath), ".npz": file_hash(epath)},
            })
            validate_graphs(root, row)
            loaded = sg.get_graph(row["repo"], graph_dir=str(root), embeddings_dir=str(root), base_commit=row["base_commit"])
            self.assertIn("pkg.helper", loaded)
            results = sg.get_similar_nodes(node="pkg.caller", repo_name=row["repo"], k=1,
                                          graph=loaded, graph_dir=str(root), embeddings_dir=str(root), base_commit=row["base_commit"])
            self.assertTrue(results)
            from unittest.mock import patch
            from swebench_pipeline.graphs import ensure_graphs

            runtime = root / "run"
            metadata = ensure_graphs(root, runtime, row, "import")
            self.assertEqual(metadata["runtime_representation"], "inline-float32-v1")
            # The native loader must consume embedded vectors directly, even
            # when its symbol-lookup cache cannot supply any embeddings.
            with patch('swegemma.graph.embedding_utils.embed', side_effect=AssertionError('Unexpected vector lookup')):
                hydrated = sg.get_graph(row["repo"], graph_dir=str(runtime / "graphs"),
                                        embeddings_dir=str(runtime / "embeddings"), base_commit=row["base_commit"])
                hydrated_results = sg.get_similar_nodes(node="pkg.caller", repo_name=row["repo"], k=1,
                    graph=hydrated, graph_dir=str(runtime / "graphs"),
                    embeddings_dir=str(runtime / "embeddings"), base_commit=row["base_commit"])
            self.assertEqual(hydrated_results[0]['node_name'], results[0]['node_name'])
            self.assertAlmostEqual(hydrated_results[0]['similarity'], results[0]['similarity'])
            with self.assertRaisesRegex(ValueError, "Graph commit mismatch"):
                meta = json.loads((root / (key + ".meta.json")).read_text())
                meta["base_commit"] = "b" * 40
                write_json(root / (key + ".meta.json"), meta)
                validate_graphs(root, row)


class GradingTests(unittest.TestCase):
    def test_grading_uses_selected_records_and_unique_runs(self):
        from unittest.mock import patch
        from swebench_pipeline.grading import grade

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = normalize_record(record())
            write_json(root / 'evaluation_tasks.json', [row])
            save_attempt(root, row, 'diff', 'model', {})
            manifest = {'instance_ids': [row['instance_id']], 'image_mode': 'pull',
                        'dataset': {'split': 'dev'}}
            calls = []

            def evaluator(command, **kwargs):
                if '-m' in command:
                    calls.append(command)
                    run_id = command[command.index('--run_id') + 1]
                    predictions_file = Path(command[command.index('--predictions_path') + 1])
                    model = json.loads(predictions_file.read_text().splitlines()[0])['model_name_or_path']
                    write_json(kwargs['cwd'] / 'logs/run_evaluation' / run_id / model / row['instance_id'] / 'report.json',
                               {row['instance_id']: {'resolved': True}})
                return subprocess.CompletedProcess(command, 0)

            with patch('swebench_pipeline.grading.subprocess.run', side_effect=evaluator):
                first = grade(root, [row], manifest, python='python', max_workers=1, timeout=30)
                second = grade(root, [row], manifest, python='python', max_workers=1, timeout=30)
                gold = grade(root, [row], manifest, python='python', max_workers=1, timeout=30, gold=True)
            self.assertEqual(first['resolved'], 1)
            self.assertEqual(gold['resolved'], 1)
            self.assertEqual(json.loads((root / 'predictions.jsonl').read_text())['model_name_or_path'], 'model')
            self.assertNotEqual(first['grading_dir'], second['grading_dir'])
            self.assertEqual(calls[0][calls[0].index('--dataset_name') + 1], str(root / 'evaluation_tasks.json'))
            self.assertEqual(calls[0][calls[0].index('--namespace') + 1], 'swebench')


class EnvironmentTests(unittest.TestCase):
    def test_workspace_preparation_removes_future_history_and_preserves_import_alias(self):
        from swebench_pipeline.environment import workspace_setup_script

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository, workspace = root / 'original repo', root / 'workspace'
            repository.mkdir()

            def git(*args):
                return subprocess.check_output(['git', '-C', str(repository), *args], text=True).strip()

            git('init', '-q')
            git('config', 'user.email', 'test@localhost')
            git('config', 'user.name', 'Test')
            (repository / 'module.py').write_text('BASE\n')
            git('add', '.')
            git('commit', '-qm', 'base')
            base = git('rev-parse', 'HEAD')
            (repository / 'module.py').write_text('GOLD_FIX\n')
            git('commit', '-qam', 'future fix')
            future = git('rev-parse', 'HEAD')
            (repository / 'hidden.patch').write_text('SECRET')
            outside_script = root / 'eval.sh'
            outside_script.write_text('SECRET')
            script = workspace_setup_script(base, repository=str(repository), workspace=str(workspace),
                                            scrub_paths=[str(outside_script)])
            # Docker overlayfs may reject this directory rename with EXDEV.
            # Exercise the actual administrative script's copy/remove fallback.
            script = ("import os, errno\n"
                      "original_rename = os.rename\n"
                      "def cross_device_rename(src, dst, *args, **kwargs):\n"
                      f"    if str(src) == {str(repository)!r} and str(dst) == {str(workspace)!r}:\n"
                      "        raise OSError(errno.EXDEV, 'Invalid cross-device link')\n"
                      "    return original_rename(src, dst, *args, **kwargs)\n"
                      "os.rename = cross_device_rename\n" + script)
            subprocess.run([sys.executable, '-c', script], check=True, capture_output=True)
            self.assertTrue(repository.is_symlink())
            self.assertFalse(workspace.is_symlink())
            self.assertEqual(repository.resolve(), workspace)
            self.assertEqual((repository / 'module.py').read_text(), 'BASE\n')
            self.assertFalse((workspace / 'hidden.patch').exists())
            self.assertFalse(outside_script.exists())
            unreachable = subprocess.run(['git', '-C', str(workspace), 'cat-file', '-e', future], capture_output=True)
            self.assertNotEqual(unreachable.returncode, 0)


class RunnerIntegrationTests(unittest.TestCase):
    def test_original_loop_tools_and_patch_capture_with_scripted_model(self):
        """Run the real ADK/compiler/runner against a local test sandbox, no API calls."""
        from adk_submission import ModelRegistry
        from google.adk.models.base_llm import BaseLlm
        from google.adk.models.llm_response import LlmResponse
        from google.genai import types
        from pydantic import PrivateAttr
        from swegemma.config import EvalConfig
        from swegemma.models import Task
        from swegemma.sandbox import ContainerManager, ExecResult
        from swebench_pipeline.agent import adapted_runner
        from swegemma.harness import agent_runner

        class ScriptedModel(BaseLlm):
            _calls: int = PrivateAttr(default=0)

            async def generate_content_async(self, llm_request, stream=False):
                self._calls += 1
                if self._calls == 1:
                    part = types.Part(function_call=types.FunctionCall(name="run_command", args={
                        "command": "sed -i 's/return 1/return 2/' module.py"}))
                elif self._calls == 2:
                    part = types.Part(function_call=types.FunctionCall(name="submit_patch", args={}))
                else:
                    part = types.Part(text="Completed.")
                yield LlmResponse(content=types.Content(role="model", parts=[part]))

        class LocalSandbox(ContainerManager):
            def __init__(self, root):
                super().__init__()
                self.root = root
                self.prepared = False
                self.stopped = False

            def start(self):
                return "local"

            def prepare(self, cid):
                self.prepared = True
                (self.root / "module.py").write_text("def value(): return 1\n")
                for command in (["git", "init", "-q"], ["git", "config", "user.email", "test@localhost"],
                                ["git", "config", "user.name", "Test"], ["git", "add", "."],
                                ["git", "commit", "-qm", "baseline"], ["git", "tag", "_swegemma_baseline"]):
                    subprocess.run(command, cwd=self.root, check=True)

            def exec(self, cid, command, *, timeout=None):
                command = command.replace("/workspace", str(self.root))
                result = subprocess.run(command, shell=True, executable="/bin/bash", cwd=self.root,
                                        capture_output=True, text=True, timeout=timeout)
                return ExecResult(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)

            def stop(self, cid):
                self.stopped = True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            submission = root / "agent"
            submission.mkdir()
            (submission / "agent.yaml").write_text(
                "name: test_agent\nmodel: scripted\ninstruction: Repair value then submit.\ntools:\n  - run_command\n  - submit_patch\n")
            models = ModelRegistry()
            models.register("scripted", ScriptedModel(model="scripted"))
            config = EvalConfig(tasks_path=root / "unused", snapshots_dir=root / "unused-snapshots",
                                results_dir=root / "results", submission_dir=submission, models=models,
                                max_time_minutes=1, max_tool_calls=5, max_turns=6, display_mode="quiet")
            sandbox = LocalSandbox(workspace)
            upstream_extract = agent_runner.extract_snapshot
            runner = adapted_runner()
            async def bounded_run():
                task = asyncio.create_task(runner(sandbox, config, Task(**agent_fields(record())), root / "unused"))
                try:
                    return await asyncio.wait_for(asyncio.shield(task), timeout=10)
                except TimeoutError:
                    waiting = task.get_coro()
                    stack = []
                    while waiting is not None:
                        stack.append(str(waiting))
                        waiting = getattr(waiting, 'cr_await', getattr(waiting, 'gi_yieldfrom', None))
                    task.cancel()
                    raise AssertionError('Runner timed out: ' + '\n'.join(stack))
            patch, error, trace = asyncio.run(bounded_run())
            self.assertTrue(sandbox.prepared)
            self.assertTrue(sandbox.stopped)
            self.assertIs(agent_runner.extract_snapshot, upstream_extract)
            self.assertIsNone(error, error)
            self.assertIn("+def value(): return 2", patch)
            subprocess.run(["git", "reset", "--hard", "_swegemma_baseline"], cwd=workspace, check=True, capture_output=True)
            subprocess.run(["git", "apply", "--check", "-"], input=patch, cwd=workspace, text=True, check=True)


if __name__ == "__main__":
    unittest.main()
