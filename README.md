# swe-gemma-agent

Master's thesis project: **improving code graphs so small language models (SLMs) get better
context when resolving issues in large repositories.**

The repo starts from the Kaggle "Gemma 4 Developer Agent" harness and adapts it to run
**locally on a single consumer GPU**. The agent uses a Gemma 4 model plus a set of sandboxed
workspace and *code-intelligence* tools (code graph + embeddings) to produce a patch for a
SWE-bench-style task, which is then verified by running the repository's tests.

The thesis work centers on the **code-graph tools** (`get_code_neighbors`,
`search_similar_code`, `get_code_subgraph`) and the pre-computed graph/embedding data that
feed them — the hypothesis being that a better graph gives an SLM more useful context and a
higher resolution rate.

For the downloaded SWE-bench dataset, use the separate `run_swebench.py` entry
point. It provides task preparation, patch generation with the existing agent,
official Docker grading, resumable runs, and optional commit-specific graphs.
See [SWEBENCH_README.md](SWEBENCH_README.md) for setup and commands.
The evaluation pipeline supports local llama.cpp and cloud OpenAI-compatible
models via `--backend local` or `--backend cloud`. Put cloud credentials in the
git-ignored `.env` file; `.env.example` includes AMD TokenLab defaults.

---

## Repository layout

```
swe-gemma-agent/
├── README.md                              # this file
├── LOCAL_RUN_README.md                    # original local-run notes (vLLM path)
├── gemma-4-developer-agent-local.ipynb    # the notebook to run (patched for llama.cpp)
├── getting-started-gemma-4-developer-agent.ipynb  # original Kaggle notebook (reference only)
│
├── gemma-4-developer-agent/               # competition data + harness docs
│   ├── HARNESS_README.md                  # full technical reference for the swegemma harness
│   ├── tasks.jsonl                        # 129 benchmark tasks (fastapi)
│   ├── snapshots/                         # per-task repo snapshots (*.tgz)
│   ├── graphs/                            # pre-computed code graphs (*.json)   <-- thesis focus
│   ├── embeddings/                        # pre-computed node embeddings (*.npz) <-- thesis focus
│   ├── wheels/                            # repo/test dependency wheels (for the sandbox)
│   ├── sample_submission/                 # baseline declarative agent (agent.yaml, prompts, ...)
│   └── docker/                            # sandbox Dockerfiles (unused on the local subprocess path)
│
├── gemma-4-developer-agent-wheelhouse/    # offline wheels (swegemma, adk_submission, vllm, ...)
├── papers/                                # literature (RepoGraph, LocAgent, Auto-Code-Rover, ...)
│
│   # --- created during local setup (git-ignored where appropriate) ---
├── .venv/                                 # Python 3.12 virtual environment
├── llama.cpp/llama-b11972/                # prebuilt CUDA 12.8 llama-server (GGUF serving)
├── models/<model-name>/*.gguf             # downloaded GGUF weights
└── working/                               # scratch: results/, logs, retargeted submission
```

---

## Agent architecture

The agent is **declarative**: it is defined entirely in YAML under
`gemma-4-developer-agent/sample_submission/`, and `adk_submission` compiles that directory into
a [Google ADK](https://google.github.io/adk-docs/) agent tree at load time. No competitor
Python is executed — every agent, tool, sub-agent, and generation parameter is resolved against
closed host registries. This is both a security model and a convenient research surface: you
change behavior by editing YAML and prompts, not framework code.

### Workbook layout

```
sample/
├── agent.yaml                 # root agent: model, instruction, tools, sub-agents
├── eval_config.yaml           # per-task budgets (time / tool calls / turns)
├── prompts/
│   ├── system.md              # root agent instruction (the "how to fix an issue" playbook)
│   └── analyzer.md            # sub-agent instruction (root-cause analysis)
├── configs/
│   └── sampling.yaml          # temperature, top_p, max_output_tokens, thinking_config
└── sub_agents/
    └── code_analyzer.yaml     # read-only analysis sub-agent, used as a tool
```

### The agent tree

```
          ┌───────────────────────────────────────────────────────────┐
 problem  │  swe_baseline_agent            (root LlmAgent, Gemma 4)     │
 ───────▶ │    tools:                                                   │
 statement│      run_command  read_file  edit_file  write_file          │
          │      get_status   submit_patch                              │
          │      get_code_neighbors  search_similar_code                │
          │      get_code_subgraph                                      │
          │      code_analyzer_agent  ◀── AgentTool, skip_summarization │
          └───────────────────────────────┬───────────────────────────┘
                                           │ delegates read-only analysis
                                           ▼
          ┌───────────────────────────────────────────────────────────┐
          │  code_analyzer_agent           (sub-agent, same base model) │
          │    tools: read_file  search_similar_code                    │
          │           get_code_neighbors  get_code_subgraph             │
          │    returns: file/line targets + root cause + suggested fix  │
          └───────────────────────────────────────────────────────────┘
```

**Root agent — `swe_baseline_agent`.** An `LlmAgent` driven in a multi-turn loop by the
harness. Its instruction (`prompts/system.md`) is a "fast, minimal, precise fix" playbook:
identify target files from the problem statement, read only those, apply a minimal change with
`edit_file`/`write_file`, run a *targeted* test, then `submit_patch` — ideally in under ~8–10
turns. The prompt also hard-codes the anti-cheat rules (never touch test files, never run
full-repo pytest).

**Analyzer sub-agent — `code_analyzer_agent`.** A read-only agent wrapped as an `AgentTool`
with `skip_summarization: true`. The root agent can "call" it like a tool to get a structured
root-cause report (file paths, line numbers, suggested change) without polluting its own
context with every intermediate `read_file` / graph-traversal result. This is the ADK pattern
for isolating exploration token cost from the main coding context.

### Tools available to the agent

The harness binds nine sandboxed tools (`swegemma.tools`); the YAML attaches whichever subset
each agent needs.

*Workspace / execution tools* (how the agent sees and changes code):

| Tool | Purpose |
|------|---------|
| `read_file(path, start, end)` | Read a file slice (capped at 150 lines / 10k chars). |
| `run_command(cmd)` | Run a shell command in `/workspace` (e.g. a targeted `pytest`). |
| `edit_file(path, old, new)` | Resilient string-replace in an existing file. |
| `write_file(path, content)` | Create/overwrite a file. |
| `get_status()` | Report remaining budget and patch status (free, no budget cost). |
| `submit_patch()` | Capture the git diff as the final patch and end the loop (free). |

*Code-intelligence tools* (**the thesis focus**) — available when a repo has a pre-computed
graph (`graphs/<repo>.json`) and embeddings (`embeddings/<repo>.npz`):

| Tool | Purpose |
|------|---------|
| `search_similar_code(query)` | Nearest nodes by embedding similarity to a symbol. |
| `get_code_neighbors(node)` | Callers/callees of a symbol in the dependency graph. |
| `get_code_subgraph(nodes)`  | Induced call subgraph over a set of symbols. |

Improving the quality/structure of these graphs and embeddings — so the SLM locates the right
code with fewer, better-targeted tool calls — is the core research contribution this repo is
built to support.

### Model and generation

All agents in a submission share **one base model** (the harness enforces a single declared
model). Generation parameters come from `configs/sampling.yaml` — the baseline uses
`temperature: 0.2`, `top_p: 0.95`, `max_output_tokens: 16384`, and a `thinking_budget` of 4096
with thoughts included (Gemma 4's reasoning channel). The model is served on an
OpenAI-compatible `/v1` endpoint (llama.cpp locally; vLLM in the competition) and reached
through ADK's LiteLLM client.

> The baseline `agent.yaml` references the 31B competition model plus LoRA adapters
> (`main_lora`, `tool_lora`). The local run (notebook cell 2 / `working/_eval_test.py`)
> automatically rewrites the model to your chosen GGUF alias and strips the adapters, since
> those adapters are shaped for the 31B model.

### How the agent run fits into scoring

The agent architecture above is **Phase 1** of evaluation. What it produces — a git diff
(`agent_patch`) — is then graded in **Phase 2**. The next section covers that end to end.

---

## How evaluation works (in detail)

This section explains exactly what a "task" is, how the agent is allowed to change code, and
how that change is graded. The authoritative source is
`gemma-4-developer-agent/HARNESS_README.md`; this is the practical summary.

### What a task is

Each line of `gemma-4-developer-agent/tasks.jsonl` is one benchmark task derived from a real
GitHub issue/PR. The fields that matter:

| Field | Meaning |
|-------|---------|
| `instance_id` | Unique task id, e.g. `fastapi_15661`. |
| `repo` | Source repo, e.g. `fastapi/fastapi`. |
| `base_commit` | The commit the repo is rewound to *before* the fix. |
| `problem_statement` | The issue text the agent is asked to solve. |
| `test_patch` | A diff adding/updating the tests that encode "is this fixed?" (**hidden from the agent**). |
| `patch` | The gold human fix (**reference only; never shown to the agent or used to grade**). |

Key idea: the agent sees the **problem statement** and the code at `base_commit`, but never
the tests or the gold patch. It must produce its own fix that makes the hidden tests pass.

> Example (`fastapi_15661`): problem = "Automate release preparation"; the gold patch touches
> `scripts/prepare_release.py`, and the hidden `test_patch` adds `tests/test_prepare_release.py`.
> The agent must change the script so those new tests pass — without ever seeing them.

### The two-phase lifecycle

Each task is evaluated in two **separate, isolated sandboxes**. Locally these are temp
directories with their own venv (`sandbox='subprocess'`); in the real competition they are
air-gapped Docker containers. The two phases never share state.

```
                 ┌─────────────────────────── PHASE 1: AGENT ───────────────────────────┐
 task ──▶ snapshot @ base_commit ──▶ agent loop (edit code w/ tools) ──▶ submit_patch ──▶ agent_patch (a git diff)
                 └──────────────────────────────────────────────────────────────────────┘
                                                                              │
                                                                              ▼
                 ┌──────────────────────── PHASE 2: VERIFICATION ───────────────────────┐
 fresh snapshot @ base_commit ──▶ apply agent_patch ──▶ apply hidden test_patch ──▶ run pytest ──▶ resolved? (pass/fail)
                 └──────────────────────────────────────────────────────────────────────┘
```

#### Phase 1 — the agent produces a patch

1. The repo snapshot (`snapshots/<instance_id>.tgz`) is extracted at `base_commit` into a
   sandbox `/workspace`, installed editable, and a `baseline` git commit is made. The repo
   has **no git history past `base_commit`**, so the agent can't peek at the real fix.
2. The agent receives the problem statement, the task budget (time / tool-call limits), the
   environment rules, and a workspace listing. It then works in a multi-turn loop, calling
   tools:
   - **Explore / understand:** `read_file`, `run_command` (shell), and the code-intelligence
     tools (`search_similar_code`, `get_code_neighbors`, `get_code_subgraph`).
   - **Change code:** `edit_file` (string-replace in an existing file) and `write_file`
     (create/overwrite a file). These edits are written directly to files in `/workspace`.
   - **Check progress:** `get_status`, and it may run its own tests with `run_command`.
   - **Finish:** `submit_patch`.
3. **How the change is captured.** The agent never emits a diff by hand. When it calls
   `submit_patch` (or when the loop ends), the harness runs, inside `/workspace`:
   ```bash
   git add -N .                 # register new files so they show up in the diff
   git diff --binary HEAD       # diff everything against the baseline commit
   ```
   That unified diff is the **`agent_patch`** — the artifact that gets graded. The loop ends
   on `submit_patch`, or when the budget (time / tool calls / turns) runs out, or after
   repeated empty turns ("nudges"). Even without an explicit `submit_patch`, a non-empty diff
   is still captured and graded.
4. **Anti-cheat stripping.** Before grading, the harness removes from `agent_patch` any changes
   to test files or test-runner config (`test_*.py`, `*_test.py`, anything under `tests/`,
   plus `conftest.py`, `pytest.ini`, `pyproject.toml`, `tox.ini`, `setup.cfg`, …). The agent
   **cannot** pass by editing the tests — only by fixing the library/app code.

#### The Phase 1 agent loop (how a turn actually runs)

"Multi-turn loop" above means this, concretely (`swegemma/harness/agent_runner.py`). The
harness builds the initial prompt, starts the clock, and enters `while True:`. **Each iteration
is one ADK turn** — one call to the model that streams back events until the model stops:

```
build initial prompt (problem + budget + rules + workspace listing)
start agent clock
current_message = initial prompt

while True:
    # --- guard: stop if the turn budget is already spent ---
    if remaining_turns <= 0: break

    # --- run ONE turn: model reasons, emits tool calls, reads tool results ---
    for event in runner.run_async(new_message=current_message):
        • reasoning text (Gemma 4 "thought" channel)
        • function_call  -> harness executes the tool in /workspace -> function_response
        • track: turn_has_tool_call, last_assistant_text, finish_reason
        • if final text arrives AND patch already submitted -> break
        • if session timed out -> break

    # --- after the turn: should we stop or nudge? ---
    if patch_submitted:            break          # success path
    if timed out:                  break
    if tool-call budget spent:     break
    if turn budget spent:          break

    if turn_has_tool_call:         consecutive_nudges = 0     # progress -> reset
    if consecutive_nudges >= 3:    break                      # stuck -> give up
    consecutive_nudges += 1

    current_message = <nudge>      # feed a continuation message into the next turn
```

Key points:

- **A turn ≠ a tool call.** In one turn the model may think, then emit one (or several) tool
  calls; the harness runs each tool and feeds the JSON result back, all within the same
  `run_async` stream. The turn ends when the model stops producing output.
- **`submit_patch` ends the run.** Once the patch is submitted and the model emits its final
  text, the loop breaks and Phase 1 is over. `submit_patch` and `get_status` are free (they
  don't consume the tool-call budget).
- **Nudges rescue stalled turns.** If a turn ends *without* `submit_patch` (e.g. the model
  wrote a text-only reply, or hit `max_output_tokens` mid-tool-call), the harness injects a
  **continuation nudge** as the next user message. There are three nudge variants depending on
  how the turn ended: a tool call truncated by the token limit, reasoning truncated by the
  token limit, or a plain stop. A turn that *did* make a tool call resets the nudge counter;
  **three consecutive no-progress turns end the loop** (`max_nudges = 3`).
- **Four ways the loop ends:** `submit_patch`, time budget exhausted, tool-call/turn budget
  exhausted, or 3 consecutive nudges. In every case the harness then extracts the diff
  (step 3 above) — so even a run that never called `submit_patch` can still yield a gradable
  `agent_patch`.

> Budgets come from `sample_submission/eval_config.yaml` (and the overrides in
> `working/_eval_test.py`). The sample caps a task at 1 minute / 10 tool calls / 50 turns;
> the local eval raises these so the agent has room to actually finish.

#### Phase 2 — the patch is graded

A **fresh** sandbox is set up from the same snapshot at `base_commit` (none of Phase 1's
scratch state carries over), then:

1. **Apply `agent_patch`** using a 4-pass resilient `git apply` / `patch` fallback. If the
   patch cannot be applied at all, the task fails immediately.
2. **Reset test files and apply the hidden `test_patch`.** Any test/config paths the agent
   touched are force-reset to baseline first, then `test_patch` is applied. This is what
   injects the tests that define "fixed".
3. **Run pytest hermetically** on the target test files, producing a JUnit XML report.

### What "resolved" means (the score)

A task counts as **`resolved = True`** (score `1.0`) only if **both**:

1. `pytest` exits `0`, **and**
2. the JUnit report shows `passed > 0`, `failures == 0`, `errors == 0`, and every required
   test (the functions introduced by `test_patch`) actually ran and passed (no skips).

Otherwise the task is `resolved = False` (`0.0`). The headline metric over a set of tasks is
the **resolution rate**:

```
resolution rate = (# resolved tasks) / (total tasks)
```

That is the single number this project exists to improve: the hypothesis is that **better code
graphs give the SLM better context in Phase 1, so it writes patches that pass more often in
Phase 2.**

### Where to see the results

`working/_eval_test.py` (and notebook cell 5) print a per-task line plus the overall resolution
rate, and the harness writes detailed artifacts to `working/results/`:

```
working/results/
├── summary.json                 # aggregate resolution rate + per-repo breakdown
├── task_results.jsonl           # one line per task with metrics
├── patches/<id>.patch           # the exact agent_patch that was graded
├── test_outputs/<id>.log        # full Phase 2 pytest output
├── traces/trace_<id>.json       # full agent trajectory (every tool call + token usage)
└── logs/<id>.log                # readable transcript of the Phase 1 agent session
```

For debugging a single task, `traces/` and `logs/` show precisely which tools the agent
called and why, and `patches/<id>.patch` vs the task's gold `patch` shows how its fix differed.

---

## Local run

This project has been set up to run on a **single 8 GB NVIDIA GPU** (developed on an RTX 5060,
Blackwell `sm_120`). Because the Gemma 4 E-models are multimodal and their full/W4A16
checkpoints are 8–11.5 GB (too large for 8 GB VRAM), the model is served as a **q4_0 GGUF via
llama.cpp** rather than vLLM. llama.cpp exposes the same OpenAI-compatible `/v1` API the harness
expects, and `--jinja` parses Gemma 4's native tool markup into standard `tool_calls`.

### Prerequisites

- Linux x86_64 with an NVIDIA GPU and a driver supporting CUDA 12.8+ (Blackwell needs this).
- A Hugging Face account/token (`hf auth login`) to download the GGUF weights.
- `uv` (installed below) — used to create the Python 3.12 environment.

### One-time setup

The environment is already built under `.venv/`. To reproduce it from scratch:

First download and extract the competition data and wheelhouse using the
[Kaggle download steps](SWEBENCH_README.md#setup). They must be available in
`gemma-4-developer-agent/` and `gemma-4-developer-agent-wheelhouse/` before
installing the wheels below.

```bash
# 1. Python 3.12 env (the wheelhouse ships cp312 wheels)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python 3.12 .venv

# 2. PyTorch with CUDA 12.8 (Blackwell sm_120 support)
VIRTUAL_ENV=.venv uv pip install torch==2.10.0 torchvision==0.25.0 torchaudio==2.10.0 \
  --index-url https://download.pytorch.org/whl/cu128

# 3. The offline wheelhouse (swegemma, adk_submission, vllm, ...) with --no-deps
VIRTUAL_ENV=.venv uv pip install --no-deps \
  gemma-4-developer-agent-wheelhouse/gemma-4-developer-agent-wheelhouse/*.whl

# 4. Runtime deps the wheelhouse does NOT ship (they come pre-installed on Kaggle)
VIRTUAL_ENV=.venv uv pip install \
  pyyaml pydantic packaging numpy networkx scipy requests httpx fastapi uvicorn \
  pandas regex tqdm psutil cloudpickle litellm openai tiktoken sentencepiece protobuf \
  einops pillow aiohttp jinja2 prometheus-client py-cpuinfo python-json-logger pyzmq ray \
  watchfiles google-auth google-api-core googleapis-common-protos opentelemetry-api \
  opentelemetry-sdk tenacity jsonschema docstring-parser mcp authlib cachetools websockets \
  graphviz rich google-cloud-storage docker huggingface_hub
```

> Note: the harness's `LOCAL_RUN_README.md` claims the wheelhouse install is fully offline.
> That is only true on Kaggle's base image — locally, `torch` and ~50 transitive deps must be
> pulled from PyPI (steps 2 and 4 above).

### llama.cpp (GGUF serving)

Prebuilt CUDA 12.8 binaries live under `llama.cpp/llama-b11972/` (no build required). To
re-download:

```bash
B=b11972
base=https://github.com/ggml-org/llama.cpp/releases/download/$B
mkdir -p llama.cpp && cd llama.cpp
curl -sL -o bin.tar.gz    $base/llama-$B-bin-ubuntu-cuda-12.8-x64.tar.gz
curl -sL -o cudart.tar.gz $base/cudart-llama-$B-bin-ubuntu-cuda-12.8-x64.tar.gz
tar xzf bin.tar.gz && tar xzf cudart.tar.gz
cp -n cudart-llama-*-bin-ubuntu-cuda-12.8-x64/*.so* llama-$B/
```

### Model weights

```bash
# E4B (~5.1 GB GGUF, fits 8 GB with a 16k context):
hf download google/gemma-4-E4B-it-qat-q4_0-gguf gemma-4-E4B_q4_0-it.gguf \
  --local-dir models/gemma-4-e4b-it-qat-w4a16-ct

# or the smaller/faster E2B (~3.4 GB):
# hf download google/gemma-4-E2B-it-qat-q4_0-gguf gemma-4-E2B_q4_0-it.gguf \
#   --local-dir models/gemma-4-e2b-it-qat-w4a16-ct
```

### Run it

Open `gemma-4-developer-agent-local.ipynb` with a Jupyter kernel using `.venv` and run the
cells top to bottom:

| Cell | GPU? | What it does |
|------|------|--------------|
| 0 Config | no | Paths + model choice + llama.cpp knobs. |
| 1 Install | no | Installs the wheelhouse (offline). |
| 2 Data | no | Loads tasks, retargets the sample submission to the chosen model. |
| 3 Graph | no | Smoke-tests the code graph / embeddings — **runs on CPU, no GPU needed**. |
| 4 llama.cpp server | **yes** | Launches `llama-server` on `:8000`, builds the ADK model registry. |
| 5 Evaluate | **yes** | Runs Phase 1 (agent) + Phase 2 (pytest) in a subprocess sandbox. |
| 6 Package | no | Zips and validates the submission. |

There is also a standalone runner that skips Jupyter (assumes the server from cell 4 is up, or
starts one):

```bash
.venv/bin/python working/_eval_test.py
```

### Config knobs (cell 0)

- `MODEL_NAME` — served alias; one of the four `gemma-4-e{2,4}b/12b/31b-it-qat-w4a16-ct` names.
- `MODEL_PATH` — folder holding the `*.gguf` (or the file directly).
- `MAX_MODEL_LEN` — llama.cpp `--ctx-size`. `16384` fits 8 GB with flash-attn (~4.3 GB used).
- `LLAMA_SERVER_BIN` — path to the `llama-server` binary.
- `LLAMA_N_GPU_LAYERS` — `999` offloads everything to GPU; lower it if you OOM.
- `NUM_SAMPLE_TASKS` — keep at `1` for a first run.

> The sample `gemma-4-developer-agent/sample_submission/eval_config.yaml` caps a task at
> **1 minute / 10 tool calls** — fine for a smoke test, too tight for a real fix. Raise
> `max_time_minutes` / `max_tool_calls` for meaningful runs.

---

## Status / verified

- Python 3.12 + `torch 2.10.0+cu128` detects the RTX 5060 with `sm_120`; GPU matmul works.
- `vllm`, `swegemma`, `adk_submission` all import; cells 0–3 pass (129 tasks, fastapi graph
  of 4263 nodes / 2430 edges, neighbor/similarity/subgraph queries return results).
- `llama-server` serves the E4B q4_0 GGUF on GPU (~4.3 GB VRAM at 16k context) and parses
  Gemma 4 tool calls into OpenAI `tool_calls`.
- A full task ran end to end: the agent edited code, submitted a 1,413-char patch, and Phase 2
  verification executed. (`resolved=False` on this untuned baseline — the target to improve.)

---

## Notes & caveats

- **8 GB VRAM is the binding constraint.** Only the q4_0 GGUF path fits; the vLLM path in the
  original notebook is left for machines with more VRAM (the competition itself runs 4× L4 =
  96 GB).
- **GGUF vs vLLM tool parsing.** llama.cpp `--jinja` replaces vLLM's `tool_call_parser=gemma4`.
  Verified working on build `b11972`; a different llama.cpp build could behave differently.
- **Submission packaging (cell 6)** targets the competition format; it is orthogonal to the
  local research loop.

## References

See `papers/` for the literature this work builds on (RepoGraph, LocAgent, Auto-Code-Rover,
SWE-Smith, ARISE). The harness internals are documented in
`gemma-4-developer-agent/HARNESS_README.md`.
