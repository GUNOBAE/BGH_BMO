# PR #36 통합 실행본 인계 — 2026-10-28

기존 `feature/bmo-legacy-arm-original-compatible-v1`의 `af38788726d0a0d4f8b1c9e1d69549febecbb484`에서 이어서 구현했다. main에서 새 프로젝트를 만들거나 기존 작업을 초기화하지 않았다. 최종 변경 SHA와 해당 SHA의 Actions 링크는 [PR #36 완료 댓글](https://github.com/GUNOBAE/BGH_BMO/pull/36)에 기록한다. 이 문서는 소프트웨어 후보의 검증 결과이며, 모터 실기 승인을 대신하지 않는다.

## 이미 성공한 실기 기준

아래는 사용자 입회 하의 기존 성공 기록이다. 이번 자동 작업에서 장치를 재실행한 결과가 아니다.

- 서보 양쪽 커넥터 분리 상태에서 B1 `HELLO → READY`, 오른팔 명령 `ACCEPTED`, heartbeat **18/18**, `HOLD → HELD`.
- 종료 후 exact disabled 펌웨어로 복원하여 **REMOTE MOTION DISABLED** 확인.
- GP2~GP6 및 GP8 HID **6/6**, 기존 GP8 STT → GPT-6 Luna low → Fish Aaron Soft 대화 성공.
- 승인 R10 얼굴 및 Camera 3 Wide 실제 사진·무음 2fps AVI 영상 성공.

원본 펌웨어·백업·live boot.py·보정값을 변경하지 않았다. 이번 변경은 `firmware/` 전체에 diff가 없다. 새로운 백업이나 보정, CDC/HID 재검사를 요구하지 않는다.

## 실제 구현한 변경

| 파일 | 변경과 의미 |
|---|---|
| `software/bmo_ai/features/motion/legacy_host.py` | 기존 B1에 발화 시작 동기·TTL·turn당 1회·중복 차단을 유지하면서, DO ACK 대기 중 취소/새 turn/만료 경합을 처리한다. 이미 승인된 오래된 동작은 HOLD하고 재시작하지 않는다. ACK 유실은 unknown이며 DO를 재전송하지 않는다. 대기·활성 요청의 취소/예산 종료 결과와 포트 정리를 추적한다. |
| `software/bmo_ai/features/host.py` | 기존 GPT/context → allowlisted gesture → 실제 playback-start 경로를 사용한다. 장식용 gesture 실패가 대화를 종료시키지 않으며 거절 결과를 장부에 남긴다. 팔 정리 실패를 앱 정리 완료로 오인하지 않는다. 기존 카메라 경로를 그대로 사용하고 음악·게임패드를 같은 ModeManager/AudioFocus/InputRouter에 연결한다. |
| `software/bmo_ai/desktop_app.py`, `desktop_browser.py`, 신규 `desktop_native_window.py` | 기존 Desktop 진입점에 `--frontend auto/native/chromium`을 통합한다. auto는 선택적 시스템 Qt의 import 가능 여부를 제한된 시간에 조사하고 없으면 Chromium을 사용한다. Qt는 별도 소유 프로세스·익명 profile·localhost 전용 창이며 팝업/외부 탐색/브라우저 오디오·장치 권한을 차단한다. 기존 프로세스 그룹·profile 정리 절차를 재사용한다. |
| `software/bmo_ai/greeting.py`, 신규 `startup_sound.py`, `desktop_runtime.py` | 검증된 로딩 checkpoint → 원본 얼굴 표시 확인 → 직접 합성한 로컬 시작 효과음 → 승인된 English v3 Aaron 캐시를 한 AudioFocus에서 순서대로 재생한다. 시작음은 앱당 한 번이다. 자동 Fish 합성은 없다. 인사 재시도도 캐시만 사용한다. |
| `software/bmo_ai/desktop_install.py` | 선택한 frontend·GP8·게임패드·음악·감독 카메라 경로를 아이콘 명령에 보존한다. dry-run은 파일을 쓰지 않으며 기존 아이콘을 덮어쓰지 않는다. 팔 활성화 플래그는 아이콘에 저장하지 않는다. |
| `software/bmo_ai/features/runtime.py`, 신규 `features/input/gamepad.py`, `desktop_web/desktop.js`, `desktop_web/index.html` | 음악은 `--music-supervised`와 검토된 WAV library가 함께 있을 때만 기존 ALSA adapter를 쓴다. 기본은 denied이다. `--usb-gamepad`는 표준 브라우저 매핑을 게임/게임 목록에서만 전달하며 GP8·서보·카메라·유료 API를 호출하지 않는다. 모드·세대·순서·TTL·watchdog·키 release를 검증한다. |
| `software/checks/check_bmo_release_e2e.py`, 기존 Desktop/카메라 browser checks, `.github/workflows/software-tests.yml` | 한 진입점으로 실제 Desktop → 원본 얼굴 → GP8 → 합성 PCM/VAD → 주입 STT/GPT/voice → 실제 B1 host/controller 코드 → repeat/cancel/STOP/종료 정리를 검사한다. Actions에서도 실행한다. 기존 카메라·게임·음악 E2E를 재사용한다. |

PR #29 `039bcf834f0edb4f523e93ba31b05c13b40469c3`, PR #32 `ab9aef94c9241b1693d9f5ec99844f9a910c0aa9`의 native/startup 구현을 비교하여 빠진 기능만 현재 수명주기에 맞게 이식했다. 오래된 브랜치를 통째로 병합하지 않았고 V2 펌웨어/통신을 새로 만들지 않았다.

기존 `emotion_gestures.py`와 GPT의 structured reply 정책은 이미 wave/celebrate/shrug를 선택하므로 재분류 API를 만들지 않았다. listening/thinking/idle/반복 재생에서 새 제스처를 만들지 않는다. 기존 90°/s·20ms·최대2° 소프트웨어 상한, watchdog, original GP7 수동 0↔180°/600~2400µs/좌우 반전은 보존한다. 이는 실제 위치·힘·안전성 측정값이 아니다.

## 자동 검증

아래는 최종 코드의 로컬 검증 결과다. 모든 장치/provider 경계는 대체했고, test-owned 프로세스와 loopback HTTP 외의 I/O는 금지했다. 기존 회귀를 삭제하거나 skip하지 않았다.

- 전체 Python 3.13.5 회귀: **1,327 PASS**, failure/error/skip **0**, 82.523초. [기계 판독 결과](PR36_INTEGRATED_APP_UNIT.json).
- Python 3.11/3.13 GitHub Actions: 최종 SHA의 run 결과를 PR 완료 댓글에 기록.
- public/R10/v13.2 headless: **126 PASS**. 각 profile에서 offline6 + Desktop7 + PTT12 + camera/media/gamepad10 + release7 = 42개씩이다. forbidden0, 각 원본 SHA 전후 일치. [전체 browser 검증 결과](PR36_INTEGRATED_APP_BROWSER.json).
- 새 release E2E: 시작음·영어 인사에 두 플레이어를 순차 사용하고 GP8 3회에서 `wave/shrug/celebrate` DO 총3회. repeat는 STT/GPT/synthesis/DO를 늘리지 않는다. cancel은 HOLD, STOP은 latch, 종료는 소유한 플레이어/브라우저/PCM/CDC를 회수한다.
- pip check, software/staged firmware compileall, diff check와 원본 SHA 보존: **PASS**.

새 Python 회귀 57개는 `test_b1_release_lifecycle.py` 21개, `test_desktop_release_frontend.py` 10개, `test_startup_sound.py` 7개, `test_release_feature_integration.py` 19개다. 기존 host 취소 검사에는 terminal receipt 검증을 보강했고 zero-DO/no-retry 검증을 유지했다.

브라우저 테스트의 `executed`/ACK는 주입한 in-process controller의 결과이다. 실제 Pi 서보·소리·USB·카메라의 PASS로 바꾸어 보고하지 않는다. Qt 설치·GUI/Wayland 표시는 이번 환경에서 실행하지 않았다.

## 다음 사용자의 단일 실행 명령

Pi의 기존 그래픽 세션 터미널에서 실행한다. 기존 카메라용 venv를 interpreter로 재사용하되 프로그램 소스는 이번 checkout에서 읽는다. 키는 기존 비공개 설정에서만 읽으며 명령행 인자로 전달하지 않는다. 이 명령을 이번 자동 작업에서 실행하지 않았다.

```bash
BMO_PYTHON=/home/bgh/BMO_REAL_ROBOT/.venv-camera/bin/python /home/bgh/BMO_B1_REVIEW/launch-desktop --live --frontend auto --ptt-microphone --ptt-trace --camera-supervised --photo-directory /home/bgh/Pictures/BMO --camera-video-supervised --video-directory /home/bgh/Videos/BMO --usb-gamepad
```

이 명령은 **팔을 활성화하지 않는다**. 카메라도 사용자가 사진·영상·시선 메뉴를 요청하기 전에는 열리지 않는다. 게임패드는 게임 모드에만 전달된다. 시작음/캐시 인사 후 GP8을 눌렀을 때만 녹음·대화가 시작한다. 승인된 English v3 캐시가 없으면 진단을 표시하고 API로 자동 생성하지 않는다. 선택적 Qt가 없으면 Chromium app/kiosk 창을 사용한다. Qt import가 성공해도 실제 GUI 성공을 뜻하지 않으며, 필요하면 동일 진입점의 `--frontend chromium`으로 명시적으로 선택한다.

## 최소 남은 실기 검수와 미완료

1. **앱만 검수:** 현재 disabled firmware와 서보 분리 상태를 유지한다. 위 통합 앱에서 시작음 → 승인 영어 Aaron 인사 한 번 → 원본 R10 → GP8 한 turn → 메뉴 종료 시 소유 프로세스 정리만 확인한다. 이미 성공한 CDC/HID/카메라 프로토콜 검사를 반복할 필요는 없다. 원한다면 기존 카메라 메뉴의 이번 앱 연결만 한 번 확인한다.
2. **팔 연결 시험은 BLOCKED:** 독립 물리 전원 차단 수단이 아직 없다. 서보 재연결·REMOTE_ENABLED=True 배포·실물 동작 시험을 이번 작업이 승인하지 않는다. 실제 접근 가능한 cutoff/독립 격리가 별도 검토되고 사용자가 입회한 뒤에만 원래 보정값 그대로 한 팔의 방향/속도/GP7 우선권/발화 동기/중단을 확인한다. software HOLD/STOP/PWM0은 전원 차단이 아니다.
3. **Qt·오디오·게임패드:** native Pi GUI, 새 시작음/영어 인사 청취, 실제 표준 USB pad 매핑, 새 감독 WAV 음악 경로의 출력은 **NOT_RUN**이다. Aaron 성우/Soft/Beemo/gain0.25, Luna low와 Piper 원본은 그대로다.
4. **실제 게임/음원:** 실제 게임 자료와 검증된 runtime·실제 사용자 WAV를 이번에 설치하지 않았다. 없는 게임은 `ASSET_PENDING`, runtime은 `NOT_CONFIGURED`로 남는다. 기존 `python -m bmo_ai.game_assets --dry-run`/archive 검토·설치 인터페이스, `GamesFeature`/`OwnedProcessRunner` 확장 지점과 `python -m bmo_ai.media_assets --help`/WAV manifest 경로를 제공한다. fixture 게임/무음 음악 성공을 실물 게임·음원 완성으로 보고하지 않는다. 게임별 실제 종료/입력 검증 전 READY로 등록하지 않는다.

## 복구

이번 pass는 설치·서비스·live 펌웨어·원본 얼굴·키·기존 checkout을 변경하지 않았다. 새 앱을 메뉴에서 정상 종료한 후 기존에 사용하던 launcher로 돌아가면 된다. 코드만 과거 버전으로 확인해야 하면 현재 checkout을 reset하지 말고 별도 새 경로에 `af38788`을 checkout한다. 기존 BMO_B1_FIELD/BMO_REAL_ROBOT/BMO_ARM_STAGE와 비공개 백업은 보존한다. 새 시작음 캐시는 원본 인사·대화 캐시/Piper와 별도 경로이며 덮어쓰지 않는다. main 병합/force push는 하지 않았다.
