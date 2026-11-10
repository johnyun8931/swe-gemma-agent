"""SWE-bench images and a sandbox compatible with the existing Gemma tools."""
from __future__ import annotations

import platform
import shlex
import shutil
import urllib.request
from importlib.metadata import version
from pathlib import Path

SWEBENCH_VERSION = "4.1.0"


def check_harness() -> None:
    installed = version("swebench")
    if installed != SWEBENCH_VERSION:
        raise ValueError(f"Expected swebench=={SWEBENCH_VERSION}, found {installed}; install requirements-swebench.txt")


def docker_client(min_free_gb: float, output_dir: Path):
    import docker

    try:
        client = docker.from_env(timeout=600)
        client.ping()
    except docker.errors.DockerException as exc:
        raise RuntimeError("Docker is unavailable. Install/start Docker Engine and make its daemon "
                           "accessible to your user (or configure DOCKER_HOST).") from exc
    existing = output_dir
    while not existing.exists():
        existing = existing.parent
    free = shutil.disk_usage(existing).free / 1024**3
    if free < min_free_gb:
        client.close()
        raise RuntimeError(f"Only {free:.1f} GiB free on output filesystem; need {min_free_gb:g}. "
                           "Also check free space on the Docker daemon's image filesystem.")
    return client


def check_server(url: str, *, api_key: str = "EMPTY", backend: str = "local") -> None:
    try:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
    except Exception as exc:
        hint = ("Start: .venv/bin/python start_server.py" if backend == "local"
                else "Check the cloud health URL and CLOUD_API_KEY")
        raise RuntimeError(f"Model server is unavailable at {url}. {hint}") from exc


def test_spec(row: dict, image_mode: str):
    check_harness()
    from swebench.harness.test_spec.test_spec import make_test_spec

    if platform.machine() not in ("x86_64", "AMD64"):
        raise RuntimeError("This entry point currently requires an x86_64 host, matching the pinned grading harness.")
    return make_test_spec(row, namespace="swebench" if image_mode == "pull" else None, arch="x86_64")


def prepare_image(client, row: dict, image_mode: str) -> tuple[str, str]:
    from docker.errors import ImageNotFound

    spec = test_spec(row, image_mode)
    image = spec.instance_image_key
    try:
        existing = client.images.get(image)
        return image, existing.id
    except ImageNotFound:
        pass
    if image_mode == "pull":
        print(f"Pulling {image}", flush=True)
        client.images.pull(image, platform=spec.platform)
    else:
        from swebench.harness.docker_build import build_env_images, build_instance_image

        _, failed = build_env_images(client, [spec], False, 1)
        if failed:
            raise RuntimeError(f"Failed to build environment for {row['instance_id']}: {failed}")
        build_instance_image(spec, client, None, False)
    return image, client.images.get(image).id


def workspace_setup_script(commit: str, *, repository: str = "/testbed", workspace: str = "/workspace",
                           scrub_paths: list[str] | None = None) -> str:
    """Container administrative script; parameterized paths allow an isolated test."""
    if scrub_paths is None:
        scrub_paths = ["/root/setup_repo.sh", "/root/setup_env.sh", "/root/.bash_history",
                       "/root/.gitconfig", "/eval.sh", "/patch.diff", "/tmp/patch.diff"]
    return """
import os, pathlib, shutil, subprocess
root = pathlib.Path(REPOSITORY)
commit = COMMIT
subprocess.run(['git', '-C', str(root), 'reset', '--hard', commit], check=True)
subprocess.run(['git', '-C', str(root), 'clean', '-fd'], check=True)
if subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip() != commit:
    raise RuntimeError('Unexpected base commit')
git_dir = root / '.git'
if not git_dir.is_dir() or git_dir.is_symlink():
    raise RuntimeError('Expected a standalone repository in /testbed')
shutil.rmtree(git_dir)
for name in SCRUB_PATHS:
    p = pathlib.Path(name)
    if p.is_file() or p.is_symlink():
        p.unlink()
workspace = pathlib.Path(WORKSPACE)
if workspace.is_symlink():
    workspace.unlink()
elif workspace.exists():
    if any(workspace.iterdir()):
        raise RuntimeError('/workspace must be empty')
    workspace.rmdir()
# Overlay filesystems can reject directory rename across image layers with
# EXDEV. shutil.move falls back to copying the tree and removing the source.
shutil.move(str(root), str(workspace))
root.symlink_to(workspace, target_is_directory=True)
subprocess.run(['git', 'init', '-q', str(root)], check=True)
subprocess.run(['git', '-C', str(root), 'config', 'user.email', 'agent@localhost'], check=True)
subprocess.run(['git', '-C', str(root), 'config', 'user.name', 'SWE-bench agent'], check=True)
""".replace("COMMIT", repr(commit)).replace("REPOSITORY", repr(repository)).replace(
        "WORKSPACE", repr(workspace)).replace("SCRUB_PATHS", repr(scrub_paths))


def make_sandbox(row: dict, image_id: str, *, mem_limit: str, cpus: int, timeout: int):
    from swegemma.sandbox import ContainerConfig, ContainerManager

    class SwebenchSandbox(ContainerManager):
        """Activate testbed on every command; preserve /testbed for editable imports."""

        def exec(self, container_id, command, *, timeout=None):
            wrapped = ("source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed && "
                       "cd /testbed && " + command)
            return super().exec(container_id, wrapped, timeout=timeout)

        def prepare(self, container_id):
            # Keep a physical /workspace: upstream file tools reject resolved
            # paths outside it. Remove all original refs and dangling git objects.
            script = workspace_setup_script(row["base_commit"])
            # Task interpreters can be as old as Python 3.5. Administrative code
            # uses Ubuntu's interpreter; agent commands still use conda testbed.
            result = self.exec(container_id, "/usr/bin/python3 -c " + shlex.quote(script))
            if result.exit_code:
                raise RuntimeError(f"SWE-bench workspace preparation failed: {result.stderr or result.stdout}")
            # baseline is taken only after the fresh repository exists.
            result = self.exec(container_id, "git add -A && git commit --allow-empty -qm baseline && "
                               "git tag _swegemma_baseline && git status --porcelain")
            if result.exit_code or result.stdout.strip():
                raise RuntimeError(f"Could not create clean baseline: {result.stderr or result.stdout}")

    return SwebenchSandbox(ContainerConfig(
        image=image_id, working_dir="/testbed", network_mode="none",
        mem_limit=mem_limit, cpu_quota=cpus * 100_000, timeout_seconds=timeout,
        environment={"TEST_TMPDIR": "/tmp"},
    ))


def environment_check(manager) -> dict:
    """Prove that a small source change survives patch capture and reverse-checks."""
    import shlex

    cid = manager.start()
    try:
        manager.prepare(cid)
        script = """
import pathlib, subprocess, sys
files = subprocess.check_output(['git', 'ls-files', '*.py'], text=True).splitlines()
if not files:
    raise RuntimeError('No Python source files to check')
target = pathlib.Path(files[0])
with target.open('a') as f:
    f.write('\\n# SWE-bench environment smoke check\\n')
patch = subprocess.check_output(['git', 'diff', '--binary', '_swegemma_baseline'])
if not patch:
    raise RuntimeError('No patch captured')
subprocess.run(['git', 'apply', '--check', '--reverse', '-'], input=patch, check=True)
subprocess.run(['git', 'reset', '--hard', '_swegemma_baseline'], check=True, stdout=subprocess.DEVNULL)
subprocess.run(['git', 'apply', '--check', '-'], input=patch, check=True)
print(sys.executable)
print('patch round-trip OK')
"""
        result = manager.exec(cid, "/usr/bin/python3 -c " + shlex.quote(script))
        if result.exit_code:
            raise RuntimeError(result.stderr or result.stdout)
        interpreter = manager.exec(cid, "python -c 'import sys; print(sys.executable); print(sys.version)'")
        if interpreter.exit_code:
            raise RuntimeError(interpreter.stderr or interpreter.stdout)
        return {"status": "ok", "output": result.stdout, "task_python": interpreter.stdout}
    finally:
        manager.cleanup_all()
