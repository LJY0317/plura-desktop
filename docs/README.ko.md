# Plura Desktop

**ChatGPT Desktop용 멀티프로필 런타임입니다.**

공식 ChatGPT 애플리케이션을 패치·재서명·수정하지 않고 서로 격리된 여러 ChatGPT/Codex 데스크톱 프로필을 실행하고 관리합니다. macOS의 managed profile은 LaunchServices에서 default 앱과 구분되도록 원본과 바이트가 동일하고 기존 서명이 그대로 유효한 APFS copy-on-write runtime clone을 사용합니다.

현재 공통 profile/domain lifecycle과 **macOS·Windows·Linux** 플랫폼 adapter로 구성됩니다. 공식/default ChatGPT 프로필은 이 프로젝트가 소유하지 않으며, managed profile은 Profile 2부터 시작합니다. 각 managed profile은 `CODEX_HOME`과 데스크톱 user-data를 분리합니다.

서로 다른 계정으로 순차 로그인 전환해 **같은 로컬 대화를 이어가는 경우**와, 프로필 1/A·프로필 2/B가 동시에 **같은 원본 대화를 공유하는 아직 미구현 기능**은 구분해야 합니다. 실제 사용 관찰, Work/Codex UI 유사성의 검증 한계, 모바일 Remote 및 안전한 공유 설계는 [프로필 연속성·공유 기준 기록](PROFILE_CONTINUITY_AND_THREAD_SHARING.ko.md)에 정리했습니다. 읽기 전용 로컬 저장소 분리 진단은 `scripts/diagnose_local_conversation_isolation.py`를 실행하면 됩니다.

> [!IMPORTANT]
> OpenAI가 제작·보증·지원하는 제품이 아닌 비공식 커뮤니티 프로젝트입니다. ChatGPT와 Codex는 OpenAI의 상표입니다.

## 빠른 시작

이전 릴리스의 Apple Silicon macOS 로그인 포함 E2E와 새 실행 경로의 검증은 구분합니다. 새 경로는 격리된 실제 macOS 앱에서 공식 앱 소유 Native 엔진 생성·정상 종료까지 확인했습니다. 로그인 상태의 Native/browser 도구 실수락, Intel·Windows·Linux 공식 앱 E2E는 별도 배포 게이트입니다. Linux Xvfb CI도 새 경로로 갱신했으며 실행 결과를 확인해야 합니다.

GitHub Releases에 suffix 없는 **`Plura-Desktop-VERSION-macOS.dmg`**가 실제로 있을 때 그 notarized/stapled DMG가 macOS 권장 설치 경로입니다. 그 전까지는 **Homebrew가 가장 간단한 지원 macOS 설치 경로**이며 별도 Python 환경이 필요하지 않습니다. source/package 경로도 portable fallback으로 유지합니다. `-unsigned.dmg`나 `-signed.dmg`를 일반 사용자용 installer로 취급하지 않습니다.

### macOS 간편 설치 — notarized DMG (제공될 때)

GitHub Releases의 **`Plura-Desktop-VERSION-macOS.dmg`**를 내려받아 열고 **Install Plura Desktop.app**을 더블클릭합니다. suffix가 없는 DMG 이름은 Developer ID 서명 + notarization + stapling까지 성공한 artifact에만 사용합니다.

installer는 현재 Mac에 맞는 `arm64`/`x86_64` standalone Plura runtime을 자동 선택하고 exact release checksum을 검증한 뒤, 공식 `/Applications/ChatGPT.app`이 있으면 **ChatGPT Profile 2**를 자동 생성합니다. 공식 ChatGPT 앱/default profile은 수정하지 않습니다. 또한 `~/Applications/Plura Desktop Tools`에 Update/Uninstall 도구를 만듭니다.

설치 후 `~/Applications/ChatGPT Profile 2.app`을 열어 평소처럼 로그인합니다. 기존 profile의 cookie, 인증 파일, DB, 대화를 복사하지 않습니다.

`-unsigned.dmg` 또는 `-signed.dmg` suffix가 붙은 artifact는 CI/개발용 trust level이며 일반 사용자 권장 다운로드가 아닙니다. 자세한 release 규칙은 [RELEASING.md](RELEASING.md)를 참고합니다.

### Homebrew 설치 (macOS, 별도 Python 불필요)

현재 공식 ChatGPT macOS 앱의 최소 요구사항은 **macOS 14 이상**이며, Homebrew formula도 같은 최소 버전을 강제합니다.

공개 tap에서 standalone Plura runtime을 설치합니다.

```sh
brew install LJY0317/plura/plura-desktop
```

그 다음 첫 managed profile을 만듭니다.

```sh
plura-desktop --version
plura-desktop status --profile 2
plura-desktop install --profile 2
open "$HOME/Applications/ChatGPT Profile 2.app"
```

formula는 immutable Plura Desktop GitHub Release의 현재 Mac 아키텍처용 standalone runtime을 받고 Homebrew가 SHA-256을 검증합니다. 공개 tap은 최신 immutable release를 검증된 release metadata/attestation으로 렌더링한 뒤 Apple Silicon과 Intel macOS runner에서 모두 install/test를 통과한 경우에만 갱신됩니다. Plura는 selector/control helper에 versioned Cellar 경로가 아니라 Homebrew의 안정된 `opt` runtime 경로를 기록하므로 formula upgrade 후 selector가 옛 keg 경로에 묶이지 않습니다.

이 무료 Homebrew 경로는 **Apple Developer ID 서명/notarization을 대신하지 않습니다.** suffix 없는 notarized DMG가 준비되기 전까지 사용할 수 있는 지원 CLI/runtime 배포 경로입니다.

### Source/package 설치 (Python 3.10+)

이 경로는 공개 저장소에서 바로 사용할 수 있으며 notarized macOS DMG가 아직 없을 때의 지원 fallback입니다. **Python 3.10+**가 필요합니다. macOS/Linux에서 fresh clone으로 설치하려면:

```sh
git clone https://github.com/LJY0317/plura-desktop.git
cd plura-desktop
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

Windows PowerShell에서는:

```powershell
git clone https://github.com/LJY0317/plura-desktop.git
cd plura-desktop
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .
```

release wheel이 있다면 `pipx`로 격리 설치할 수도 있습니다.

```sh
pipx install ./plura_desktop-VERSION-py3-none-any.whl
```

release 파일에는 GitHub artifact attestation도 붙습니다. GitHub CLI가 있다면 내려받은 wheel 등 release asset이 실제로 이 공개 저장소의 GitHub Actions에서 생성된 정확한 바이트인지 확인할 수 있습니다.

```sh
gh attestation verify plura_desktop-VERSION-py3-none-any.whl --repo LJY0317/plura-desktop
```

이 provenance 검증은 무료이며 Apple Developer 서명과 독립적입니다. 따라서 unsigned macOS DMG를 notarized 일반 사용자용 DMG와 동등하게 만드는 것은 아닙니다.

현재 공개 release는 새 저장소 lineage의 `v0.1.13`부터 GitHub repository-native **immutable release**를 사용합니다. publish 이후 GitHub가 release asset과 연결 tag를 잠급니다. 각 release의 `RELEASE-METADATA.json`에는 그 고정 asset set을 만든 source tag/commit과 workflow run이 기록됩니다. SHA-256 + immutable-release + workflow attestation 전체 검증 절차는 [VERIFY_RELEASE.md](VERIFY_RELEASE.md)를 참고합니다.

source checkout에서는 현재 immutable release를 `python3 scripts/verify-release.py vVERSION` 한 줄로도 전체 검증할 수 있습니다. 이 helper는 GitHub native release verification, 전체 SHA-256, metadata source binding, checksummed asset의 GitHub Actions attestation을 함께 확인하며, 개별 수동 검증 절차는 위 문서에 독립적으로 계속 남겨둡니다.

설치된 CLI를 확인합니다.

```sh
plura-desktop --version
```

managed profile이 존재하는 동안에는 이 격리 Python 환경을 유지합니다. Profile selector/control helper는 마지막 `install`/`refresh`에 사용한 interpreter의 절대 경로를 의도적으로 기록합니다. CLI package/environment를 지우기 전에 managed profile부터 제거합니다. 이 Python 환경을 유지하고 싶지 않은 macOS 사용자는 위 Homebrew standalone 경로를 사용할 수 있습니다.

첫 managed profile을 만들고 실행합니다.

```sh
plura-desktop status --profile 2
plura-desktop install --profile 2
plura-desktop launch --profile 2
```

새 **ChatGPT Profile 2** 창에서 평소처럼 정상 로그인합니다. 기존 profile의 cookie, 인증 파일, DB, 대화를 복사하지 않습니다.

macOS에서는 `~/Applications/ChatGPT Profile 2.app`이 생성되며 이후 일반 앱처럼 실행할 수 있습니다. 공식 `/Applications/ChatGPT.app`은 그대로 유지됩니다.

### ChatGPT 자체 업데이트

macOS에서는 공식 ChatGPT 앱(프로필 1)의 메뉴에서 **Check for Updates**를 선택하세요. 업데이트가 끝나면 사용 중이던 관리 프로필을 정상 종료한 뒤 다시 여세요. Plura는 로그인과 대화를 보존하면서 공식 앱 기준으로 갱신합니다. 닫혀 있던 프로필은 다음에 열 때 갱신됩니다.

관리 프로필에도 업데이트 메뉴가 보일 수 있습니다. 업데이트는 공식 앱 메뉴에서 진행하세요. 자세한 내용은 [macOS 업데이트·복구 안내](UPDATE_RECOVERY_AND_DIAGNOSTICS.ko.md)를 참고하세요.

### 업데이트

이 절은 위의 ChatGPT 앱 자체가 아니라 **Plura Desktop 자체 업데이트**를 설명합니다.

macOS DMG 간편 설치를 사용했다면 다음 파일을 더블클릭합니다.

`~/Applications/Plura Desktop Tools/Update Plura Desktop.command`

동일 GitHub 저장소에서 가장 최근에 공개된 release(prerelease 포함)의 installer를 받아 현재 Mac용 checksum-bound standalone runtime을 설치하고, 기존 login/profile state를 교체하지 않은 채 모든 managed profile을 refresh합니다.

guided updater를 실행하기 전에 모든 managed **ChatGPT Profile N** 창을 정상 종료합니다. managed profile이 실행 중이면 distribution runtime 교체는 fail-closed하며 updater가 프로세스를 강제 종료하지 않습니다.

source/package 설치라면 처음 사용한 환경에서 갱신한 뒤 managed profile의 selector/control runtime을 refresh합니다. source clone이라면:

```sh
git pull --ff-only
python -m pip install --upgrade .
plura-desktop refresh --profile 2
```

release wheel을 사용했다면:

```sh
pipx install --force ./plura_desktop-NEW_VERSION-py3-none-any.whl
plura-desktop refresh --profile 2
```

Homebrew 설치라면 managed ChatGPT Profile 창을 정상 종료한 뒤:

```sh
brew update
brew upgrade plura-desktop
plura-desktop refresh --profile 2
```

### managed profile 제거

macOS DMG 간편 설치를 사용했다면 다음 파일을 더블클릭합니다.

`~/Applications/Plura Desktop Tools/Uninstall Plura Desktop.command`

installer ownership record를 검증하고 모든 managed profile의 삭제 예정 범위를 먼저 보여준 뒤, literal `REMOVE` 확인을 받아 managed profile state와 Plura 소유 CLI runtime/tools를 제거합니다. 공식 ChatGPT 앱/default profile은 삭제 대상이 아닙니다.

CLI/package 설치라면 해당 ChatGPT Profile을 정상 종료한 뒤 먼저 삭제 예정 경로를 확인합니다.

```sh
plura-desktop uninstall --profile 2
```

검토 후 실제 제거합니다.

```sh
plura-desktop uninstall --profile 2 --yes
```

모든 managed profile을 제거한 뒤 CLI 패키지까지 없애려면 처음 설치에 사용한 package manager로 제거합니다. 예: `pipx uninstall plura-desktop` 또는 `python -m pip uninstall plura-desktop`. 공식 ChatGPT 앱/default profile은 제거 대상이 아닙니다.

Homebrew에서는 managed profile을 먼저 모두 제거한 뒤:

```sh
brew uninstall plura-desktop
```

### 문제 해결

- **`plura-desktop: command not found`** — Plura Desktop을 설치한 격리 환경/package tool에서 실행하거나 해당 tool의 일반적인 PATH 설정을 확인합니다. 활성화된 venv라면 `python -m plura_desktop --version`도 같은 fallback입니다. console script만 따로 복사하지 않습니다.
- **Windows/Linux에서 `ChatGPT executable not found`** — 실제 실행 파일을 `--app PATH`로 넘기거나 `CHATGPT_EXECUTABLE`을 설정합니다. 알 수 없는 설치 layout을 추측하지 않습니다.

## 소유권 규칙

공식 앱이 Native 실행·도구·엔진 수명을 소유합니다. Plura는 profile identity, manifest, 격리된 상태 경로와 selector의 설치·갱신·제거를 소유합니다. 원본 앱과 계정 데이터는 변경하지 않습니다.

별도 Native 엔진, 중계, supervisor, 상주 감시를 시작하지 않습니다. 이미 실행 중인 프로필은 재사용하고, 정상 종료는 공식 앱에 맡깁니다.

## 제품 원칙

- **Mainstream-first:** 공식 ChatGPT Desktop + 일반 managed profile 흐름을 기본 경로로 두고 특수 launcher/진단/downstream client는 optional adapter/contract로 격리합니다.
- **Change-resilient:** OpenAI 소유의 모델/tool 이름, UI/DOM, 실행 파일 위치, incidental schema를 제품 전체의 고정 가정으로 두지 않습니다. runtime discovery, capability detection, versioned contract와 platform adapter를 우선합니다.
- **Easy in, easy out:** 설치·refresh·제거는 대칭적인 ownership lifecycle입니다. 제품이 만든 상태는 `PluraDesktop` namespace에 귀속되며 공식 ChatGPT 앱이나 소유권이 불명확한 데이터는 삭제하지 않습니다.
- **Consistent naming:** 제품명은 **Plura Desktop**이고 ChatGPT/Codex는 integration 설명입니다. 제품/CLI/source/metadata namespace는 canonical Plura 이름만 사용합니다.
- **Multi-OS / efficient diagnostics:** macOS·Windows·Linux를 공통 architecture 대상으로 두고 OS 차이는 adapter에 격리합니다. polling/background sampler를 피하고 CPU·배터리·I/O·로그 비용을 제한하며, 진단은 구조화·bounded·privacy-safe하게 필요할 때만 수집합니다.

## 플랫폼별 managed 경로

| 플랫폼 | Selector | Codex home | Desktop user data |
| --- | --- | --- | --- |
| macOS | `~/Applications/ChatGPT Profile N.app` | `~/.codex-profileN` | `~/Library/Application Support/Codex-ProfileN` |
| Windows | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\ChatGPT Profile N.cmd` | `%USERPROFILE%\.codex-profileN` | `%LOCALAPPDATA%\Codex-ProfileN` |
| Linux | `$XDG_DATA_HOME/applications/chatgpt-profile-N.desktop` | `~/.codex-profileN` | `$XDG_CONFIG_HOME/Codex-ProfileN` |

Linux는 XDG 환경 변수가 없으면 `~/.local/share`, `~/.config`, `~/.local/state`를 사용합니다.

`PluraDesktop` metadata 내부의 역할도 고정합니다. `profile-N-install-manifest.json`은 ownership/provenance state, `control-runtime`은 refresh 가능한 설치 코드, `runtime-sessions`는 live runtime용 임시 claim/descriptor, macOS `runtime-apps/profile-N` 및 Windows/Linux `profile-N-runtime`은 재생성 가능한 derived runtime입니다. 선택적으로 저장하는 진단 증거는 `diagnostics/profile-N/incidents` 아래에만 두며 프로필·runtime 진단 전체 32MiB/30일, incident당 최대 256 KiB로 제한합니다. macOS 간편 installer를 사용한 경우 `distribution/cli-runtime`과 `bin`은 installer-owned standalone CLI runtime/launcher이며 full uninstall 도구가 managed profile 제거 후 함께 정리합니다. 별도 background cache/telemetry/rolling product log는 만들지 않습니다.

새 경로는 Apple Silicon macOS의 격리된 실제 앱 실행·정상 종료까지 확인했습니다. 원본 앱은 read-only이고 프로필의 로그인·대화 경로는 보존합니다. 로그인 포함 Native/browser 실수락과 Intel·Windows·Linux 공식 앱 검증은 새 배포 전 확인해야 합니다. 이전 릴리스의 E2E 결과를 새 경로 전체의 검증으로 간주하지 않습니다.

Windows/Linux를 실제 지원으로 표시하기 위한 정확한 실기기 gate는 [PLATFORM_VERIFICATION.md](PLATFORM_VERIFICATION.md)에 고정합니다.

## 요구 사항

- 현재 OS용 공식 ChatGPT Desktop 애플리케이션
- macOS에서는 현재 공식 ChatGPT 앱이 macOS 14 이상을 요구하며 Apple Silicon과 Intel을 지원합니다. Homebrew formula도 같은 최소 버전을 적용합니다.
- wheel/source-package CLI 경로에서는 Python 3.10 이상. macOS guided DMG는 bundled standalone runtime을 사용합니다.
- source checkout에서 직접 설치/업데이트할 때만 Git이 필요합니다.
- macOS 기본 실행 파일은 `/Applications/ChatGPT.app/Contents/MacOS/ChatGPT`입니다.
- Windows/Linux는 `ChatGPT` 실행 파일을 자동 탐색하고, 실패하면 `--app PATH` 또는 `CHATGPT_EXECUTABLE`을 사용합니다.

## CLI

패키지 설치 후 모든 지원 OS에서 canonical 명령은 `plura-desktop`입니다.

```sh
plura-desktop --version
plura-desktop --help
plura-desktop status --profile 2
```

Windows/Linux에서 실행 파일을 자동 탐색할 수 없으면 `--app PATH` 또는 `CHATGPT_EXECUTABLE`을 사용합니다.

처음 Profile 2를 설정할 때는 `status --profile 2`로 미설치 상태를 확인하고, `install --profile 2` → `launch --profile 2` 순서로 실행한 뒤 새 Profile 2 창에서 정상 로그인합니다. 기존 profile DB나 cookie를 복사하지 않습니다. 이후에는 필요할 때 `status`/진단을 사용하고, package 갱신 후 `refresh`, 제거 시에는 먼저 dry-run `uninstall`을 검토한 뒤 `--yes`를 적용합니다.

## 외부용 target contract v2

공식 앱이 Native 엔진·도구·종료를 소유합니다. Plura는 프로필 격리와 앱 실행만 담당하며 별도 엔진·중계·감시 프로세스를 시작하지 않습니다.

```sh
plura-desktop targets --json
plura-desktop launch-target --target local.plura-desktop.profile2 --json
plura-desktop target-session --target local.plura-desktop.profile2 --json
plura-desktop quit-target --target local.plura-desktop.profile2 --expected-desktop-pid PID --json
```

`contractVersion: 2`, `backendPolicy: "official-desktop-owned"`와 실제 앱의 `running`/`stopped` 상태를 제공합니다. 실행 요청 수락은 `launch-requested`이며 로그인·도구 준비 완료를 뜻하지 않습니다. PID를 확인할 수 있는 플랫폼에서는 `desktopProcessID`도 제공합니다. 정상 종료를 지원하지 않는 플랫폼에서는 강제 종료 대신 제한을 알립니다.

**v1과 호환되지 않습니다.** 외부 app-server endpoint, engine override, Responses 중계, 모델 목록 콜백·catalog overlay, renderer CDP 기능과 옵션을 제거했습니다. 외부 연계 제품이 v2에 대응한 후 설치해야 합니다. 모델 공급자는 공식 앱의 설정으로 등록하며 Plura가 설정·계정을 대신 변경하지 않습니다. 이전 버전으로 실행 중인 프로필은 한 번 정상 종료하고, 업데이트 후 selector를 refresh합니다.

## 진단

profile 전용 structural Rust trace는 기본 off이며 watcher/polling을 만들지 않습니다.

```sh
plura-desktop diagnostics-on --profile 2
plura-desktop diagnostics-status --profile 2
plura-desktop diagnostics-off --profile 2
```

설치되는 `plura-desktop-diagnose` 명령은 **명시적 macOS 전용** on-demand 진단입니다. allowlist된 구조 로그만 읽고 인증/profile/state DB, 대화 본문, prompt/result, private MCP 이름, raw conversation/thread ID를 출력하지 않습니다.

기본 동작은 stdout-only이며 파일을 만들지 않습니다.

```sh
plura-desktop-diagnose --profile 2 --minutes 60 --json
```

증거 파일이 실제로 필요할 때만 `--write-artifact`를 사용합니다. managed Profile 2..99에만 허용되며 sanitized summary를 `PluraDesktop/diagnostics/profile-N/incidents` 아래 저장합니다. incident당 최대 256 KiB, 프로필·runtime 진단 전체 32MiB/30일로 제한하고 오래된 incident를 bounded retention으로 정리합니다. symlink/replaced/unexpected incident 구조가 있으면 새 artifact를 쓰기 전에 fail-closed합니다. 이 artifact는 해당 Profile uninstall 때 함께 제거됩니다.

```sh
plura-desktop-diagnose --profile 2 --minutes 60 --json --write-artifact
```

## 제거

managed profile을 먼저 정상 종료합니다.

```sh
plura-desktop uninstall --profile 2
plura-desktop uninstall --profile 2 --yes
```


## 격리 범위


공식 ChatGPT 앱/실행 파일은 항상 read-only 입력입니다. macOS에서만 managed profile을 위해 바이트 동일성·기존 코드서명을 검증한 APFS copy-on-write runtime clone을 만들며, 원본 앱 자체는 수정·재서명·교체하지 않습니다.

## 개발/검증

```sh
python3 -m unittest discover -s tests -v
for script in scripts/*.sh; do sh -n "$script"; done
python3 -m compileall -q src scripts tests
python3 scripts/check_repository_invariants.py
```

CI는 macOS·Windows·Linux에서 실행됩니다. 실제 공식 앱이 필요한 플랫폼 동작은 해당 OS 실기기 검증 여부를 따로 명시합니다.

보안 문제는 [SECURITY.md](../SECURITY.md), 기여 방법은 [CONTRIBUTING.md](../CONTRIBUTING.md)를 참고하세요.
