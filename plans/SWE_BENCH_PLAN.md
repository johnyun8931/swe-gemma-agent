# Plan: evaluate the Gemma agent on SWE-bench

Add a SWE-bench entry point that reuses the existing Gemma model server, ADK agent
configuration, tools, budgets, and traces. Prepare each task in its SWE-bench
environment, export the agent's patch, and use the official SWE-bench harness for
grading. Keep the existing Kaggle evaluation available.

Implementation is provided by `run_swebench.py` and `swebench_pipeline/`.
See `SWEBENCH_README.md` for setup, commands, graph modes, and limitations.
Live Docker environment checks, gold sanity grading, real model evaluation,
graph-tool checks, and resume validation have now run. See
`SWEBENCH_VALIDATION.md` for results and remaining limitations. These development
smoke checks do not establish a full benchmark score.

## 1. Establish dependencies and the integration boundary

- Add a documented, reproducible dependency setup for `datasets` and a tested
  version of `swebench`. Check compatibility with the installed ADK and
  `swegemma` packages; use a separate grading environment if their dependencies
  conflict.
- Check Docker availability and daemon access, the model server's health, and
  available disk space before an execution run. Report which requirement is
  missing with a concrete setup instruction.
- Inspect the pinned SWE-bench version's environment APIs and the cached dataset's
  schema. The local dataset includes `image`, `eval_script`, and `log_parser` in
  addition to the standard task fields; retain these for grading compatibility.
- Resolve how to reuse the agent loop before implementing batch execution.
  `swegemma.harness.agent_runner.run_agent_sandbox()` currently performs Kaggle
  setup internally: wheel installation, snapshot extraction, editable installation,
  and test configuration. Merely selecting a different Docker image is insufficient.
- Prefer a supported environment preparation hook. If none exists, introduce a
  small, versioned local adaptation with an explicit preparation boundary. Avoid
  edits inside `.venv` or global runtime monkeypatching.

Acceptance: one task's official environment can be prepared and the reusable agent
entry point is identified, including the exact changes needed to bypass Kaggle setup.

## 2. Add task loading and reproducible selection

- Add a tracked `swebench_pipeline/` package and root `run_swebench.py` CLI.
- Default to `SWE-bench/SWE-bench`; support a Hub cache, locally downloaded Parquet
  files, and a directory produced by `datasets.save_to_disk()` through explicit
  loader options.
- Support `--split`, `--instance-ids`, `--limit`, and an output directory.
  Default the first smoke run to one development task; select `test` explicitly
  for benchmark measurements.
- Map the agent-facing fields to `swegemma.models.Task`, while retaining complete
  evaluation records separately. Normalize test lists whether stored as JSON
  strings or arrays.
- Exclude gold patches, hidden test patches, grading scripts, and expected test
  outcomes from agent inputs. Leave optional issue hints off by default and record
  whether hints were enabled.
- Save the dataset revision or local file hashes and selected instance IDs in a
  run manifest. Reject missing IDs and an empty selection.

Acceptance: a dry run lists exactly the selected tasks using the local download,
without starting Docker or inference.

## 3. Prepare per-instance SWE-bench environments

- Use the official harness's compatible image preparation path, pulling an
  appropriate prebuilt image or building it as required by the pinned version.
- Start each agent in a clean repository at its exact `base_commit`, with the
  required Python environment active for every tool command.
- Bridge SWE-bench's `/testbed` repository location to the existing tools'
  `/workspace` expectation without breaking editable imports or test paths.
- Establish a clean patch baseline and ensure future commits, grading artifacts,
  and gold changes are unavailable to the agent. Verify what prebuilt images
  contain before exposing them to the agent.
- Bypass Kaggle wheels, dependency installation, and replacement test configs.
  Preserve each repository's intended test configuration.
- Clean up the task container on completion, timeout, or interruption; cache
  reusable images separately from task working state.

Acceptance: a known, harmless source edit produces a patch that applies to a fresh
checkout at `base_commit`, and targeted repository tests use the intended environment.

## 4. Run the existing agent and persist predictions

- Reuse model selection and endpoint configuration from `local_config.py`.
- Accept `--agent-dir` and copy the selected agent configuration into the run's
  artifacts before retargeting model aliases. Preserve custom agent changes.
- Reuse the tool loop, tool budgets, timeout handling, context compaction, and
  trace capture. Expose time, tool-call, and turn limits as CLI options.
- Initially disable graph-dependent tools in both root and analyzer agents and
  adjust their copied prompts to use source search. Record this as a baseline
  with graph tools disabled; it does not measure the full graph-assisted framework.
- Inspect and document the existing harness's protected-file patch filtering,
  including restrictions on project configuration changes that could affect
  SWE-bench results. Record the policy in the manifest.
- Write official-format `predictions.jsonl` records containing `instance_id`,
  `model_name_or_path`, and `model_patch`. Retain empty patches as empty strings
  and store setup errors, agent errors, and traces separately.
- Save after each task and support resume using the manifest. Refuse to reuse
  predictions when task selection, agent configuration, model, or budgets differ.

Acceptance: one actual inference attempt produces a valid prediction and trace;
interrupting and resuming does not duplicate completed instances.

## 5. Automate official grading and reporting

- Provide `prepare`, `generate`, `grade`, and `run` commands. `run` performs the
  complete sequence; `grade` reuses existing predictions without model inference.
- Pass the same selected evaluation records and split to the official evaluator,
  preserving compatibility with the recorded dataset revision.
- Grade in fresh containers using the official test scripts and result parsing.
  Use a unique evaluation run ID for changed predictions to avoid stale results.
- Report selected instances, predictions, empty patches, setup/agent/grading
  errors, resolved and unresolved instances, tool calls, and inference duration.
  State the resolution-rate denominator and retain failed attempts in the total
  selected-task count.
- Store manifests, copied agent configs, predictions, traces, grading logs, and
  summary files under `working/swebench/<run_id>/`.

Acceptance: the end-to-end command returns an official grading report whose
instance IDs match the selection and predictions.

## 6. Validate before scaling

- Add focused tests for supported local dataset formats, field normalization,
  task selection, agent-input redaction, prediction serialization, and resume
  compatibility checks.
- Run an environment-only check and an official gold-patch grading sanity check
  on one development instance. Keep gold-patch artifacts separate from agent runs.
- Run one real agent attempt through generation and official grading. A passing
  infrastructure check does not require the model to solve the issue.
- Run a small fixed development sample across multiple repositories to expose
  environment differences before a larger test-split evaluation.
- Verify that the existing Kaggle entry point still starts with its current
  configuration, then document setup and example commands in `README.md`.

Acceptance: a failed solve is distinguishable from an infrastructure failure,
and both Kaggle and SWE-bench execution paths remain usable.

## 7. Restore graph-assisted evaluation

- Build or import graphs and embeddings from each selected repository at its
  exact base commit. Cache by repository, commit, and graph/embedding configuration.
- Validate graph lookup and semantic search before enabling the graph tools in
  root and analyzer agents.
- Compare graph-disabled and graph-enabled runs on the same fixed task selection,
  model, and inference budgets. Record graph preparation costs separately.

Acceptance: full-framework measurements identify the graph and embedding versions
and can be compared with the initial baseline.

Implementation order: complete steps 1–6 for a working baseline, then step 7 for
the full graph-assisted framework.

References:

- [Official SWE-bench evaluation guide](https://www.swebench.com/SWE-bench/guides/evaluation/)
- [SWE-bench dataset](https://huggingface.co/datasets/SWE-bench/SWE-bench)
