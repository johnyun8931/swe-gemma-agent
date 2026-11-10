"""Versioned local adaptation of the installed Gemma runner's setup boundary.

The function's code and dependencies remain upstream. A private globals dictionary
replaces only environment setup callbacks; no installed module is mutated. Fail
closed when the inspected runner source or version changes.
"""
from __future__ import annotations

import hashlib
import inspect
import os
import re
import shutil
from importlib.metadata import version
from pathlib import Path
from types import FunctionType

RUNNER_VERSION = "0.2.11"
RUNNER_SHA256 = "4d7eccabd203c26f7c39e882684d2fd851f383f6a5c76c4a75b0e559cd5d6a18"
GRAPH_TOOLS = {"get_code_neighbors", "get_code_subgraph", "search_similar_code"}


def adapted_runner():
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    from swegemma.harness import agent_runner

    if version("swegemma") != RUNNER_VERSION:
        raise RuntimeError(f"This adapter requires swegemma=={RUNNER_VERSION}")
    source = Path(inspect.getfile(agent_runner))
    if hashlib.sha256(source.read_bytes()).hexdigest() != RUNNER_SHA256:
        raise RuntimeError("Installed swegemma agent_runner differs from the reviewed source. "
                           "Review its setup/patch behavior and update the adapter checksum first.")

    def already_prepared(*args, **kwargs):
        return None

    def prepare(manager, container_id, *args, **kwargs):
        manager.prepare(container_id)

    bindings = dict(agent_runner.run_agent_sandbox.__globals__)
    for name in ("setup_container_wheels", "install_editable_package", "install_test_dependencies",
                 "setup_workspace_test_config", "setup_baseline_commit"):
        bindings[name] = already_prepared
    bindings["extract_snapshot"] = prepare
    upstream = agent_runner.run_agent_sandbox
    adapted = FunctionType(upstream.__code__, bindings, upstream.__name__, upstream.__defaults__, upstream.__closure__)
    adapted.__kwdefaults__ = upstream.__kwdefaults__
    return adapted


def copy_agent(source: Path, target: Path, model: str, graphs: bool, *, backend: str = "local") -> None:
    if target.exists():
        # The run's immutable copy is verified by its recorded hash on resume.
        return
    staging = target.with_name(target.name + ".staging")
    if staging.exists():
        raise ValueError(f"Incomplete agent copy exists: {staging}; use a new run ID")
    shutil.copytree(source, staging, ignore=shutil.ignore_patterns("__pycache__", "adapters"))
    for path in sorted(staging.rglob("*")):
        if path.suffix in (".yaml", ".yml"):
            text = path.read_text(encoding="utf-8")
            text = re.sub(r"^(\s*model:\s*).*$", lambda m: m[1] + model, text, flags=re.MULTILINE)
            text = re.sub(r"^\s*adapter:\s*[^\n]*\n?", "\n", text, flags=re.MULTILINE)
            if not graphs:
                for tool in GRAPH_TOOLS:
                    text = re.sub(r"^\s*-\s*" + tool + r"\s*$", "", text, flags=re.MULTILINE)
                # Give analyzer agents a way to search when graph tools are disabled.
                if re.search(r"^tools:\s*$", text, flags=re.MULTILINE) and not re.search(
                        r"^\s*-\s*run_command\s*$", text, flags=re.MULTILINE):
                    text = re.sub(r"^tools:\s*$", "tools:\n  - run_command", text, flags=re.MULTILINE)
            if backend == "cloud":
                # The compiler translates thinking_config into llama.cpp/vLLM
                # extra_body fields, which hosted APIs can reject. Only the run's
                # copy is changed; local Gemma retains its reasoning settings.
                import yaml

                def remove_thinking(node):
                    if isinstance(node, yaml.MappingNode):
                        node.value = [(key, value) for key, value in node.value
                                      if key.value != "thinking_config"]
                        for _, value in node.value:
                            remove_thinking(value)
                    elif isinstance(node, yaml.SequenceNode):
                        for value in node.value:
                            remove_thinking(value)

                # Work on YAML nodes to preserve the compiler's !include tags.
                document = yaml.compose(text)
                if document is not None:
                    remove_thinking(document)
                    text = yaml.serialize(document)
            path.write_text(text, encoding="utf-8")
        elif path.suffix in (".md", ".txt") and not graphs:
            text = path.read_text(encoding="utf-8")
            lines = []
            for line in text.splitlines():
                if any(tool in line for tool in GRAPH_TOOLS):
                    line = "- Use run_command to search source files (for example git grep -n 'symbol') and read_file to inspect matches."
                lines.append(line)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    staging.rename(target)


def model_registry(model: str, base_url: str, *, api_key: str | None = None):
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    import litellm
    from adk_submission import ModelRegistry
    from adk_submission.resolvers.generation import install_litellm_reasoning_patch
    from google.adk.models.lite_llm import LiteLlm

    litellm.drop_params = True
    install_litellm_reasoning_patch()
    registry = ModelRegistry()
    registry.register(model, LiteLlm(model=f"openai/{model}", api_base=base_url,
                                   api_key=api_key if api_key is not None else os.environ.get("LOCAL_API_KEY") or "EMPTY",
                                   custom_llm_provider="openai"))
    return registry


async def generate_patch(row: dict, manager, run_dir: Path, manifest: dict, models, index: int):
    from google.adk.agents.context_cache_config import ContextCacheConfig
    from google.adk.apps._configs import EventsCompactionConfig
    from swegemma.config import EvalConfig
    from swegemma.context import SwegemmaContext
    from swegemma.harness.verification import save_trace_artifact
    from swegemma.models import Task
    from .data import agent_fields

    task = Task(**agent_fields(row, manifest["hints"]))
    config = EvalConfig(
        tasks_path=run_dir / "agent_tasks.jsonl", snapshots_dir=run_dir / "unused-snapshots",
        results_dir=run_dir, submission_dir=run_dir / "agent", models=models,
        sandbox="docker", graph_dir=str(run_dir / "graphs"),
        embeddings_dir=str(run_dir / "embeddings"), display_mode="quiet",
        max_time_minutes=manifest["budgets"]["minutes"],
        max_tool_calls=manifest["budgets"]["tool_calls"],
        max_turns=manifest["budgets"]["turns"], timeout_seconds=manifest["budgets"]["command_timeout"],
        context_cache_config=ContextCacheConfig(min_tokens=2048, ttl_seconds=1800, cache_intervals=10),
        events_compaction_config=EventsCompactionConfig(compaction_interval=15, overlap_size=2,
                                                      token_threshold=14336, event_retention_size=5),
    )
    context = SwegemmaContext(docker_manager=manager, container_id="", task=task,
                             repo=task.repo, problem_statement=task.problem_statement,
                             graph_dir=config.graph_dir, embeddings_dir=config.embeddings_dir,
                             budget=config.budget, harness=config.harness)
    runner = adapted_runner()
    patch, error, trace = await runner(manager, config, task, run_dir / "unused-snapshot",
                                      task_index=index, total_tasks=len(manifest["instance_ids"]), context=context)
    trace_path = save_trace_artifact(trace, run_dir, task.instance_id)
    if trace_path is None:
        raise RuntimeError("Agent trace could not be saved")
    return patch, {"agent_error": error, "tool_calls": context.tool_calls_used,
                   "llm_calls": context.llm_calls_used, "trace": trace_path}
