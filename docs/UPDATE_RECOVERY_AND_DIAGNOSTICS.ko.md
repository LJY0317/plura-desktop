# macOS Profile 2+ 업데이트·바로가기 복구·진단

## 권장 사용 방법

**ChatGPT 자체 업데이트는 공식 기본 프로필 1에서만 진행한다.**
Plura의 프로필 2+가 정상 종료된 뒤 다시 실행되면 공식 앱의
새 버전으로 파생 signed clone을 맞춘다. 기존 계정·Work/Codex
대화·모바일 Remote 데이터 저장소는 교체하지 않는다.

프로필 2+에 **수동 ChatGPT 업데이트 버튼이 여전히 보일 수 있지만
사용하지 않는다.** Plura는 공식 서명된 앱의 업데이트 UI를
패치하지 않고 자동 Sparkle 확인·다운로드만 별도로 억제한다.
수동 버튼을 완전히 비활성화했다고 주장할 수 없다.

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

## 진단 기록의 범위·개인정보

기존 Plura diagnostic artifact 저장소에 **실패 또는 비정상
재조정이 발생했을 때만** `UpdateLifecycleReceipt`를 기록한다.
단계는 `update_lock`, `manifest_validation`,
`official_source_validation`, `runtime_prepare`,
`selector_staging`, `control_runtime_prepare`,
`manifest_commit`, `runtime_spawn`,
`runtime_session_wait` 등 내부 허용값으로 제한한다.

예외 *종류*, 제한된 `errno`, 단계별 상대 경과 시간,
구조적 무결성 요약, 실행 중 여부, 재생성 여부만 담는다.
**원문 예외 메시지, 절대 사용자 경로, 실행 인수, 토큰·계정,
대화·브라우저 데이터, 원문 Remote payload는 기록하지 않는다.**
기존 소유권·권한 검증과 프로필당 최대 10건 등의 보관 정책을
적용한다. 기록 자체가 실패하더라도 원래의 업데이트 오류를
숨기지 않는다. 이는 Sparkle 내부 버튼 클릭/다운로드 로그를
감시하는 기능이 아니며, 과거 사고의 직접 원인을 자동으로
확정할 수는 없다.

## 회귀 테스트 및 남은 게이트

```sh
python3 -m unittest discover -s tests -p test_update_lifecycle_diagnostics.py -v
```

가짜 홈·가짜 ChatGPT 앱을 사용해 빈 selector 자동 복구,
원본 관리 디렉터리의 inode 보존, 동시 잠금 충돌,
수정된 파생 runtime 교체, 수동 updater 변형 복구,
unsafe symlink 거부, 백엔드 시작 실패,
개인정보 비노출, 진단 저장소 실패·최대 보관 건수를
검증한다. 테스트는 실제 계정·대화·Remote를 변경하지 않는다.

이 구현은 Plura `main`에 통합되는 **업데이트/복구 기능**이며,
macOS LaunchServices 기반 네이티브 GUI 실행·PID 감독 개선은
다른 개발자의 구현과 겹치므로 별도로 검토한다. 실제 signed
ChatGPT 앱의 다음 업데이트에서 완전한 재발 방지 효과가
증명됐다는 뜻도 아니다.
