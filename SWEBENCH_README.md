# Running the Gemma agent on SWE-bench

`run_swebench.py` uses a local llama.cpp or cloud endpoint and the Gemma ADK agent loop,
prepares SWE-bench Docker environments, and exports patches for the official
SWE-bench evaluator. The existing Kaggle notebook and `working/_eval_test.py`
remain available.

## Setup

Run setup commands from the repository root. Download the developer-agent
competition data and wheelhouse first if they are not already present.
Install the [Kaggle CLI and configure authentication](https://github.com/Kaggle/kaggle-cli/blob/main/docs/README.md),
and join the [competition](https://www.kaggle.com/competitions/gemma-4-developer-agent)
and accept its rules before downloading competition files:

```bash
uv tool install kaggle
```

Download and extract the developer-agent competition data:

```bash
kaggle competitions download -c gemma-4-developer-agent \
  -p gemma-4-developer-agent --unzip
```

Download and extract the wheelhouse:

```bash
kaggle datasets download metric/gemma-4-developer-agent-wheelhouse \
  -p gemma-4-developer-agent-wheelhouse --unzip
```

The `-p` options place the files in the directories used by this project, and
`--unzip` extracts the downloaded archives. Skip either download when its files
are already available. The expected layout is:

```text
gemma-4-developer-agent/
  tasks.jsonl
  sample_submission/agent.yaml
  snapshots/
  graphs/
  embeddings/
  wheels/
gemma-4-developer-agent-wheelhouse/
  gemma-4-developer-agent-wheelhouse/
    *.whl
```

The competition data supplies the agent template and harness resources; the
wheelhouse supplies packages such as `swegemma` and `adk_submission`. Both
download directories are ignored by Git. For a fresh Python environment, follow
the [one-time local setup](README.md#one-time-setup) to install the wheelhouse
and its runtime dependencies before adding the SWE-bench dependencies below.

Use the project's existing Python 3.12 environment with the competition harness
installed. Add the SWE-bench dependencies locked to the validated versions:

```bash
uv pip install --python .venv/bin/python -r requirements-swebench.lock
```

Alternatively, use `.venv/bin/python -m pip install -r requirements-swebench.lock`
if pip is installed in that environment. The runner adapter requires the reviewed
`swegemma==0.2.11` source; its SHA-256 is checked before inference. A different
wheel or a locally edited runner requires reviewing the adapter before changing
the checksum in `swebench_pipeline/agent.py`.
`requirements-swebench.txt` contains the direct dependencies; the lock file pins
their transitive dependencies for Linux/Python 3.12. Existing optional competition
packages are outside this lock.

Download the [SWE-bench/SWE-bench dataset](https://huggingface.co/datasets/SWE-bench/SWE-bench)
from Hugging Face before using the `--offline` examples below:

```bash
.venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download

snapshot = snapshot_download(
    repo_id="SWE-bench/SWE-bench",
    repo_type="dataset",
    allow_patterns="data/*.parquet",
)
print(f"SWE-bench dataset ready at: {snapshot}")
PY
```

This downloads the available `dev` and `test` Parquet files into the standard
Hugging Face cache. It reuses files already downloaded, so the command can be
rerun without duplicating the dataset. The pipeline reads this cache directly;
no `--dataset` override is needed because `SWE-bench/SWE-bench` is the default.
This step needs internet access to check the Hub, but does not need Docker, a
model server, or a cloud model API key. See the
[Hugging Face download guide](https://huggingface.co/docs/huggingface_hub/guides/download)
for cache and download options.

Install and start [Docker Engine](https://docs.docker.com/engine/install/) and
ensure your user can access its daemon. Configure `DOCKER_HOST` for a remote or
rootless daemon. This entry point currently targets Linux x86_64 hosts.
Check the daemon's image filesystem as well as your output
filesystem for free space; image requirements vary by repository and accumulate
across tasks. The CLI checks for at least 10 GiB free on the output filesystem;
this is a minimum preflight threshold, not an estimate for the complete benchmark.

The host needs network access to pull/build images and, for some repositories,
fetch requirements while constructing official test specifications. `--offline`
only disables dataset downloads. Agent tool containers have networking disabled.

## First run

Inspect one development task from the existing Hugging Face cache, without
starting Docker or the model server:

```bash
.venv/bin/python run_swebench.py prepare --run-id dev-smoke --offline --dry-run
```

The default loader reads cached Parquet files directly. It does not duplicate the
download or build a new dataset cache in your home directory. The selected
revision and file checksums appear in the dry-run output.

Prepare its image and check environment activation and patch round-trip behavior:

```bash
.venv/bin/python run_swebench.py prepare --run-id dev-smoke --offline --check-environment
```

Run a gold-patch grading sanity check in a separate artifact directory:

```bash
.venv/bin/python run_swebench.py grade --run-id dev-smoke --offline --resume --gold-sanity
```

Gold sanity checks are restricted to `dev`. They do not create agent predictions
or enter the agent-resolution summary.

Start the configured model server in another terminal:

```bash
.venv/bin/python start_server.py
```

Generate a patch and grade it:

```bash
.venv/bin/python run_swebench.py run --run-id dev-smoke --offline --resume
```

`run` prepares missing images, generates patches, and grades them. A model may
produce an empty or incorrect patch; that is distinct from a setup or grading
error. Infrastructure errors cause a nonzero CLI exit status. Inspect the saved
logs and summary rather than treating process exit alone as the model's score.

## Local and cloud models

The CLI automatically loads `.env` from the project root, including when invoked
from another directory. Existing shell variables take precedence. The `.env`
file is ignored by Git; `.env.example` is the shareable template. If `.env` does
not exist, create it with `cp .env.example .env`.

For AMD TokenLab, fill in the API key in `.env`:

```dotenv
SWEBENCH_BACKEND=local
CLOUD_BASE_URL=https://api.tokenlab.amd.com/v1
CLOUD_MODEL=Gemma-4-31B-IT
CLOUD_API_KEY=your-tokenlab-api-key
```

The cloud backend uses OpenAI-compatible Chat Completions with tool calling
through [LiteLLM's endpoint adapter](https://docs.litellm.ai/docs/providers/openai_compatible).
Use the exact model ID and API base from your provider; the client appends
`/chat/completions`. The TokenLab values above are the supplied account settings.

After adding the key, inspect the cloud configuration without making API calls:

```bash
.venv/bin/python run_swebench.py run --backend cloud --run-id cloud-smoke --offline --dry-run
```

Run the same task selection locally and in the cloud with separate run IDs:

```bash
# Start the local model in another terminal first.
.venv/bin/python start_server.py
.venv/bin/python run_swebench.py run --backend local --run-id local-smoke --offline

# Cloud inference needs no local model server; Docker is still used for tasks and grading.
.venv/bin/python run_swebench.py run --backend cloud --run-id cloud-smoke --offline
```

Both commands select the first `dev` task by default. For comparisons, pass the
same `--instance-ids` or split/limit, graph mode, agent, and budgets to both runs.
Each run has its own predictions, traces, grading results, and summary. Set
`SWEBENCH_BACKEND=cloud` to make cloud the default; `--backend` takes precedence.

Local model and endpoint defaults still come from `local_config.py`. Optional
`LOCAL_MODEL`, `LOCAL_BASE_URL`, and `LOCAL_API_KEY` settings affect only the local
backend. `--model` and `--base-url` override the selected backend's defaults.
Keys are read only from environment settings and are excluded from manifests
and dry-run configuration output. Credentials can be rotated without changing
run identity. Cloud `prepare`, `grade`, and dry runs do not require a key;
`generate` and `run` validate it before Docker setup.

Local generation checks the server's `/health` endpoint. Cloud generation skips
that local check; the first model call validates authentication and availability.
An explicit `--health-url` enables a check with bearer authentication for either
backend. Cloud agent copies omit the sample's `thinking_config`, which the
harness otherwise translates into llama.cpp/vLLM-specific request fields.
Other sampling settings are retained; the cloud server controls its default
reasoning behavior. Local copies retain the original thinking settings.

When resuming or grading a cloud run, include `--backend cloud` along with the
original options. Source changes in this update require new run IDs for existing
evaluations because the pipeline fingerprints its implementation.

## Selection, budgets, and resuming

The default selection is one `dev` task. Select a small test sample explicitly:

```bash
.venv/bin/python run_swebench.py run --run-id test-five --split test --limit 5 --offline
```

Choose exact tasks, or run the complete split:

```bash
.venv/bin/python run_swebench.py run --run-id selected --split test --offline \
  --instance-ids astropy__astropy-11693 astropy__astropy-12057

.venv/bin/python run_swebench.py run --run-id full-test --split test --all --offline
```

Before a full run, use a small fixed development selection spanning several
repositories. Inference is sequential by default, with 10 minutes, 20 tool calls,
and 50 turns per task. Override with `--minutes`, `--tool-calls`, and `--turns`.
`--command-timeout` controls each tool command; `--grading-timeout` and
`--max-workers` control the official evaluator. `--memory` and `--cpus` control
agent container resources. `--image-mode build` builds official images locally
instead of pulling prebuilt images.

Use `--agent-dir PATH` to evaluate a custom agent. The default is the competition
sample submission. The pipeline copies it, retargets model aliases, and disables
sample LoRA declarations; source files are preserved. Adapter evaluation is not
supported in this entry point. See the local/cloud settings above for model and
endpoint selection; `--health-url` overrides the optional health check.

Reuse exactly the same selection, agent, model, graph mode, and budgets with
`--resume` after interruption. Atomic task attempts prevent duplicate prediction
rows. Completed error attempts are skipped too; add `--retry-errors` to retry
saved setup/agent errors after correcting their cause. A changed configuration,
dataset, copied agent, pipeline source, or image identity requires a new run ID.

To separate generation and grading:

```bash
.venv/bin/python run_swebench.py generate --run-id separate --offline
.venv/bin/python run_swebench.py grade --run-id separate --offline --resume
```

Pass the same nondefault selection/configuration arguments to both commands.
`--grading-python /path/to/other/venv/bin/python` supports a separate grading
environment with `swebench==4.1.0` installed. Each grading invocation uses a fresh
evaluation ID to avoid reusing stale grading results.

## Other dataset locations

```bash
# A snapshot directory containing data/dev-*.parquet and data/test-*.parquet
.venv/bin/python run_swebench.py prepare --run-id parquet-check --dry-run \
  --source parquet --dataset /path/to/snapshot

# Dataset or DatasetDict previously saved with save_to_disk()
.venv/bin/python run_swebench.py prepare --run-id disk-check --dry-run \
  --source disk --dataset /path/to/saved-dataset
```

`--source json` accepts a JSON array or JSONL file. Full grading records are saved
on the host; only an explicit allowlist of issue/repository fields reaches the
agent. Gold patches, test patches, evaluation scripts, and expected test outcomes
are excluded. Issue hints are disabled unless `--hints` is set.

## Graph-assisted experiments

The default `--graphs off` removes graph tools from root and analyzer agents and
updates copied prompts to use source search. This measures a graph-disabled
baseline.

To build commit-specific Python symbol graphs and token-hash embeddings:

```bash
.venv/bin/python run_swebench.py run --run-id dev-graph --offline --graphs build
```

The builder uses Python AST declarations, containment edges, and approximate call
resolution. Files incompatible with the host Python parser are listed in metadata.
The deterministic 256-dimensional token-hash encoder measures lexical similarity;
it is not the Kaggle semantic encoder or a learned embedding model. Graph
preparation costs are separate from reported inference time.

Use `--graphs import --graph-cache /path/to/artifacts` for graphs and numerical
embeddings produced by your research pipeline. For `owner/repo` at a base commit
`<sha>`, supply:

```text
owner_repo_<sha>.json       NetworkX node-link graph, edges or links schema
owner_repo_<sha>.npz        Symbol-name keys, finite nonzero vectors of equal dimension
owner_repo_<sha>.meta.json  Provenance and checksums
```

The metadata format is:

```json
{
  "repo": "owner/repo",
  "base_commit": "<40-character commit SHA>",
  "configuration": {"builder": "your-builder-version", "encoder": "your-model-and-revision"},
  "sha256": {".json": "<graph-file-sha256>", ".npz": "<embedding-file-sha256>"}
}
```

Graph node IDs must match embedding keys. Nodes should contain `name`, `text`,
`file_path`, and `line_number` attributes for useful tool responses. The pipeline
validates commit provenance, checksums, and vector shape before enabling tools.
Built artifacts are cached by repository, commit, and builder/encoder configuration.

Runtime graph copies include the same float32 vectors inline, using the existing
harness's native graph format. This avoids expensive symbol-cache misses on large
repositories such as Django; source graph/embedding caches stay unchanged. The
representation and runtime checksums are recorded in run artifacts. Graph caches
in memory are cleared between tasks so a longer evaluation does not retain every
repository graph.

Compare graph modes with the same selected tasks, model, and budgets in separate
run IDs. Record parser omissions and embedding configurations in research results.

## Artifacts and grading policy

Each run writes under `working/swebench/<run_id>/`:

- `manifest.json`: selection, dataset revision/hashes, agent hashes, model, budgets,
  graph mode, package versions, and source fingerprint.
- `agent/`, `agent_tasks.jsonl`: copied agent configuration and redacted task inputs.
- `evaluation_tasks.json`: selected full records used by official grading.
- `prepared.json`: image identities, graph metadata, and environment-check results.
- `attempts/`, `predictions.jsonl`: atomic task results and official-format predictions.
- `logs/`, `traces/`: existing Gemma runner artifacts.
- `grading/<evaluation_id>/`: evaluator command, output, per-instance reports,
  and summary. Gold checks instead write under `gold-sanity/`.
- `summary.json`: selected tasks, predictions, empty patches, errors, resolved and
  unresolved instances, tool/LLM calls, and separate inference, image preparation,
  and graph preparation durations.

Resolution rate uses **all selected instances**, including setup errors and empty
patches. Check `grading_status`: a generation-only summary is not an evaluated
model score. Official reports remain the source of truth for each graded patch.

The pipeline uses the pinned official harness's generated test specifications and
parsers. Extra fields in the downloaded dataset, including archived evaluation
scripts, are retained in `evaluation_tasks.json` but do not override that policy.

The original Gemma patch filter is preserved. It strips edits to Python test
files, test directories, and files such as `pyproject.toml`, `setup.cfg`,
`tox.ini`, `pytest.ini`, and `conftest.py`. This can restrict tasks requiring project
configuration edits; the policy is recorded in the manifest. Repositories keep
their original test configuration during execution. The agent's repository is
reset to the base commit and its git history is replaced with a fresh baseline,
while official grading uses a fresh container.

## Validation

Live Docker/Gemma development-split checks have completed. See
[SWEBENCH_VALIDATION.md](SWEBENCH_VALIDATION.md) for outcomes, artifact links,
the verified runtime fixes, and the configuration-filter and pydicom locale
limitations exposed by those checks.

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tests cover local dataset formats, selection and redaction, resume protection,
prediction persistence, summary denominators, graph compatibility with the
original retrieval tools, and the real ADK runner/tool/patch loop with a scripted
model and local test sandbox. A repository-preparation regression check verifies
base-code restoration, removal of future history, and the import-path bridge.
Docker environment checks, gold sanity grading,
and real Gemma evaluations require a running Docker daemon and model server.

References: [official evaluation guide](https://www.swebench.com/SWE-bench/guides/evaluation/),
[pinned harness source](https://github.com/SWE-bench/SWE-bench/tree/v4.1.0).
