"""Focused tests for the optional artifact hygiene service adapter."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from mythify_artifacts import _write_atomic, normalize_artifact_findings


REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "mythify.py"


class ArtifactServiceHandler(BaseHTTPRequestHandler):
    capabilities = {"scorers": {"synthid": False}, "pixel_backends": {"ctrlregen": False}}
    inspect_report = {
        "findings": ["C2PA manifest present"],
        "findings_confidence": ["confirmed"],
    }

    def log_message(self, _format, *_args):
        return

    def send_json(self, payload, status=200):
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == "/health":
            self.send_json({"ok": True, "version": "test-1"})
            return
        if self.path == "/capabilities":
            self.send_json({"ok": True, **self.capabilities})
            return
        self.send_json({"ok": False, "error": "not found"}, status=404)

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        request = json.loads(self.rfile.read(length).decode("utf-8"))
        if (
            self.path == "/inspect"
            and isinstance(request.get("file"), str)
            and request.get("name")
        ):
            artifact_bytes = base64.b64decode(request["file"])
            if artifact_bytes.startswith(b"postfail"):
                self.send_json(
                    {"ok": True, "kind": "text", "suspicious": False}
                )
                return
            report = self.inspect_report
            if artifact_bytes.startswith(b"cleaned"):
                report = {
                    "findings": ["frontmatter value hit on description"],
                    "findings_confidence": ["probable"],
                }
            elif artifact_bytes.startswith(b"frontmatter"):
                report = {
                    "findings": ["frontmatter value hit on description"],
                    "findings_confidence": ["probable"],
                }
            elif artifact_bytes.startswith(b"generator"):
                report = {
                    "findings": ["frontmatter value hit on generator"],
                    "findings_confidence": ["probable"],
                }
            self.send_json(
                {
                    "ok": True,
                    "kind": "text",
                    "suspicious": True,
                    "report": report,
                }
            )
            return
        if (
            self.path == "/clean"
            and isinstance(request.get("file"), str)
            and request.get("name")
        ):
            self.server.clean_calls += 1
            artifact_bytes = base64.b64decode(request["file"])
            if artifact_bytes.startswith(b"invalid-clean"):
                encoded = "***"
            elif artifact_bytes.startswith(b"make-postfail"):
                encoded = base64.b64encode(b"postfail content\n").decode("ascii")
            elif artifact_bytes.startswith(b"residual"):
                encoded = base64.b64encode(b"residual content\n").decode("ascii")
            elif artifact_bytes.startswith(b"empty-clean"):
                encoded = ""
            else:
                encoded = base64.b64encode(b"cleaned content\n").decode("ascii")
            self.send_json({"ok": True, "cleaned": encoded, "report": {"removed": 1}})
            return
        self.send_json({"ok": False, "error": "not found"}, status=404)


class ArtifactCliTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="mythify-artifact-proj-"))
        self.home = Path(tempfile.mkdtemp(prefix="mythify-artifact-home-"))
        self.addCleanup(shutil.rmtree, str(self.project), True)
        self.addCleanup(shutil.rmtree, str(self.home), True)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ArtifactServiceHandler)
        self.server.clean_calls = 0
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.service_url = "http://127.0.0.1:{0}".format(self.server.server_port)

    def run_cli(self, *args):
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
        env["HOME"] = str(self.home)
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=str(self.project),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_probe_reports_capabilities_without_writing_workspace_state(self):
        result = self.run_cli(
            "artifact", "probe", "--service-url", self.service_url, "--json"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "available")
        self.assertEqual(payload["version"], "test-1")
        self.assertTrue(payload["material_not_evidence"])
        self.assertFalse(payload["verification_recorded"])
        self.assertFalse((self.project / ".mythify").exists())

    def test_container_layer_a_hits_match_the_service_report_shape(self):
        findings = normalize_artifact_findings(
            {
                "suspicious_total": 1,
                "layer_a_hits": [
                    {
                        "codepoint": "U+200B",
                        "label": "zero width space",
                        "count": 1,
                        "kind": "strip",
                        "confidence": "probable",
                    }
                ],
            }
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["disposition"], "actionable")

    def test_inspect_returns_actionable_material_without_writing_workspace_state(self):
        artifact = self.project / "sample.txt"
        artifact.write_text("owned content\n", encoding="utf-8")
        result = self.run_cli(
            "artifact",
            "inspect",
            str(artifact),
            "--service-url",
            self.service_url,
            "--json",
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "actionable")
        self.assertEqual(payload["findings"][0]["classification"], "deterministic")
        self.assertEqual(payload["findings"][0]["disposition"], "actionable")
        self.assertTrue(payload["material_not_evidence"])
        self.assertFalse((self.project / ".mythify").exists())

    def test_remote_service_requires_explicit_opt_in(self):
        result = self.run_cli(
            "artifact", "probe", "--service-url", "https://example.com", "--json"
        )
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "blocked")
        self.assertIn("--allow-remote", payload["error"])

    def test_probe_enforces_version_pin_and_api_key_allowlist(self):
        mismatch = self.run_cli(
            "artifact",
            "probe",
            "--service-url",
            self.service_url,
            "--expected-version",
            "other-version",
            "--json",
        )
        self.assertEqual(mismatch.returncode, 2)
        self.assertIn("version mismatch", json.loads(mismatch.stdout)["error"])

        invalid_env = self.run_cli(
            "artifact",
            "probe",
            "--service-url",
            self.service_url,
            "--api-key-env",
            "UNSAFE_SECRET",
            "--json",
        )
        self.assertEqual(invalid_env.returncode, 2)
        self.assertIn("api_key_env", json.loads(invalid_env.stdout)["error"])

    def test_prose_frontmatter_value_is_allowed_but_provenance_key_is_actionable(self):
        prose = self.project / "prose.md"
        prose.write_text("frontmatter content\n", encoding="utf-8")
        allowed = self.run_cli(
            "artifact", "inspect", str(prose), "--service-url", self.service_url, "--json"
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        allowed_payload = json.loads(allowed.stdout)
        self.assertEqual(allowed_payload["status"], "clear")
        self.assertEqual(allowed_payload["findings"][0]["disposition"], "allowed")

        provenance = self.project / "provenance.md"
        provenance.write_text("generator metadata\n", encoding="utf-8")
        actionable = self.run_cli(
            "artifact",
            "inspect",
            str(provenance),
            "--service-url",
            self.service_url,
            "--json",
        )
        self.assertEqual(actionable.returncode, 1, actionable.stderr)
        actionable_payload = json.loads(actionable.stdout)
        self.assertEqual(actionable_payload["findings"][0]["disposition"], "actionable")

        strict = self.run_cli(
            "artifact",
            "inspect",
            str(prose),
            "--service-url",
            self.service_url,
            "--no-default-allowlist",
            "--json",
        )
        self.assertEqual(strict.returncode, 1, strict.stderr)
        self.assertEqual(json.loads(strict.stdout)["findings"][0]["disposition"], "actionable")

    def test_clean_requires_authorization_and_separate_output(self):
        artifact = self.project / "sample.txt"
        artifact.write_text("owned content\n", encoding="utf-8")
        output = self.project / "cleaned.txt"
        unauthorized = self.run_cli(
            "artifact",
            "clean",
            str(artifact),
            "--output",
            str(output),
            "--service-url",
            self.service_url,
            "--json",
        )
        self.assertEqual(unauthorized.returncode, 2)
        self.assertIn("--confirm-authorized", json.loads(unauthorized.stdout)["error"])
        self.assertFalse(output.exists())

        in_place = self.run_cli(
            "artifact",
            "clean",
            str(artifact),
            "--output",
            str(artifact),
            "--confirm-authorized",
            "--service-url",
            self.service_url,
            "--json",
        )
        self.assertEqual(in_place.returncode, 2)
        self.assertIn("separate output", json.loads(in_place.stdout)["error"])
        self.assertEqual(artifact.read_text(encoding="utf-8"), "owned content\n")

    def test_clean_reinspects_then_writes_separate_output_atomically(self):
        artifact = self.project / "sample.txt"
        artifact.write_text("owned content\n", encoding="utf-8")
        output = self.project / "cleaned.txt"
        result = self.run_cli(
            "artifact",
            "clean",
            str(artifact),
            "--output",
            str(output),
            "--confirm-authorized",
            "--service-url",
            self.service_url,
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "clean")
        self.assertTrue(payload["written"])
        self.assertEqual(payload["post_clean_inspection"]["status"], "clear")
        self.assertEqual(
            payload["post_clean_inspection"]["findings"][0]["disposition"], "allowed"
        )
        self.assertEqual(output.read_text(encoding="utf-8"), "cleaned content\n")
        self.assertEqual(artifact.read_text(encoding="utf-8"), "owned content\n")
        self.assertFalse(any(self.project.glob(".cleaned.txt.*.tmp")))

    def test_clean_preserves_allowed_only_input_without_calling_service_clean(self):
        artifact = self.project / "sample.md"
        artifact.write_text("frontmatter content\n", encoding="utf-8")
        output = self.project / "cleaned.md"
        result = self.run_cli(
            "artifact",
            "clean",
            str(artifact),
            "--output",
            str(output),
            "--confirm-authorized",
            "--service-url",
            self.service_url,
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["service_clean_called"])
        self.assertTrue(payload["clean_report"]["skipped"])
        self.assertEqual(output.read_bytes(), artifact.read_bytes())
        self.assertEqual(self.server.clean_calls, 0)

    def test_clean_rejects_invalid_base64_without_writing_output(self):
        artifact = self.project / "sample.txt"
        artifact.write_text("invalid-clean content\n", encoding="utf-8")
        output = self.project / "cleaned.txt"
        result = self.run_cli(
            "artifact",
            "clean",
            str(artifact),
            "--output",
            str(output),
            "--confirm-authorized",
            "--service-url",
            self.service_url,
            "--json",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid base64", json.loads(result.stdout)["error"])
        self.assertFalse(output.exists())

    def test_clean_rejects_symlink_and_requires_overwrite_for_existing_output(self):
        artifact = self.project / "sample.txt"
        artifact.write_text("owned content\n", encoding="utf-8")
        target = self.project / "target.txt"
        target.write_text("protected\n", encoding="utf-8")
        symlink = self.project / "linked.txt"
        symlink.symlink_to(target)
        linked = self.run_cli(
            "artifact",
            "clean",
            str(artifact),
            "--output",
            str(symlink),
            "--confirm-authorized",
            "--service-url",
            self.service_url,
            "--json",
        )
        self.assertEqual(linked.returncode, 2)
        self.assertIn("symbolic link", json.loads(linked.stdout)["error"])
        self.assertEqual(target.read_text(encoding="utf-8"), "protected\n")

        existing = self.run_cli(
            "artifact",
            "clean",
            str(artifact),
            "--output",
            str(target),
            "--confirm-authorized",
            "--service-url",
            self.service_url,
            "--json",
        )
        self.assertEqual(existing.returncode, 2)
        self.assertIn("--overwrite", json.loads(existing.stdout)["error"])
        self.assertEqual(target.read_text(encoding="utf-8"), "protected\n")

        overwritten = self.run_cli(
            "artifact",
            "clean",
            str(artifact),
            "--output",
            str(target),
            "--confirm-authorized",
            "--overwrite",
            "--service-url",
            self.service_url,
            "--json",
        )
        self.assertEqual(overwritten.returncode, 0, overwritten.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), "cleaned content\n")

    def test_clean_does_not_write_when_post_clean_inspection_is_invalid(self):
        artifact = self.project / "sample.txt"
        artifact.write_text("make-postfail content\n", encoding="utf-8")
        output = self.project / "cleaned.txt"
        result = self.run_cli(
            "artifact",
            "clean",
            str(artifact),
            "--output",
            str(output),
            "--confirm-authorized",
            "--service-url",
            self.service_url,
            "--json",
        )
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["written"])
        self.assertIn("missing report", payload["error"])
        self.assertFalse(output.exists())

    def test_clean_writes_residual_output_and_exits_one(self):
        artifact = self.project / "sample.txt"
        artifact.write_text("residual content\n", encoding="utf-8")
        output = self.project / "cleaned.txt"
        result = self.run_cli(
            "artifact",
            "clean",
            str(artifact),
            "--output",
            str(output),
            "--confirm-authorized",
            "--service-url",
            self.service_url,
            "--json",
        )
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "residual")
        self.assertTrue(payload["written"])
        self.assertTrue(payload["post_clean_inspection"]["actionable"])
        self.assertEqual(output.read_text(encoding="utf-8"), "residual content\n")

    def test_clean_accepts_an_empty_base64_result(self):
        artifact = self.project / "sample.txt"
        artifact.write_text("empty-clean content\n", encoding="utf-8")
        output = self.project / "cleaned.txt"
        result = self.run_cli(
            "artifact",
            "clean",
            str(artifact),
            "--output",
            str(output),
            "--confirm-authorized",
            "--service-url",
            self.service_url,
            "--json",
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(json.loads(result.stdout)["written"])
        self.assertEqual(output.read_bytes(), b"")

    def test_atomic_writer_rejects_an_output_that_appears_without_overwrite(self):
        source = self.project / "source.txt"
        destination = self.project / "destination.txt"
        source.write_text("source\n", encoding="utf-8")
        destination.write_text("protected\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "appeared before replacement"):
            _write_atomic(
                destination,
                b"replacement\n",
                source,
                source.stat().st_mode,
                False,
            )
        self.assertEqual(destination.read_text(encoding="utf-8"), "protected\n")
        self.assertFalse(any(self.project.glob(".destination.txt.*.tmp")))


if __name__ == "__main__":
    unittest.main()
