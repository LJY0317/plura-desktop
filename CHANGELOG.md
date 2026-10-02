# Changelog

All notable public changes to Plura Desktop will be documented here from the first public source snapshot onward.

## [Unreleased]

- Make macOS managed-profile selector refresh transactional: build and validate a complete replacement `Contents` tree before atomically exchanging it with the live selector, preserving the selector app and its manifest-owned identity if preparation fails.
- Fix source/package selector self-refresh when the running selector's copied Plura runtime is also the source for the replacement; refresh no longer deletes its own source files before copying them.
- Make the official/default ChatGPT installation the sole application-update authority: a stopped managed profile automatically follows a changed official app on its next cold launch, while a managed clone changed independently is discarded and rebuilt from the official app without replacing profile state.
- Keep a running managed profile addressable for normal quit even when the official app has already updated, and on current Sparkle-based macOS builds suppress scheduled automatic update checks/downloads only for managed runtimes via process-local defaults rather than patching the upstream app.

## [0.1.16] - 2026-09-29

- Make `quit-target` wait through transient `restart-required` state until the exact Desktop target is actually relaunchable. This closes a race where a normal quit completed but an immediate canonical relaunch could still be rejected, leaving the profile closed instead of reopening it.

## [0.1.15] - 2026-09-28

- Align the Linux adapter with OpenAI's current official preview package layout: resolve the documented `chatgpt` launcher to the real packaged `ChatGPT` executable for fingerprinting, resolve the sibling bundled `resources/codex` app-server executable, and fail closed when that official package root is incomplete instead of silently mixing in an unrelated Codex from `PATH`.
- Preserve only the narrow X11/Wayland/D-Bus desktop-session environment required by the official Linux GUI while keeping unrelated caller environment variables out of managed launches.
- Add a scheduled/manual/PR official-package smoke workflow that downloads OpenAI's current Ubuntu/Debian `.deb`, verifies Plura discovery against the real package, starts the bundled Codex app-server, exercises isolated install/uninstall, and executes the actual generated selector to a canonical renderer-enabled Desktop session under Xvfb. This remains package/launch smoke rather than the signed-in real-user verification gate.
- Route generated Linux desktop selectors through the same canonical `launch-target --renderer-cdp` runtime that the official-package smoke verifies, instead of creating a second direct Desktop launch path.

## [0.1.14] - 2026-09-28

- Track the current official Linux desktop preview without broadening support claims: add a regression test for the documented `chatgpt` PATH command that the Linux adapter already discovers, and clarify that full Linux real-app verification is still required before calling the adapter proven.
- Align the documented/Homebrew macOS minimum with the current official ChatGPT app requirement: macOS 14 or newer, on Apple Silicon or Intel.
- Align the macOS standalone runtime and universal DMG installer deployment targets with that same macOS 14 minimum instead of producing release binaries that advertise an older unsupported target.

## [0.1.13] - 2026-09-28

- Continue the fresh public repository's release line at `v0.1.13`. GitHub immutable releases permanently reserve release tag names even after a release/repository is deleted, so the previously used `v0.1.0` through `v0.1.12` names are intentionally not reused. Runtime/source behavior is otherwise the same as the fresh public root snapshot.

## [0.1.12] - 2026-09-28

- Cover every currently published Python 3.10–3.14 minor in CI/release validation without multiplying the full three-OS matrix: 3.10/3.12/3.14 continue to exercise macOS, Ubuntu, and Windows boundaries, while 3.11/3.13 run the full safety/package smoke suite on Ubuntu. Add the missing Python 3.13 package classifier to match the verified compatibility range.

## [0.1.11] - 2026-09-28

- Add `scripts/verify-release.py`, a Python-stdlib + GitHub CLI helper that verifies repository-native release immutability, the exact downloaded asset set and `SHA256SUMS`, `RELEASE-METADATA.json` source binding, and GitHub Actions attestations for every checksummed asset. Keep the individual manual verification steps documented as an independent fallback.
- Add a post-publish release job that runs the same end-to-end verifier against the newly published immutable GitHub Release, so a release workflow succeeds only after the public consumer surface has been independently re-downloaded and verified.

## [0.1.10] - 2026-09-28

- Extend public-source repository invariants to fail CI when tracked Markdown contains a broken relative link, so documentation moves/renames cannot silently ship stale internal navigation.
- Harden the free PyPI Trusted Publishing path to publish the exact wheel/sdist bytes from an immutable GitHub Release. The workflow now verifies release immutability, SHA-256, GitHub attestations, and `RELEASE-METADATA.json` source identity instead of rebuilding distributions separately for PyPI.
- Add Python 3.14 to the macOS/Windows/Linux CI and release-validation matrices while retaining Python 3.10 as the minimum-version gate and Python 3.12 as the controlled standalone-build interpreter.

## [0.1.9] - 2026-09-28

- Publish and document the free `LJY0317/plura` Homebrew tap as a supported macOS standalone install/update/remove path, with native arm64/Intel tap CI.
- Preserve the validated stable package-manager runtime path in generated selectors and control helpers themselves. This keeps later `refresh` operations on Homebrew's stable `opt` path after the Cellar keg changes, instead of silently rewriting launchers to a versioned keg that can disappear on upgrade.

## [0.1.8] - 2026-09-28

- Allow package-manager wrappers to provide a validated stable absolute path for the current frozen runtime. This lets integrations such as Homebrew write selectors/control helpers against a stable `opt` path instead of a versioned Cellar path, while rejecting missing, relative, or mismatched runtime overrides.

## [0.1.7] - 2026-09-28

- Keep the generated macOS Update command's friendly `Press Return to close` pause only for interactive TTY launches. Non-interactive update/verification runs now exit immediately after a successful update instead of blocking forever waiting for stdin.

## [0.1.6] - 2026-09-28

- Add a safe `publish=false` dry-run mode to the manual PyPI Trusted Publishing workflow so tag checkout, build, and wheel smoke can be validated on GitHub before any PyPI account-side publisher is configured.
- Add a public release-verification guide covering GitHub native immutable-release verification, per-asset verification, SHA-256 checks, workflow attestations, and release-source metadata.
- Add canonical homepage/repository/issues/changelog/security URLs to Python package metadata so a future free PyPI publication links back to the verified public project surfaces.

## [0.1.5] - 2026-09-28

- Prepare release publication for GitHub's native immutable-release enforcement: assemble each release as a draft, upload the complete asset set, publish once, and clean up only an unpublished draft on failure. This allows GitHub to lock future published assets and tags server-side.

## [0.1.4] - 2026-09-28

- Make public GitHub Releases immutable: remove the existing-tag rebuild path, refuse publication if the tag already has a release, and remove all release-asset clobber/edit behavior. Release fixes now require a new version/tag.
- Add a manual-only, tokenless PyPI Trusted Publishing workflow prepared for the free Python-package distribution path. It builds from an exact immutable release tag and uses GitHub OIDC; PyPI account-side publisher registration remains a one-time external setup.

## [0.1.3] - 2026-09-28

- Give renderer-CDP cold startup the remaining bounded 30-second supervisor startup budget and a 35-second caller handoff window. This fixes legitimate concurrent managed-profile cold starts that can take about 20 seconds while preserving a bounded failure path.

## [0.1.2] - 2026-09-28

- Pin every external GitHub Action to its currently verified immutable commit SHA while retaining version comments for Dependabot-managed updates.
- Fix the generated macOS Update tool for the free prerelease-only phase: resolve the newest published GitHub Release through the public releases API (including prereleases) instead of relying on `/releases/latest`, which excludes prereleases.

## [0.1.1] - 2026-09-28

- Add GitHub/Sigstore build-provenance attestations for release assets and allow an existing release tag to be rebuilt manually, so Developer ID/notarization can be added later without rewriting tag history.
- Move release artifact transfer to current Node 24 GitHub Actions generations and add weekly Dependabot checks for GitHub Actions and Python packaging dependencies.
- Pin Linux CI/release jobs to the already-verified Ubuntu 24.04 runner instead of inheriting an upcoming `ubuntu-latest` distribution migration implicitly.
- Add attested `RELEASE-METADATA.json` so manual release rebuilds separately identify the immutable source tag/commit and the workflow commit/run that rebuilt it.
- Pin Windows CI/release validation to the explicit Windows Server 2025 runner instead of inheriting future `windows-latest` migrations implicitly.
- Add privacy-first public issue forms and a pull-request checklist so bug reports and contributions preserve the project's profile-data, official-app, lifecycle, and diagnostics boundaries by default.
- Add pull-request dependency review on Ubuntu 24.04 and fail changes that introduce dependencies with known high-or-higher severity vulnerabilities.
- Make the route-preserving app-server proxy's loopback bind explicit at the final socket boundary (`127.0.0.1` or `::1` only), removing DNS ambiguity and making the existing loopback-only invariant statically auditable by CodeQL.
- Move CodeQL from opaque default setup to a repository-owned Python/Actions workflow on push, pull request, and weekly schedule, using the `security-extended` query suite on the pinned Ubuntu 24.04 baseline.

## [0.1.0] - 2026-09-28

- Initial public source snapshot.
