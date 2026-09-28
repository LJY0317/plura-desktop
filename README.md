# Plura Desktop

**A multi-profile runtime for ChatGPT Desktop.**

**Plura Desktop** is an independent runtime for running and managing multiple isolated profiles of the official ChatGPT Desktop application. It does so without patching, re-signing, or modifying the official application. On macOS, managed profiles use a byte-identical signed APFS clone as a derived runtime so LaunchServices can distinguish them from the untouched default app.

The project now has a shared profile/domain lifecycle with platform adapters for **macOS, Windows, and Linux**. The official/default ChatGPT profile remains outside this project's ownership. Managed profiles start at Profile 2 and isolate `CODEX_HOME` plus the desktop user-data directory.

`Plura Desktop` is the product name. ChatGPT and Codex describe the upstream products it integrates with rather than this project's brand identity.

> [!IMPORTANT]
> This is an unofficial community project. It is not affiliated with, endorsed by, or supported by OpenAI. ChatGPT and Codex are trademarks of OpenAI.

[한국어 문서](docs/README.ko.md)

## Quick start

**macOS on Apple Silicon is the real-app verified platform today.** The Intel standalone runtime is built and smoke-tested on GitHub's Intel macOS runner, but an Intel official-app E2E is still a separate verification gate. Windows/Linux share the same core and CI coverage but still require their real-app verification gates described below. OpenAI currently distributes a [Linux desktop preview](https://learn.chatgpt.com/docs/linux/linux-app) and documents launching it as `chatgpt`; Plura Desktop already discovers that command from `PATH`, but this does not substitute for the repository's full Linux real-app verification flow.

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

### Update

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

- **A standalone release runtime is missing or fails checksum verification** — the release asset set is incomplete or damaged. Do not bypass the check; use an intact release or the wheel/CLI fallback.
- **Only `-unsigned.dmg` or `-signed.dmg` exists, or there is no GitHub Release yet** — use the source/package path above or wait for a notarized un-suffixed DMG. Do not bypass macOS security prompts as the normal install path.
- **`plura-desktop: command not found`** — run it from the isolated environment/package tool where you installed Plura Desktop, or fix that tool's normal PATH setup. In an activated venv, `python -m plura_desktop --version` is an equivalent fallback. Do not copy the console script by itself.
- **`ChatGPT executable not found` on Windows/Linux** — pass the real executable with `--app PATH` or set `CHATGPT_EXECUTABLE`. Plura Desktop does not guess unknown install layouts.
- **`restart-required`** — the target is already running outside the requested canonical capability/session. Quit that ChatGPT window normally once, then launch it again through Plura Desktop.
- **Uninstall says the profile is running** — quit the managed ChatGPT profile normally and rerun the dry run. The uninstaller never kills it automatically.
- **A path/identity/symlink safety error** — inspect the reported path instead of deleting it manually through Plura Desktop. Ambiguous destructive state intentionally fails closed.

## Architecture and ownership

One invariant controls the lifecycle:

> A logical managed profile has one authoritative runtime: one lifecycle-bound supervisor owns one official Desktop process and one loopback Codex app-server. Every managed launch surface converges on that runtime, so remote clients can attach later without asking the user to choose a separate "shared mode".

The shared core owns profile identity, manifests, allowlisted paths, install/refresh/uninstall state, diagnostics settings, and the public target contract. Platform adapters own executable discovery, selector integration, process inspection, filesystem identity, environment construction, and launch/error behavior.

This project is the independent source of truth for managed profile/runtime lifecycle. Downstream clients such as menu-bar controllers or mobile remote clients consume its public contracts; this repository must not depend on those clients or contain product-specific mobile/bridge policy.

There is no watcher, idle polling daemon, login item, updater, or background sampler. A lightweight supervisor exists only for the lifetime of an actively launched managed profile and exits with its Desktop/app-server pair.

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
| `diagnostics/profile-N/incidents/` | optional sanitized diagnostic evidence | explicit opt-in, max 10 incidents, max 256 KiB each; removed with Profile N |
| `distribution/cli-runtime/` + `bin/` | guided macOS install's standalone CLI runtime/stable launchers | installer-owned; removed by `Uninstall Plura Desktop.command` after managed profiles |

There is currently no Plura Desktop background cache, telemetry store, or rolling product log. Managed `CODEX_HOME` and Desktop user-data remain integration/profile payloads outside this product-metadata layout and are not renamed merely for branding.

## Platform support and verification

- **macOS Apple Silicon:** official-app E2E verified. The official `/Applications/ChatGPT.app` remains read-only. Managed profiles launch from a byte-identical, still-validly-signed APFS copy-on-write runtime clone under the launcher metadata directory; the clone is derived code, not profile state, and is refreshed when the official app changes.
- **macOS Intel:** the standalone runtime is built and smoke-tested on a native Intel GitHub runner, but the current official ChatGPT app has not completed this repository's full Intel real-app verification flow yet.
- **Windows:** lifecycle, path layout, selector generation, sanitized environment, and process-ownership adapter are covered by CI. A real official-app launch has not yet been verified on a Windows machine in this repository.
- **Linux:** lifecycle, XDG layout, selector generation, sanitized environment, documented `chatgpt` command discovery, and `/proc` process detection are covered by CI. OpenAI's official Linux desktop app is currently available in preview, but a full real-app launch/login/profile-isolation/refresh/uninstall flow has not yet been verified on a Linux machine in this repository.

Windows/Linux therefore require evidence from a real machine before platform-specific launch assumptions should be treated as proven. The common core does not add fallback behavior when an executable or process contract is unknown.

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

### Stable target contract

External tools should use the installed control CLI and versioned target contract instead of reconstructing selector paths, `CODEX_HOME`, ports, or profile numbering rules. On macOS/Linux the control CLI lives in the platform metadata directory as `plura-desktop`; Windows installs `plura-desktop.cmd` there.

```sh
plura-desktop targets --json
```

Contract version 1 exposes target identity, display name, lifecycle state, ownership, backend policy, and canonical session state. It intentionally does **not** expose private profile paths.

The same target contract also exposes renderer capability independently from app-server readiness. `rendererCDPSupported` describes whether a target can be launched with the loopback renderer contract, while `rendererCDPState` is `available`, `ready`, `restart-required`, `unsupported`, or `unavailable`. This prevents companion clients from assuming that a `ready` app-server session is automatically renderer-capable: renderer CDP is a launch-time capability, so an already-running canonical session without it must be quit normally once before a renderer-enabled relaunch.

Controllers should launch a discovered target by ID rather than reconstructing a selector or profile index:

```sh
plura-desktop launch-target --target local.plura-desktop.profile2
```

`launch-target` is the canonical managed-profile launch path. The installed selector (`ChatGPT Profile N`), the menu-bar integration, and future controllers should all use it. Starting a stopped target creates one lifecycle-bound app-server + Desktop pair; calling it again reuses the live canonical session. If an older/private Desktop instance is already running outside the canonical runtime, the command fails closed and asks for one normal quit rather than creating a second writer.

Cold Desktop launches can take materially longer when renderer CDP is requested. The canonical caller therefore waits up to 30 seconds for the single owned supervisor to publish its ready session; it does not start a second runtime when startup is merely slow.

Controllers that need a temporary local Responses provider can request one at launch without learning or rewriting the target's private `CODEX_HOME`:

```sh
CHATGPT_TELA_RUNTIME_TOKEN=... plura-desktop launch-target \
  --target local.plura-desktop.profile2 \
  --responses-base-url http://127.0.0.1:18741/v1 \
  --responses-env-key CHATGPT_TELA_RUNTIME_TOKEN \
  --json
```

The Responses route is launch-time only. The URL must be loopback HTTP with a `/v1` path, and the credential value is inherited from the named environment variable rather than written to argv, manifests, or the target contract. Credentials must be high-entropy values of at least 32 characters. The public route fingerprint binds the loopback URL, env-key name, provider identity, and a one-way hash of that credential; it never exposes the credential itself. A live target can therefore be reused only when endpoint, key, and credential all still match. Changing any of them requires one normal quit. Ordinary launches that do not request a route remain unchanged.

When a Responses route is active, the supervisor keeps the Codex app-server on a private loopback endpoint and publishes a second loopback WebSocket endpoint for Desktop/controllers. ChatGPT Desktop may reload user config after attaching to the app-server; that reload must not silently drop the launch-time provider. The published endpoint therefore overlays the same **secret-free** provider config only on `thread/start`, `thread/resume`, and `thread/fork`, the lifecycle requests that accept thread config. All other app-server traffic is forwarded unchanged. The user `config.toml` is never rewritten, the official ChatGPT application is never modified, and the credential value stays only in the owned backend process environment.

With `--json`, `launch-target` returns the same public ready-session shape as `target-session --json`. Controllers can therefore obtain the canonical loopback app-server endpoint and route fingerprint without reading launcher metadata or private profile paths.

Controllers that need to inspect the official Desktop renderer can opt in to a Chromium DevTools endpoint at launch:

```sh
plura-desktop launch-target \
  --target local.plura-desktop.profile2 \
  --renderer-cdp \
  --json
```

The generic `launch-target` contract keeps this capability **off by default** unless `--renderer-cdp` is requested. On macOS, the installed `ChatGPT Profile N` selector requests it explicitly so local read-only companion clients can attach to the renderer without depending on Accessibility UI timing. The canonical supervisor allocates an ephemeral loopback port and starts Desktop with Chromium remote debugging bound to `127.0.0.1` only; the allowed WebSocket origin is the exact loopback endpoint rather than `*`. The ready-session contract then adds `rendererCDPEndpoint` (for example `http://127.0.0.1:49152`). No profile path, credential, cookie, or debugging token is added to the public contract. Requesting renderer CDP for an already-running canonical session that was launched without it fails closed and requires one normal Desktop quit before relaunch.

Renderer state is deliberately separate from the target's main session state. A client that only needs Work/Codex can continue using a rendererless `ready` session; a client that needs the Desktop semantic renderer can inspect `rendererCDPState` and explain the one-time normal relaunch instead of silently falling back to a slower UI automation path.

Integrations that need to attach to the live runtime can request the current loopback endpoint explicitly:

```sh
plura-desktop target-session --target TARGET_ID --json
```

The endpoint is dynamic and local-only. Plura Desktop owns its lifetime; remote/bridge projects attach to it and never create their own fallback app-server.

When a launch-time Responses route is active, `target-session --json` adds only `responsesRouteFingerprint`. When renderer CDP was explicitly enabled at launch, it adds only the loopback `rendererCDPEndpoint`. It does not expose the route URL, environment-variable name/value, `CODEX_HOME`, Desktop user-data path, authentication state, or profile data paths. If a Desktop target is already running outside the canonical runtime, `state` remains `restart-required`, but platforms that can prove the exact top-level Desktop process may also expose `desktopProcessID` so a controller can foreground that existing app without claiming runtime ownership.

### Refresh

After updating the Plura Desktop package, refresh the installed selector/runtime:

```sh
plura-desktop refresh --profile 2
```

Refresh preserves the profile state directories and their recorded identities.

## Diagnostics

The profile-scoped structural Rust trace remains opt-in and adds no background work:

```sh
plura-desktop diagnostics-on --profile 2
plura-desktop diagnostics-status --profile 2
plura-desktop diagnostics-off --profile 2
```

The trace setting is stored in the profile manifest and takes effect on the next normal launch. It never accepts arbitrary log filters from the manifest.

The packaged `plura-desktop-diagnose` command is **explicitly macOS-specific** because it consumes the currently documented/observed macOS Desktop log boundary. It is on-demand and read-only. It may query only the allowlisted structural targets in `CODEX_HOME/logs_2.sqlite`; it never opens authentication/profile/state databases or emits prompts, results, private MCP names, raw conversation/thread IDs, or private paths.

By default the deeper diagnostic writes nothing and prints only a sanitized summary:

```sh
plura-desktop-diagnose --profile 2 --minutes 60 --json
```

When persistent evidence is explicitly useful, `--write-artifact` stores the same sanitized summary under the product-owned `diagnostics/profile-N/incidents/` namespace. Persistent artifacts are available only for managed profiles (`2..99`), are capped at **256 KiB per incident**, retain at most **10 incidents per profile**, and are removed with that profile's uninstall. Existing incident directories that are replaced, symlinked, or structurally unexpected cause the write/retention operation to fail closed.

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
