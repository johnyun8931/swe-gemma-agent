# Live SWE-bench validation — November 9, 2026

The pipeline now runs against a live Docker Engine and the local Gemma E4B
server. These are development-split smoke checks, not a benchmark performance
estimate. No full test-split evaluation was launched.

## Configuration

- Dataset: cached `SWE-bench/SWE-bench`, revision
  `c6fe717fd7a4c3ac1daa4055a4fd082c6a1d28a2`, `dev` split.
- Model alias: `gemma-4-e4b-it-qat-w4a16-ct`, using the configured local E4B
  Q4_0 GGUF through llama.cpp, 16,384-token context.
- Inference budgets: 10 minutes, 20 tool calls, 50 turns per instance.
- Official evaluator: `swebench==4.1.0`; prebuilt `swebench/` images.
- Gold patches were used only in separate sanity-check grading runs. They were
  not supplied to the agent.
- Each run's `manifest.json` records the complete configuration and hashes.

## Results

| Task | Agent configuration | Official outcome | Agent tool calls | Inference time |
|---|---|---|---:|---:|
| `marshmallow-code__marshmallow-1164` | Graph tools disabled | Unresolved; 802-character patch graded | 5 | 30.5 s |
| `marshmallow-code__marshmallow-1164` | Built graphs enabled | Resolved; 626-character patch graded | 4 | 31.0 s |
| `pylint-dev__astroid-1030` | Graph tools disabled | Empty exported patch; configuration edit stripped | 4 | 25.1 s |

All three agent runs completed with zero reported setup, agent, or grading
errors. Empty patches are counted in the selected-task denominator, but the
official evaluator does not run tests for them. See the policy limitation below.

The Marshmallow gold-patch control resolved 1/1 instances. The Astroid gold-patch
control also resolved 1/1 instances. An additional pydicom control failed because
of its environment, as described below; no model attempt was run for that task.

The graph-enabled run built 1,420 nodes and 2,570 edges in 3.8 seconds. Separate
checks of `get_code_neighbors`, `search_similar_code`, and `get_code_subgraph`
returned successful results using those artifacts. The agent did not invoke
graph tools during this particular attempt. The outcome difference between the
two Marshmallow runs is not evidence that graphs improved the model: this is one
sample with different available tools/prompts and sampled generation.

The built-in encoder uses deterministic token hashing for lexical similarity,
not learned semantic embeddings. Imported research embeddings remain supported.

## Artifacts

- [Marshmallow baseline summary](working/swebench/dev-smoke-3/summary.json)
- [Marshmallow baseline predictions](working/swebench/dev-smoke-3/predictions.jsonl)
- [Marshmallow gold control](working/swebench/dev-smoke-3/gold-sanity/gold-a544abb4e04a-8122d158/summary.json)
- [Graph-enabled summary](working/swebench/dev-graph-smoke/summary.json)
- [Graph-enabled predictions](working/swebench/dev-graph-smoke/predictions.jsonl)
- [Direct graph-tool checks](working/swebench/dev-graph-smoke/graph-tool-checks.json)
- [Astroid summary](working/swebench/dev-astroid-smoke/summary.json)
- [Astroid gold control](working/swebench/dev-astroid-smoke/gold-sanity/gold-bac776b1ea9c-4a3da262/summary.json)
- [Pydicom gold control](working/swebench/dev-pydicom-smoke/gold-sanity/gold-8455b92558e3-03c84d50/summary.json)

Run artifacts remain local under the git-ignored `working/` directory. Each run
also contains copied agent configuration, traces, original evaluation records,
image identities, and complete official evaluator logs/reports.

## Runtime fixes verified

1. Docker's overlay filesystem rejected moving `/testbed` to `/workspace` with
   `EXDEV`. Workspace preparation now uses `shutil.move`, including its
   cross-filesystem copy/remove fallback. A regression test simulates `EXDEV`
   while checking base-code restoration, removal of future git history, and the
   preserved `/testbed` import alias.
2. The pinned evaluator writes reports under `logs/run_evaluation`. Report
   discovery now uses its `RUN_EVALUATION_LOG_DIR` constant. Regression coverage
   verifies normal grading and gold sanity summaries.
3. A live resumed Marshmallow baseline run skipped generation, retained exactly
   one prediction/attempt, and regraded the saved patch with a fresh evaluation
   ID. The outcome remained unresolved.

All nine local regression tests pass. Docker workspace checks passed for
Marshmallow, pydicom, and Astroid. Containers created by these validation runs
were cleaned up; images are cached for reuse.

## Limitations exposed by the checks

### Configuration-file changes are filtered

For `pylint-dev__astroid-1030`, the agent edited `setup.cfg`. The inherited
competition filter removed the edit during patch submission, producing an empty
prediction. This is distinct from an agent making no changes.

The current entry point deliberately retains that policy, as recorded in the
implementation plan and manifests. Tasks requiring `setup.cfg`,
`pyproject.toml`, or other protected configuration edits cannot be evaluated
fairly as unconstrained SWE-bench attempts with this policy. Changing it requires
an explicit policy revision and new run IDs; it should not be silently mixed
with these results.

### The pydicom gold control fails a locale-sensitive test

For `pydicom__pydicom-1000`, the official image uses Python 3.6 and its default
filesystem encoding is ASCII. The gold fix passes the required regression test,
but `TestReader::test_UTF8_filename` raises `UnicodeEncodeError`. The test output
reports 95 passed and 1 failed, and the official result is unresolved because a
PASS_TO_PASS test failed.

The archived dataset evaluation script and the pinned harness's generated script
both omit a UTF-8 locale setting. This validation did not change the official
image, alter tests, or omit that failing test. No model score is reported for
this instance. Diagnose or explicitly version any environment correction before
using it in a larger experiment.

An official harness process can exit successfully even when a test environment
is unsuitable. Gold controls and the per-instance test output are necessary to
distinguish that situation from a failed model fix.

## Repeating or extending the checks

The Gemma server was left running on `http://127.0.0.1:8000` after validation.
Use new run IDs for new model attempts. For example, to try five tasks from the
test split:

```bash
.venv/bin/python run_swebench.py run --run-id test-five --split test --limit 5 --offline
```

Use `--graphs build` for the lexical graph baseline, or `--graphs import` with
your own commit-specific graph/embedding artifacts. Keep task selections, model,
budgets, and patch policy fixed when comparing configurations.
