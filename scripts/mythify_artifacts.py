"""Guarded watermarks-remover service adapter for artifact hygiene."""

from __future__ import annotations

import base64
import json
import os
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


MANIFEST_PATH = Path(__file__).resolve().parent.parent / "protocol" / "artifact-hygiene.json"
DEFAULT_SERVICE_URL = "http://127.0.0.1:8765"
ARTIFACT_API_KEY_ENV = "WATERMARKS_SERVER_API_KEY"
MAX_ARTIFACT_BYTES = 67108864
ARTIFACT_EVIDENCE_STATUS = "artifact_service_output_not_verification"
ARTIFACT_HYGIENE_MANIFEST = None


def _load_manifest():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or manifest.get("adapter") != "watermarks-remover":
        raise RuntimeError("Invalid artifact hygiene manifest")
    expected = {
        "default_service_url": DEFAULT_SERVICE_URL,
        "api_key_env": ARTIFACT_API_KEY_ENV,
        "max_input_bytes": MAX_ARTIFACT_BYTES,
        "evidence_status": ARTIFACT_EVIDENCE_STATUS,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise RuntimeError("Invalid artifact hygiene manifest field: {0}".format(key))
    return manifest


def _manifest():
    global ARTIFACT_HYGIENE_MANIFEST
    if ARTIFACT_HYGIENE_MANIFEST is None:
        ARTIFACT_HYGIENE_MANIFEST = _load_manifest()
    return ARTIFACT_HYGIENE_MANIFEST


LOCAL_SERVICE_HOSTS = frozenset(("localhost", "127.0.0.1", "::1", "0.0.0.0"))
MAX_RESPONSE_BYTES = MAX_ARTIFACT_BYTES * 2 + (1 << 20)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _base_result(service_url):
    return {
        "adapter": "watermarks-remover",
        "status": "blocked",
        "service_url": service_url,
        "material_not_evidence": True,
        "evidence_status": ARTIFACT_EVIDENCE_STATUS,
        "verification_recorded": False,
        "writes_mythify_state": False,
        "error": "",
    }


def _normalize_service_url(value, allow_remote=False):
    raw = str(value or "").strip() or os.environ.get("WATERMARKS_SERVICE_URL", "").strip()
    raw = raw or DEFAULT_SERVICE_URL
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        return None, False, "artifact service URL is invalid"
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None, False, "artifact service URL must use http or https and include a host"
    if parsed.username or parsed.password:
        return None, False, "artifact service URL must not include credentials"
    if parsed.query or parsed.fragment:
        return None, False, "artifact service URL must not include a query or fragment"
    local = parsed.hostname.lower() in LOCAL_SERVICE_HOSTS
    if not local and not allow_remote:
        return None, False, "remote artifact service requires --allow-remote"
    normalized = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
    ).rstrip("/")
    return normalized, local, ""


def _request_headers(api_key_env, include_json=False):
    selected = ARTIFACT_API_KEY_ENV if api_key_env is None else str(api_key_env).strip()
    if selected not in ("", ARTIFACT_API_KEY_ENV):
        return None, selected, "api_key_env must be {0} or empty".format(ARTIFACT_API_KEY_ENV)
    headers = {"accept": "application/json"}
    if include_json:
        headers["content-type"] = "application/json"
    if selected and os.environ.get(selected, "").strip():
        headers["authorization"] = "Bearer " + os.environ[selected].strip()
    return headers, selected, ""


def _endpoint(service_url, name):
    suffix = _manifest()["endpoints"][name]
    return service_url.rstrip("/") + "/" + suffix.lstrip("/")


def _read_bounded(response):
    declared = response.headers.get("Content-Length", "")
    if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
        raise ValueError("artifact service response exceeds the size limit")
    payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("artifact service response exceeds the size limit")
    return payload


def _request_json(service_url, endpoint_name, method, headers, timeout_seconds, payload=None):
    body = None if payload is None else json.dumps(payload, allow_nan=False).encode("utf-8")
    request = urllib.request.Request(
        _endpoint(service_url, endpoint_name),
        data=body,
        headers=headers,
        method=method,
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            raw = _read_bounded(response)
            status_code = response.status
    except urllib.error.HTTPError as exc:
        try:
            raw = _read_bounded(exc)
        except (OSError, ValueError):
            raw = b""
        try:
            error_payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            error_payload = {}
        message = str(error_payload.get("error") or "HTTP {0}".format(exc.code))
        return {"ok": False, "status_code": exc.code, "json": None, "error": message}
    except (OSError, ValueError) as exc:
        return {"ok": False, "status_code": 0, "json": None, "error": str(exc)}
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {
            "ok": False,
            "status_code": status_code,
            "json": None,
            "error": "artifact service returned invalid JSON",
        }
    if not isinstance(decoded, dict):
        return {
            "ok": False,
            "status_code": status_code,
            "json": None,
            "error": "artifact service returned a non-object JSON response",
        }
    if not decoded.get("ok", True):
        return {
            "ok": False,
            "status_code": status_code,
            "json": decoded,
            "error": str(decoded.get("error") or "artifact service request failed"),
        }
    return {"ok": True, "status_code": status_code, "json": decoded, "error": ""}


def _value_at_path(value, dotted_path):
    current = value
    for part in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _capability_warnings(capabilities):
    warnings = []
    for dotted_path, message in _manifest()["heavy_backend_warnings"].items():
        if _value_at_path(capabilities, dotted_path) is True:
            warnings.append(message)
    return warnings


def probe_artifact_service(
    service_url=None,
    api_key_env=None,
    expected_version=None,
    allow_remote=False,
    timeout_seconds=10,
):
    try:
        _manifest()
    except (OSError, ValueError, RuntimeError) as exc:
        result = _base_result(str(service_url or DEFAULT_SERVICE_URL))
        result["error"] = "artifact hygiene manifest unavailable: {0}".format(exc)
        return result
    normalized, local, error = _normalize_service_url(service_url, allow_remote=allow_remote)
    result = _base_result(normalized or str(service_url or DEFAULT_SERVICE_URL))
    result.update(
        {
            "local_service": local,
            "remote_service": not local,
            "version": "",
            "expected_version": str(expected_version or "").strip(),
            "capabilities": {},
            "license_warnings": [],
            "checks": [],
        }
    )
    if error:
        result["error"] = error
        return result
    headers, selected_env, error = _request_headers(api_key_env)
    result["api_key_env"] = selected_env
    result["api_key_present"] = bool(selected_env and os.environ.get(selected_env, "").strip())
    if error:
        result["error"] = error
        return result
    timeout = timeout_seconds if isinstance(timeout_seconds, (int, float)) and timeout_seconds > 0 else 10
    health = _request_json(normalized, "health", "GET", headers, timeout)
    result["checks"].append(
        {
            "name": "health",
            "ok": health["ok"],
            "status_code": health["status_code"],
            "error": health["error"],
        }
    )
    if not health["ok"]:
        result["error"] = health["error"]
        return result
    health_json = health["json"]
    result["version"] = str(health_json.get("version") or "")
    if result["expected_version"] and result["version"] != result["expected_version"]:
        result["error"] = "artifact service version mismatch: expected {0}, found {1}".format(
            result["expected_version"], result["version"] or "unknown"
        )
        return result
    capabilities = _request_json(normalized, "capabilities", "GET", headers, timeout)
    result["checks"].append(
        {
            "name": "capabilities",
            "ok": capabilities["ok"],
            "status_code": capabilities["status_code"],
            "error": capabilities["error"],
        }
    )
    if not capabilities["ok"]:
        result["error"] = capabilities["error"]
        return result
    result["capabilities"] = capabilities["json"]
    result["license_warnings"] = _capability_warnings(capabilities["json"])
    result["status"] = "available"
    return result


def _finding_confidence(message, supplied):
    if supplied in ("confirmed", "probable", "informational", "likely_false_positive"):
        return supplied
    lowered = message.lower()
    if "c2pa" in lowered or "jumbf" in lowered:
        return "confirmed"
    if "stylometry" in lowered:
        return "informational"
    return "probable"


def _default_allowed_finding(message):
    prefix = "frontmatter value hit on "
    lowered = message.strip().lower()
    if not lowered.startswith(prefix):
        return False
    field = lowered[len(prefix) :].strip()
    return field in _manifest()["prose_frontmatter_fields"]


def normalize_artifact_findings(report, allow_findings=None, use_default_allowlist=True):
    exact_allowlist = {str(item) for item in (allow_findings or []) if str(item)}
    normalized = []
    raw_findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    confidences = (
        report.get("findings_confidence")
        if isinstance(report.get("findings_confidence"), list)
        else []
    )
    for index, value in enumerate(raw_findings):
        message = str(value)
        confidence = _finding_confidence(
            message, confidences[index] if index < len(confidences) else ""
        )
        classification = "heuristic" if "stylometry" in message.lower() else "deterministic"
        allowed = message in exact_allowlist or (
            use_default_allowlist and _default_allowed_finding(message)
        )
        disposition = "allowed" if allowed else "advisory"
        if classification == "deterministic" and confidence in ("confirmed", "probable") and not allowed:
            disposition = "actionable"
        normalized.append(
            {
                "message": message,
                "classification": classification,
                "confidence": confidence,
                "disposition": disposition,
            }
        )
    hits = report.get("hits") if isinstance(report.get("hits"), list) else []
    layer_a_hits = (
        report.get("layer_a_hits")
        if isinstance(report.get("layer_a_hits"), list)
        else []
    )
    hits = [*hits, *layer_a_hits]
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        message = "layer-a [{0}] {1} x{2}".format(
            hit.get("kind") or "unknown",
            hit.get("label") or hit.get("codepoint") or "suspicious Unicode",
            hit.get("count") or 1,
        )
        confidence = _finding_confidence(message, hit.get("confidence"))
        normalized.append(
            {
                "message": message,
                "classification": "deterministic",
                "confidence": confidence,
                "disposition": (
                    "actionable"
                    if confidence in ("confirmed", "probable")
                    else "advisory"
                ),
            }
        )
    stylometry = report.get("stylometry") if isinstance(report.get("stylometry"), dict) else {}
    score = stylometry.get("score")
    if isinstance(score, (int, float)) and score >= 0.65:
        normalized.append(
            {
                "message": "stylometry score {0:.2f}".format(score),
                "classification": "heuristic",
                "confidence": str(stylometry.get("confidence_level") or "informational"),
                "disposition": "advisory",
            }
        )
    if not normalized and report.get("has_c2pa") is True:
        normalized.append(
            {
                "message": "C2PA provenance detected",
                "classification": "deterministic",
                "confidence": "confirmed",
                "disposition": "actionable",
            }
        )
    if not normalized and report.get("has_ai_metadata") is True:
        normalized.append(
            {
                "message": "AI metadata detected",
                "classification": "deterministic",
                "confidence": "probable",
                "disposition": "actionable",
            }
        )
    return normalized


def _inspect_bytes(
    artifact_bytes,
    artifact_name,
    probe,
    api_key_env,
    timeout_seconds,
    allow_findings=None,
    use_default_allowlist=True,
):
    headers, _selected_env, error = _request_headers(api_key_env, include_json=True)
    if error:
        return {"status": "blocked", "error": error}
    payload = {
        "file": base64.b64encode(artifact_bytes).decode("ascii"),
        "name": artifact_name,
    }
    inspected = _request_json(
        probe["service_url"], "inspect", "POST", headers, timeout_seconds, payload
    )
    if not inspected["ok"]:
        return {"status": "blocked", "error": inspected["error"]}
    response = inspected["json"]
    report = response.get("report")
    if not isinstance(report, dict):
        return {
            "status": "blocked",
            "error": "artifact service inspect response is missing report",
        }
    findings = normalize_artifact_findings(
        report,
        allow_findings=allow_findings,
        use_default_allowlist=use_default_allowlist,
    )
    actionable = any(item["disposition"] == "actionable" for item in findings)
    return {
        "status": "actionable" if actionable else "clear",
        "error": "",
        "kind": str(response.get("kind") or "unknown"),
        "raw_report": report,
        "raw_suspicious": bool(response.get("suspicious")),
        "findings": findings,
        "actionable": actionable,
    }


def inspect_artifact(
    path,
    service_url=None,
    api_key_env=None,
    expected_version=None,
    allow_remote=False,
    acknowledge_data_upload=False,
    timeout_seconds=30,
    allow_findings=None,
    use_default_allowlist=True,
):
    probe = probe_artifact_service(
        service_url=service_url,
        api_key_env=api_key_env,
        expected_version=expected_version,
        allow_remote=allow_remote,
        timeout_seconds=timeout_seconds,
    )
    result = {
        **_base_result(probe.get("service_url")),
        "path": str(path),
        "probe": probe,
        "raw_report": {},
        "raw_suspicious": False,
        "findings": [],
        "actionable": False,
        "license_warnings": probe.get("license_warnings", []),
    }
    if probe["status"] != "available":
        result["error"] = probe["error"]
        return result
    if probe["remote_service"] and not acknowledge_data_upload:
        result["error"] = "remote artifact inspection requires --acknowledge-data-upload"
        return result
    artifact_path = Path(path).expanduser().resolve()
    if not artifact_path.is_file():
        result["error"] = "artifact path is not a file: {0}".format(artifact_path)
        return result
    size = artifact_path.stat().st_size
    if size > MAX_ARTIFACT_BYTES:
        result["error"] = "artifact exceeds the {0}-byte size limit".format(MAX_ARTIFACT_BYTES)
        return result
    inspection = _inspect_bytes(
        artifact_path.read_bytes(),
        artifact_path.name,
        probe,
        api_key_env,
        timeout_seconds,
        allow_findings=allow_findings,
        use_default_allowlist=use_default_allowlist,
    )
    if inspection["status"] == "blocked":
        result["error"] = inspection["error"]
        return result
    result.update(
        {
            "path": str(artifact_path),
            **inspection,
        }
    )
    return result


def _validate_clean_paths(input_path, output_path, overwrite):
    source = Path(input_path).expanduser().resolve()
    destination = Path(output_path).expanduser()
    destination_resolved = destination.resolve(strict=False)
    if not source.is_file():
        return None, None, "artifact path is not a file: {0}".format(source)
    if source.stat().st_size > MAX_ARTIFACT_BYTES:
        return None, None, "artifact exceeds the {0}-byte size limit".format(
            MAX_ARTIFACT_BYTES
        )
    if destination.is_symlink():
        return None, None, "artifact output must not be a symbolic link"
    if destination_resolved == source:
        return None, None, "artifact cleaning requires a separate output path"
    if destination.exists():
        try:
            if os.path.samefile(source, destination):
                return None, None, "artifact cleaning requires a separate output file"
        except OSError:
            pass
        if not destination.is_file():
            return None, None, "artifact output exists and is not a file"
        if not overwrite:
            return None, None, "artifact output already exists; pass --overwrite to replace it"
    parent = destination_resolved.parent
    if not parent.is_dir():
        return None, None, "artifact output directory does not exist: {0}".format(parent)
    return source, destination_resolved, ""


def _assert_safe_destination(destination, source, overwrite):
    if destination.is_symlink():
        raise ValueError("artifact output became a symbolic link before replacement")
    if not destination.exists():
        return
    try:
        if os.path.samefile(source, destination):
            raise ValueError("artifact output became the input file before replacement")
    except OSError:
        pass
    if not destination.is_file():
        raise ValueError("artifact output became a non-file before replacement")
    if not overwrite:
        raise ValueError(
            "artifact output appeared before replacement; pass --overwrite to replace it"
        )


def _write_atomic(destination, payload, source, source_mode, overwrite):
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".{0}.".format(destination.name), suffix=".tmp", dir=str(destination.parent)
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, stat.S_IMODE(source_mode))
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _assert_safe_destination(destination, source, overwrite)
        os.replace(str(temporary_path), str(destination))
        if hasattr(os, "O_DIRECTORY"):
            parent_descriptor = os.open(str(destination.parent), os.O_DIRECTORY)
            try:
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path.exists():
            temporary_path.unlink()


def clean_artifact(
    path,
    output_path,
    service_url=None,
    api_key_env=None,
    expected_version=None,
    allow_remote=False,
    acknowledge_data_upload=False,
    timeout_seconds=30,
    allow_findings=None,
    use_default_allowlist=True,
    confirm_authorized=False,
    overwrite=False,
    keep_non_ai_metadata=False,
    nfkc=False,
    aggressive_homoglyphs=False,
):
    result = {
        **_base_result(str(service_url or DEFAULT_SERVICE_URL)),
        "input_path": str(path),
        "output_path": str(output_path),
        "input_inspection": {},
        "post_clean_inspection": {},
        "clean_report": {},
        "written": False,
        "actionable": False,
        "service_clean_called": False,
    }
    if not confirm_authorized:
        result["error"] = "artifact cleaning requires --confirm-authorized"
        return result
    source, destination, error = _validate_clean_paths(path, output_path, overwrite)
    if error:
        result["error"] = error
        return result
    probe = probe_artifact_service(
        service_url=service_url,
        api_key_env=api_key_env,
        expected_version=expected_version,
        allow_remote=allow_remote,
        timeout_seconds=timeout_seconds,
    )
    result["service_url"] = probe.get("service_url")
    result["probe"] = probe
    result["license_warnings"] = probe.get("license_warnings", [])
    if probe["status"] != "available":
        result["error"] = probe["error"]
        return result
    if probe["remote_service"] and not acknowledge_data_upload:
        result["error"] = "remote artifact cleaning requires --acknowledge-data-upload"
        return result
    source_bytes = source.read_bytes()
    input_inspection = _inspect_bytes(
        source_bytes,
        source.name,
        probe,
        api_key_env,
        timeout_seconds,
        allow_findings=allow_findings,
        use_default_allowlist=use_default_allowlist,
    )
    result["input_inspection"] = input_inspection
    if input_inspection["status"] == "blocked":
        result["error"] = input_inspection["error"]
        return result
    if not input_inspection["actionable"]:
        post_inspection = _inspect_bytes(
            source_bytes,
            destination.name,
            probe,
            api_key_env,
            timeout_seconds,
            allow_findings=allow_findings,
            use_default_allowlist=use_default_allowlist,
        )
        result["post_clean_inspection"] = post_inspection
        result["clean_report"] = {
            "skipped": True,
            "reason": "no actionable deterministic findings after normalization",
        }
        if post_inspection["status"] == "blocked":
            result["error"] = post_inspection["error"]
            return result
        try:
            _write_atomic(
                destination,
                source_bytes,
                source,
                source.stat().st_mode,
                overwrite,
            )
        except (OSError, ValueError) as exc:
            result["error"] = str(exc)
            return result
        result.update(
            {
                "status": "clean",
                "input_path": str(source),
                "output_path": str(destination),
                "written": True,
                "actionable": False,
            }
        )
        return result
    headers, _selected_env, error = _request_headers(api_key_env, include_json=True)
    if error:
        result["error"] = error
        return result
    result["service_clean_called"] = True
    cleaned = _request_json(
        probe["service_url"],
        "clean",
        "POST",
        headers,
        timeout_seconds,
        {
            "file": base64.b64encode(source_bytes).decode("ascii"),
            "name": source.name,
            "options": {
                "keep_non_ai_metadata": bool(keep_non_ai_metadata),
                "nfkc": bool(nfkc),
                "aggressive_homoglyphs": bool(aggressive_homoglyphs),
            },
        },
    )
    if not cleaned["ok"]:
        result["error"] = cleaned["error"]
        return result
    encoded = cleaned["json"].get("cleaned")
    if not isinstance(encoded, str):
        result["error"] = "artifact service clean response is missing cleaned"
        return result
    try:
        cleaned_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        result["error"] = "artifact service clean response contains invalid base64"
        return result
    if len(cleaned_bytes) > MAX_ARTIFACT_BYTES:
        result["error"] = "cleaned artifact exceeds the {0}-byte size limit".format(
            MAX_ARTIFACT_BYTES
        )
        return result
    post_inspection = _inspect_bytes(
        cleaned_bytes,
        destination.name,
        probe,
        api_key_env,
        timeout_seconds,
        allow_findings=allow_findings,
        use_default_allowlist=use_default_allowlist,
    )
    result["post_clean_inspection"] = post_inspection
    result["clean_report"] = cleaned["json"].get("report", {})
    if post_inspection["status"] == "blocked":
        result["error"] = post_inspection["error"]
        return result
    try:
        _write_atomic(
            destination,
            cleaned_bytes,
            source,
            source.stat().st_mode,
            overwrite,
        )
    except (OSError, ValueError) as exc:
        result["error"] = str(exc)
        return result
    result.update(
        {
            "status": "residual" if post_inspection["actionable"] else "clean",
            "input_path": str(source),
            "output_path": str(destination),
            "written": True,
            "actionable": post_inspection["actionable"],
        }
    )
    return result


def _format_probe(result):
    prefix = "[OK]" if result["status"] == "available" else "[FAIL]"
    lines = [
        "{0} Artifact service probe {1}.".format(prefix, result["status"]),
        "service: {0}".format(result.get("service_url") or "unset"),
        "version: {0}".format(result.get("version") or "unknown"),
        "remote service: {0}".format("yes" if result.get("remote_service") else "no"),
        "evidence: service output is material, not verification evidence.",
    ]
    for warning in result.get("license_warnings", []):
        lines.append("warning: " + warning)
    if result.get("error"):
        lines.append("error: " + result["error"])
    return "\n".join(lines)


def _format_inspection(result):
    prefix = "[FAIL]" if result["status"] == "blocked" else "[OK]"
    lines = [
        "{0} Artifact inspection {1}.".format(prefix, result["status"]),
        "path: {0}".format(result.get("path") or "unset"),
        "actionable: {0}".format("yes" if result.get("actionable") else "no"),
        "evidence: service output is material until a caller records this command through verify run.",
    ]
    for finding in result.get("findings", []):
        lines.append(
            "{0}: [{1}/{2}] {3}".format(
                finding["disposition"],
                finding["classification"],
                finding["confidence"],
                finding["message"],
            )
        )
    for warning in result.get("license_warnings", []):
        lines.append("warning: " + warning)
    if result.get("error"):
        lines.append("error: " + result["error"])
    return "\n".join(lines)


def _format_clean(result):
    prefix = "[FAIL]" if result["status"] == "blocked" else "[OK]"
    lines = [
        "{0} Artifact clean {1}.".format(prefix, result["status"]),
        "input: {0}".format(result.get("input_path") or "unset"),
        "output: {0}".format(result.get("output_path") or "unset"),
        "written: {0}".format("yes" if result.get("written") else "no"),
        "residual actionable findings: {0}".format(
            "yes" if result.get("actionable") else "no"
        ),
        "evidence: service output is material until a caller records this command through verify run.",
    ]
    for finding in result.get("post_clean_inspection", {}).get("findings", []):
        lines.append(
            "{0}: [{1}/{2}] {3}".format(
                finding["disposition"],
                finding["classification"],
                finding["confidence"],
                finding["message"],
            )
        )
    for warning in result.get("license_warnings", []):
        lines.append("warning: " + warning)
    if result.get("error"):
        lines.append("error: " + result["error"])
    return "\n".join(lines)


def cmd_artifact_probe(args, _state):
    result = probe_artifact_service(
        service_url=args.service_url,
        api_key_env=args.api_key_env,
        expected_version=args.expected_version,
        allow_remote=args.allow_remote,
        timeout_seconds=args.timeout,
    )
    print(json.dumps(result, indent=2) if args.json_output else _format_probe(result))
    return 0 if result["status"] == "available" else 2


def cmd_artifact_inspect(args, _state):
    result = inspect_artifact(
        args.path,
        service_url=args.service_url,
        api_key_env=args.api_key_env,
        expected_version=args.expected_version,
        allow_remote=args.allow_remote,
        acknowledge_data_upload=args.acknowledge_data_upload,
        timeout_seconds=args.timeout,
        allow_findings=args.allow_finding,
        use_default_allowlist=not args.no_default_allowlist,
    )
    print(json.dumps(result, indent=2) if args.json_output else _format_inspection(result))
    if result["status"] == "blocked":
        return 2
    return 1 if result["actionable"] else 0


def cmd_artifact_clean(args, _state):
    result = clean_artifact(
        args.path,
        args.output,
        service_url=args.service_url,
        api_key_env=args.api_key_env,
        expected_version=args.expected_version,
        allow_remote=args.allow_remote,
        acknowledge_data_upload=args.acknowledge_data_upload,
        timeout_seconds=args.timeout,
        allow_findings=args.allow_finding,
        use_default_allowlist=not args.no_default_allowlist,
        confirm_authorized=args.confirm_authorized,
        overwrite=args.overwrite,
        keep_non_ai_metadata=args.keep_non_ai_metadata,
        nfkc=args.nfkc,
        aggressive_homoglyphs=args.aggressive_homoglyphs,
    )
    print(json.dumps(result, indent=2) if args.json_output else _format_clean(result))
    if result["status"] == "blocked":
        return 2
    return 1 if result["actionable"] else 0
