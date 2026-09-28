# Real-app platform verification

This document is the release gate for behavior that fake-executable CI cannot prove. A platform is not described as real-app verified until the current official ChatGPT Desktop build has completed this flow on that operating system.

Record the OS/version, ChatGPT Desktop version and installation source, Plura Desktop commit, executable path used, and the final pass/fail result. Do not attach credentials, cookies, profile databases, conversation content, or unredacted private paths to verification evidence.

## Verification flow

Use a disposable managed Profile 2. Do not copy an existing profile into it.

1. Use the release surface that will actually be advertised for that platform. On macOS, prefer the candidate **notarized DMG** and double-click `Install Plura Desktop.app`; confirm no separate Python setup is requested, `ChatGPT Profile 2.app` is created, and the guided install record reports standalone mode. For the CLI/package fallback (and Windows/Linux until a native distribution is defined), install the candidate wheel/source checkout in an isolated Python environment. Record `plura-desktop --version`, then confirm `status --profile 2` is `not-installed` before creating the disposable profile when using the CLI path.
2. Run `plura-desktop install --profile 2` using normal executable discovery. If discovery is unavailable on Windows/Linux, repeat with the documented `--app PATH` override and record that discovery remains unverified.
3. Run `plura-desktop launch --profile 2`. Confirm the official ChatGPT Desktop executable starts with an isolated managed profile and the default profile remains independently usable.
4. Sign in normally inside Profile 2, quit it normally, relaunch it, and confirm that the managed login/profile state persists without copying authentication files.
5. Run `plura-desktop targets --json` and confirm one `local.plura-desktop.profile2` managed target, no private profile paths in the public contract, and lifecycle state consistent with the running/stopped app.
6. Quit Profile 2 normally. On macOS guided installs, exercise `Update Plura Desktop.command` with a candidate update/reinstall when practical; otherwise run `plura-desktop refresh --profile 2`. Relaunch and confirm login/profile state remains intact.
7. Run `plura-desktop diagnostics-status --profile 2`. macOS should report native structural diagnostics support; Windows/Linux should clearly report that the macOS-only structural trace is unsupported rather than guessing an equivalent log boundary.
8. Run `plura-desktop uninstall --profile 2` without `--yes` and review the exact dry-run targets. Confirm the official ChatGPT installation and default profile are not listed.
9. On a CLI/package verification, apply `plura-desktop uninstall --profile 2 --yes` only on the disposable profile. On a macOS guided-install verification, use `Uninstall Plura Desktop.command`, review the same profile dry run, enter the explicit `REMOVE` confirmation, and confirm both managed profile state and installer-owned distribution/runtime/tools are removed. In either case the official ChatGPT installation and default profile must remain intact.
10. For a macOS consumer release, additionally verify the DMG/app trust surface appropriate to the advertised artifact: Developer ID signature, notarization acceptance, and stapled ticket for the un-suffixed normal-user DMG. Record Apple Silicon and Intel results separately; one architecture does not prove the other standalone runtime.
11. Run the repository test/compile/invariant checks and record the commit that passed both automated and real-app verification.

## Support declaration

CI coverage may establish shared lifecycle, path-layout, selector-generation, environment, and safety contracts. It does not establish that the current official ChatGPT Desktop build accepts a platform's discovery/launch/user-data behavior. README support wording must keep that distinction until the real-app flow above passes for the current platform contract.
