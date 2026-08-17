#!/usr/bin/env python3
"""Argument parser wiring for Mythify artifact hygiene commands."""

from __future__ import annotations


def add_artifact_parser(
    subparsers,
    *,
    default_service_url,
    default_api_key_env,
    probe_handler,
    inspect_handler,
    clean_handler,
):
    artifact = subparsers.add_parser(
        "artifact",
        help="Probe, inspect, or clean through an external artifact-hygiene service.",
        description=(
            "Use the optional watermarks-remover service without vendoring it. "
            "Direct service output is material, not Mythify verification evidence."
        ),
    )
    actions = artifact.add_subparsers(
        dest="artifact_command", metavar="ACTION", required=True
    )

    def add_service_arguments(command_parser, include_upload_ack=False):
        command_parser.add_argument(
            "--service-url",
            default=None,
            help=(
                "Service base URL. Defaults to WATERMARKS_SERVICE_URL or {0}."
            ).format(default_service_url),
        )
        command_parser.add_argument(
            "--api-key-env",
            default=None,
            help=(
                "Allowlisted environment variable containing bearer auth. "
                "Only {0} or an empty value is accepted."
            ).format(default_api_key_env),
        )
        command_parser.add_argument(
            "--expected-version",
            default=None,
            help="Require an exact service version from /health.",
        )
        command_parser.add_argument(
            "--allow-remote",
            action="store_true",
            help="Allow a non-loopback service URL.",
        )
        if include_upload_ack:
            command_parser.add_argument(
                "--acknowledge-data-upload",
                action="store_true",
                help="Acknowledge that artifact bytes will be sent to a remote service.",
            )
        command_parser.add_argument(
            "--timeout",
            type=float,
            default=30,
            help="HTTP timeout per request in seconds. Defaults to 30.",
        )
        command_parser.add_argument(
            "--json", dest="json_output", action="store_true", help="Print JSON."
        )

    probe = actions.add_parser(
        "probe",
        help="Probe health, version, capabilities, and licensing warnings.",
    )
    add_service_arguments(probe)
    probe.set_defaults(handler=probe_handler, needs_state=False)

    inspect = actions.add_parser(
        "inspect",
        help="Inspect one local artifact through the external service.",
    )
    inspect.add_argument("path", help="Artifact file to inspect.")
    inspect.add_argument(
        "--allow-finding",
        action="append",
        default=[],
        help="Downgrade one exact service finding. Repeat for multiple findings.",
    )
    inspect.add_argument(
        "--no-default-allowlist",
        action="store_true",
        help="Disable built-in prose-frontmatter false-positive downgrades.",
    )
    add_service_arguments(inspect, include_upload_ack=True)
    inspect.set_defaults(handler=inspect_handler, needs_state=False)

    clean = actions.add_parser(
        "clean",
        help="Clean an authorized artifact to a separate output path.",
    )
    clean.add_argument("path", help="Artifact file to clean.")
    clean.add_argument("--output", required=True, help="Separate output file path.")
    clean.add_argument(
        "--confirm-authorized",
        action="store_true",
        help="Confirm ownership of or authorization to process the artifact.",
    )
    clean.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing regular output file atomically.",
    )
    clean.add_argument(
        "--keep-non-ai-metadata",
        action="store_true",
        help="Ask the service to preserve metadata it does not classify as AI-related.",
    )
    clean.add_argument(
        "--nfkc",
        action="store_true",
        help="Ask the service to apply Unicode NFKC normalization.",
    )
    clean.add_argument(
        "--aggressive-homoglyphs",
        action="store_true",
        help="Ask the service to normalize suspicious homoglyphs aggressively.",
    )
    clean.add_argument(
        "--allow-finding",
        action="append",
        default=[],
        help="Downgrade one exact post-clean finding. Repeat for multiple findings.",
    )
    clean.add_argument(
        "--no-default-allowlist",
        action="store_true",
        help="Disable built-in prose-frontmatter false-positive downgrades.",
    )
    add_service_arguments(clean, include_upload_ack=True)
    clean.set_defaults(handler=clean_handler, needs_state=False)
