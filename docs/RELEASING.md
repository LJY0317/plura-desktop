# Releasing Plura Desktop

The public release pipeline is tag-driven and fail-closed. `src/plura_desktop/version.py` is the single product-version source and a release tag must be exactly `v<version>`.

The repository was recreated from a fresh public source snapshot on 2026-09-28. GitHub immutable releases permanently reserve tag names that were previously attached to immutable releases, even after those releases or the former repository are deleted. Consequently, the historical `v0.1.0` through `v0.1.12` tag names must never be reused; the fresh public lineage resumes release publication at `v0.1.13` and later.

## Release gate

Before a GitHub Release is created, the tag workflow runs the full test/package/invariant suite on macOS, Windows, and Linux with Python 3.10 and 3.12. The build job creates and verifies the wheel/source distribution. Separate Apple Silicon and Intel jobs then build one-file standalone Plura runtimes for `arm64` and `x86_64`.

The macOS assembly job signs the standalone runtimes first when Developer ID credentials are available, computes SHA-256 over those final bytes, then renders a bootstrap installer bound to the exact tag, wheel, standalone filenames, and hashes. It builds a DMG containing a universal (`x86_64` + `arm64`) native `Install Plura Desktop.app`, both architecture-specific standalone runtimes, the wheel fallback, and the rendered checksum-bound installer. The final publish job creates `SHA256SUMS` across every downloadable asset before creating/updating the GitHub Release.

The release also includes `RELEASE-METADATA.json`, which records the exact immutable source tag/commit checked out for the build plus the workflow commit/run that performed the build. `SHA256SUMS` covers this metadata alongside the downloadable artifacts.

The publish job creates a GitHub artifact attestation for every file named by `SHA256SUMS`. Public-repository attestations use GitHub's Sigstore-backed provenance service and can be verified with GitHub CLI, for example:

```sh
gh attestation verify plura_desktop-VERSION-py3-none-any.whl --repo LJY0317/plura-desktop
```

The GitHub attestation establishes the repository and workflow run that vouched for the bytes. Published GitHub Releases are intentionally immutable: the release workflow has no manual rebuild path, refuses an already-existing release tag, never uses `--clobber`, and never edits published release assets in place. It creates a **draft** release, uploads the complete asset set while the release is still mutable, then publishes the draft exactly once. If draft creation/upload fails, the workflow removes only that unpublished draft so the tag can be retried safely. Fixes or signing changes after publication require a new package version and tag.

After publication, a separate read-only `verify-published-release` job checks out the exact release tag and runs `scripts/verify-release.py` against the just-published GitHub Release. That final gate re-downloads the public release state and verifies repository-native immutability, the exact asset set/checksums, `RELEASE-METADATA.json` source binding, and GitHub Actions attestations. This intentionally verifies the published consumer surface rather than trusting only the pre-publish workspace.

Repository-native GitHub **immutable releases** should remain enabled. Once the draft is published, GitHub itself locks the release assets and associated tag against replacement, deletion, or movement. This server-side policy complements the workflow checks and gives one public version one fixed published byte set. Existing releases created before the repository policy was enabled remain historical mutable releases; do not rewrite them. GitHub provenance is not a substitute for macOS Developer ID signing/notarization.

## Optional free PyPI publication

PyPI publication does not require Apple Developer membership or a paid signing certificate. The repository includes a separate **manual-only** `Publish Python package to PyPI` workflow that uses PyPI Trusted Publishing (GitHub OIDC), so no long-lived PyPI API token is stored in GitHub Secrets.

Before the first PyPI publication, configure a PyPI Trusted Publisher for project `plura-desktop` with these exact values:

- GitHub owner: `LJY0317`
- Repository: `plura-desktop`
- Workflow: `publish-pypi.yml`
- Environment: `pypi`

The workflow defaults to `publish=false`, which is a safe dry run. It checks out the exact tag, requires the corresponding GitHub Release to be published and repository-native immutable, downloads the **exact wheel and sdist already published in that release**, verifies their `SHA256SUMS`, GitHub attestations, and `RELEASE-METADATA.json` source tag/commit/repository identity, then smoke-installs that exact wheel. It does not rebuild a second PyPI-specific copy of the distributions. The PyPI environment/publish job is skipped. This dry run can be used before any PyPI account setup.

After the Trusted Publisher is configured, run the same workflow with an already-published immutable `v<version>` tag and set `publish=true`. Only then does the `pypi` environment job request the OIDC token and publish the already-verified GitHub Release wheel/sdist bytes. Do not set `publish=true` before the PyPI Trusted Publisher is configured; the publish job is intentionally expected to fail without that account-side one-time setup.

Once the project exists on PyPI, the normal Python install surface may additionally use `pipx install plura-desktop` or `pip install plura-desktop`. Until then, GitHub source/release installation remains the documented public path.

## Free Homebrew distribution

The public `LJY0317/homebrew-plura` tap is the supported no-Python macOS package-manager path while Developer ID/notarization is deferred. The formula installs the architecture-specific standalone runtime from a published immutable Plura Desktop release and verifies the exact upstream SHA-256.

Formula updates are intentionally downstream of a completed Plura Desktop release. The tap's scheduled/manual `Update Plura Desktop formula` workflow finds the newest immutable semantic-version upstream release, verifies `SHA256SUMS`, recomputes the downloaded sdist/arm64/x86_64 hashes, verifies GitHub attestations and `RELEASE-METADATA.json`, then renders the formula. When an update is required, the workflow serializes update runs and tests the transition on both `macos-15` and `macos-15-intel`: it installs the currently published formula first, replaces the formula with the verified candidate, runs style/audit, upgrades to the candidate, and runs the formula test/version smoke. Only after both architectures pass does a write-scoped final job commit the formula update to tap `main`.

The tap also keeps a separate scheduled `Upstream release drift` check. It is an alarm rather than a writer: if the automatic updater ever fails to bring the formula to the newest immutable upstream release, the later drift check fails visibly instead of silently leaving Homebrew behind. `scripts/update-formula.sh VERSION` remains the manual verified fallback and `--check` remains the read-only provenance gate used by tap CI.

The standalone runtime accepts `PLURA_DESKTOP_STABLE_RUNTIME` only when the supplied path is absolute, exists, and resolves to the currently executing frozen binary. The Homebrew wrapper sets this to `opt_libexec/plura-desktop`, which causes newly installed/refreshed selectors and the metadata control helper to record the stable Homebrew `opt` path instead of a versioned Cellar path. Generated standalone selectors/control helpers propagate the same validated override when they invoke Plura again, so a later `refresh` cannot regress back to the current versioned Cellar keg after Homebrew retargets `opt`.

Homebrew distribution does not change the Apple trust claim: a Homebrew-installed standalone runtime is not equivalent to the suffix-free Developer ID signed/notarized/stapled DMG.

## macOS signing and notarization

The workflow recognizes these optional repository secrets:

- `MACOS_CERTIFICATE_P12` — base64-encoded Developer ID Application certificate/key bundle
- `MACOS_CERTIFICATE_PASSWORD`
- `MACOS_SIGNING_IDENTITY` — the exact Developer ID Application identity
- `APPLE_ID`
- `APPLE_TEAM_ID`
- `APPLE_APP_SPECIFIC_PASSWORD`

Artifact naming intentionally communicates the trust level:

- `Plura-Desktop-VERSION-macOS.dmg` — Developer ID signed, notarized, and stapled; this is the normal end-user DMG.
- `Plura-Desktop-VERSION-macOS-signed.dmg` — Developer ID signed but not notarized because notarization credentials were unavailable; do not present this as the normal consumer path.
- `Plura-Desktop-VERSION-macOS-unsigned.dmg` — ad-hoc signed CI/development artifact; do not present this as the normal consumer path.

Do not rename a `-signed` or `-unsigned` artifact to the notarized filename. The un-suffixed filename is produced only after `notarytool --wait`, `stapler staple`, and `stapler validate` succeed.

GitHub Release status follows the same trust contract. A release that contains only `-unsigned.dmg` or `-signed.dmg` is published as a **prerelease** and is not marked Latest. Only a release containing the suffix-free, notarized/stapled `Plura-Desktop-VERSION-macOS.dmg` is promoted to a normal/latest GitHub Release.

## macOS installer behavior

The normal user-facing guided surface is the DMG's `Install Plura Desktop.app`. It does not patch or install the official ChatGPT application and does not require a separate Python installation. The DMG uses its bundled architecture-specific runtime and verifies the rendered SHA-256 before installing it into the Plura-owned distribution layout and creating the Update/Uninstall tools.

`install-plura-desktop-macos.sh` remains a release-bound bootstrap asset because the DMG installer and the generated Update tool reuse that lifecycle. It is not presented as a separate normal-user installation choice. The wheel path remains the advanced/source fallback and installs with package-index access disabled (`--no-index --no-deps`).

The installer also creates local Update/Uninstall commands in `~/Applications/Plura Desktop Tools`. The full uninstaller first validates the installer ownership record, previews every managed-profile removal, requires the literal confirmation `REMOVE`, then removes the managed profiles and installer-owned CLI runtime. Unknown/replaced distribution artifacts fail closed.

The generated Update command resolves the newest **published** GitHub Release through the public releases API, intentionally including prereleases. This keeps updates functional during the free unsigned-release phase, when GitHub's `/releases/latest` endpoint has no normal release to return. The updater then downloads the release-bound installer from that exact tag; the installer's embedded filenames and SHA-256 values remain the authority for the actual runtime/wheel bytes.

Guided distribution-runtime replacement is stricter than selector-only `refresh`: if an existing managed profile is not proven stopped, install/update refuses before swapping the standalone runtime. This avoids mixing a live supervisor from one release with child/control processes from another release.

## Tagging

After all real-app platform gates required for the intended support statement have passed:

Create a lightweight `v<version>` tag that exactly matches `src/plura_desktop/version.py`, then push that tag to the public remote. Avoid an annotated tag unless there is a deliberate need for separate tagger metadata.

Do not delete/recreate a published release tag and do not reuse a version after publication. If release tooling, signatures, or artifacts need to change, increment the package version and publish a new tag. The workflow itself refuses to overwrite an existing GitHub Release.
