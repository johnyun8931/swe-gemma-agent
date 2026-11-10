"""Commit-specific Python symbol graphs and compatible embedding artifacts.

The built-in encoder uses deterministic token hashing, a lexical baseline rather
than a learned semantic model. Import mode supports externally generated graphs
and numerical embeddings in the original harness's format.
"""
from __future__ import annotations

import ast
import gc
import hashlib
import io
import json
import re
import shutil
import tarfile
from pathlib import Path

from .artifacts import write_json
from .data import file_hash

GRAPH_CONFIG = {"builder": "python-ast-v1", "encoder": "sha256-token-hash-v1", "dimensions": 256}
RUNTIME_REPRESENTATION = "inline-float32-v1"


def graph_key(row: dict) -> str:
    return row["repo"].replace("/", "_") + "_" + row["base_commit"]


def build_graph(sources: dict[str, str], row: dict):
    import networkx as nx
    import numpy as np

    graph = nx.MultiDiGraph(repo_name=row["repo"], base_commit=row["base_commit"], **GRAPH_CONFIG)
    functions = {}
    skipped = []
    for filename, source in sorted(sources.items()):
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            skipped.append(filename)
            continue
        module = filename[:-3].replace("/", ".")
        if module.endswith(".__init__"):
            module = module[:-9]

        def visit(body, scope):
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = ".".join([scope, node.name]).strip(".")
                    text = ast.get_source_segment(source, node) or ""
                    graph.add_node(name, name=name, text=text, file_path=filename,
                                   line_number=node.lineno, kind=type(node).__name__)
                    functions[name] = node
                    if scope in graph:
                        graph.add_edge(scope, name, type="contains")
                    visit(node.body, name)

        visit(tree.body, module)
    by_short = {}
    for name in graph:
        by_short.setdefault(name.rsplit(".", 1)[-1], []).append(name)
    for name, node in functions.items():
        # Only inspect this scope's calls; nested declarations own their own calls.
        stack = list(ast.iter_child_nodes(node))
        while stack:
            child = stack.pop()
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Call):
                called = ast.unparse(child.func)
                short = called.rsplit(".", 1)[-1]
                local = name.rsplit(".", 1)[0] + "." + short
                target = local if local in graph else (
                    by_short[short][0] if len(by_short.get(short, [])) == 1 else None)
                if target:
                    graph.add_edge(name, target, type="calls")
            stack.extend(ast.iter_child_nodes(child))
    embeddings = {}
    for name, attrs in graph.nodes(data=True):
        vector = np.zeros(GRAPH_CONFIG["dimensions"], dtype=np.float32)
        tokens = re.findall(r"[a-z_][a-z_0-9]*", (name + "\n" + attrs["text"]).lower())
        for token in tokens:
            value = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(value[:4], "little") % len(vector)
            vector[index] += 1 if value[4] & 1 else -1
        norm = np.linalg.norm(vector)
        if norm:
            vector /= norm
        embeddings[name] = vector
    if not embeddings:
        raise ValueError(f"No Python symbols found for {row['instance_id']}")
    return graph, embeddings, skipped


def validate_graphs(root: Path, row: dict) -> dict:
    import networkx as nx
    import numpy as np

    key = graph_key(row)
    graph_file, embedding_file = root / f"{key}.json", root / f"{key}.npz"
    metadata = json.loads((root / f"{key}.meta.json").read_text())
    if metadata.get("repo") != row["repo"] or metadata.get("base_commit") != row["base_commit"]:
        raise ValueError(f"Graph commit mismatch for {key}")
    if not metadata.get("configuration"):
        raise ValueError("Graph metadata must describe its builder and embedding configuration")
    for path in (graph_file, embedding_file):
        if metadata.get("sha256", {}).get(path.suffix) != file_hash(path):
            raise ValueError(f"Graph artifact checksum mismatch: {path}")
    raw = json.loads(graph_file.read_text())
    graph = nx.node_link_graph(raw, edges="edges" if "edges" in raw else "links")
    with np.load(embedding_file, allow_pickle=False) as vectors:
        if not graph or set(graph) != set(vectors.files):
            raise ValueError("Graph symbols and embedding keys must match exactly")
        shapes = set()
        for key in vectors.files:
            value = vectors[key]
            if value.ndim != 1 or not value.size or not np.isfinite(value).all() or not np.linalg.norm(value):
                raise ValueError(f"Invalid embedding for {key}")
            shapes.add(value.shape)
        if len(shapes) != 1:
            raise ValueError("Embedding dimensions must match")
    return metadata


def sources_from_sandbox(manager) -> dict[str, str]:
    """Copy tracked Python files only, with no git history or evaluation artifacts."""
    cid = manager.start()
    try:
        manager.prepare(cid)
        script = """
import io, pathlib, subprocess, tarfile
files = subprocess.check_output(['git', 'ls-files', '-z', '*.py']).decode().split('\\0')
with tarfile.open('/tmp/swegemma-source.tar', 'w') as tar:
    for name in files:
        p = pathlib.Path(name)
        if name and p.is_file() and not p.is_symlink():
            tar.add(p, arcname=name, recursive=False)
"""
        import shlex

        result = manager.exec(cid, "/usr/bin/python3 -c " + shlex.quote(script))
        if result.exit_code:
            raise RuntimeError(result.stderr or result.stdout)
        stream, _ = manager.client.containers.get(cid).get_archive("/tmp/swegemma-source.tar")
        outer = tarfile.open(fileobj=io.BytesIO(b"".join(stream)))
        with outer:
            member = outer.extractfile("swegemma-source.tar")
            if member is None:
                raise RuntimeError("Missing source archive")
            with tarfile.open(fileobj=io.BytesIO(member.read())) as archive:
                return {entry.name: archive.extractfile(entry).read().decode("utf-8", errors="replace")
                        for entry in archive if entry.isfile()}
    finally:
        manager.cleanup_all()


def write_runtime_graph(source: Path, embeddings: Path, target: Path) -> None:
    """Inline the same vectors to bypass upstream's expensive symbol cache misses.

    The source cache stays unchanged. On large repositories, the upstream
    symbol/text LRU can evict vectors while loading them, causing repeated
    full-repository suffix searches. Its native inline-vector format avoids
    those lookups without changing topology, text, or numerical embeddings.
    """
    import numpy as np

    data = json.loads(source.read_text())
    with np.load(embeddings, allow_pickle=False) as vectors:
        for node in data["nodes"]:
            node["embedding"] = np.asarray(vectors[node["id"]], dtype=np.float32).tolist()
    temporary = target.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(data, output, separators=(",", ":"), ensure_ascii=False)
        output.write("\n")
    temporary.replace(target)


def clear_runtime_caches() -> None:
    """Release commit-specific graphs between tasks in a long evaluation run."""
    from swegemma.graph.retrieval_utils import clear_caches

    clear_caches()
    gc.collect()


def ensure_graphs(cache: Path, run_dir: Path, row: dict, mode: str, manager=None) -> dict:
    import networkx as nx
    import numpy as np

    key = graph_key(row)
    if mode == "build":
        cache = cache / "python-ast-v1_sha256-token-hash-v1_256"
    cache.mkdir(parents=True, exist_ok=True)
    if not (cache / f"{key}.meta.json").exists():
        if mode == "import":
            raise ValueError(f"Missing graph metadata: {cache / (key + '.meta.json')}")
        graph, embeddings, skipped = build_graph(sources_from_sandbox(manager), row)
        graph_file, embedding_file = cache / f"{key}.json", cache / f"{key}.npz"
        write_json(graph_file, nx.node_link_data(graph, edges="edges"))
        np.savez_compressed(embedding_file, **embeddings)
        write_json(cache / f"{key}.meta.json", {
            "repo": row["repo"], "base_commit": row["base_commit"], "configuration": GRAPH_CONFIG,
            "skipped_files": skipped,
            "sha256": {".json": file_hash(graph_file), ".npz": file_hash(embedding_file)},
        })
    metadata = validate_graphs(cache, row)
    if mode == "build" and metadata["configuration"] != GRAPH_CONFIG:
        raise ValueError("Cached graph builder configuration changed")
    for suffix, directory in ((".json", "graphs"), (".npz", "embeddings")):
        target = run_dir / directory / (key + suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        if suffix == ".json":
            write_runtime_graph(cache / (key + suffix), cache / (key + ".npz"), target)
        else:
            shutil.copyfile(cache / (key + suffix), target)
    return dict(metadata, runtime_representation=RUNTIME_REPRESENTATION,
                runtime_sha256={suffix: file_hash(run_dir / directory / (key + suffix))
                                for suffix, directory in ((".json", "graphs"), (".npz", "embeddings"))})
