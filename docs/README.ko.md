# Plura Desktop

**ChatGPT Desktop용 멀티프로필 런타임입니다.**

공식 ChatGPT 애플리케이션을 패치·재서명·수정하지 않고 서로 격리된 여러 ChatGPT/Codex 데스크톱 프로필을 실행하고 관리합니다. macOS의 managed profile은 LaunchServices에서 default 앱과 구분되도록 원본과 바이트가 동일하고 기존 서명이 그대로 유효한 APFS copy-on-write runtime clone을 사용합니다.

현재 공통 profile/domain lifecycle과 **macOS·Windows·Linux** 플랫폼 adapter로 구성됩니다. 공식/default ChatGPT 프로필은 이 프로젝트가 소유하지 않으며, managed profile은 Profile 2부터 시작합니다. 각 managed profile은 `CODEX_HOME`과 데스크톱 user-data를 분리합니다.

> [!IMPORTANT]
> OpenAI가 제작·보증·지원하는 제품이 아닌 비공식 커뮤니티 프로젝트입니다. ChatGPT와 Codex는 OpenAI의 상표입니다.

## 빠른 시작

**현재 로그인까지 포함한 전체 공식 앱 E2E가 검증된 경로는 Apple Silicon macOS**입니다. Intel standalone runtime은 GitHub의 Intel macOS runner에서 native build/smoke까지 검증했지만 Intel 공식 앱 E2E는 별도 gate로 남아 있습니다. Windows는 공통 core/CI 검증만 완료되어 실기기 gate가 남아 있습니다. Linux는 한 단계 더 나아가 CI가 OpenAI의 현재 공식 [데스크톱 preview](https://learn.chatgpt.com/docs/linux/linux-app) `.deb`를 직접 설치하고 실제 packaged Desktop/bundled Codex를 탐색한 뒤 Xvfb에서 generated selector를 canonical renderer-enabled ready session까지 실행합니다. 다만 실제 사용자 로그인 지속성 및 정상 quit/refresh/uninstall 전체 흐름은 별도 real-user gate로 남아 있습니다.

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

### 업데이트

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

- **standalone release runtime이 없거나 checksum이 맞지 않음** — release asset 구성이 불완전하거나 손상된 상태입니다. 검사를 우회하지 말고 정상 release 또는 wheel/CLI fallback을 사용합니다.
- **`-unsigned.dmg` 또는 `-signed.dmg`만 있거나 아직 GitHub Release가 없음** — 위 source/package 경로를 사용하거나 suffix 없는 notarized DMG를 기다립니다. 일반 설치 경로에서 macOS 보안 경고를 우회하지 않습니다.
- **`plura-desktop: command not found`** — Plura Desktop을 설치한 격리 환경/package tool에서 실행하거나 해당 tool의 일반적인 PATH 설정을 확인합니다. 활성화된 venv라면 `python -m plura_desktop --version`도 같은 fallback입니다. console script만 따로 복사하지 않습니다.
- **Windows/Linux에서 `ChatGPT executable not found`** — 실제 실행 파일을 `--app PATH`로 넘기거나 `CHATGPT_EXECUTABLE`을 설정합니다. 알 수 없는 설치 layout을 추측하지 않습니다.
- **`restart-required`** — 요청한 canonical capability/session 밖에서 이미 실행 중입니다. 해당 ChatGPT 창을 한 번 정상 종료한 뒤 Plura Desktop으로 다시 실행합니다.
- **uninstall이 running profile 때문에 거부됨** — managed ChatGPT profile을 정상 종료한 뒤 dry-run부터 다시 실행합니다. uninstaller는 프로세스를 강제 종료하지 않습니다.
- **path/identity/symlink 안전 오류** — Plura Desktop으로 임의 삭제하지 말고 보고된 경로를 먼저 확인합니다. destructive ownership이 모호하면 의도적으로 fail-closed합니다.

## 소유권 규칙

핵심 불변식은 하나입니다.

> 하나의 managed profile에는 하나의 authoritative runtime만 존재합니다. lifecycle-bound supervisor 하나가 공식 Desktop 하나와 loopback Codex app-server 하나를 함께 소유하며, 모든 managed 실행 경로는 이 runtime으로 합쳐집니다. 사용자가 별도의 "shared 모드"를 선택할 필요가 없습니다.

공통 core는 profile identity, manifest, allowlist, install/refresh/uninstall lifecycle, 진단 설정, 외부용 target contract를 담당합니다. 실행 파일 탐색, selector, 프로세스 확인, 파일 정체성, 환경 구성과 OS 오류 UI는 각 플랫폼 adapter가 담당합니다.

watcher, idle polling daemon, login item, updater, background sampler는 없습니다. supervisor는 managed profile이 실제 실행 중일 때만 존재하며 Desktop/app-server와 함께 종료됩니다.

## 제품 원칙

- **Mainstream-first:** 공식 ChatGPT Desktop + 일반 managed profile 흐름을 기본 경로로 두고 특수 launcher/진단/downstream client는 optional adapter/contract로 격리합니다.
- **Change-resilient:** OpenAI 소유의 모델/tool 이름, UI/DOM, 실행 파일 위치, incidental schema를 제품 전체의 고정 가정으로 두지 않습니다. runtime discovery, capability detection, versioned contract와 platform adapter를 우선합니다.
- **Fail closed:** ownership/path/process/외부 구조가 모호하면 추측해서 변경하지 않고 중단하거나 읽기 전용 진단으로 제한합니다.
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

`PluraDesktop` metadata 내부의 역할도 고정합니다. `profile-N-install-manifest.json`은 ownership/provenance state, `control-runtime`은 refresh 가능한 설치 코드, `runtime-sessions`는 live runtime용 임시 claim/descriptor, macOS `runtime-apps/profile-N` 및 Windows/Linux `profile-N-runtime`은 재생성 가능한 derived runtime입니다. 선택적으로 저장하는 진단 증거는 `diagnostics/profile-N/incidents` 아래에만 두며 Profile당 최대 10개, incident당 최대 256 KiB로 제한합니다. macOS 간편 installer를 사용한 경우 `distribution/cli-runtime`과 `bin`은 installer-owned standalone CLI runtime/launcher이며 full uninstall 도구가 managed profile 제거 후 함께 정리합니다. 별도 background cache/telemetry/rolling product log는 만들지 않습니다.

Apple Silicon macOS에서는 현재 공식 ChatGPT 앱으로 로그인까지 포함한 실제 E2E를 검증했습니다. `/Applications/ChatGPT.app` 원본은 항상 read-only이며, managed profile용 runtime clone은 launcher metadata 아래의 재생성 가능한 derived code입니다. Intel macOS standalone은 native Intel GitHub runner에서 build/smoke까지 통과했지만 Intel 공식 앱 E2E는 아직 별도 gate입니다. Windows는 CI에서 fake executable로 공통 lifecycle, selector, 경로/환경 contract를 검증합니다. Linux는 이 공통 CI에 더해 OpenAI의 현재 공식 Ubuntu/Debian preview package를 Ubuntu 24.04 runner에 실제 설치하고, `chatgpt` wrapper → 실제 `ChatGPT` executable, bundled `resources/codex`, 좁은 GUI session 환경, isolated Profile 2 설치, generated selector를 통한 renderer-enabled canonical launch까지 검증합니다. **다만 실제 사용자 로그인 유지, default profile과의 독립 사용, product 정상 quit, refresh/uninstall 전체 실사용 흐름은 아직 별도 gate입니다.**

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

## 외부용 target contract

외부 도구는 selector 경로, `CODEX_HOME`, profile별 포트 등을 재구성하지 말고 설치된 control CLI와 다음 JSON contract를 사용해야 합니다. macOS/Linux는 플랫폼 metadata 디렉터리의 `plura-desktop`, Windows는 `plura-desktop.cmd`가 control CLI입니다.

```sh
plura-desktop targets --json
```

contract version 1은 target ID, 표시 이름, lifecycle 상태, ownership/backend policy, canonical session 상태를 노출하고 private profile path는 내보내지 않습니다.

외부 controller는 selector나 profile index를 재구성하지 않고 target ID로 실행합니다.

```sh
plura-desktop launch-target --target local.plura-desktop.profile2
```

`launch-target` 자체가 canonical managed-profile 실행 경로입니다. 설치된 `ChatGPT Profile N` selector, 메뉴바, 향후 controller는 모두 이 명령으로 수렴해야 합니다. stopped target을 시작하면 하나의 app-server + Desktop pair를 만들고, 이미 canonical session이 살아 있으면 그대로 재사용합니다. 과거 방식으로 private Desktop이 실행 중이면 두 번째 writer를 만들지 않고 한 번의 정상 종료를 요구합니다.

launch-time Responses route가 활성화된 경우 supervisor는 실제 Codex app-server를 private loopback endpoint에 두고, Desktop/controller에는 별도의 loopback WebSocket endpoint를 노출합니다. ChatGPT Desktop은 app-server 연결 후 user config를 reload할 수 있으므로 launch-time provider가 사라지지 않도록, 노출 endpoint는 thread config를 받는 lifecycle 요청인 `thread/start`, `thread/resume`, `thread/fork`에만 같은 **secret-free** provider overlay를 적용합니다. 그 외 app-server 메시지는 그대로 전달합니다. 사용자 `config.toml`은 수정하지 않고, 공식 ChatGPT 앱도 변경하지 않으며, credential 값은 소유 backend process 환경 밖으로 내보내지 않습니다.

원격/브리지 통합이 live runtime endpoint가 필요하면 다음 local-only contract를 사용합니다.

```sh
plura-desktop target-session --target TARGET_ID --json
```

endpoint는 동적으로 할당되는 loopback 주소이며 lifetime은 Plura Desktop이 소유합니다. 외부 프로젝트는 attach만 하고 별도 fallback app-server를 만들지 않습니다.

Desktop target이 canonical runtime 밖에서 이미 실행 중이면 `target-session --json`은 계속 `restart-required`를 반환합니다. 다만 exact top-level Desktop process를 증명할 수 있는 플랫폼에서는 `desktopProcessID`도 함께 제공할 수 있습니다. 이는 기존 앱을 foreground하기 위한 관찰 정보일 뿐 canonical runtime ownership을 부여하지 않습니다.

패키지 업데이트 후 설치된 selector/runtime만 갱신하려면:

```sh
plura-desktop refresh --profile 2
```

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

증거 파일이 실제로 필요할 때만 `--write-artifact`를 사용합니다. managed Profile 2..99에만 허용되며 sanitized summary를 `PluraDesktop/diagnostics/profile-N/incidents` 아래 저장합니다. incident당 최대 256 KiB, Profile당 최대 10개로 제한하고 오래된 incident를 bounded retention으로 정리합니다. symlink/replaced/unexpected incident 구조가 있으면 새 artifact를 쓰기 전에 fail-closed합니다. 이 artifact는 해당 Profile uninstall 때 함께 제거됩니다.

```sh
plura-desktop-diagnose --profile 2 --minutes 60 --json --write-artifact
```

## 제거

managed profile을 먼저 정상 종료합니다.

```sh
plura-desktop uninstall --profile 2
plura-desktop uninstall --profile 2 --yes
```

실제 삭제 전 manifest와 현재 path identity를 다시 검증합니다. 실행 중 profile은 자동 종료하지 않고, 바뀐 path·symlink/junction·보호된 default 경로·mount subtree는 삭제를 거부합니다. 해당 Profile의 fixed product-generated runtime/diagnostic artifact도 안전성 검증 후 함께 제거합니다.

## 격리 범위

managed `CODEX_HOME`과 Desktop user-data를 분리하지만 OS sandbox는 아닙니다. 시스템 credential store, 권한, URL handler, cache/helper process, 계정 서버 상태의 완전한 격리는 보장하지 않습니다.

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
