# macOS Profile 2+ 업데이트·바로가기 복구·진단

## 권장 사용 방법

1. 공식 ChatGPT 앱(프로필 1)의 메뉴에서 **Check for Updates**를 선택한다.
2. 설치가 끝나면 사용 중이던 관리 프로필을 정상 종료하고 다시 연다. 로그인과 대화는 유지되며, 닫혀 있던 프로필은 다음에 열 때 갱신된다.

관리 프로필에서 OpenAI의 설치를 시작하거나 완료를 통보받을 공개 연결은 확인되지 않았다. Plura는 관리 프로필의 자동 Sparkle 확인·다운로드를 억제한다. 관리 프로필에 수동 업데이트 메뉴가 남아 있어도 공식 앱에서 업데이트한다. 관리 프로필의 복제본이 별도로 바뀌면 다음 실행에서 공식 앱 버전으로 맞춘다.

## 바로가기 앱이 사라지거나 열리지 않을 때

프로필 2를 정상 종료한 뒤 아래 두 명령으로 상태를 확인한다.

```sh
plura-desktop status --profile 2
plura-desktop diagnostics-status --profile 2
```

- `Selector integrity: ready`: Plura 바로가기 내부의
  `Info.plist`·실행 파일·아이콘 및 소유권을 확인했다.
- `Selector integrity: incomplete`: `.app` 디렉터리는
  남았지만 필수 파일이 없거나 손상됐다. 해당 프로필이 완전히
  종료됐고 원래 관리 경로가 안전하면
  `plura-desktop refresh --profile 2`로 **파생 파일만 복구**한다.
  다음 정상 관리형 실행 준비 시에도 같은 상태를 감지해 복구한다.
- `Selector integrity: unsafe`: 소유권 불일치 또는
  링크/경로 변경 의심. 자동 복구하지 않으며 원인을 먼저 확인한다.
- `Managed runtime integrity`: signed clone의 *구조적* 구성
  파일 존재 여부다. `ready`라고 해서 서명 검증을 대신하지 않는다.

기존 대화 디렉터리·쿠키·인증 토큰·원격 등록 파일을 복사·삭제하거나
프로필을 초기화할 필요는 없다.

## 코드 불변식

1. **단일 업데이트 권위:** 기본 공식 ChatGPT 앱만 업데이트 기준이다.
   프로필 2+ runtime/selector는 재생성 가능한 파생 코드이며
   독립된 계정·대화 상태는 다른 소유권에 속한다.
2. **동시 writer 방지:** macOS `refresh`/
   `prepare_launch`는 Plura 소유 metadata 디렉터리의
   OS advisory lock 안에서 파생 실행 파일과 selector를
   조정한다. 다른 Plura writer가 점유했으면 재시도 안내와 함께
   **실패로 종료**하며 사용자 프로세스를 강제 종료하지 않는다.
   이 잠금은 다른 프로그램의 Sparkle updater나 구형 Plura의
   작업까지 제어할 수 있다는 뜻은 아니다.
3. **상태 검사:** 단순히 `.app` 디렉터리가 존재하는지만 보지
   않고 `CFBundleIdentifier`, 실행 파일·아이콘,
   구성 디렉터리의 심볼릭 링크와 소유권을 확인한다.
   `unsafe`를 조용히 재작성하지 않는다.
4. **원자적 교체:** 기존 selector의 staging/exchange 구현과
   정상 종료 조건을 유지한다. 실패 시 기존 로그인·대화 상태
   디렉터리는 건드리지 않는다.

## Plura 설치가 중단됐을 때

**Update Plura Desktop.command**를 다시 실행합니다. 다음 실행이 Plura 소유의 미완성 다운로드를 정리합니다. 설치 기록이 확정되기 전에 중단됐으면 이전 런타임을 복구하고, 확정된 뒤 중단됐으면 새 런타임과 기록을 유지합니다. 관리 프로필의 로그인과 대화는 그대로 유지됩니다. 공식 ChatGPT의 Sparkle 캐시는 공식 업데이터가 관리합니다.

## 진단 기록의 범위·개인정보

기존 Plura diagnostic artifact 저장소에 **실패 또는 비정상
재조정이 발생했을 때만** `UpdateLifecycleReceipt`를 기록한다.
단계는 `update_lock`, `manifest_validation`,
`official_source_validation`, `runtime_prepare`,
`selector_staging`, `control_runtime_prepare`,
`manifest_commit`, `desktop_spawn` 등으로 제한합니다. 앱 실행 요청 실패는
`launch_desktop`으로 구분합니다. 준비 전에 실패하면 `launch_target`으로 기록하며 프로필 조회·상태 판정·실행 중인 앱 확인·앱 활성화 단계를 구분합니다.
LaunchServices는 대기용 `open(1)` PID와 실제 GUI PID가
다르므로, 원본 앱을 정상 종료할 때에는 **확인된 GUI PID와
실행 파일의 정규 경로가 모두 일치할 때만** AppKit에 정상 종료를
요청한다. GUI PID를 확인할 수 없으면 대기용 wrapper를 사용자
앱으로 간주하여 종료하지 않는다.

예외 *종류*, 제한된 `errno`, 단계별 상대 경과 시간,
구조적 무결성 요약, 실행 중 여부, 재생성 여부만 담는다.
**원문 예외 메시지, 절대 사용자 경로, 실행 인수, 토큰·계정,
대화·브라우저 데이터, 원문 Remote payload는 기록하지 않는다.**
기존 소유권·권한 검증과 보관 정책(30일, 진단 전체 32 MiB, 건당 256 KiB)을 적용한다. 기록 자체가 실패하더라도 원래의 업데이트 오류를
숨기지 않는다. 이는 Sparkle 내부 버튼 클릭/다운로드 로그를
감시하는 기능이 아니며, 과거 사고의 직접 원인을 자동으로
확정할 수는 없다.

## 회귀 테스트 및 남은 게이트

```sh
python3 -m unittest discover -s tests -p test_update_lifecycle_diagnostics.py -v
```

가짜 홈과 시험 앱으로 바로가기 복구, 중단된 임시 파일 정리, 원본 변경 중 기존 복제본 보존, 진단 개인정보 비노출을 확인한다. 설치기와 다운로드 복구는 `test_release_distribution.py`에서 별도로 검증한다.

Apple Silicon에서 실제 Plura 갱신과 프로필 2 바로가기 실행·창 활성화를 확인했다. 다음 공식 ChatGPT 업데이트에서 Dock과 앱 보관함의 동작까지 확인하는 것은 별도 검증이다.
