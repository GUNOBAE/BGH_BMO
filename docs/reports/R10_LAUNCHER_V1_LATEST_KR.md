**PARTIAL — 작업 B의 버전 분리·비공개 키 저장·수동 Launcher 코드와 Mock/headless 검사는 PASS다. 지정 ZIP/master order와 최종 승인 R10 일치 확인은 INPUT_PENDING, 실제 키 설정·API·LCD 검수는 NOT_RUN이다.**

2026-10-23 KST. `/home/bgh/BMO_EMOTION`, `feature/bmo-auto-emotion-v1`, [draft PR #13](https://github.com/GUNOBAE/BGH_BMO/pull/13).

## 기준과 변경 SHA

A checkpoint는 `d2a75317e8b36108ff932aea0d49ca3a40fe5b7b`, 검증된 감정 연속성 소스는
`5545880fedf1edf35268a5e1b935bd9cabb6ce5e`다. A 제출 후 B를 진행했다.
B 구현 커밋은 [`9555863ae5d7dc5d8b3d997be9ea032c2d744758`](https://github.com/GUNOBAE/BGH_BMO/commit/9555863ae5d7dc5d8b3d997be9ea032c2d744758).
본 보고서는 별도 문서 커밋으로 같은 브랜치에 제출한다. main/기존 통합 브랜치는 병합하지 않는다.

첨부 `BMO_R10_CODEX_INTEGRATION_HANDOFF_2026-10-23.zip`과 master order는
접근 가능한 관련 Pi 경로·GitHub 브랜치/PR에서 미발견이다. 경로/링크를 요청했으며
전체 인계문서를 읽었다고 보고하지 않는다. 확보한 GitHub 작업서와 현재 사용자 메시지의
안전한 코드 범위를 수행했다. 과거 PR의 v13.2 확대 검토 지시는 읽었으나, 새 R10에
낡은 1.5배 표시층을 중복 삽입하거나 v13.2 원본을 변경하지 않았다.

## R10·v13.2 분리 결과

| 항목 | 실제 상태 |
|---|---|
| 기존 v13.2 | `/home/bgh/Downloads/BMO_Screen_v13_2.html`, 159,892 bytes, SHA-256 `38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`. 기본 `--screen-profile v13.2` 및 복구 경로 유지. |
| R10 로컬 후보 | `/home/bgh/Downloads/BMO_Screen_v13_2_7inch_preview_v10.html`, 196,987 bytes, SHA-256 `8c3ef98ba0e0645213c95295a24d7353fc2865b5ad9bc85390360c4ec9b78c82`. 파일 title에 R10이 있고 본문에는 검토본/Pi 미적용 표기가 있어 최종 승인본 일치 확인을 요청했다. |
| 버전 선택 | 불변 `screen_profiles.py` registry에서 경로·핀 SHA·label·승인 상태를 분리한다. R10은 명시적 `--screen-profile r10`으로만 선택하고 `candidate_pending_user_approval`로 표시한다. 승인 여부를 추정해 기본값을 바꾸지 않았다. |
| 외부 연결 | 기존 Python FaceServer/외부 adapter가 선택 파일을 읽기 전용으로 제공한다. 제공된 raw bytes가 원본과 같고, hash 불일치는 거부한다. `--face-file`은 같은 핀을 가진 로컬 위치 변경만 허용한다. |
| 공개 상태 | GET `/api/screen`은 선택 profile/version·SHA·승인 상태만 제공하며 경로/키를 내보내지 않는다. 원래 정적 허용 목록과 GET-only 상태 경계를 유지한다. |
| 표시층 | v13.2의 기존 viewport는 그대로, R10은 원본 내장 1.6 scale controller만 사용한다. 아트·SVG·프레임을 수정하거나 외부 확대를 중복 주입하지 않았다. |

R10 surprised의 terminal idle 작은 O는 해당 후보 원본의 표현이다. 외부 silence cue의
closed와 구분해 검증했고, v13.2와 모양을 강제로 맞추지 않았다. R10 API의 13.2/version
표기와 R10 layout 버전을 구분한다. 후보 연결 검사는 최종 승인이나 실제 LCD 합격이 아니다.

## 키 설정·수동 실행 구현

- `launch-bmo` → `manual_launcher.py` → 기존 integration 경로를 사용한다. 기본 무인 live 실행은 없고 `--simulate`/`--live`를 명시한다. desktop entry는 repository 안의 예제만 제공했으며 Desktop/autostart/systemd에 설치하지 않았다.
- 명시 setup 또는 첫 live 실행에서 필요한 키를 숨김 입력으로 받는다. 다음 실행에서는 저장 값을 재사용한다. `${XDG_CONFIG_HOME:-$HOME/.config}/bmo/credentials.json`, 계정 소유 디렉터리 700/파일 600의 로컬 평문이다. 암호화/keyring이라고 주장하지 않는다.
- 환경 → 저장값 → 누락 입력 순서. 환경 키를 자동 영구 저장하지 않고 새로 입력한 키만 저장한다. `--setup-credentials`는 두 키 입력이 끝난 뒤 원자적으로 교체하며 API를 호출하지 않는다.
- 디렉터리 모든 경로를 dirfd/O_NOFOLLOW로 열고 최종 UID/700을 확인한다. 파일은 열린 fd의 정규파일/UID/600/nlink=1/크기를 검증한다. 중복 JSON·잘못된 version/필드·control/nonASCII/과대 키를 거부한다. getpass echo fallback도 입력 전에 차단한다.
- 같은 private 디렉터리의 600 임시 파일 쓰기/fsync → 원자 replace → directory fsync. 교체 전 실패/취소는 기존 bytes를 보존한다. 교체 뒤 fsync 실패에는 완전한 새 파일이 남을 수 있다. 잠금으로 동시 입력을 직렬화하고 자동 인증 재입력 loop를 넣지 않았다.
- 기존 앱의 shared lock을 먼저 확보한 뒤 live에서만 resolver를 호출한다. simulate/help/effective-config는 credential 저장소 접근·prompt·생성 0이다. 비TTY 누락은 즉시 고정 오류로 종료한다.
- 키는 SDK constructor와 Fish Authorization에 메모리로만 전달한다. BMOConfig/effective-config/HTTP/event/인자/로그/새 subprocess 환경에 넣지 않는다. 실제 키를 탐색하거나 프로덕션 설정 파일을 열거나 만들지 않았다.

## 실행한 검사

| 검사 | 결과 |
|---|---|
| 최종 전체 unittest | **471/471 PASS, skip 0**, 28.583초. A 415 + private store 30 + manual launcher 10 + provider 7 + screen 5 + CLI 2 + shell entry 2. |
| private store | 합성 temp 파일만으로 최초 저장/재사용/부분 환경 우선·미저장/동시 입력 1회/중단/교체 전 실패·교체 후 fsync/UID·권한·symlink·hardlink·FIFO·JSON·크기·echo fallback·repr 가림을 검증했다. |
| provider/launcher | 합성 키가 SDK/Fish에만 들어가고 config/event/player에는 없는지, setup API0, help/simulate/config 키 조회0, lock 이후 resolver1회, 기존 launcher 충돌은 키 조회 전 거부, 인증 실패 재질문0을 확인했다. |
| 두 화면 headless | **12 checks PASS = profile당 6**, 6감정/외부 cue·silence·terminal/reload/resize/lifecycle. 원본 bridge 각각 1,452 fixtures, 합 2,904 일치. 원본 raw served bytes 동일, browser media play0. |
| 새 manual Launcher 실제 모의 E2E | v13.2 **4 PASS**, R10 후보 **4 PASS**. 각 질문2/player3/회수3/API·장치0/credential resolver0. 실제 wrapper·launcher·server·선택 iframe 경로를 사용했다. 두 원본 hash 전후 동일. |
| 정적·보존 | compileall / git diff --check / 제출 경로·자산·키 패턴 검사 PASS. BMO_INTEGRATION 추적 파일128개 및 두 원본 SHA 재비교 일치. |

검사는 실제 인증을 제외한 허용 child 환경에서 수행했다. 실제 키의 인증 성공,
Luna schema 지원, 실제 음성/Piper/카메라/서보/센서/LCD/물리 버튼은 NOT_RUN이다.
Fish Aaron Soft / beemo / gain 0.25 / Luna low / 출력320 / STT / 고정 prompt blob / 원본 Piper opt-in /
4.1 mini 복귀는 유지했다. 팬·SSH·펌웨어·서비스·OS·다른 프로젝트·기존 백업은 변경하지 않았다.

## 수동 명령과 복구

[software/LAUNCHER_KR.md](../../software/LAUNCHER_KR.md)에 정확한 설정·실행·검사 명령을 기록했다.
키를 준비하는 사용자 전용 명령(자동 작업에서는 미실행):

```bash
cd /home/bgh/BMO_EMOTION
./launch-bmo --setup-credentials
```

장치·API 없는 실행은 `./launch-bmo --simulate --emotion-mode auto --screen-profile v13.2 --port 0`.
사용자 입회 live 검수는 `./launch-bmo --live --emotion-mode auto --screen-profile v13.2`이며
저장 키를 재사용한다. 최종 R10 일치 확인 뒤에만 `--screen-profile r10`을 명시해 검수한다.

복구는 `/quit` 후 v13.2/manual 선택 또는 보존된 `/home/bgh/BMO_INTEGRATION` 사용이다.
private 설정은 Git rollback과 분리돼 있으며 자동 삭제하지 않는다. 기존 데이터·백업을
덮어쓰거나 서비스/프로세스/lock을 강제로 제거하지 않는다. C는 이 checkpoint를 기준으로
별도 BMO_FEATURES clone/branch/PR에서 이어가며 기존 완성본에 기능 diff를 섞지 않는다.
