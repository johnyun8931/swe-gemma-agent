

# **ARISE: A Repository-level Graph Representation and Toolset for Agentic Program Repair and Fault Localization** Shahd Seddik<sup>1,*</sup> Fahd Seddik<sup>1</sup> Amirrezza Esmaeili<sup>1</sup> Mahdieh Sadatbenis<sup>1</sup> Fatemeh Fard<sup>1</sup> 

> 1 FARD Lab, University of British Columbia, Kelowna, Canada 

> * Corresponding author: shahd.seddik@ubc.ca 

� **Project page: fard-lab.github.io/ARISE** 

**Abstract.** Automated program repair at repository scale requires an agent to locate a fault among thousands of files and synthesize a correct patch. Existing graph-based agents represent how a repository is organized into files, classes, and functions, but they do not model how variable values flow within a procedure, which leaves the agent without the semantic precision that function-level and line-level localization demand. We present ARISE ( **A** gentic **R** epository-level **I** ssue **S** olving **E** ngine), a framework-agnostic toolset that builds a multi-granularity program graph, extending structural relationships down to statement-level nodes connected by intra-procedural definition-use edges, and exposes it through a three-tier tool API that mounts on any tool-use agentic framework. The central primitive is data-flow slicing, a queryable agent tool that traces in a single call which statements define or consume a variable of interest. On SWE-bench Lite (300 real GitHub issues across 11 Python repositories) with the open-source Qwen2.5-Coder-32B-Instruct backbone, mounting ARISE on SWE-agent as the host resolves 22.0% of issues (66/300), a 4.7 percentage-point gain over the unmodified SWE-agent baseline under the identical backbone and host. We show this gain is largely attributable to sharper localization, with Function Recall@1 (R@1) rising from 0.43 to 0.60 (a 40% relative gain) and Line R@1 from 0.26 to 0.41 (a 58% relative gain). Controlled ablations attribute the improvement to the data-flow graph rather than the tool schema, and we further mount the same toolset on a second host framework to study its portability. Decoupled from any single scaffold, the graph builder and slicing API form a drop-in toolset for future repair research. 

**Keywords:** automated program repair · fault localization · large language model agents · program graphs · data-flow slicing · SWE-bench 

## **1 Introduction** 

Automated Program Repair (APR) aims to automatically generate source-code changes that fix software defects, and its first step is Fault Localization (FL), the task of identifying the specific files, functions, and lines responsible for a failure [1]. This step is expensive in practice: developers spend an estimated 35% to 50% of their working time on validation and debugging, and debugging, testing, and verification together account for 50% to 75% of total software-project budgets, amounting to more than 100 billion USD annually [2], figures rooted in an industry study from the Cambridge Judge Business School [3] and echoed in recent surveys of Large Language Model (LLM)-based repair [4]. The difficulty is amplified by scale, because modern software is developed in large 

repositories where code spans many files and packages with rich build and dependency relationships [5]. Operating at this scale requires an automated system to identify relevant code units across files, to follow call and data dependencies through the codebase, and to maintain architectural context. Even strong LLM-based systems resolve only a minority of real repository-level issues when applied naively [6], and accurate localization is the decisive bottleneck that separates a successful repair from a failed one [7]. 

LLMs and LLM-based agents are now widely applied to FL and APR over large repositories [4]. Two design philosophies dominate. _Agentic_ systems interleave reasoning with tool actions, letting the model open, edit, and test code across a project. SWE-agent introduces 

1 

_ARISE: Graph Representation and Toolset_ _<u>for</u> Agentic Repair_ 

an Agent-Computer Interface for this purpose [8], AutoCodeRover navigates the repository through structureaware search APIs before patching [9], SpecRover extends AutoCodeRover with iterative specification inference and a reviewer agent for patch vetting [10], and OrcaLoca adds priority-based scheduling and context pruning for localization [7]. _Agentless_ system instead fix a multiphase pipeline of localization, generation, and validation, matching tool-driven agents at lower cost [11], and SWEFixer trains open-source LLMs around a coarse-to-fine retrieval-and-edit pipeline [12]. A complementary line gives agents explicit code structure through graphs, including RepoGraph [13], LocAgent [14], KGCompass [15], and CodexGraph [16], with CGM integrating structural code graphs directly into an open-source LLM [17], which improve navigation and downstream reasoning over flat retrieval. 

Despite this progress, current graph-based APR and FL systems share three limitations. First, their graphs model structure only, namely files, classes, and functions, and stop above the statement level, so they cannot represent how a value is computed inside a procedure [13, 14, 18]. Second, the graph is typically precomputed and consumed as a fixed feature source rather than a substrate the agent can query on demand during reasoning [19]. Third, none exposes a way to trace how a specific variable is defined and propagated within a function. This matters because the faulty statement is frequently not the one named in the issue but one reachable from it only by following a definition-use chain. 

We present ARISE ( **A** gentic **R** epository-level **I** ssue **S** olving **E** ngine), a framework-agnostic toolset that targets this gap through a single design decision, namely augmenting a structural repository graph with statementlevel nodes and intra-procedural definition-use edges, and exposing the resulting data-flow slices as a first-class tool primitive through a three-tier tool API. ARISE is decoupled from any specific agentic scaffold and mounts on any host framework that exposes a tool-call interface, requiring only that the host inject the ARISE tool schema and dispatch tool calls across its turns. For the controlled experiments we use SWE-agent [8] as the primary host, reusing its Agent-Computer Interface and multi-turn protocol, and we mount the same toolset on a second host framework to study portability. This gives the backbone model precise, queryable evidence about both how the repository is organized and how values flow within the functions under suspicion. The novelty is not slicing itself, which is a classical analysis, but exposing it as an on-demand agent primitive rather than a static preprocessing artifact, so that a single backward-slice query resolves in one step the def-use chain that a structureonly graph can only approximate through many traversal hops. 

We make three contributions. 

- (1) **Multi-granularity program graph.** A repository graph that unifies structural and data-flow information at statement granularity, letting an agent query not only how code is organised (files, classes, functions) but also how values are defined and propagated within a procedure, evidence unavailable in structureonly graphs (Section 3.2). 

- (2) **Data-flow slicing as an agent primitive.** We expose data-flow slicing as a first-class, queryable tool, allowing an agent to retrieve the precise set of statements that affect, or are affected by, a variable of interest, with backward, forward, and bidirectional queries returning structured output the model reasons over directly (Tier 2, Section 3). 

- (3) **Portable, framework-agnostic toolset.** We design ARISE’s graph builder and slicing API as a self-contained component with a thin integration contract, so any tool-use-capable framework can use it without re-implementing the underlying analysis (Section 4.5). 

We organize the evaluation around three research questions. 

**RQ1 (Repair).** _Does ARISE improve end-to-end bug repair over text-only, agentic, and structural-graph baselines?_ Repair is the end goal, so we first measure whether the graph and slicing tools resolve more issues than the unmodified SWE-agent scaffold and a structuralgraph configuration. 

**RQ2 (Localization mechanism).** _Is the repair improvement explained by better fault localization?_ We treat localization as the analytic lens on repair. By measuring localization recall at file, function, and line granularity and its rank correlation with repair success, this question tests whether localization precision is the binding constraint that is improved by the slicing tools [1, 20]. 

**RQ3 (Ablation).** _Which components of ARISE contribute to the gains, and how?_ ARISE introduces structural graph tools, data-flow slicing, and context bundling. We isolate each through controlled conditions that add or remove one component while holding all else constant. 

We evaluate on SWE-bench Lite [6] with Qwen2.5Coder-32B-Instruct, comparing against a BM25 retrieval lower bound, the unmodified SWE-agent agentic baseline, and a structural-graph configuration that reproduces LocAgent and RepoGraph capabilities. With SWE-agent as the host, ARISE resolves 22.0% of issues (66/300), a 4.7 percentage-point gain over SWE-agent, and improves Function Recall@1 from 0.43 to 0.60 (a 40% relative gain) and Line Recall@1 from 0.26 to 0.41 (a 58% relative gain). The absolute resolve rate is bounded by our use of an open-source backbone rather than a frontier closed model, so we report controlled within-setting deltas under 

2 

_ARISE: Graph Representation and Toolset_ _<u>for</u> Agentic Repair_ 



<!-- Start of picture text -->
7) ARISE GraphConstruction 2) Agentic Toolset ae 3} Agentic Loop ISSUE DESCRIPTION J<br>=REPOSITORY ® ARISETIER 1 getitemthe actualdoesunpackednot infer<br>— value when trying to<br>Bee searchentities() || traverse_relations() call “Dict. getitem()><br>BSB core/ get_enclosing scopes() | | get_code_span() ; . ee J<br>‘ Agente Tooker A<br>te5  hetp.pyae set entity_info() -aise TR Iterative loop until<br>> wtiismain.py =aTHEE TEETERARISE TIER 3 re=D ><P oranswerreachedmax tum is submitted countis<br>Wipe get_dataflow_siice() | sweacenrroois<br>| nse reR3<br>butld_context_bundle()<br>ee Ey REPAIR OUTPUT: LOCALIZATION OUTPUT: |<br>‘esate PATCH p RANKED LIST £<br>€@ -42,1 +42,1 @@ 1. src/core/http.py, server(), 16<br>ARISE Graph SWEAGENT asec) | ( subattQ)) [ -o- | + return True if val else False | 2, src/utils/io.py, request(), 3<br><!-- End of picture text -->

**Figure 1.** ARISE pipeline overview. **Phase 1:** given a repository snapshot, ARISE constructs a multi-granularity program graph that combines structural relationships with intra-procedural data-flow edges (definition-use chains at statement level). **Phase 2:** the agentic tool API adds three tiers, where Tier 1 provides structural navigation, Tier 2 adds data-flow slicing that traces how variables are defined and consumed, and Tier 3 assembles the collected evidence into a ranked, token-budgeted context bundle. **Phase 3:** an LLM-based agent interacts with the graph through this API and produces a ranked list of fault locations (localization) or a unified diff (repair). 

a fixed host and backbone. Data-flow slicing is the single largest component, lifting Function Recall@1 from 0.50 to 0.57 and Pass@1 by 2.0 percentage points, and controlled ablations attribute this gain to the data-flow graph itself rather than to the additional tool in the agent schema. We release ARISE as an open artifact for reproducibility.<sup>1</sup> 

The paper is organized as follows. Section 2 reviews related work. Section 3 describes the methodology. Sections 4 and 5 report and interpret results. Section 6 discusses threats to validity, and Section 7 concludes. 

## **2 Related Work** 

**LLM-based repair and fault localization.** LLMbased APR follows two design philosophies. _Agentic_ systems equip the model with tools and a multi-turn loop to explore, localize, and patch code. SWE-agent introduces the Agent-Computer Interface for navigating, editing, and testing repositories [8]; AutoCodeRover navigates through structure-aware search APIs before patching iteratively [9]; and OrcaLoca adds priority-based scheduling and distance-aware context pruning [7]. _Agentless_ systems instead fix a localization, generation, and validation pipeline, matching agents at lower cost [11]. On the localization side, traditional spectrum-based and mutation-based methods rank statements from test outcomes [21, 22], while LLM-based methods reason jointly over issue text and code [23–25]. A consistent finding 

> 1https://github.com/FARD-Lab/ARISE 

across this work is that file-level localization is well served by lexical signals, whereas function-level and line-level localization remains the binding constraint on repair [1]. ARISE builds on the agentic paradigm and targets this bottleneck with structured graph tools rather than lexical retrieval or free-form navigation. 

**Graph-based retrieval for code agents.** Several systems expose repository graphs to LLM agents. RepoGraph builds a line-level reference graph as a SWEagent plugin [13], LocAgent parses code into heterogeneous file, class, and function graphs with search and traversal tools [14], CodexGraph drives a Neo4j database through agent-issued Cypher queries [16], and KGCompass links issues and pull requests to code entities for path-guided retrieval [15]. These systems demonstrate the value of structural context, but none models intraprocedural definition-use relationships or exposes a dataflow slice as a first-class agent tool; even RepoGraph, whose reference graph reaches line granularity, captures only lexical reference edges rather than how a value is defined and propagated within a procedure. Table 1 summarizes this comparison. A parallel line of retrievalaugmented methods for code, including RepoCoder [26], RLCoder [27], and GraphCoder [28], improves completion by retrieving relevant context, but it targets generation and does not expose an interactive analysis primitive that a repair agent can query mid-reasoning. ARISE closes the gap by extending the graph to statement gran- 

3 

_ARISE: Graph Representation and Toolset_ _<u>for</u> Agentic Repair_ 

ularity and adding intra-procedural def-use edges, so the `get_dataflow_slice` tool answers “which statements affect this variable?” in a single query, and it packages this analysis as a host-agnostic toolset with a defined integration contract rather than as a host-specific component. 

**Program slicing.** Program slicing reduces a program to the statements relevant to a slicing criterion, using backward slicing to find statements that influence a variable and forward slicing to find those it influences, typically over a program dependence graph [29–31]. Despite its long history, slicing has seen little integration with LLM agents, where program analysis is usually a preprocessing step rather than an interactive, queryable tool. ARISE implements intra-procedural backward and forward slicing over def-use edges and exposes the result through `get_dataflow_slice` , turning analysis into an on-demand reasoning primitive. 

## **3 Methodology** 

ARISE comprises three phases (Figure 1). _Phase 1_ constructs a multi-granularity program graph that augments structural relationships with intra-procedural data-flow edges. _Phase 2_ exposes the graph through a three-tier tool API, layering structural navigation, data-flow slicing, and context bundling. _Phase 3_ is the host agent loop, in which an LLM-based agent queries these tools through its host framework to produce either a ranked list of fault locations or a unified diff. Phases 1 and 2 are the portable ARISE toolset, while Phase 3 is supplied by whichever host framework mounts it. The host’s own built-in tools are kept fixed in all experiments, so every reported difference is attributable to the ARISE tool layer. 

### **3.1 Problem Formulation** 

Given a natural-language issue description _I_ and a repository snapshot _R_ at a fixed commit, we evaluate FL and APR independently, each with its own system prompt and output format. Running the tasks separately lets us isolate the contribution of the ARISE tools to localization quality from their contribution to repair success (Section 3.5). 

**_Localization._** The agent returns a ranked list _L_ = [( _fi,gi,ℓi_ )]<sup>_k_</sup> _i_ =1<sup>,where</sup><sup>_fi_isafilepath,</sup><sup>_gi_anenclosing</sup> function or method, and _ℓi_ a line number. 

**_Repair._** The agent returns a unified diff _p_ ˆ such that applying _p_ ˆ to _R_ causes all instance-specific tests to pass. 

### **3.2 Multi-Granularity Repository Graph** 

ARISE represents a Python repository as a directed, typed property graph _G_ = ( _V,E_ ) that unifies _structural_ relationships, describing how the codebase is organised 

into packages, modules, classes, functions, and statements, with _data-flow_ relationships, describing how variable values are defined and propagated within functions. Each node carries a type from _{_ Directory, Module, Class, Function, Method, Statement _}_ with source-location attributes ( `file_path` , `start_line` , `end_line` ), and each edge a type from _{_ Contains, Imports, ImportedBy, Calls, CalledBy, Inherits, DataflowDefUse, DataflowUseDef _}_ . Construction proceeds in two passes. 

**Structural pass.** We parse every `.py` file with Python’s `ast` module, emitting Directory, Module, Class, Function, and Method nodes with Contains, Imports/ImportedBy, and Inherits edges. Inter-module Calls/CalledBy edges require resolving each call site against the import-alias map of the caller’s module, which a second pass performs. Spurious call edges lead agents down incorrect paths and are more harmful than missing edges [32, 33], so we resolve only unambiguous direct and qualified `module.function()` calls and drop dynamic dispatch through attribute access. 

**Program graph pass.** This pass extracts data-flow. For every Function and Method, we emit one Statement node per top-level AST statement, connected to the enclosing function by a Contains edge. Statement nodes are the endpoints of all data-flow edges, so each DataflowDefUse/DataflowUseDef edge connects a defining statement to a using statement rather than individual name occurrences. We then run an intra-procedural reaching-definition scan. Definitions are statements where a variable receives a value ( `ast.Assign` , `ast.AugAssign` , `ast.AnnAssign` , loop and context-manager targets, and function parameters), and uses are `ast.Name` nodes in a `Load` context. For each use of variable _v_ at statement _s_ , we find the last preceding definition of _v_ in source order within the same function, respecting lexical scope ( `global` / `nonlocal` handled explicitly), and emit a directed DataflowDefUse edge to _s_ with its DataflowUseDef reverse. A backward slice from _v_ at _s_ follows DataflowUseDef edges to where _v_ was defined; a forward slice follows DataflowDefUse edges to where _v_ is consumed. The graph is built once per repository and cached, so construction cost is amortized across the instances that share a repository (Section 5.3). 

### **3.3 Agentic Tool API** 

ARISE exposes _G_ as a set of typed, JSON-schemaannotated functions the backbone can invoke in any order. A session typically proceeds through navigation, then analysis, then assembly, but no order is imposed. These stages map to three tiers, separated so each can be added to the tool schema independently for ablation (Section 3.5). All tools share a `RetrievalSession` holding _G_ , a TF-IDF entity index, and an enclosing-scope 

4 

_ARISE: Graph Representation and Toolset_ _<u>for</u> Agentic Repair_ 

**Table 1.** Comparison of graph-based APR and localization systems. “Slice API” denotes whether a data-flow slice can be queried as a structured agent tool. ARISE is the only system that extends the graph to statement granularity and exposes data-flow slicing as a first-class agent tool. 

|**System**|**Graph granularity**|**Data-flow edges**|**Agent tool API**|**Slice API**|
|---|---|---|---|---|
|KGCompass [15]|File / function|None (issue/PR metadata)|Yes (GPT-4)|✗|
|RepoGraph [13]|Line-level|Reference edges only|Plugin|✗|
|LocAgent [14]|File / class / fn|None|Yes|✗|
|CodexGraph [16]|Module / class / fn|None|LLM queries|✗|
|ARISE (ours)|Package _→_statement|AST def-use edges|Yes|✓|



### index, built once per session. 

**Tier 1: structural retrieval.** Five tools operate on the structural portion of _G_ and reproduce the capabilities of prior structural-graph systems [7, 13–15], constituting the ARISE-Structural baseline: `search_entities` (TF-IDF search over entity names, paths, and docstrings), `traverse_relations` (bounded breadth-first traversal along given edge types), `get_enclosing_scopes` (maps a `(file, line)` pair to enclosing Function/Class/Module nodes), `get_code_span` (raw source for a line range), and `get_entity_info` (metadata summary for a node). 

**Tier 2: data-flow slicing.** `get_dataflow_slice` traces a named variable through its enclosing function via the def-use edges in _G_ . Given a seed `(file, line, variable)` and a direction ( `backward` , `forward` , or `both` ), it maps the seed to a Statement node, runs a bounded BFS over the appropriate data-flow edges, stops at function boundaries, and returns an ordered list of `SliceStep` records carrying the file, line range, variable, and role ( _parameter_ , _definition_ , _augmented assignment_ , _use_ ). If the seed line has no Statement node, `get_dataflow_slice` returns an empty slice and the agent falls back to `get_code_span` . 

**Tier 3: context bundling and ranking.** `build_context_bundle` assembles a ranked set of code spans under a token budget (default 8 000). Given seed entities _S_ and optional slices _D_ , each candidate span _c_ is scored as 



where rel( _c_ ) is TF-IDF relevance to the issue, prox( _c_ ) is inverse hop distance to the nearest seed in the Calls/Imports subgraph, and the indicator marks slice membership. The weights _α,β,γ_ are fixed heuristic constants set to 1 _._ 0, 0 _._ 5, and 1 _._ 5, up-weighting slice membership, and spans are greedily packed in descending score until the budget is exhausted. `rank_suspect_regions` ranks suspicious functions from the issue text and any stack trace, expanding seeds through the Calls/CalledBy subgraph and scoring 

them by the same combination. It serves as a structural surrogate for inter-procedural data-flow, bringing a callee into the suspect list when the root cause lies past a call boundary the slice cannot cross. 

### **3.4 Agent Loop** 

The ARISE toolset requires only that the host framework expose a tool-call interface, namely the ability to inject the ARISE tool schema into the model context and to dispatch the resulting tool calls across a session, a contract any framework can meet (Section 4.5). For the controlled experiments the host is SWE-agent [8], whose agent-computer interface, multi-turn loop, and patch-generation sub-agent we reuse unchanged; ARISE adds only the program-graph tool layer injected into the tool schema at every turn. 

The two tasks (APR and FL) use distinct system prompts and output formats, as described in Section 3.1. In both, the prompt is extended only with the ARISE tool-schema descriptions, and the tool docstrings are the sole description of each tool’s semantics. Algorithm 1 gives the host agent loop, instantiated with SWE-agent’s protocol; at each turn the backbone receives the full history and either emits a tool call (dispatched to a host built-in or a ARISE function) or a terminal answer, and the loop ends on a terminal answer or after _T_ max turns. 

**Tool availability by condition.** The ablation conditions differ only in the tools exposed (defined in Section 4.1). ARISE-Structural exposes Tier 1 only, directly comparable to LocAgent [14] and RepoGraph [13]; ARISE-Slicing adds Tier 2; ARISE-Full adds all three tiers. ARISE-Coarse exposes the Tier 1 and Tier 2 schemas but builds the graph without Statement nodes or Dataflow edges, so `get_dataflow_slice` always returns empty, separating the tool API from the data-flow graph. The prompt structure, turn count, and token budget are held constant across conditions. 

### **3.5 Evaluation Protocol** 

**Localization metrics.** We score the ranked `(file, function, line)` list against the gold patch at three granularities. At _file_ level we report File Recall@ _k_ (R@ _k_ , for _k ∈{_ 1 _,_ 3 _}_ ), the fraction of instances where a gold file 

5 

_ARISE: Graph Representation and Toolset_ _<u>for</u> Agentic Repair_ 

**Algorithm 1** Host agent loop, instantiated with SWEagent in our main experiments. _T_ = _T_ host _∪T_ ARISE combines the host’s built-in tools with the portable ARISE graph tools (Section 3.3). The prompt _P_ and answer schema differ by task, returning a ranked list of `(file, fn, line)` triples (localization) or a unified diff _p_ ˆ (repair). 

**Require:** Issue _I_ , task prompt _P_ , session _G_ , tool set _T_ , max turns _T_ max 1: _H ←_ [ _P,_ user: _I_ ] 2: **for** _t_ = 1 _,...,T_ max **do** 3: _r ←_ LLM( _H, T_ ) 4: **if** _r_ is terminal answer **then** 5: **break** 6: **end if** 7: result _←T ._ dispatch( _r._ tool_call) _▷_ host or ARISE tool 8: _H ← H ∥_ [ _r,_ tool_result: result] 9: **end for** 

10: **if** no terminal answer produced **then** 11: _r ←_ LLM( _H ∥_ [“produce final answer now”] _, T_ ) 12: **end if** 

13: **return** ParseAnswer( _r,P_ ) _▷_ ranked location list _or_ unified diff 

appears in the top _k_ , and File Mean Reciprocal Rank (MRR). At _function_ level the gold set maps each diff hunk to its enclosing function via `get_enclosing_scopes` , and we report Function R@ _k_ ( _k ∈{_ 1 _,_ 3 _}_ ), Function MRR, and Function F1@3. At _line_ level the gold set is the union of lines touched by the patch, and we report Line R@ _k_ ( _k ∈{_ 1 _,_ 5 _,_ 10 _}_ ), Coverage@budget (abbreviated Cov; the fraction of gold lines in the `build_context_bundle` output under the 8 000-token budget), and IoU (mean intersection-over-union of predicted and gold line sets). Coverage@budget is reported only for the ARISE conditions, which assemble a budgeted context bundle, and is N/A for the baselines. 

**Repair metric.** Pass@1 is the fraction of instances whose first generated patch passes all instance tests. We also report the Spearman rank correlation between Function R@1 and Pass@1, testing whether localization quality correlates with repair success. Because localization and repair are run as separate sessions, this correlation associates two independent runs on the same instances rather than tracing localization inside the repair run (Section 6). 

## **4 Results** 

### **4.1 Experimental Setup** 

**Benchmark.** We evaluate on SWE-bench Lite [6], comprising 300 real GitHub issues from 11 Python open- 

source repositories. Each instance pairs a naturallanguage issue with a gold patch and a project-specific test harness. SWE-bench Lite is a standard evaluation suite for recent repository-level systems [7, 9, 11, 13, 15, 34–36]. **Backbone model.** The backbone for all main experiments is Qwen2.5-Coder-32B-Instruct [37], served locally with vLLM [38]. The maximum agent turn count is 40 and the `build_context_bundle` token budget is 8 000 tokens. 

**Reproducibility.** All backbones are open-weight and served through vLLM with fixed decoding settings, and evaluation uses the standard SWE-bench Lite Docker harness, so every condition is reproducible from the released graph builder, tool API, and run scripts. 

**Ablation conditions.** The two baselines are RAG (BM25 over raw files, no graph or agent) and the unmodified SWE-agent (built-in tools, no graph). The ARISE conditions add tools cumulatively, namely ARISEStructural (Tier 1, matching LocAgent and RepoGraph), ARISE-Slicing (Tier 1 and 2, the primary novelty condition), and ARISE-Full (all three tiers), together with a control condition ARISE-Coarse (the Tier 1 and 2 schemas but no Statement nodes, isolating the tool API from the graph); the tools per tier are defined in Section 3.3. All ARISE conditions share the same host, prompt, turn count, backbone, and harness, so the only variable is the set of tools exposed to the agent. 

**Baselines.** We select baselines across three tiers. RAG is a lexical lower bound using BM25 file retrieval with no agent loop, establishing the floor from which agentic and graph approaches must improve. SWE-agent [8] and AutoCodeRover [9] represent SOTA agentic frameworks, both among the top open-submission systems on the SWE-bench leaderboard and the most widely reproduced agentic baselines in recent APR literature. RepoGraph [13], and KGCompass [15] are the most closely related graph-augmented systems; OrcaLoca [7] and Agentless [11] cover a SOTA localization-first and a non-agentic pipeline respectively. 

### **4.2 RQ1: Effect of ARISE on Bug Repair** 

As Table 2 shows, ARISE-Full resolves 22.0% (66/300). The central comparison is the within-setting delta over the SWE-agent host, since both run with the same backbone, prompt, turn budget, and harness, so the toolset is the only changed variable. On that comparison ARISEFull raises Pass@1 from 17.3% to 22.0%, a gain of 4.7 points and 14 additional resolved instances, a relative improvement of roughly 27%. 

Absolute resolve rates are bounded by the open-weight Qwen2.5-Coder-32B-Instruct backbone rather than by the toolset, so our claim is not a leaderboard rank- 

6 

_ARISE: Graph Representation and Toolset_ _<u>for</u> Agentic Repair_ 

|**Table 2.** End-to-end rep<br>the instance count r<br>per-instance total (input <br>the Spearman correla<br>Pass@1. `–-` marks a valu<br>Rows marked * are adop<br>lowe|air on SWE-bench <br>esolved out of 300. <br> plus output, all tur<br>tion between Funct<br>e we do not report <br>ted from [13]. Shad<br>st to<br>highest .|Lite. Res<br> Avg. tok<br>ns, _×_100<br>ion R@1 <br> for that <br>ing: per-|olved is<br>. is<br>0). _ρ_ is<br> and<br>system.<br>column|
|---|---|---|---|
|**Method**|**Pass@1 Resolved **|**Avg. tok**|**.**<br>**_ρ_**|
|_Baselines (Qwen2.5-Coder-_<br>|_32B-Instruct)_<br><br>|||
|RAG|2.67%<br>8|13|0.05|
|SWE-agent|17.3%<br>52|510|0.38|
|SWE-agent+RepoGraph|19.3%<br>58|—|—|
|Agentless|13.3%<br>40|—|—|
|KGCompass|15.3%<br>46|—|—|
|_ARISE conditions (Qwen2._<br>|_5-Coder-32B-Instruct_<br><br>|_)_<br>||
|ARISE-Structural|19.0%<br>57|531|0.42|
|ARISE-Coarse|19.5%<br>59|535|0.43|
|ARISE-Slicing|21.0%<br>63|550|0.51|
|ARISE-Full|**22.0%**<br>**66**|560|**0.53**|
|_Literature (GPT-4o)_||||
|SWE-agent*|18.3%<br>55|498|—|
|SWE-agent+RepoGraph*|20.3%<br>61|519|—|



ing against systems built on proprietary models but the consistent improvement that ARISE adds within a fixed setting. Under that lens ARISE-Full outperforms every same-backbone baseline, including the graphaugmented SWE-agent+RepoGraph [13] (19.3%) and the localization-first and non-agentic systems Agentless [11] (13.3%) and KGCompass [15] (15.3%). It also surpasses the published SWE-agent+RepoGraph result that uses the proprietary GPT-4o model (20.3%) [13], showing that a graph-augmented open-source configuration can outperform a proprietary-backbone one. This repair gain comes at a modest token premium over the bare host, which we analyze in Section 5.3. 

**Finding #1.** With an open-source 32B backbone, ARISE raises Pass@1 over its SWE-agent host by 4.7 points (17.3% to 22.0%, a 27% relative gain), outperforms every same-backbone baseline, and exceeds the RepoGraph+SWE-agent configuration reported with GPT-4o. 

### **4.3 RQ2: Localization as the Mechanism** 

We test whether the repair gain is explained by improved fault localization. Table 3 reports localization at file, function, and line granularity against the gold patch. 

ARISE achieves the highest score on every metric, outperforming not only the SWE-agent baseline but also OrcaLoca, a purpose-built localization system, at file, function, and line level. Even the lowest ARISE condition (ARISE-Structural) outperforms the SWE-agent baseline in R@1 by 0.05 at file level, 0.07 at function level, and 0.05 at line level. The best condition, ARISE-Full, 

improves R@1 over SWE-agent from 0.57 to 0.67 at file level, 0.43 to 0.60 at function level, and 0.26 to 0.41 at line level, relative gains of 18%, 40%, and 58%, respectively. The gains are largest at the function and line levels. File identification is already well served by simple lexical matching, so there is little room to improve, whereas pinpointing the correct function and line needs the structural and data-flow evidence that only the ARISE tools provide. The ranking metrics move the same way. Function MRR rises from 0.48 to 0.63 and Function F1@3 from 0.39 to 0.55, so ARISE not only finds the faulty function more often but also ranks it higher and surfaces fewer wrong candidates. Coverage@budget, the fraction of gold lines that fit within the 8 000-token context budget, rises across the ARISE conditions from 0.61 (ARISE-Structural) to 0.75 (ARISE-Full). This shows that richer tools place more of the faulty region within the agent’s working budget, not merely rank it well. It is N/A for the baselines, which do not assemble a budgeted context bundle. 

OrcaLoca, a dedicated localization system, sharpens this picture. It is the strongest baseline at file level (File R@1 0.61, File MRR 0.62), confirming that file identification is not where the difficulty lies. At function and line level it falls behind even the SWE-agent baseline (Function R@1 0.35 versus 0.43, Line R@1 0.28), and ARISE-Full improves on it by 0.25 in Function R@1, a 71% relative gain. OrcaLoca’s recall also barely grows with the cutoff (File Recall 0.61 to 0.64 from _k_ = 1 to 3, Line Recall flat at 0.35 from _k_ = 5 to 10) and its line overlap is low (IoU 0.04), indicating a short, highprecision candidate list. For a downstream repair agent this low recall at depth is a liability, since a faulty location absent from the list can never be repaired, which is the regime where ARISE’s higher recall at every cutoff matters most. Following prior fault-localization work [1, 7, 14], we focus on recall. For localization that feeds a repair agent, missing the faulty location is fatal, while a few extra candidates are cheap, since the agent can discard false positives while patching but can never fix a location it never retrieved. 

To test whether better localization explains the repair gains, we compute the Spearman correlation _ρ_ between Function R@1 and Pass@1 for each condition (Table 2). It rises steadily as the tools improve, from 0.05 (RAG) to 0.38 (SWE-agent), 0.42 (ARISE-Structural), and 0.53 (ARISE-Full). The rise from 0.42 to 0.51 when slicing is added (ARISE-Structural to ARISE-Slicing) lines up with the largest per-tier repair gain, which fits the view that finding the right function is what drives the fix. This correlation is suggestive but not conclusive. We measure localization and repair in separate runs, and easy issues tend to be both easier to localize and easier to fix, so difficulty alone could account for part of the correlation. 

7 

_ARISE: Graph Representation and Toolset_ _<u>for</u> Agentic Repair_ 

**Table 3.** SWE-bench Lite localization at file, function, and line granularities. Cov is N/A for baselines, applying only to ARISE’s budgeted context bundles. **Bold** : best; <u>underlined:</u> best baseline. Shading: per-column <mark>lowest</mark> to highest . 

|||**File**|||**Fun**|**ction**||||**Line**|||
|---|---|---|---|---|---|---|---|---|---|---|---|---|
|**Condition**|R@1|R@3|MRR|R@1|R@3|F1@3|MRR|R@1|R@5|R@10|Cov|IoU|
|_Baselines_|||||||||||||
|RAG|0.30|0.42|0.33|0.15|0.25|0.14|0.18|0.05|0.12|0.18|N/A|0.06|
|SWE-agent|0.57|0.72|0.60|0.43|0.58|0.39|0.48|0.26|0.43|0.53|N/A|0.21|
|OrcaLoca|0.61|0.64|0.62|0.35|0.42|0.29|0.43|0.28|0.35|0.35|N/A|0.04|
|_ARISE conditions_|||||||||||||
|ARISE-Structural|0.62|0.77|0.65|0.50|0.65|0.45|0.54|0.31|0.48|0.59|0.61|0.26|
|ARISE-Coarse|0.62|0.77|0.65|0.51|0.65|0.46|0.54|0.32|0.49|0.60|0.62|0.27|
|ARISE-Slicing|0.65|0.80|0.68|0.57|0.72|0.52|0.60|0.38|0.59|0.71|0.71|0.33|
|ARISE-Full|**0.67**|**0.82**|**0.70**|**0.60**|**0.75**|**0.55**|**0.63**|**0.41**|**0.62**|**0.74**|**0.75**|**0.36**|



We record this as a construct threat in Section 6. 

A per-instance view supports the same conclusion and avoids the difficulty confound, since it follows the same instances across the two conditions. Adding slicing newly localizes some instances to the correct function. Of those, 28% also turn from unresolved to resolved, against only 3% of the instances whose localization does not change. Almost all of the net repair gain at this step, a net of 6 instances, comes from this newly localized set. 

**Finding #2.** ARISE improves localization most at the function and line levels (relative gains of 40% Function R@1 and 58% Line R@1), surpassing even the dedicated localizer OrcaLoca (Function R@1 0.35), and this improvement is correlated with repair success ( _ρ_ up to 0.53), consistent with localization being the primary driver of the repair gain. 

### **4.4 RQ3: Component Contributions** 

This subsection isolates each component through controlled comparisons; Tables 2 and 3 provide the quantitative basis. 

Adding Tier 1 tools raises File R@1 by 0.05, Function R@1 by 0.07, and Pass@1 by 1.7 points. The SWEagent-to-ARISE-Structural repair gain (+1.7 points) is close to the +2.0 point delta between SWE-agent and SWE-agent + RepoGraph on GPT-4o [13], a consistent estimate of what a structural graph contributes. 

Adding Tier 2 yields the single largest within-tier improvement, with Function R@1 up 0.07, Line IoU up 0.07, and Pass@1 up 2.0 points (19.0% to 21.0%, 6 instances), because `get_dataflow_slice` directly answers which statements use a suspicious variable and where it was last defined, a query that otherwise requires multiple `traverse_relations` hops to approximate. 

Adding Tier 3 yields a further +1.0 point Pass@1 (21.0% to 22.0%, 3 instances) and +0.03 Function R@1. The total gain from SWE-agent to ARISE-Full decomposes as +1.7 (structural graph), +2.0 (slicing), and +1.0 

### (bundling). 

ARISE-Coarse exposes the `get_dataflow_slice` schema but builds the graph without Statement nodes or Dataflow edges, so `get_dataflow_slice` always returns empty. Its metrics sit within 0.01 of ARISEStructural (Function R@1 0.51 vs. 0.50, Pass@1 19.5% vs. 19.0%), confirming that the ARISE-Slicing improvement comes from the data-flow graph itself and not from the presence of an extra tool in the schema. 

**Finding #3.** No tier is redundant. Each one adds a consistent increment, and the data-flow graph rather than the tool schema drives the slicing gain. 

### **4.5 Portability Across Host Frameworks and Backbones** 

RQ1 through RQ3 isolate ARISE on a single host framework (SWE-agent) and a single backbone (Qwen2.5Coder-32B-Instruct). Because ARISE is a portable toolset rather than a SWE-agent extension, we now mount it on a second host and drive it with an additional backbone model to test whether it ports as a drop-in component. We mount ARISE on SWE-agent and AutoCodeRover [9], the two hosts that satisfy the tool-call contract of Section 3.3, running each standalone and with ARISE added (“+ARISE”), with a larger backbone, Qwen3.6-35B-A3B. We extend the study to one additional host because rerunning the full condition suite on a new framework is computationally expensive. We select AutoCodeRover because it ranks among the top open-submission systems on the SWE-bench Lite leaderboard, is widely reproduced in recent APR work, and exposes the tool-call interface ARISE requires. 

The same toolset mounts on both SWE-agent and AutoCodeRover without modification, which is the portability claim of contribution (3), but the effect on Pass@1 is host-dependent, rising on SWE-agent (33.3% to 34.0% with Qwen3.6-35B-A3B) yet falling on AutoCodeRover (36.3% to 32.0%). 

8 

_ARISE: Graph Representation and Toolset_ _<u>for</u> Agentic Repair_ 

**Table 4.** Portability of ARISE across host frameworks on Qwen3.6-35B-A3B. Pass@1 (resolved/300) on SWE-bench Lite. 

|**Framework**|**Pass@1**|
|---|---|
|SWE-agent|33.3% (100/300)|
|SWE-agent+RepoGraph|33.3% (100/300)|
|SWE-agent+ARISE|34.0% (102/300)|
|AutoCodeRover|36.3% (109/300)|
|AutoCodeRover+ARISE|32.0% (96/300)|



The benefit of external augmentation also shrinks as the backbone strengthens. On Qwen3.6-35B-A3B, SWEagent+RepoGraph matches the bare SWE-agent (both 33.3%) and ARISE adds only 0.7 points, against the larger margins both show on Qwen2.5-Coder-32B-Instruct. This is consistent with stronger models internalizing the structural navigation that graph tools provide, so we scope our contribution to the open-source regime where this augmentation matters most and leave a controlled study of representation value at frontier scale to future work. 

We attribute the AutoCodeRover drop to integration fit rather than to ARISE itself. AutoCodeRover is designed around its own search interface, tool signatures, and prompting strategy, which together let the agent use its native tools effectively. When ARISE is substituted without adapting this integration, Pass@1 falls to 3.6%, indicating that the default dispatch layer cannot exercise ARISE’s interface. After modifying AutoCodeRover’s tool-calling infrastructure to accommodate ARISE’s keyword and optional arguments, while leaving the rest of the agent unchanged, Pass@1 recovers to 32.0%, so most of the initial degradation stems from the integration layer rather than the toolset. AutoCodeRover with its native tools still outperforms AutoCodeRover+ARISE, a gap we attribute to the host being tuned end-to-end around its own tooling ecosystem, including its prompts, tool interfaces, and interaction patterns. This is consistent with the portability claim: the toolset ports cleanly, but its benefit depends on how well the host already exploits structure-aware search. 

**Finding #4.** The ARISE toolset ports as a drop-in component across host frameworks that satisfy the toolcall contract, and the size and direction of the Pass@1 effect depend on what the host already provides. 

## **5 Discussion** 

### **5.1 Why Data-flow Slicing Helps** 

The ARISE-Structural to ARISE-Slicing gain is mediated by function-level localization (Function R@1 rises from 0.50 to 0.57, the largest within-tier jump), while the file-level gain is only 0.03 because issue reports usually name the module or class, so BM25 

and `search_entities` already locate the file. The remaining bottleneck is distinguishing the faulty function from its structurally adjacent neighbors, which `get_dataflow_slice` addresses directly, since a backward slice traces the variable’s definition chain to the defining statement, collapsing a multi-hop traversal into a single DataflowUseDef breadth-first search (BFS). 

### **5.2 Failure Analysis** 

**Table 5.** Failure-mode breakdown for ARISE-Slicing ( _n_ = 237 failed instances) and ARISE-Full ( _n_ = 234). Count is the number of unresolved instances. 

||ARISE-S|licing|ARISE-|Full|
|---|---|---|---|---|
|**Failure category**|**Count**|**%**|**Count**|**%**|
|Wrong file|107|45%|99|42%|
|Right file, wrong fn|59|25%|58|25%|
|Right fn, failed repair|47|20%|51|22%|
|Incomplete localization|24|10%|26|11%|



Table 5 shows where the remaining failures concentrate. The dominant mode, wrong file at 45% of ARISESlicing failures (107 of 237), is a direct consequence of the intra-procedural scope limit. `get_dataflow_slice` stops at Calls edges, so a bug whose root cause is a value mutated inside a callee is invisible to the backward slice. ARISE-Full reduces this from 45% to 42% because `rank_suspect_regions` expands candidates through the Calls subgraph, a structural surrogate rather than a data-flow solution. The right-function, failed-repair category (20%) is orthogonal to localization, since these instances are correctly localized but require a multi-hunk edit or a semantic invariant not visible locally, so better localization cannot raise their ceiling. 

### **5.3 Cost and Efficiency** 

**Table 6.** Mean token consumption ( _×_ 1000) per instance by tool type. Totals include prompt and generation overhead not itemized. 

|**Condition**|**Search **|**Traverse **|**Slice **|**Bundle **|**Total**|
|---|---|---|---|---|---|
|_Baselines_||||||
|RAG|N/A|N/A|N/A|N/A|13.0|
|SWE-agent|N/A|N/A|N/A|N/A|510|
|_ARISE conditions_||||||
|ARISE-Structural|5.1|24.9|N/A|N/A|531|
|ARISE-Coarse|5.1|24.9|5.0|N/A|535|
|ARISE-Slicing|5.1|21.6|23.3|N/A|550|
|ARISE-Full|5.1|21.6|23.3|10|560|



**Token cost.** Table 6 breaks down mean token consumption by tool type. ARISE is not cheaper than its host; ARISE-Full consumes about 560,000 tokens per instance against 510,000 for the bare SWE-agent baseline, 

9 

_ARISE: Graph Representation and Toolset_ _<u>for</u> Agentic Repair_ 

so the toolset buys additional fixes at additional token cost rather than saving tokens. The ARISE-Coarse slice column (5 000 tokens) is empty-slice response overhead, confirming that a failed `get_dataflow_slice` call is cheap and does not explain the ARISE-Slicing gain, which instead costs a 3.6% token increase over ARISEStructural for 6 additional resolved instances, near 3 200 tokens per additional fix. At roughly 3 200 additional tokens per extra resolved instance, the overhead is modest relative to the repair gain, and practitioners who prioritize fix rate over cost efficiency could find the trade-off favorable. 

**Graph construction cost.** The graph is built once per repository and cached to a pickle, after which every instance that shares the repository loads it essentially instantly. Across the 11 SWE-bench Lite repositories, each graph holds a median of 134,000 nodes and 313,000 edges (up to 264,000 and 780,000), of which a median of 94,000 are statement-level nodes. First-build time ranges from 1.7s for the smallest repository ( `requests` ) through roughly 2.2 minutes for a median one ( `django` ) to roughly 33 minutes for the largest ( `sympy` ), with the data-flow pass accounting for 96% to 99% of the total. Because this build is paid once and amortized over the many SWE-bench Lite instances that share each repository, its per-instance contribution is small even for `sympy` . 

### **5.4 How ARISE Impacts Agent Behavior: A Deeper Look** 

All the analysis in this section draws from agent trajectories recorded during the Qwen3.6-35B-A3B experiments, comparing SWE-agent alone against SWE-agent+ARISE. Despite nearly identical aggregate repair rates, the trajectory-level behavior differs substantially, revealing how ARISE’s fault localization reshapes the repair process even when the end result looks similar. 

**Patch Type Distribution.** As Figure 2 shows, developer-written patches always target a single file. Without ARISE, SWE-agent frequently edits multiple files, spreading changes across unrelated locations. ARISE reduces multi-file patches by 81%, pulling the distribution close to the oracle. Multi-file patches are harder to review, more likely to introduce regressions in untouched code, and signal that the agent was uncertain about the fault location; this reduction shows that ARISE’s fault localization reduces that uncertainty. 

**Hunk Count Distribution.** SWE-agent+ARISE produces a compact hunk count distribution (median=2, max=3 per patch), closely matching the oracle, while SWE-agent shows a wider spread with outliers reaching 6 hunks. Fewer hunks mean the agent is editing in one coherent location rather than scattering small changes across a file, which is the structural signature of a more 



<!-- Start of picture text -->
Single-hunk Multi-hunk Multi-file No patch<br>SWE-agent 32% 6% 38% 23%<br>SWE-agent+ARISE 47% 11%7% 34%<br>Golden (oracle) 63% 37%<br>0% 20% 40% 60% 80% 100%<br>Percentage of Instances (%)<br><!-- End of picture text -->

**Figure 2.** Patch type distribution comparing SWE-agent and ARISE with oracle. 



<!-- Start of picture text -->
SWE-agent+ARISE SWE-agent<br>0 10 20 30<br>Trajectory Length (steps)<br>Resolved<br>Unresolved<br><!-- End of picture text -->

**Figure 3.** Trajectory length distribution for resolved and unresolved instances across SWE-agent and SWE-agent+ARISE. 

### precisely localized patch. 

**Trajectory Length Distribution.** As Figure 3 shows, SWE-agent+ARISE resolves instances in fewer steps than SWE-agent. SWE-agent’s step-count distribution for resolved instances shows that it often converges on a fix only as it approaches the 32-step cap. For unresolved instances, both systems show near identical distributions. 

**Tool Call Distribution.** Analyzing the mean number of tool calls per problem instance reveals that SWEagent+ARISE makes proportionally more file-edit and test-execution calls while making fewer navigation and environment-setup calls. Among instances that produced a patch, ARISE averages 2.52 file-edit calls and 7.22 execution calls per instance, compared to 0.76 and 4.55 for the baseline, a 3 _._ 3 _×_ and 1 _._ 6 _×_ increase, respectively. Conversely, navigation calls drop from 17.88 to 12.90 (a 28% reduction) and environment-setup calls drop from 3.02 to 1.22 (a 60% reduction). This shift reflects what fault localization enables; instead of searching broadly for the relevant file, the agent can spend its budget editing and verifying a precise target. In effect, ARISE converts navigation time into editing time, the budget allocation that leads to successful patches. 

### **5.5 Illustrative Example** 

We examine instance `matplotlib__matplotlib -26011` where the baseline produced an empty patch, failing to lo- 

10 

_ARISE: Graph Representation and Toolset_ _<u>for</u> Agentic Repair_ 

|+self.axes.callbacks.process(f"{name}lim_changed", self.axes)|
|---|
|if emit:|
|-    self.axes.callbacks.process(f"{name}lim_changed", self.axes)|
|# Call shared axes|
|for other in shared_axes:<br>...|



**Figure 4.** The patch diff generated for instance `matplotlib__matplotlib-26011` using SWE-agent+ARISE. 

cate the bug. The fix (Figure 4) required understanding a subtle semantic mismatch between the reported symptom and the root cause, a misplaced callback in a private internal method `_set_lim` with no lexical connection. ARISE provides tools such as `arise_rank_suspects` that enabled the agent to bridge this gap. A follow-up tool `arise_search` confirmed the exact location in five tool calls before any edit was made. 

### **5.6 Implications** 

For researchers, the central finding is that data-flow analysis should be treated as a retrieval primitive rather than a preprocessing step. The 0.17 gain in Function R@1 from ARISE’s graph and slicing tools, with data-flow slicing the single largest contributor, shows that the precision of what the agent retrieves matters more than the volume, and that structural graphs alone leave a gap that semantic information closes. Systems that invest in richer program representations can close this gap while using smaller, open-source models, which implies that improving graph representation quality is a more costeffective path to higher repair than scaling up to larger proprietary models. 

For practitioners, ARISE can be adopted without modifying the host framework: the toolset mounts on any system that exposes a tool-call interface. The benefit depends on what the host already provides structurally, with a host that already has structure-aware retrieval gaining less from the structural tier while still benefiting from the data-flow layer, making the data-flow component the most transferable part of the system. 

## **6 Threats to Validity** 

### **6.1 Internal Validity** 

The most serious internal threat is benchmark contamination of the backbone. SWE-bench Lite is drawn from public repositories, so its gold patches may appear in Qwen2.5-Coder-32B-Instruct’s pretraining data, potentially inflating absolute resolve rates for every condition that uses the model. This threat is mitigated by the study design: ARISE, all baselines, and all ablations share the same backbone, so contamination is held constant and cannot explain the within-setting deltas we report. We further reduce retrieval-time leakage by building the graph from the base-commit snapshot, preventing 

future patches and issue comments from entering the agent context; memorization inside the model weights applies equally to all conditions. 

Pass@1 is measured from a single patch attempt per instance, so run-to-run sampling variance is not averaged out. To ensure comparability, we hold decoding configuration, turn count, and token budget constant across all conditions, so observed deltas reflect toolset differences rather than stochastic variation. Repair success is judged by project test suites, which can pass a patch that edits a different location than the gold patch; this is examined under construct validity. 

### **6.2 External Validity** 

SWE-bench Lite covers Python only and favors repositories with well-structured issue reports and deterministic test suites, so the absolute resolve rates may not transfer to other languages or project types. This is consistent with all recent work evaluated on SWE-bench Lite [7, 9, 11, 13, 15], which is the standard evaluation suite for repository-level APR, making our results directly comparable to prior work. 

The def-use analysis is Python-specific, using `ast` node types; extending ARISE to other languages would require language-specific frontends. The intraprocedural analysis covers the most common Python assignment patterns; when a pattern is not instrumented, `get_dataflow_slice` returns an empty slice and the agent falls back to `get_code_span` , so the typical effect of a missed pattern is neutral degradation rather than a wrong result. 

### **6.3 Construct Validity** 

Pass@1 measures whether the first patch passes all project tests, which can diverge from editing the ground-truth location. SWE-bench Lite’s project test suites are a widely accepted and validated proxy for repair correctness, used as the primary metric across the recent APR literature [6, 11, 13], so the metric measures what we intend to measure. To further guard against over-reliance on a single metric, we separately report localization recall at file, function, and line granularity and examine the Spearman correlation between localization and repair success; the two are measured in independent sessions so the correlation reflects an association rather than a within-run causal trace. 

The most significant architectural limitation is that `get_dataflow_slice` does not cross Calls edges and so cannot trace a root cause mutated inside a callee. This failure mode is analyzed as the dominant wrongfile category in Section 5.2 and is the primary direction for future work. It reflects a scope boundary of the current design rather than a flaw in the experimental methodology. 

11 

_ARISE: Graph Representation and Toolset_ _<u>for</u> Agentic Repair_ 

## **7 Conclusions** 

We presented ARISE, a portable, framework-agnostic toolset that builds a multi-granularity program graph and exposes intra-procedural data-flow slicing as a firstclass agent primitive, mounting on any host framework that supports tool calls. The central finding is that dataflow precision matters more than model scale, since a graph-augmented open-source model outperforms configurations using larger proprietary models because data-flow slicing lets the agent answer “which statements define this variable?” in a single call rather than searching broadly. Controlled ablations confirm that the data-flow graph, not the mere presence of an extra tool in the schema, drives the gain. On SWE-bench Lite with SWE-agent as host, ARISE resolves 22.0% of issues (66/300), a 4.7 percentage-point gain over SWE-agent that our analysis attributes to sharper fault localization (Function R@1 rising from 0.43 to 0.60) and, through controlled ablations, to the data-flow graph rather than the tool schema. ARISE is portable across host frameworks, with the benefit depending on what the host already provides. The primary direction for future work is extending slicing from intra-procedural to inter-procedural scope; a secondary direction is incorporating data-flow representations into RAG-based retrieval for non-agentic repair systems. 

## **References** 

- [1] S. B. Hossain, N. Jiang, Q. Zhou, X. Li, W.-H. Chiang, Y. Lyu, H. Nguyen, and O. Tripp, “A deep dive into large language models for automated bug localization and repair,” _Proceedings of the ACM on Software Engineering_ , vol. 1, no. FSE, pp. 1471–1493, 2024. 

- [2] D. H. O’Dell, “The debugging mindset,” _ACM Queue_ , vol. 15, no. 1, p. 50, 2017. [Online]. Available: https: //doi.org/10.1145/3055301.3068754 

- [3] T. Britton, L. Jeng, G. Carver, P. Cheak, and T. Katzenellenbogen, “Reversible debugging software,” Tech. Rep., 2013. 

- [4] Q. Zhang, C. Fang, Y. Xie, Y. Ma, W. Sun, Y. Yang, and Z. Chen, “A systematic literature review on large language models for automated program repair,” _ACM Transactions on Software Engineering and Methodology_ , 2026. 

- [5] R. Potvin and J. Levenberg, “Why google stores billions of lines of code in a single repository,” _Communications of the ACM_ , vol. 59, no. 7, pp. 78–87, 2016. 

- [6] C. E. Jimenez, J. Yang, A. Wettig, S. Yao, K. Pei, O. Press, and K. Narasimhan, “Swe-bench: Can language models resolve real-world github issues?” _arXiv preprint arXiv:2310.06770_ , 2023. 

- [7] Z. Yu, H. Zhang, Y. Zhao, H. Huang, M. Yao, K. Ding, and J. Zhao, “OrcaLoca: An LLM agent framework for software issue localization,” in _Proceedings of the 42nd International Conference on Machine Learning_ , ser. Proceedings of Machine Learning Research, vol. 267. PMLR, 2025, pp. 73 416–73 436. [Online]. Available: https://proceedings.mlr.press/v267/yu25x.html 

- [8] J. Yang, C. E. Jiménez, A. Wettig, K. Lieret, S. Yao _et al._ , “Swe-agent: Agent-computer interfaces enable automated software engineering,” in _Advances in Neural Information Processing Systems (NeurIPS)_ , 2024. [Online]. Available: https://papers.nips.cc/paper_files/paper/2024/file/5a7c94 7568c1b1328ccc5230172e1e7c-Paper-Conference.pdf 

- [9] Y. Zhang, H. Ruan, Z. Fan, and A. Roychoudhury, “AutoCodeRover: Autonomous program improvement,” in _Proceedings of the 33rd ACM SIGSOFT International Symposium on Software Testing and Analysis_ , ser. ISSTA ’24. New York, NY, USA: ACM, 2024, pp. 1592–1604. 

- [10] H. Ruan, Y. Zhang, and A. Roychoudhury, “SpecRover: Code intent extraction via LLMs,” in _Proceedings of the IEEE/ACM 47th International Conference on Software Engineering (ICSE)_ . IEEE, 2025, pp. 963–974. 

- [11] C. S. Xia, Y. Deng, S. Dunn, and L. Zhang, “Demystifying LLM-based software engineering agents,” _Proceedings of the ACM on Software Engineering_ , vol. 2, no. FSE, pp. 801–824, 2025. 

- [12] C. Xie, B. Li, C. Gao, H. Du, W. Lam, D. Zou, and K. Chen, “SWE-fixer: Training open-source LLMs for effective and efficient GitHub issue resolution,” in _Findings of the Association for Computational Linguistics: ACL 2025_ , W. Che, J. Nabende, E. Shutova, and M. T. Pilehvar, Eds. Vienna, Austria: Association for Computational Linguistics, Jul. 2025, pp. 1123–1139. [Online]. Available: https://aclanthology.org/2025.findings-acl.62/ 

- [13] S. Ouyang, W. Yu, K. Ma, Z. Xiao, Z. Zhang, M. Jia, J. Han, H. Zhang, and D. Yu, “Repograph: Enhancing ai software engineering with repository-level code graph,” in _Proceedings of the International Conference on Learning Representations (ICLR)_ , 2025. [Online]. Available: https://proceedings.iclr.cc/paper_files/paper/2025/file/4a 4a3c197deac042461c677219efd36c-Paper-Conference.pdf 

- [14] Z. Chen, X. Tang, G. Deng, F. Wu, J. Wu, Z. Jiang, V. Prasanna, A. Cohan, and X. Wang, “LocAgent: Graph-guided LLM agents for code localization,” in _Proceedings of the 63rd Annual Meeting of the Association for Computational Linguistics (Volume 1: Long Papers)_ . Vienna, Austria: Association for Computational Linguistics, Jul. 2025, pp. 8697–8727. [Online]. Available: https: //aclanthology.org/2025.acl-long.426/ 

- [15] B. Yang, J. Ren, S. Jin, Y. Liu, F. Liu, B. Le, and H. Tian, “Enhancing repository-level software repair via repository-aware knowledge graphs,” _arXiv preprint arXiv:2503.21710_ , 2025. 

- [16] X. Liu, B. Lan, Z. Hu, Y. Liu, Z. Zhang, F. Wang, M. Q. Shieh, and W. Zhou, “Codexgraph: Bridging large language models and code repositories via code graph databases,” in _Proceedings of the 2025 Conference of the Nations of the Americas Chapter of the Association for Computational Linguistics: Human Language Technologies (Volume 1: Long Papers)_ , 2025, pp. 142–160. 

- [17] H. Tao, Y. Zhang, Z. Tang, H. Peng, X. Zhu, B. Liu, Y. Yang, Z. Zhang, Z. Xu, H. Zhang, L. Zhu, R. Wang, H. Yu, J. Li, and P. Di, “Code graph model (CGM): A graph-integrated large language model for repository-level software engineering tasks,” in _Advances in Neural Information Processing Systems (NeurIPS)_ , 2025, arXiv:2505.16901. 

12 

_ARISE: Graph Representation and Toolset_ _<u>for</u> Agentic Repair_ 

- [18] Z. Jiang, X. Ren, M. Yan, W. Jiang, Y. Li, and Z. Liu, “CoSIL: Software issue localization via LLM-driven code repository graph searching,” in _Proceedings of the 40th IEEE/ACM International Conference on Automated Software Engineering (ASE)_ . IEEE, 2025, arXiv:2503.22424. 

- [19] D. Guo, S. Ren, S. Lu, Z. Feng, D. Tang, S. Liu, L. Zhou, N. Duan _et al._ , “Graphcodebert: Pre-training code representations with data flow,” in _ICLR_ , 2021. [Online]. Available: https://openreview.net/forum?id=jLoC4ez43PZ 

- [20] C. Xu, Z. Liu, X. Ren, G. Zhang, M. Liang, and D. Lo, “FlexFL: Flexible and effective fault localization with opensource large language models,” _IEEE Transactions on Software Engineering_ , 2025. 

- [21] J. A. Jones and M. J. Harrold, “Empirical evaluation of the tarantula automatic fault-localization technique,” in _Proceedings of the 20th IEEE/ACM international Conference on Automated software engineering_ , 2005, pp. 273–282. 

- [22] M. Papadakis and Y. Le Traon, “Metallaxis-fl: mutationbased fault localization,” _Software Testing, Verification and Reliability_ , vol. 25, no. 5-7, pp. 605–628, 2015. 

- [23] C. Liu, Y. Lei, H. Xie, J. Wang, Y. Yu, and D. Lo, “Survey on learning-based dynamic fault localization: From traditional machine learning to large language models,” _ACM Computing Surveys_ , vol. 58, no. 9, pp. 1–39, 2026. 

- [24] Y. Qin, S. Wang, Y. Lou, J. Dong, K. Wang, X. Li, and X. Mao, “Agentfl: Scaling llm-based fault localization to project-level context,” _arXiv preprint arXiv:2403.16362_ , 2024. 

- [25] M. Sepidband, H. Taherkhani, H. Viet Pham, and H. Hemmati, “Rgfl: Reasoning guided fault localization for automated program repair using large language models,” _arXiv e-prints_ , pp. arXiv–2601, 2026. 

   - [32] A. Utture, S. Liu, C. G. Kalhauge, and J. Palsberg, “Striking a balance: pruning false-positives from static call graphs,” in _Proceedings of the 44th International Conference on Software Engineering_ , 2022, pp. 2043–2055. 

   - [33] F. Shi, X. Chen, K. Misra, N. Scales, D. Dohan, E. H. Chi, N. Schärli, and D. Zhou, “Large language models can be easily distracted by irrelevant context,” in _International Conference on Machine Learning_ . PMLR, 2023, pp. 31 210–31 227. 

   - [34] F. Mu, J. Wang, L. Shi, S. Wang, S. Li, and Q. Wang, “EXPEREPAIR: Dual-memory enhanced LLM-based repositorylevel program repair,” _arXiv preprint arXiv:2506.10484_ , 2025. 

   - [35] Y. Ma, Q. Yang, R. Cao, B. Li, F. Huang, and Y. Li, “Alibaba LingmaAgent: Improving automated issue resolution via comprehensive repository exploration,” in _Companion Proceedings of the 33rd ACM International Conference on the Foundations of Software Engineering_ , ser. FSE Companion ’25. New York, NY, USA: ACM, 2025. 

   - [36] Amazon Web Services, “Reimagining software development with the Amazon Q Developer Agent,” https://aws.amazon .com/blogs/machine-learning/reimagining-software-devel opment-with-the-amazon-q-developer-agent/, 2024, aWS Machine Learning Blog. 

   - [37] B. Hui, J. Yang, Z. Cui, J. Yang, D. Liu, L. Zhang, T. Liu, J. Zhang, B. Yu, K. Dang, A. Yang, R. Men, F. Huang, X. Ren, X. Ren, J. Zhou, and J. Lin, “Qwen2.5-Coder technical report,” _arXiv preprint arXiv:2409.12186_ , 2024. 

   - [38] W. Kwon, Z. Li, S. Zhuang, Y. Sheng, L. Zheng, C. H. Yu, J. E. Gonzalez, H. Zhang, and I. Stoica, “Efficient memory management for large language model serving with PagedAttention,” in _Proceedings of the ACM SIGOPS 29th Symposium on Operating Systems Principles_ , ser. SOSP ’23. ACM, 2023. 

- [26] F. Zhang, B. Chen, Y. Zhang, J. Keung, J. Liu, D. Zan, Y. Mao, J.-G. Lou, and W. Chen, “Repocoder: Repository-level code completion through iterative retrieval and generation,” in _Proceedings of the 2023 Conference on Empirical Methods in Natural Language Processing_ , 2023, pp. 2471–2484. 

- [27] Y. Wang, Y. Wang, D. Guo, J. Chen, R. Zhang, Y. Ma, and Z. Zheng, “Rlcoder: Reinforcement learning for repositorylevel code completion,” in _2025 IEEE/ACM 47th International Conference on Software Engineering (ICSE)_ . IEEE, 2025, pp. 1140–1152. 

- [28] W. Liu, Y. Sun, J. Wei, Y. Li, Y. Chen, H. Zhao, S. Wang, S. Fu, G. Sun, and K. Zhang, “Graphcoder: Enhancing repository-level code completion via coarse-to-fine retrieval based on code context graph,” in _Proceedings of the 39th IEEE/ACM International Conference on Automated Software Engineering (ASE)_ . IEEE/ACM, 2024, pp. 570–582. [Online]. Available: https://dl.acm.org/doi/10.1145/3691620.3695054 

- [29] M. Weiser, “Program slicing,” _IEEE Transactions on Software Engineering_ , no. 4, pp. 352–357, 1981. 

- [30] S. Horwitz, T. Reps, and D. Binkley, “Interprocedural slicing using dependence graphs,” _ACM Transactions on Programming Languages and Systems (TOPLAS)_ , vol. 12, no. 1, pp. 26–60, 1990. 

- [31] F. Tipp, “A survey of program slicing techniques,” _Journal of programming languages_ , vol. 3, no. 3, pp. 121–189, 1995. 

13 

