# Contributing to Plura Desktop

Plura Desktop is the independent multi-profile runtime for ChatGPT Desktop in the Plura family. Contributions should keep it small, auditable, change-resilient, fail-closed, easy to install/remove, and native to macOS, Windows, and Linux where the feature is not explicitly platform-specific.

## Principles

- Treat **Plura Desktop** as the product brand. Use ChatGPT/Codex only to describe upstream integrations. Source/package/CLI/process/product-metadata names use the Plura Desktop namespace; do not add compatibility aliases for retired product names.
- Optimize the normal path for an ordinary supported official ChatGPT Desktop installation plus generic managed profiles. Custom executable locations, downstream controllers, diagnostics, and unusual environments stay behind optional adapters/capabilities instead of branching through the core.
- Do not hardcode OpenAI-owned model IDs, tool names, DOM/UI labels, private schemas, fixed ports, or incidental process layout as product-wide truth. Prefer runtime discovery/capabilities and isolate unavoidable observations behind narrow adapters.
- Never modify, re-sign, bundle, replace, or uninstall the official ChatGPT application. The macOS adapter's byte-identical, signature-preserving APFS copy-on-write runtime clone is the sole clone exception and remains disposable Plura-owned derived code.
- Never read, print, copy, migrate, or delete authentication material from an existing profile.
- Keep managed profiles isolated; do not share `CODEX_HOME` or desktop user-data directories.
- Keep destructive paths on exact manifest allowlists and preserve default-profile paths.
- One logical profile has one authoritative official-desktop owner. Do not add hidden fallback writers/app-servers.
- Put shared domain/manifest/protocol logic in the common core and OS integration behind platform adapters.
- Prefer on-demand/event-driven, lifecycle-bound, single-flight/coalesced behavior. Idle CPU, wakeups, disk I/O, memory, and laptop battery cost are design constraints; do not add polling services, permanent samplers, login items, telemetry, or unnecessary network dependencies.
- Keep diagnostics failure-triggered or explicitly on-demand, privacy-safe, structured, and bounded. Persistent diagnostic artifacts live only under `PluraDesktop/diagnostics/profile-N/incidents`, remain opt-in, and must preserve the repository's size/retention and fail-closed ownership checks.
- Treat install, refresh, any future explicit migration, and uninstall as one ownership lifecycle. Persistent product-created resources need exact provenance and should support reviewable inspect/plan/apply/verify or equivalent dry-run semantics before destructive changes.
- Unknown OS/app behavior should be marked unverified and fail clearly rather than be inferred from another OS.
- Selector/runtime refresh may run while a profile is active because it does not mutate the authoritative running process or profile-state directories. Destructive state removal must still refuse a running profile.

Plura Mobile, menu-bar controllers, Tela, and other downstream tools are consumers of the versioned target/session contract. Plura Desktop must remain independently usable and must not absorb downstream UI, transport, credential, or lifecycle ownership.

## Development

Use temporary home directories and fake executables in tests. Never point tests at real Codex/ChatGPT data.

Before opening a pull request:

```sh
python3 -m unittest discover -s tests -v
for script in scripts/*.sh; do sh -n "$script"; done
python3 -m compileall -q src scripts tests
python3 scripts/check_repository_invariants.py
```

GitHub Actions runs the suite on macOS, Windows, and Linux. For platform-specific launch changes, state which real OS/app versions were manually verified and which remain CI-only.

Redact usernames, account data, credentials, cookies, conversations, and private paths from logs/screenshots. The macOS on-demand session diagnostic must remain structural/read-only and hash conversation/thread identities before output.

## Packaging and releases

- `src/plura_desktop/version.py` is the single product-version source. Do not duplicate a release version in CLI/runtime code.
- `pyproject.toml` reads that version dynamically and installs the canonical `plura-desktop` and `plura-desktop-diagnose` commands.
- A release tag must be exactly `v<package-version>`; the release workflow fails closed when the tag and package version differ.
- Selectors/control runtimes must be reproducible from either the installed Python package or the frozen standalone runtime capability. Do not add dependencies on repository-only files outside the packaged/frozen boundary.
- Before release, build the wheel/sdist and install the wheel into a fresh virtual environment, then build/smoke the macOS standalone runtime. Verify both package and frozen execution modes before publishing assets.
- PyPI publication must reuse the exact wheel/sdist bytes from the already-published immutable GitHub Release after checksum, attestation, and release-metadata verification. Do not create a separate rebuilt PyPI artifact set for the same version.
- Homebrew formula updates must be derived only from immutable upstream release data. The tap's automatic updater verifies provenance and requires ARM/Intel install/upgrade tests before it writes a formula update; keep the manual helper as a fail-closed fallback rather than bypassing those checks.
- A platform's package may be distributable before its official ChatGPT app path is real-app verified, but documentation must preserve the CI-only vs real-app-verified distinction.

See [`docs/RELEASING.md`](docs/RELEASING.md) for the tag pipeline, macOS DMG trust-level naming, and optional Developer ID/notarization secret contract.
