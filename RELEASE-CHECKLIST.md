# Release checklist

Current release target: `v6.0.0`.

The ordered commands to cut a Mythify release. [MAINTAINING.md](MAINTAINING.md)
explains the version model and why each step exists. Run every command from
the repository root on the final commit, and stop at the first failure: fix,
commit, and start again from step 3.

Release assets for 6.0.0:

- `mythify.skill`, the packaged `skills/mythify` skill
- `mythify-cli-6.0.0.tar.gz`, the self-contained CLI with the MCP server
  (`scripts/mythify_mcp.py`), the installer, the protocol files, and all four
  skills
- `SHA256SUMS`, the flat checksum manifest for both

Below, `X.Y.Z` stands for the release version.

## 1. Set the version

- [ ] `VERSION = "X.Y.Z"` in `scripts/mythify.py` (the single source).
- [ ] `CHANGELOG.md` has `## [X.Y.Z] - YYYY-MM-DD` under `## [Unreleased]`,
      `[Unreleased]` compares `vX.Y.Z...HEAD`, and `[X.Y.Z]` compares the
      previous tag with `vX.Y.Z`.
- [ ] `SECURITY.md` names the supported major line (`X.x`).
- [ ] The "Current release target" line at the top of this file says
      `vX.Y.Z`, and the asset names above say `X.Y.Z`.
- [ ] `tests/test_release_metadata.py` pins `VERSION = "X.Y.Z"` and the new
      CHANGELOG heading date.

## 2. Regenerate generated files

```bash
python3 scripts/build_variants.py
python3 scripts/build_commands_doc.py
git status --short
```

Commit anything the generators changed.

## 3. Full suite

Run it on a Python 3.9 interpreter (the oldest supported version; set `PY39`
to its path, for example `/usr/bin/python3` on a macOS install that ships
3.9) and on a current Python:

```bash
"$PY39" --version
"$PY39" -m unittest discover -s tests -v
python3 -m unittest discover -s tests -v
```

## 4. Lint

```bash
python3 scripts/lint.py
```

It must print `[OK] lint passed` and exit 0. It covers the CHANGELOG
heading, the SECURITY.md supported line, and the release target from step 1
(the step 3 suite covers the asset names and the test pins), the generated
files from step 2, the prose check, and the runtime source size.

## 5. Release workflow gates, locally

These are the checks `.github/workflows/release.yml` runs before it
publishes, besides the full suite and the step 4 lint:

```bash
python3 scripts/package_cli.py --check-release-tag vX.Y.Z
python3 -m unittest tests.test_install_user tests.test_release_checksums tests.test_mcp_server tests.test_release_version -v
python3 scripts/mythify.py protocol check AGENTS.md CLAUDE.md
git diff --check
```

## 6. Installer smoke

Install into a throwaway prefix, data directory, and skills root, so nothing
touches your real installation:

```bash
tmp=$(mktemp -d)
mkdir -p "$tmp/project"
XDG_DATA_HOME="$tmp/data" scripts/install_user.sh \
  --prefix "$tmp/install" --project "$tmp/project" --skills-root "$tmp/skills"
(cd "$tmp/project" && "$tmp/install/bin/mythify" status)
"$tmp/install/bin/mythify" --version
```

`--version` must print `Mythify vX.Y.Z`.

## 7. MCP smoke

Through the installed launcher, against the project from step 6:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"release-smoke","version":"1"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"status","arguments":{}}}' \
  | MYTHIFY_DIR="$tmp/project/.mythify" "$tmp/install/bin/mythify-mcp" \
  | python3 -c '
import json, sys
r = {m["id"]: m for m in map(json.loads, sys.stdin)}
assert r[1]["result"]["protocolVersion"] == "2025-11-25", r[1]
names = [t["name"] for t in r[2]["result"]["tools"]]
assert "verify_run" in names and "mythify" in names, names
assert r[3]["result"]["isError"] is False, r[3]
print("[OK] MCP smoke: {0} tools, status call ok".format(len(names)))
'
"$tmp/install/bin/mythify-uninstall"
```

The uninstaller must report that it preserved the project state.

## 8. Package

```bash
python3 scripts/package_skill.py
python3 scripts/package_cli.py
```

This writes `dist/mythify.skill` and `dist/mythify-cli-X.Y.Z.tar.gz`.

## 9. Checksums

```bash
mkdir -p dist/release-assets
cp dist/mythify.skill dist/mythify-cli-X.Y.Z.tar.gz dist/release-assets/
python3 scripts/build_release_checksums.py \
  --output dist/release-assets/SHA256SUMS \
  dist/release-assets/mythify.skill \
  dist/release-assets/mythify-cli-X.Y.Z.tar.gz
python3 scripts/build_release_checksums.py \
  --check dist/release-assets/SHA256SUMS \
  --directory dist/release-assets
```

The staging directory mirrors the flat GitHub release download layout. The
checker rejects repository-relative names, duplicate basenames, missing
assets, and digest mismatches.

## 10. Tag

Push the release commit first and wait for CI on it (`Python 3.9`,
`Python 3.13`, and `Repository hygiene`) to pass. Then:

```bash
git tag -a vX.Y.Z -m "Mythify X.Y.Z"
git push origin vX.Y.Z
```

## 11. Release workflow

The tag push starts `.github/workflows/release.yml`. It checks out the tagged
commit, runs the step 5 gates and the full suite, builds both packages,
verifies the flat checksums, and only then runs `gh release create` with the
three assets. A failed gate creates no release.

When it finishes, download the published assets and check them:

```bash
gh release view vX.Y.Z
out=$(mktemp -d)
gh release download vX.Y.Z --dir "$out"
python3 scripts/build_release_checksums.py --check "$out/SHA256SUMS" --directory "$out"
```
