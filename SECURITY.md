# Plura Desktop Security Policy

## Reporting a vulnerability

Do not open a public issue containing credentials, cookies, conversation content, private file paths, or other sensitive data. Use private vulnerability reporting when available; otherwise publish only a redacted description and request a private channel.

Include the operating system, ChatGPT app version/source, Plura Desktop revision, reproduction steps, and sanitized diagnostics. Never attach authentication files, profile databases, cookies, or a complete process environment.

## Security boundary

Plura Desktop isolates managed `CODEX_HOME` and desktop user-data paths. It is not an operating-system sandbox and does not claim to isolate Keychain/Credential Manager/Secret Service state, OS permissions, URL handlers, helper processes, caches, or account-side data.

The official ChatGPT application/executable is immutable input. The project never patches, re-signs, replaces, bundles, or uninstalls the official installation. On macOS only, Plura Desktop may create a byte-identical, signature-preserving APFS copy-on-write runtime clone under its own metadata so LaunchServices sees a distinct application URL; that derived clone is disposable and is never treated as the official/default app.

The lifecycle is deliberately conservative:

- fixed manifest-owned path roles/kinds,
- platform-specific stable identity checks,
- protected default-profile boundaries,
- link/junction and mounted-subtree refusal where applicable,
- no automatic termination of a running profile,
- strict rejection of unsupported manifest schemas,
- no silent fallback writer/app-server.

If ownership or identity cannot be proven, destructive operations stop for manual review.

OpenAI-owned executable paths, model/tool names, DOM/UI details, fixed ports, and private implementation schemas are not security assumptions. When behavior cannot be established through a supported contract or a bounded platform/integration adapter, Plura Desktop must fail closed rather than guess.

Product-owned state uses the Plura Desktop namespace and remains attributable to an exact ownership record so a supported uninstaller can remove Plura-created resources without deleting the official ChatGPT installation or unrelated user data. Retired product aliases are not accepted by steady-state code.

Persistent diagnostic evidence is opt-in and managed-profile scoped. It stores only the sanitized structural summary, is size/retention bounded, and is removed with the corresponding managed profile. Diagnostic retention never follows symlinks or silently adopts unexpected incident contents.

The macOS release bootstrap is bound to exact SHA-256 values for both architecture-specific standalone runtimes and the wheel fallback. The guided DMG uses the standalone runtime and therefore does not require a separate Python installation; the wheel fallback keeps package-index access disabled (`--no-index --no-deps`). Its full-removal tool validates the recorded distribution runtime/launcher identities and refuses unexpected distribution artifacts rather than recursively deleting ambiguous state. Public consumer releases should prefer the Developer ID signed, notarized, stapled DMG; signing/notarization requirements and artifact naming are documented in `docs/RELEASING.md`.

The supported Homebrew formula is a separate free CLI/runtime distribution surface. Homebrew verifies the pinned SHA-256 of the architecture-specific immutable GitHub Release asset. Its wrapper may provide `PLURA_DESKTOP_STABLE_RUNTIME` so managed selectors can use Homebrew's stable `opt` path; Plura accepts that override only when it is an absolute existing file that resolves to the exact frozen executable currently running. This prevents a package-manager path override from redirecting managed selectors to unrelated code.

The optional PyPI Trusted Publishing path does not rebuild a parallel package artifact set. It downloads the exact wheel/sdist from an already-published immutable GitHub Release, verifies the release checksums, GitHub attestations, and source identity recorded in `RELEASE-METADATA.json`, then publishes those same verified bytes through GitHub OIDC. This keeps the GitHub Release and PyPI package byte provenance aligned without storing a long-lived PyPI token.

The project has no background updater, polling updater service, or silent runtime download path. The guided macOS install creates an explicit user-invoked update helper; release downloads occur only when the user runs that helper/installer and remain bound to the release/distribution checks described above.
