"""Endpoint selection and real client transport without external API calls."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from swebench_pipeline.model_config import load_environment, resolve_model_settings


def arguments(backend="local", **kwargs):
    return SimpleNamespace(backend=backend, model=None, base_url=None, health_url=None, **kwargs)


class ModelSettingsTests(unittest.TestCase):
    def test_dotenv_preserves_shell_values(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"CLOUD_API_KEY": "shell-key"}, clear=True):
            path = Path(directory) / ".env"
            path.write_text('CLOUD_API_KEY=file-key\nCLOUD_MODEL="Gemma-4-31B-IT"\n')
            load_environment(path)
            self.assertEqual(os.environ["CLOUD_API_KEY"], "shell-key")
            self.assertEqual(os.environ["CLOUD_MODEL"], "Gemma-4-31B-IT")

    def test_local_defaults_ignore_cloud_credentials(self):
        import local_config as cfg

        with patch.dict(os.environ, {"CLOUD_API_KEY": "cloud-secret", "CLOUD_MODEL": "cloud-model"}, clear=True):
            settings = resolve_model_settings(arguments())
        self.assertEqual(settings.model, cfg.get_model_config()["served_alias"])
        self.assertEqual(settings.base_url, cfg.base_url())
        self.assertEqual(settings.health_url, cfg.health_url())
        self.assertEqual(settings.api_key, "EMPTY")

    def test_cloud_selection_overrides_and_missing_key(self):
        environment = {"CLOUD_MODEL": "Gemma-4-31B-IT", "CLOUD_BASE_URL": "https://api.tokenlab.amd.com/v1/",
                       "LOCAL_API_KEY": "local-secret"}
        with patch.dict(os.environ, environment, clear=True):
            settings = resolve_model_settings(arguments("cloud"))
            self.assertIsNone(settings.health_url)
            self.assertEqual(settings.base_url, environment["CLOUD_BASE_URL"].rstrip("/"))
            self.assertEqual(settings.api_key, "")
            with self.assertRaisesRegex(ValueError, "CLOUD_API_KEY"):
                settings.require_credentials()
            os.environ["CLOUD_API_KEY"] = "cloud-secret"
            args = arguments("cloud")
            args.model, args.base_url = "alternate-model", "https://example.org/api/v1"
            settings = resolve_model_settings(args)
            settings.require_credentials()
            self.assertEqual(settings.model, "alternate-model")
            self.assertEqual(settings.base_url, args.base_url)
            self.assertNotIn("cloud-secret", repr(settings))

    def test_custom_local_url_derives_health_url(self):
        with patch.dict(os.environ, {"LOCAL_BASE_URL": "http://localhost:9000/v1"}, clear=True):
            self.assertEqual(resolve_model_settings(arguments()).health_url, "http://localhost:9000/health")

    def test_cloud_does_not_fall_back_to_local_model_or_endpoint(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "CLOUD_MODEL and CLOUD_BASE_URL"):
                resolve_model_settings(arguments("cloud"))

    def test_endpoint_cannot_embed_credentials_in_artifacts(self):
        for url in ("https://user:secret@host/v1", "https://host/v1?key=secret", "file:///tmp/api", "https://host/v1#secret"):
            with self.subTest(url=url), patch.dict(os.environ, {"CLOUD_MODEL": "model", "CLOUD_BASE_URL": url}, clear=True):
                with self.assertRaises(ValueError):
                    resolve_model_settings(arguments("cloud"))

    def test_parser_loads_project_env_from_another_directory_and_flag_wins(self):
        from swebench_pipeline.cli import parser

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), contextlib.chdir(directory):
            args = parser().parse_args(["run", "--run-id", "test", "--backend", "cloud"])
            settings = resolve_model_settings(args)
            self.assertEqual(settings.model, "Gemma-4-31B-IT")
            self.assertEqual(settings.base_url, "https://api.tokenlab.amd.com/v1")


class CloudPipelineTests(unittest.TestCase):
    def test_real_sample_compiles_with_includes_for_both_backends_and_graph_modes(self):
        import local_config as cfg
        from adk_submission import compile_submission
        from google.adk.tools.agent_tool import AgentTool
        from swegemma.context import SwegemmaContext
        from swebench_pipeline.agent import copy_agent, model_registry

        with tempfile.TemporaryDirectory() as directory:
            for backend in ("local", "cloud"):
                for graphs in (False, True):
                    with self.subTest(backend=backend, graphs=graphs):
                        target = Path(directory) / f"{backend}-{graphs}"
                        copy_agent(cfg.DATA_DIR / "sample_submission", target, "Gemma-4-31B-IT", graphs, backend=backend)
                        context = SwegemmaContext(repo="owner/repo", problem_statement="Fix the issue")
                        models = model_registry("Gemma-4-31B-IT", "https://example.org/v1", api_key="test-key")
                        agent = compile_submission(target, context.create_tools(), models)
                        analyzer = next(tool.agent for tool in agent.tools if isinstance(tool, AgentTool))
                        for llm_agent in (agent, analyzer):
                            extra = llm_agent.model._additional_args.get("extra_body", {})
                            self.assertEqual("chat_template_kwargs" in extra, backend == "local")
                            self.assertEqual(llm_agent.generate_content_config.temperature, 0.2)

    def test_cloud_agent_omits_server_specific_thinking_and_preserves_source(self):
        import yaml
        from swebench_pipeline.agent import copy_agent

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            original = ("name: agent\nmodel: old\nadapter: old-adapter\n"
                        "generate_content_config:\n  temperature: 0.2\n"
                        "  thinking_config:\n    thinking_budget: 4096\n"
                        "tools:\n  - run_command\n  - get_code_neighbors\n")
            (source / "agent.yaml").write_text(original)
            for backend in ("local", "cloud"):
                target = root / backend
                copy_agent(source, target, "Gemma-4-31B-IT", False, backend=backend)
                copied = yaml.safe_load((target / "agent.yaml").read_text())
                self.assertEqual(copied["model"], "Gemma-4-31B-IT")
                self.assertNotIn("adapter", copied)
                self.assertNotIn("get_code_neighbors", copied["tools"])
                self.assertEqual(copied["generate_content_config"]["temperature"], 0.2)
                self.assertEqual("thinking_config" in copied["generate_content_config"], backend == "local")
            self.assertEqual((source / "agent.yaml").read_text(), original)

    def test_cloud_dry_run_is_secret_free_and_needs_no_docker_or_key(self):
        from swebench_pipeline.cli import main

        row = {"instance_id": "owner__repo-1", "repo": "owner/repo", "base_commit": "a" * 40,
               "version": "1.0", "problem_statement": "Fix value", "patch": "", "test_patch": ""}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data.json"
            data.write_text(json.dumps([row]))
            agent = root / "agent"
            agent.mkdir()
            (agent / "agent.yaml").write_text("name: agent\nmodel: old\ninstruction: Fix the issue.\n")
            flags = ["run", "--backend", "cloud", "--run-id", "smoke", "--source", "json", "--dataset", str(data),
                     "--agent-dir", str(agent), "--output-dir", str(root / "output")]
            env = {"CLOUD_MODEL": "Gemma-4-31B-IT", "CLOUD_BASE_URL": "https://api.tokenlab.amd.com/v1",
                   "CLOUD_API_KEY": ""}
            for key in ("", "cloud-secret"):
                env["CLOUD_API_KEY"] = key
                output = io.StringIO()
                with patch.dict(os.environ, env, clear=True), patch("swebench_pipeline.environment.docker_client") as docker, \
                        contextlib.redirect_stdout(output):
                    self.assertEqual(main(flags + ["--dry-run"]), 0)
                    docker.assert_not_called()
                manifest = json.loads(output.getvalue())["manifest"]
                self.assertEqual(manifest["backend"], "cloud")
                self.assertNotIn("cloud-secret", output.getvalue())
                self.assertNotIn("api_key", output.getvalue())
            with patch.dict(os.environ, {**env, "CLOUD_API_KEY": ""}, clear=True), \
                    patch("swebench_pipeline.environment.docker_client") as docker, \
                    contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(main(flags), 1)
                self.assertIn("CLOUD_API_KEY", errors.getvalue())
                docker.assert_not_called()


class ClientTransportTests(unittest.TestCase):
    def test_real_litellm_client_sends_selected_key_model_and_tools(self):
        """Exercise the actual ADK -> LiteLLM -> HTTP -> tool-call response path."""
        from google.adk.models.llm_request import LlmRequest
        from google.genai import types
        from swebench_pipeline.agent import model_registry

        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append((self.path, self.headers.get("Authorization"), body))
                response = {"id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": body["model"],
                            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant",
                                "content": None, "tool_calls": [{"id": "call-1", "type": "function",
                                    "function": {"name": "submit_patch", "arguments": "{}"}}]}}],
                            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
                encoded = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            async def call(model, key):
                registry = model_registry(model, f"http://127.0.0.1:{server.server_port}/v1", api_key=key)
                request = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="Submit the patch.")])],
                    config=types.GenerateContentConfig(tools=[types.Tool(function_declarations=[
                        types.FunctionDeclaration(name="submit_patch", description="Submit patch",
                                                  parameters={"type": "object", "properties": {}})])]))
                return [response async for response in registry.get(model).generate_content_async(request)]

            for model, key in (("local-gemma", "local-test-key"), ("Gemma-4-31B-IT", "cloud-test-key")):
                with self.subTest(model=model):
                    responses = asyncio.run(call(model, key))
                    self.assertEqual(responses[0].content.parts[0].function_call.name, "submit_patch")
                    path, auth, body = received[-1]
                    self.assertEqual(path, "/v1/chat/completions")
                    self.assertEqual(auth, f"Bearer {key}")
                    self.assertEqual(body["model"], model)
                    self.assertEqual(body["tools"][0]["function"]["name"], "submit_patch")
                    self.assertNotIn("thinking_token_budget", body)
                    self.assertNotIn("chat_template_kwargs", body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
