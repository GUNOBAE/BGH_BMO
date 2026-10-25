# BMO Desktop App v1 최종 보고서

2026-10-25 KST. 계약: [Issue #17](https://github.com/GUNOBAE/BGH_BMO/issues/17) 전체 및 댓글 0개를 확인했다.
제출: [Draft PR #18](https://github.com/GUNOBAE/BGH_BMO/pull/18),
head `feature/bmo-desktop-app-v1`, base `feature/bmo-offline-integration-v2`.

## 결론

바탕화면 실행 명령 → BMO Loading → 얼굴 준비 확인 → 고정 인사 `난 비모야~!` 한 번 →
R10 전용 화면의 코드와 모의 통합을 구현했다. 기존 Python FeatureHost/brain/session/
voice cache와 Chromium/외부 얼굴 어댑터를 재사용하며 Electron/Qt를 추가하지 않았다.

최종 로컬 전체 회귀 **886/886 PASS, skip 0 (69.986초)**, pip check/compileall PASS.
최종 제품 headless·보존 shell 결과는 아래 표에 기록한다. 실제 Desktop 설치·더블클릭·
Wayland/VNC/LCD·유료 Fish 한국어 인사와 장치 실행은 **NOT_RUN**이다.

- 시작 HEAD: `067d0a4ef7cdb89e54c60b90d796e2ba9163bf58`, `/home/bgh/BMO_FEATURES` clean.
- 검증 코드 HEAD: `3f5945d063ee2c04cd0a54e80ea9a5f4d1721eef`.
- [코드 CI](https://github.com/GUNOBAE/BGH_BMO/actions/runs/40196082613): Python 3.11·3.13 전체 회귀/pip check/compileall 및 두 공개 headless checker **성공**.
- 최종 문서 제출 HEAD·원격 동기화·그 HEAD의 CI는 PR #18 본문의 제출 검증 항목에도 기록한다. 보고서는 자기 커밋 SHA를 사전 계산하지 않는다.
- [실행·설치·복구 가이드](../../software/DESKTOP_APP_KR.md)에 남은 수동 검수를 한 절차로 정리했다.

Issue #17의 수동 통합 GPT/Fish 실기 성공은 **사용자 보고**로 보존했다. 이번 Desktop
App의 실물 검수 결과로 전환하거나 해당 관측을 새로 실행한 것으로 보고하지 않는다.
관련 경로의 AGENTS.md를 확인했으며 적용 파일은 없었다. 기존 작업서·보존 조건과
Issue #17을 기준으로 수행했다.

## 구현

| 구간 | 구현 및 경계 |
|---|---|
| 진입점 | 새 `launch-desktop` → `desktop_app.main`; terminal/stdin 없는 앱 루프. 기존 launch-features/launch-bmo는 보존 |
| 제품 화면 | 별도 desktop_web 외부 shell; Loading/진단/숨김 메뉴. 정상 기본 화면은 R10 iframe뿐이며 개발 버튼·가상 패드·모의 상태 없음 |
| 준비 확인 | loopback server/engine/browser/iframe 상태 분리, SHA·run ID·BMOFace API readiness를 검증한 POST 후만 인사. GET polling을 준비 승인으로 취급하지 않음 |
| UI 수명주기 | 800×480 전체 iframe, 원본 scale controller 한 번. shell refresh·iframe 단독 reload·모드 복귀로 인사 재실행 안 함 |
| 개발 모드 | --developer opt-in일 때만 /developer와 Ctrl+Alt+D. 기본은 404. --windowed 별도 지원 |
| 사용자 메뉴 | Esc/왼쪽 위 길게 누르기로 듣기·repeat·stop·게임·얼굴·종료. 실제 물리 버튼 맵 미연결 |
| 시작 인사 | 기존 IntegrationVoice._play/Python player를 재사용, 단일 AudioFocus. 인사 전용 cache·cue·표정 projection이며 brain/history/답변 repeat cache와 분리 |
| 비용·승인 | 정상 실행은 승인 cache만 읽음. 별도 live prepare 1회 → cached audition → WAV SHA 청취 승인. 없음·손상·음색 변경·미승인은 진단+얼굴, 자동 API/Piper 호출 없음 |
| private cache | 0700 디렉터리/0600 단일 bundle, voice/text/gain/version fingerprint+WAV/cue SHA, bounded 검증·원자 교체·재생 snapshot; synthetic live 거부 |
| 실제 오디오 준비 | 기존 USB mic/MAX98357A 기기 기본·Luna low/Aaron Soft/beemo/.25/320 유지, 원본 Piper opt-in·4.1 mini 복귀 보존 |
| 브라우저 | Wayland --app/--kiosk/--start-fullscreen, 전용 0700 profile+HOME, 키 없는 env allowlist, mute/browser audio 금지. 기존 Chromium profile·창과 분리 |
| 설치 준비 | 기본 dry-run; 명시 --install-desktop만 새 사용자 entry/icon 생성. Terminal=false, generic 문자 BMO SVG; 원본 art 사용 안 함 |
| 보안 | 기존 exact Host/Origin/token·8KiB bounded JSON·중복/shape 검사 재사용. private path/key/임의 명령 endpoint 추가 없음. 앱 stdout에 URL/token을 출력하지 않음 |
| 키 | 기존 credentials.json resolver만 공유 lock 뒤 live에서 사용. 새 저장소·자동 복사·키 argv 없음. help/status/dry-run/simulate/CI는 실제 비밀 IO 0 |
| 종료·복구 | 앱 소유 자원만 정리, 고정 시간 제한·에러 코드·무자동 재시작. 공유 lock이 중복 클릭과 기존 앱 중복을 차단 |

Chromium 플래그와 profile 분리 근거는 [공식 Ozone 문서](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/docs/ozone_overview.md),
[공식 switches 정의](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/chrome/common/chrome_switches.h),
[공식 user-data-dir 문서](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/docs/user_data_dir.md)를 확인했다.
플래그 구성과 headless DOM 검증은 실제 labwc/VNC에서 OS 패널이 사라지는지의 관측을 대신하지 않는다.

## 검증 과정에서 수정한 결함

- 인사 Thread.start 실패 시 lease와 미시작 thread가 남던 경계: rollback 및 join 실패에도 모든 다른 소유 자원을 정리하도록 수정했다.
- 새 런처가 기본 ALSA 기기로 바뀌던 경계: 기존 USB mic/MAX98357A 명시 기기를 복원하고 override·정책 회귀를 추가했다.
- ready/retry와 사용자 stop/cancel 경쟁: 같은 admission lock과 sticky cancel, worker 검사로 우선순위를 보장했다.
- timeout 뒤 늦은 완료와 숨김 재연결: 최초 실패 원인 보존, 실제 started 사실 보존, 새 heartbeat일 때만 controls 복구·인사 무재생을 검사했다.
- 인사 뒤 이전 대화 snapshot이 retired로 거부될 수 있는 경계: presentation epoch만 새로 발급하며 실제 engine/history/cache는 변경하지 않았다.
- iframe만 reload하면 닫힌 face adapter가 남는 경계: 외부 wrapper가 재생성하고 준비된 iframe에도 메뉴 입력을 한 번 연결한다.
- 브라우저 mark_ready 직전 종료: False이면 인사·player를 시작하지 않는다.
- 정상 아이콘이 터미널도 열던 설정: 새 entry/template을 Terminal=false로 바꾸었다.
- 부모 Chromium이 먼저 종료되면 자식이 남던 경계: 새 무해한 프로세스 그룹으로 재현 후 waitid(WNOWAIT)로 PID를 유지하며 소유 PGID/SID를 확인하고 TERM→KILL→부모 회수→profile 정리 순서로 수정했다. 판정 불가·timeout이면 profile 보존/실패이며 다른 그룹을 추측해 종료하지 않는다. bounded /proc stat 메타데이터만 사용하고 cmdline/env를 읽지 않는다.
- CI의 모의 시간 파일 경합: truncate/write 중 빈 값을 읽어 test server가 종료되던 fixture를 임시 파일+원자 교체로 수정하고 동시 읽기 회귀로 확인했다. 기대 동작을 약화하지 않았다.

## 실제 실행한 검사

| 검사 | 결과 |
|---|---|
| 전체 unittest | **886/886 PASS**, skip 0, 69.986초. 기존 795개 보존 + 새 91개 |
| 개발 의존성/문법 | 기존 venv 재사용; pip check PASS, compileall PASS. Pi OS 패키지 설치 0 |
| 인사 cache/player | 19 PASS: 누락·손상·승인·fingerprint·gain·cue·synthesis 실패·취소·원자 저장·대화 cache 분리 |
| Desktop browser | 20 PASS: flags/profile/env·시작/timeout·소유 group·부모 먼저 종료·TERM 무시 자식·실패 보존·reap 이후 추가 signal 0 |
| Desktop installer | 16 PASS: dry-run·동일/충돌·경로/권한·금지 autostart 경로·원자 게시·rollback·generic icon |
| 앱 mock E2E | 20 PASS: dry-run desktop Exec를 파싱해 실제 wrapper/Host/Server와 무해한 fake browser child 실행 |
| 수명주기 실패 | 11 PASS: stop/ready/retry 경쟁·thread/join·timeout/late·first cause·disconnect 복구·개인 오류 code 세척 |
| CLI | 5 PASS: help/status/dry-run secret 0·ALSA/voice 기본·opt-in 비용/설치 경계 |
| 제품 protected headless | 공개 합성 fixture 6 + 승인 R10 6 + v13.2 6 = **18/18 PASS**, 각 800×480 |
| 기존 shell 보존 headless | 기존 통합 shell R10 6 + v13.2 6 = **12/12 PASS**, 기본 개발 UI 회귀 보존 |
| 공개 CI | 원본 작화 없는 synthetic API fixture만 사용. 기존 shell checker + 새 Desktop checker 순차 실행 |

Desktop headless 각 profile에서 host=1, fake browser=1/reaped=1, 인사 play/started/ended=각1,
GPT mock=1, 대화 합성 mock=1, player=3/reaped=3, 최대 동시 player=1, forbidden=0을
관측했다. 인사 직후 history=0·답변 cache=False이며 이후 대화와 repeat가 같은 기존
brain/cache를 사용했다. 브라우저 HTMLMediaElement 및 face.playAudio/playBlob 호출은 0이다.
이는 실제 한국어 음성 품질·유료 API·네트워크 지연·장치 성능 측정이 아니다.

재생 시간 관측을 위해 합성 단계 지연을 넣었고 실제 음원/모델이 아닌 sine PCM을
사용했다. 임시 HOME의 캐시 재사용·cache corrupt·Fish unavailable 모의·ready 중복·
전체 refresh/iframe reload·게임 목록 왕복·종료·engine/browser failure를 검사했다.
소유 그룹의 실제 무해한 부모/자식 회수 검증은 테스트 전용 하위 프로세스에서만 했으며
앱이나 OS에 전역 subreaper/서비스 설정을 설치하지 않았다.

## 보존과 실제 미실행

| 대상 | 확인 |
|---|---|
| R10 원본 | 196987 bytes, SHA 8c3ef98ba0e0645213c95295a24d7353fc2865b5ad9bc85390360c4ec9b78c82, headless 전후 동일 |
| v13.2 원본 | 159892 bytes, SHA 38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932, headless 전후 동일 |
| BMO_EMOTION | HEAD 174dd974fdf119fc3d24a5cda0870ae70c007cf6, tracked 157개, Git clean |
| BMO_INTEGRATION | HEAD 902b09739844ab4c78ea6234851b7bfb9cdb7634, tracked 128개, Git clean |
| Piper 원본 | 기존 모델/JSON/binary의 알려진 SHA 세 개 모두 일치, 실제 실행 0 |
| PR #13/#14/#16, main | 기존 HEAD를 보존하며 merge/force push 0. 이 후속 PR만 변경 |

승인 원본 HTML/SVG를 Git에 올리지 않았다. 새 아이콘은 직접 작성한 generic 문자 SVG다.
실제 credentials.json/.env/개인 로그를 읽거나 출력하지 않았다. 팬·SSH·VNC·Tailscale·
전원·OS/Wayland 세션·펌웨어·서보·다른 프로그램·기존 백업은 변경하지 않았다.

실제 Desktop entry/icon 설치·trust/더블클릭, interactive Chromium, labwc/VNC/LCD
fullscreen/OS 패널, 한국어 Fish Aaron 발음·음색·음량 청취/승인, 실제 GPT/Fish/STT·
마이크·스피커·카메라·팔·IMU·pad·serial은 이번 작업에서 **NOT_RUN**이다.
코드/Mock/CI PASS를 실물 PASS로 표시하지 않는다. 강제 SIGKILL/전원 상실 및 실제
Chromium/드라이버 hang은 실물 검수 대상이며 확인 실패를 성공으로 숨기지 않는다.

## 커밋과 안전한 복구

- f17db666cf5de819a5a4affbe148ae8ed60a6361: private 승인 인사 cache, 전용 browser/installer.
- fd4607b0e34df0a62c1e9749d2d1d33776318a80: 제품 wrapper·ready/greeting·단일 앱·E2E/CI 연결.
- e097f65b3de7f61da99f9e82dde5279b3a9495aa: 터미널 없는 새 desktop entry.
- 3f5945d063ee2c04cd0a54e80ea9a5f4d1721eef: 소유 자식 그룹 회수·진단/재연결·CI 시계 경합 보강.

[PR Files changed](https://github.com/GUNOBAE/BGH_BMO/pull/18/files)에 변경 범위를 남긴다.
새 source/tests/desktop assets/문서와 선택적 FeatureServer product route, CI만 변경했다.
기존 테스트를 삭제하거나 실패 기대값을 느슨하게 바꾸지 않았다. mock startup 오류의
nonzero exit와 원자적 시계 fixture, 새 entry의 Terminal=false 계약을 정확히 검사한다.

메뉴 종료/개발 창 닫기로 새 앱의 자원만 정리하고, clean을 확인한 뒤 기존
feature/bmo-offline-integration-v2 또는 보존된 수동 런처를 입회 승인 범위에서 선택하면
된다. 기존 사용자 파일과 backup을 삭제하는 uninstall·hard reset·clean·force-push·
main merge는 실행하지 않았다. 사용자 홈 설치/음성 준비/청취/승인의 남은 단계는
설치 가이드 한 곳에서 확인한다. 실제 설치를 이 보고서 업로드와 함께 자동 실행하지 않는다.
