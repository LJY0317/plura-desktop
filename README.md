# Plura Desktop

**A multi-profile runtime for ChatGPT Desktop.**

**Plura Desktop** is an independent runtime for running and managing multiple isolated profiles of the official ChatGPT Desktop application. It does so without patching, re-signing, or modifying the official application. On macOS, managed profiles use a byte-identical signed APFS clone as a derived runtime so LaunchServices can distinguish them from the untouched default app.

The project now has a shared profile/domain lifecycle with platform adapters for **macOS, Windows, and Linux**. The official/default ChatGPT profile remains outside this project's ownership. Managed profiles start at Profile 2 and isolate `CODEX_HOME` plus the desktop user-data directory.

For the distinction between **independent local profiles**, sequential account switching, mobile Remote continuity and the still-unavailable **simultaneous cross-account sharing of one original thread**, see [profile continuity and sharing boundaries](docs/PROFILE_CONTINUITY_AND_THREAD_SHARING.ko.md). A read-only macOS metadata-only storage separation audit is included at `scripts/diagnose_local_conversation_isolation.py`.
Plura Desktop launches isolated official Desktop apps; the official app owns Native execution
and its tools. Plura does not start or proxy a Codex engine. Model providers belong in the
official app's supported configuration. Companion integrations must support target contract v2;
v1 routed app-server integrations are no longer supported. Profile data is preserved.

`Plura Desktop` is the product name. ChatGPT and Codex describe the upstream products it integrates with rather than this project's brand identity.

> [!IMPORTANT]
> This is an unofficial community project. It is not affiliated with, endorsed by, or supported by OpenAI. ChatGPT and Codex are trademarks of OpenAI.

[한국어 문서](docs/README.ko.md)

## Quick start

The new app-owned execution path has passed an isolated real-app startup/normal-quit probe on Apple Silicon macOS. Signed-in Native/browser acceptance and Intel, Windows and Linux real-app validation remain release gates. Earlier full E2E results apply to earlier releases.

When GitHub Releases contains the suffix-free **`Plura-Desktop-VERSION-macOS.dmg`**, that notarized/stapled DMG is the recommended macOS install. Until then, **Homebrew is the simplest supported macOS install path** and does not require a separate Python environment. The source/package path remains available as the portable fallback. Do not treat `-unsigned.dmg` or `-signed.dmg` artifacts as the normal consumer installer.

### macOS guided install — notarized DMG (when available)

Download **`Plura-Desktop-VERSION-macOS.dmg`** from GitHub Releases, open it, and double-click **Install Plura Desktop.app**. The un-suffixed DMG name is reserved for the Developer ID signed, notarized, and stapled artifact.

The installer:

- selects the bundled `arm64` or `x86_64` standalone Plura runtime for the current Mac and verifies its exact release checksum,
- creates **ChatGPT Profile 2** automatically when the official `/Applications/ChatGPT.app` is present,
- leaves the official ChatGPT app/default profile untouched,
- creates **Update Plura Desktop.command** and **Uninstall Plura Desktop.command** under `~/Applications/Plura Desktop Tools`.

Then open **ChatGPT Profile 2** from `~/Applications` and sign in normally. Do not copy cookies, authentication files, profile databases, or conversations from another profile.

Release assets ending in `-unsigned.dmg` or `-signed.dmg` are CI/development trust levels, not the recommended normal-user download. See [docs/RELEASING.md](docs/RELEASING.md).

### Homebrew install (macOS, no separate Python)

The current official ChatGPT macOS app requires **macOS 14 or newer**; the Homebrew formula enforces the same minimum.

Install the standalone Plura runtime from the public tap:

```sh
brew install LJY0317/plura/plura-desktop
```

Then create the first managed profile:

```sh
plura-desktop --version
plura-desktop status --profile 2
plura-desktop install --profile 2
open "$HOME/Applications/ChatGPT Profile 2.app"
```

The formula downloads the architecture-specific standalone runtime from an immutable Plura Desktop GitHub Release and Homebrew verifies its SHA-256. The public tap updates only after it renders the newest immutable release from verified release metadata/attestations and the resulting formula passes install/test on both Apple Silicon and Intel macOS runners. Plura additionally records Homebrew's stable `opt` runtime path in managed selectors/control helpers rather than a versioned Cellar path, so formula upgrades do not strand existing selectors on an old keg.

This free Homebrew path is **not Apple Developer ID signing/notarization**. It is a supported CLI/runtime distribution path while the suffix-free notarized DMG remains a future consumer distribution surface.

### Source/package install (Python 3.10+)

This path is usable directly from the public repository and is the supported fallback whenever a notarized macOS DMG is not available. It requires **Python 3.10+**. From a fresh clone on macOS/Linux:

```sh
git clone https://github.com/LJY0317/plura-desktop.git
cd plura-desktop
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

On Windows PowerShell, the equivalent environment setup is:

```powershell
git clone https://github.com/LJY0317/plura-desktop.git
cd plura-desktop
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .
```

If a release wheel is available, `pipx` is also a convenient isolated install surface:

```sh
pipx install ./plura_desktop-VERSION-py3-none-any.whl
```

Release files also carry GitHub artifact attestations. After downloading a wheel or other release asset, users with GitHub CLI can verify that the exact bytes were produced by this repository's GitHub Actions workflow:

```sh
gh attestation verify plura_desktop-VERSION-py3-none-any.whl --repo LJY0317/plura-desktop
```

This provenance check is free and independent of Apple Developer signing; it does not make an unsigned macOS DMG equivalent to a notarized consumer DMG.

Current public releases, beginning with the fresh repository line at `v0.1.13`, use GitHub's repository-native **immutable releases**: after publication GitHub locks the release assets and associated tag. Each release also includes `RELEASE-METADATA.json`, which records the immutable source tag/commit and workflow run that produced that fixed asset set. See [docs/VERIFY_RELEASE.md](docs/VERIFY_RELEASE.md) for the complete checksum + immutable-release + workflow-attestation verification procedure.

From a source checkout, current immutable releases can also be verified end to end with `python3 scripts/verify-release.py vVERSION`; the manual commands remain documented independently.

Verify the installed command:

```sh
plura-desktop --version
```

Keep that isolated Python environment installed while managed profiles exist; Profile selectors/control helpers intentionally use the interpreter recorded by the latest `install`/`refresh`. Remove managed profiles before removing the CLI package/environment. macOS users who prefer not to maintain this Python environment can use the Homebrew standalone path above instead.

Create and launch the first managed profile:

```sh
plura-desktop status --profile 2
plura-desktop install --profile 2
plura-desktop launch --profile 2
```

Sign in normally in the new **ChatGPT Profile 2** window. Do not copy cookies, authentication files, profile databases, or conversations from another profile.

On macOS the install creates `~/Applications/ChatGPT Profile 2.app`, which can then be launched like a normal app. The official `/Applications/ChatGPT.app` remains untouched.

### Updating ChatGPT itself

The official/default ChatGPT installation is the **only application-update authority**. Update ChatGPT from the normal/default ChatGPT app. Managed Profile 2+ macOS runtimes are disposable derived clones, not independent application installations: after the official app changes, the next cold managed-profile launch refreshes its derived runtime/selector from the official app while preserving that profile's login and conversation state. A managed profile that is already running is never killed or rewritten underneath; it follows the new official build only after a normal quit and later launch.

Plura does not patch ChatGPT's update UI. On current macOS builds that expose Sparkle, managed launches use process-local Sparkle defaults to suppress scheduled automatic update checks/downloads without changing the official app or default-profile preferences. Manual upstream update UI can still exist. If an upstream self-updater nevertheless changes a managed clone, Plura treats that clone as disposable and reconciles it back to the official app before the next managed launch.

**Update the official ChatGPT application from Profile 1 only.** Do not use a manual updater control that might remain visible in managed profiles. Plura checks whether a managed launcher is complete, serializes update/launch preparation, repairs only safe disposable derived code, and records privacy-limited failure diagnostics. See [macOS update recovery and diagnostics](docs/UPDATE_RECOVERY_AND_DIAGNOSTICS.ko.md).

### Update

This section updates **Plura Desktop itself**, not the ChatGPT application above.

For the guided macOS DMG install, double-click:

`~/Applications/Plura Desktop Tools/Update Plura Desktop.command`

It downloads the installer from the latest published release in the same GitHub repository (including prereleases while the project is in its free unsigned-release phase), installs the new checksum-bound standalone runtime for the current Mac, and refreshes every installed managed profile without replacing its profile/login state.

Quit all managed **ChatGPT Profile N** windows normally before running the guided updater. Distribution-runtime replacement fails closed while a managed profile is running; the updater never kills it automatically.

For a source/package install, update using the same environment you used to install it, then refresh each managed profile. For a source clone:

```sh
git pull --ff-only
python -m pip install --upgrade .
plura-desktop refresh --profile 2
```

For a downloaded wheel:

```sh
pipx install --force ./plura_desktop-NEW_VERSION-py3-none-any.whl
plura-desktop refresh --profile 2
```

For a Homebrew install, quit managed ChatGPT Profile windows normally, then run:

```sh
brew update
brew upgrade plura-desktop
plura-desktop refresh --profile 2
```

### Remove a managed profile

For the guided macOS DMG install, double-click:

`~/Applications/Plura Desktop Tools/Uninstall Plura Desktop.command`

It validates the installer ownership record, previews every managed-profile removal, and requires the literal confirmation `REMOVE` before deleting managed profile state plus the Plura-owned CLI runtime/tools. The official ChatGPT app/default profile are not removal targets.

For a CLI/package install, quit the selected managed ChatGPT profile normally and preview the exact removal set first:

```sh
plura-desktop uninstall --profile 2
```

Apply only after reviewing it:

```sh
plura-desktop uninstall --profile 2 --yes
```

After all managed profiles are removed, uninstall the CLI package with the same package manager you used to install it (for example `pipx uninstall plura-desktop` or `python -m pip uninstall plura-desktop`). The official ChatGPT application/default profile are never uninstall targets.

For Homebrew, remove managed profiles first as above, then run:

```sh
brew uninstall plura-desktop
```

### Troubleshooting

- **`plura-desktop: command not found`** — run it from the isolated environment/package tool where you installed Plura Desktop, or fix that tool's normal PATH setup. In an activated venv, `python -m plura_desktop --version` is an equivalent fallback. Do not copy the console script by itself.
- **`ChatGPT executable not found` on Windows/Linux** — pass the real executable with `--app PATH` or set `CHATGPT_EXECUTABLE`. Plura Desktop does not guess unknown install layouts.
- **Uninstall says the profile is running** — quit the managed ChatGPT profile normally and rerun the dry run. The uninstaller never kills it automatically.
- **A path/identity/symlink safety error** — inspect the reported path instead of deleting it manually through Plura Desktop. Ambiguous destructive state intentionally fails closed.

## Architecture and ownership

The official Desktop owns Native execution, tools and engine lifetime. Plura owns profile
identity, manifests, isolated state paths, selectors and install/refresh/uninstall operations.
Platform adapters handle executable discovery, filesystem identity and normal OS app launch.
All managed launch surfaces use `launch-target`; an existing running app is reused.

Plura starts no separate Native engine, model-routing proxy, supervisor or background watcher.
The original app remains unchanged. Managed macOS runtime clones are disposable, signed copies;
login and conversation data remain in their isolated profile directories.

## Product principles

- **Mainstream-first within the product's scope** — the normal path is the official ChatGPT Desktop plus generic managed profiles. Special launchers, diagnostic probes, or downstream clients stay behind optional adapters/contracts rather than contaminating the core lifecycle.
- **Change-resilient** — OpenAI-owned executable locations, model/tool names, DOM/UI details, and incidental protocol fields are not product-wide contracts. Prefer runtime discovery, capability detection, stable public contracts, and platform adapters; isolate unavoidable observations at the boundary.
- **Fail closed** — ambiguous ownership, replaced paths, unknown process identity, unsupported platform behavior, or unsafe destructive state must stop with a diagnostic rather than guess.
- **Easy in, easy out** — install, refresh, and uninstall are symmetric ownership lifecycles. Product-created state is attributable to the `PluraDesktop` namespace and removable without touching the official app or unrelated user data.
- **Consistent naming** — public product/repository terminology uses `Plura Desktop`; ChatGPT/Codex remain integration terms. Steady-state product/CLI/source/metadata identifiers use only the canonical Plura names.
- **Multi-OS by default** — shared lifecycle/domain logic is portable across macOS, Windows, and Linux; OS integration stays in adapters and unverified real-app behavior is never inferred from CI alone.
- **Quiet when idle** — long-lived work is event-driven and lifecycle-bound. Avoid polling, periodic wakeups, unbounded logging, and unnecessary disk I/O/CPU/battery cost.
- **Diagnostics are evidence, not telemetry exhaust** — diagnostics are on-demand or failure-triggered, privacy-safe, structured, bounded, and separated by product/profile/incident so another agent can diagnose without scraping arbitrary user state.

## Managed layout

Profile indexes are generic (`2..99`); there is no Account 1/2 branching.

| Platform | Selector | Codex home | Desktop user data | Metadata |
| --- | --- | --- | --- | --- |
| macOS | `~/Applications/ChatGPT Profile N.app` | `~/.codex-profileN` | `~/Library/Application Support/Codex-ProfileN` | `~/Library/Application Support/PluraDesktop` |
| Windows | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\ChatGPT Profile N.cmd` | `%USERPROFILE%\.codex-profileN` | `%LOCALAPPDATA%\Codex-ProfileN` | `%LOCALAPPDATA%\PluraDesktop` |
| Linux | `$XDG_DATA_HOME/applications/chatgpt-profile-N.desktop` | `~/.codex-profileN` | `$XDG_CONFIG_HOME/Codex-ProfileN` | `$XDG_STATE_HOME/PluraDesktop` |

Linux follows the usual XDG fallbacks when variables are unset (`~/.local/share`, `~/.config`, `~/.local/state`).

The manifest is schema 5 and records the platform, exact path role/kind, stable local identity, executable used at install time, and bounded diagnostic settings. Destructive operations revalidate the manifest and current path identities immediately before deletion.

Within the product-owned metadata root, persistent and derived data have explicit roles:

| Entry | Role | Lifetime |
| --- | --- | --- |
| `profile-N-install-manifest.json` | ownership/provenance state | until Profile N uninstall |
| `control-runtime/` + `plura-desktop` | installed control code | regenerated by refresh; removed after the last managed profile |
| `runtime-sessions/` | canonical live-session claims/descriptors | runtime-only; stale entries are never treated as authority |
| `runtime-apps/profile-N/` | macOS derived signed runtime clone | regenerated/removed with Profile N lifecycle |
| `profile-N-runtime/` | Windows/Linux derived launcher runtime | manifest-owned and removed with Profile N |
| `diagnostics/profile-N/incidents/` | optional sanitized diagnostic evidence | explicit opt-in, 30 days / 32 MiB shared diagnostic budget, max 256 KiB each; removed with Profile N |
| `distribution/cli-runtime/` + `bin/` | guided macOS install's standalone CLI runtime/stable launchers | installer-owned; removed by `Uninstall Plura Desktop.command` after managed profiles |

There is currently no Plura Desktop background cache, telemetry store, or rolling product log. Managed `CODEX_HOME` and Desktop user-data remain integration/profile payloads outside this product-metadata layout and are not renamed merely for branding.

## Platform support and verification

- **macOS Apple Silicon:** previous releases completed signed-in official-app E2E. The new app-owned launch has passed an isolated real-app startup/normal-quit probe; signed-in Native/browser acceptance is still pending. The official `/Applications/ChatGPT.app` remains read-only and is the sole application-update authority. Managed profiles launch from a byte-identical, still-validly-signed APFS copy-on-write runtime clone under the launcher metadata directory; the clone is disposable derived code, not profile state, and is reconciled to the official app on the next cold launch after an official update or managed-clone mutation.
- **macOS Intel:** the standalone runtime is built and smoke-tested on a native Intel GitHub runner, but the current official ChatGPT app has not completed this repository's full Intel real-app verification flow yet.
- **Windows:** lifecycle, path layout, selector generation, sanitized environment, and process-ownership adapter are covered by CI. A real official-app launch has not yet been verified on a Windows machine in this repository.
- **Linux:** shared lifecycle and isolated launch construction are covered by fixtures. The Xvfb workflow has been updated for app-owned execution; its current real-package run and the full signed-in lifecycle remain release gates.

Windows still requires real-machine official-app evidence before its platform-specific launch assumptions are treated as proven. Linux now has official-package launch smoke evidence in addition to the shared CI contract, but remains short of the full signed-in real-user verification flow. The common core does not add fallback behavior when an executable or process contract is unknown.

The exact real-app release gate is documented in [docs/PLATFORM_VERIFICATION.md](docs/PLATFORM_VERIFICATION.md).

## Requirements

- The official ChatGPT desktop application for the current OS
- On macOS, the current official ChatGPT app requires macOS 14 or newer and supports both Apple Silicon and Intel; the Homebrew formula mirrors that minimum.
- Python 3.10+ for the wheel/source-package CLI path; the guided macOS DMG uses its bundled standalone runtime.
- Git only when installing/updating directly from a source checkout.
- macOS uses the canonical `/Applications/ChatGPT.app/Contents/MacOS/ChatGPT` executable by default.
- Windows/Linux use a discovered `ChatGPT` executable when available; otherwise pass `--app PATH` or set `CHATGPT_EXECUTABLE`.

## CLI reference

The packaged install exposes the canonical `plura-desktop` command on every supported OS:

```sh
plura-desktop --version
plura-desktop --help
plura-desktop status --profile 2
```

### Install and launch

```sh
plura-desktop install --profile 2
plura-desktop launch --profile 2
```

On Windows/Linux, provide the executable explicitly if discovery is unavailable:

```sh
plura-desktop install --profile 2 --app /path/to/ChatGPT
```

The first managed launch starts with empty isolated state. Do not copy authentication files, cookies, profile databases, or conversations between different profiles.

For a first managed profile, the intended lifecycle is: run `status --profile 2` (expect `not-installed`), run `install --profile 2`, run `launch --profile 2`, sign in normally in the new ChatGPT Profile 2 window, quit it normally once, then use `status`, diagnostics as needed, `refresh` after package updates, and `uninstall` first as a dry run before applying `--yes`. No step requires copying an existing ChatGPT/Codex profile database.

Managed profiles stay open when their launcher shell disconnects. Quit the official Desktop normally; it owns and closes its Native engine.

### Target contract v2

```sh
plura-desktop targets --json
plura-desktop launch-target --target local.plura-desktop.profile2 --json
plura-desktop target-session --target local.plura-desktop.profile2 --json
plura-desktop quit-target --target local.plura-desktop.profile2 --expected-desktop-pid PID --json
```

`targets` returns `contractVersion: 2`, platform and targets. Each target includes its ID,
display name, managed/default role, `backendPolicy: "official-desktop-owned"` and observed
`state` (`running`, `stopped`, `invalid` or `not-installed`). Private profile paths and
credentials are not part of the contract.

`launch-target` reuses a running app or returns `launch-requested` after the OS accepts the
launch request. This does not claim account sign-in, Native tools or Web models are ready.
`target-session` reports the current app state and, where the platform can establish it,
`desktopProcessID`. On macOS `quit-target` requests normal quit for that exact app; unsupported
platforms report the limitation rather than killing it. It never stops a separate engine.

**Breaking change from v1:** no loopback app-server endpoint, engine override, Responses
routing, model-list callback, startup-catalog overlay or renderer CDP is supplied. Removed
flags fail explicitly. Consumers must adopt v2 before this release is installed. Configure
optional model providers through the official app's supported settings; Plura does not rewrite
provider settings or account data. Quit profiles launched by an older Plura normally before
the first launch with this version. Refresh selectors after updating Plura.

### Refresh

After updating the Plura Desktop package, refresh the installed selector/runtime:

```sh
plura-desktop refresh --profile 2
```

Refresh preserves the profile state directories and their recorded identities.

## Diagnostics

The profile-scoped structural Rust trace remains opt-in and adds no background work:

Permission diagnostics also record bounded, permission-only RPC classifications after the next normal launch. They work without a Tela route and never change selected permissions or record prompts, paths, credentials, request IDs or custom profile names. Thread identifiers are hashed; at most 64 events per connection use the existing private incident retention.

```sh
plura-desktop diagnostics-on --profile 2
plura-desktop diagnostics-status --profile 2
plura-desktop diagnostics-off --profile 2
```

The trace setting is stored in the profile manifest and takes effect on the next normal launch. It never accepts arbitrary log filters from the manifest.

`diagnostics-status` also summarizes the latest retained permission resume: whether the request explicitly supplied permissions, whether forwarding changed their classifications, and what Native reported. This is diagnostic evidence, not a test of effective execution permissions. Missing or truncated evidence is labeled; identifiers and custom permission names are never displayed.

The packaged `plura-desktop-diagnose` command is **explicitly macOS-specific** because it consumes the currently documented/observed macOS Desktop log boundary. It is on-demand and read-only. It may query only the allowlisted structural targets in `CODEX_HOME/logs_2.sqlite`; it never opens authentication/profile/state databases or emits prompts, results, private MCP names, raw conversation/thread IDs, or private paths.

By default the deeper diagnostic writes nothing and prints only a sanitized summary:

```sh
plura-desktop-diagnose --profile 2 --minutes 60 --json
```

When persistent evidence is explicitly useful, `--write-artifact` stores the same sanitized summary under the product-owned `diagnostics/profile-N/incidents/` namespace. Persistent artifacts are available only for managed profiles (`2..99`), are capped at **256 KiB per incident**, share a **32 MiB / 30-day budget across profile and runtime diagnostics**, and are removed with that profile's uninstall. Existing incident directories that are replaced, symlinked, or structurally unexpected cause the write/retention operation to fail closed.

```sh
plura-desktop-diagnose --profile 2 --minutes 60 --json --write-artifact
```

## Uninstall

Quit the selected managed profile first. Preview:

```sh
plura-desktop uninstall --profile 2
```

Apply only after reviewing the exact list:

```sh
plura-desktop uninstall --profile 2 --yes
```

The uninstaller never terminates profile processes. It removes only exact manifest-owned profile paths plus fixed product-generated metadata for that profile (for example its derived runtime and bounded diagnostic incidents) after ownership/safety checks. Replaced paths, symlinks/junctions, protected default-profile paths, mounted subtrees, and unexpected diagnostic structures fail closed.

## Security/isolation boundary

The project isolates the managed `CODEX_HOME` and desktop user-data directory. It is not an OS sandbox and does not claim to isolate system credential stores, permissions, URL handlers, caches, helper processes, or account-side state.

The official application/executable is read-only input and is never patched, re-signed, renamed, replaced, or uninstalled. On macOS only, the launcher creates a byte-identical signed APFS copy-on-write runtime clone for each managed profile so the managed instance has a distinct application URL. The clone is disposable derived runtime state; the official app remains the sole default/Spotlight application.

## Development

For source development, either install the checkout into an isolated environment (`python3 -m pip install -e .`) or use the thin repository wrappers:

```sh
./scripts/plura-desktop.sh --version
./scripts/plura-desktop.sh status --profile 2
python3 scripts/diagnose_profile_session.py --help
```

Run the validation suite locally:

```sh
python3 -m unittest discover -s tests -v
for script in scripts/*.sh; do sh -n "$script"; done
python3 -m compileall -q src scripts tests
python3 scripts/check_repository_invariants.py
```

GitHub Actions runs the Python safety/contract suite on macOS, Windows, and Linux. Platform-specific behavior that needs a real official desktop app must still be verified on real hardware/OS and documented as such.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
