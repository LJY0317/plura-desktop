# Verifying a Plura Desktop release

Plura Desktop publishes enough free provenance data for users to verify GitHub-hosted release bytes without trusting a copied checksum from a third party.

This does **not** turn an unsigned macOS DMG into an Apple-notarized application. Apple Developer ID signing/notarization is a separate trust layer. These checks establish GitHub release integrity, repository/workflow provenance, and byte-for-byte checksum consistency.

## One-command verification from a source checkout

For current immutable releases, a source checkout can run the full procedure below with one helper command:

```sh
python3 scripts/verify-release.py vVERSION
```

The helper requires the GitHub CLI (`gh`). It verifies that the release is repository-native immutable, runs GitHub's native release verification, downloads the complete asset set into a temporary directory, recomputes every `SHA256SUMS` entry, validates `RELEASE-METADATA.json` against the tag's resolved source commit, and verifies the GitHub Actions attestation for every checksummed asset. The temporary download is removed afterward.

The individual steps below remain documented so the helper itself is not a separate trust requirement and each layer can be checked manually.

## 1. Verify the immutable GitHub Release

Current public releases, beginning with the fresh repository line at `v0.1.13`, are repository-native immutable releases. GitHub locks the published assets and associated tag after publication.

With GitHub CLI:

```sh
gh release verify vVERSION --repo LJY0317/plura-desktop
```

A successful result verifies GitHub's release attestation, including the release tag, commit, and published asset digests.

The pre-reset `v0.1.0` through `v0.1.12` names are retired historical identifiers and are not reused. GitHub immutable-release tag tombstones prevent reusing names that were previously published, even after the former release/repository is removed.

## 2. Download the exact release assets

```sh
mkdir plura-release
gh release download vVERSION --repo LJY0317/plura-desktop --dir plura-release
cd plura-release
```

The release includes `SHA256SUMS`, `RELEASE-METADATA.json`, Python distributions, architecture-specific macOS standalone runtimes, the release-bound installer script, and the DMG trust level produced by that release.

## 3. Verify every downloaded SHA-256

On macOS:

```sh
shasum -a 256 -c SHA256SUMS
```

On Linux with GNU coreutils:

```sh
sha256sum -c SHA256SUMS
```

Every listed asset should report `OK`.

## 4. Verify one asset against GitHub's immutable release attestation

For example, the Python wheel:

```sh
gh release verify-asset \
  vVERSION \
  plura_desktop-VERSION-py3-none-any.whl \
  --repo LJY0317/plura-desktop
```

Or the current unsigned macOS prerelease DMG:

```sh
gh release verify-asset \
  vVERSION \
  Plura-Desktop-VERSION-macOS-unsigned.dmg \
  --repo LJY0317/plura-desktop
```

Use the actual filename shown in that release. A future notarized normal-user DMG intentionally has no `-unsigned`/`-signed` suffix.

## 5. Verify the GitHub Actions build attestation

Plura Desktop also creates explicit GitHub Actions artifact attestations during the release workflow:

```sh
gh attestation verify \
  plura_desktop-VERSION-py3-none-any.whl \
  --repo LJY0317/plura-desktop
```

The immutable-release attestation and the workflow artifact attestation are complementary: one binds the final GitHub Release, while the other records the GitHub Actions provenance that vouched for the built bytes.

## 6. Check release source metadata

Inspect `RELEASE-METADATA.json`:

```sh
cat RELEASE-METADATA.json
```

For current immutable releases, `sourceTag`, `sourceCommit`, and `workflowCommit` should identify the tag/commit that was actually checked out and released. `workflowRunID` identifies the GitHub Actions run that built the asset set.

If any checksum, release verification, asset verification, or attestation check fails, do not install that copy. Re-download the asset from the official GitHub Release rather than bypassing verification.
