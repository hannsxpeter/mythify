# Artifact hygiene integration

Mythify integrates with
[`watermarks-remover`](https://github.com/guillaumemeyer/watermarks-remover)
as an optional external service. Mythify does not vendor its cleaners, research
models, or heavy backends.

## Public surfaces

The Python CLI exposes:

- `mythify artifact probe`
- `mythify artifact inspect PATH`
- `mythify artifact clean PATH --output OUTPUT --confirm-authorized`

The MCP server exposes the matching tools `artifact_probe`,
`artifact_inspect`, and `artifact_clean`.

The default service URL is `http://127.0.0.1:8765`. It can be changed with
`WATERMARKS_SERVICE_URL` or an explicit `service_url`. Authentication is read
only from the allowlisted `WATERMARKS_SERVER_API_KEY` environment variable.
Secrets are never accepted as command arguments or returned in results.

## Adapter contract

Every inspect or clean request probes `/health` and `/capabilities` before it
sends artifact bytes. An optional expected version can pin the service. The
adapter then uses `/inspect` or `/clean` and preserves the service's raw report
inside its result.

Loopback is the default trust boundary. A non-loopback URL requires an explicit
remote-service opt-in. Inspecting or cleaning through a remote service also
requires a separate data-upload acknowledgement. Redirects are refused so a
loopback request cannot silently move artifact content to another host.

Input files are size-capped. A clean requires all of the following:

1. The caller confirms that the artifact is owned or authorized for processing.
2. The output path is explicit and different from the input path.
3. The output is not a symbolic link.
4. The service returns valid base64 content.
5. A second `/inspect` call succeeds on the cleaned bytes before Mythify writes
   the output atomically.

An existing regular output file also requires `--overwrite` or
`overwrite: true`. Mythify never exposes in-place cleaning.

If normalization leaves no actionable deterministic finding, Mythify does not
call `/clean`. It performs the second inspection on the original bytes and
writes a byte-identical output atomically. This prevents an allowed-only prose
frontmatter match from becoming a destructive clean. Disable the built-in
allowlist when strict removal of that service finding is intended.

### Service protocol

The adapter expects JSON objects at four endpoints:

- `GET /health` returns `{"ok": true, "version": "..."}`.
- `GET /capabilities` returns `{"ok": true, ...}`. Optional
  `pixel_backends.ctrlregen` and `scorers.synthid` booleans trigger licensing
  warnings when true.
- `POST /inspect` accepts `{"file": "BASE64", "name": "..."}` and returns
  `{"ok": true, "kind": "...", "suspicious": false, "report": {...}}`.
- `POST /clean` accepts the same file and name plus an `options` object. It
  returns `{"ok": true, "cleaned": "BASE64", "report": {...}}`.

Supported clean options are `keep_non_ai_metadata`, `nfkc`, and
`aggressive_homoglyphs`. Pixel regeneration and reverse-SynthID controls stay
outside Mythify's core surface. The service owns those optional backends and
must report them through capabilities.

## Findings and false positives

The service report remains available as `raw_report`. Mythify also returns a
normalized finding list with one of these classes:

- `deterministic`: explicit C2PA, metadata, or suspicious Unicode observations
- `heuristic`: stylometry and statistical watermark scores
- `allowed`: retained findings downgraded by the built-in or caller allowlist

The built-in allowlist downgrades ordinary prose frontmatter fields such as
`description`, `title`, `summary`, and `keywords` when the service reports only
a broad value match. Explicit provenance keys such as `generator`,
`ai_generated`, `model`, `provenance`, and `digital_source_type` remain
actionable. Callers can add exact finding strings to an allowlist, or disable
the built-in allowlist for strict raw-service behavior.

The allowlist changes only Mythify's normalized disposition. It never deletes
or rewrites the service's raw report.

An inspection is actionable only when a deterministic finding remains after
normalization. Heuristic results are always advisory.

## Evidence boundary

Direct adapter output is material, not Mythify verification evidence. The CLI
uses exit status 0 for no actionable deterministic findings, 1 for actionable
findings or residuals, and 2 for adapter or input errors. A caller that needs a
recorded gate runs the CLI inspection through `mythify verify run`.

MCP results state `material_not_evidence: true`,
`evidence_status: artifact_service_output_not_verification`, and
`verification_recorded: false`. They never write Mythify state.

## Licensing and residual risk

The watermarks-remover core is MIT licensed. CtrlRegen is kept local because
its upstream has no license, and reverse-SynthID is restricted to
non-commercial research. Mythify neither downloads nor redistributes these
backends. Capability results surface a warning when either backend is enabled.

Cleaning removes only what the external service reports. Soft-bound C2PA,
private vendor detectors, statistical text signals, and media watermarks can
remain. A clean result must never be described as proof that an artifact was
human-created or that every provenance signal is gone.
