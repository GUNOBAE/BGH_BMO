# BMO 실제 로봇 milestone — 사용자 입회 실행 순서

대상은 `/home/bgh/BMO_REAL_ROBOT`의 `feature/bmo-real-robot-milestone-v1`이다.
기존 PR #27의 `0287bb23c1fafc357c9a18419f62b2ebdf0de767`에서 분리한 새 작업 경로다.
기존 BMO_NIGHTLY/BMO_FEATURES, 원본 R10, Aaron/Piper, 개인 키, 펌웨어와 서비스는 보존한다.
정확한 변경 SHA·최종 검사 수·CI는 [이번 milestone 보고서](../docs/reports/BMO_REAL_ROBOT_MILESTONE_V1_KR.md)를 따른다.

**이 문서의 실제 실행 명령은 사용자가 Pi 옆에서 검수할 때의 절차다. 자동 개발 중 실제
마이크·스피커·카메라·서보·유료 API를 실행했다는 뜻이 아니다.** 카메라와 팔의 코드 통합 승인은
보존하지만, 실제 팔 실행은 검증된 프로토콜이 없어 현재 BLOCKED다. 그 때문에 대화·화면·카메라
작업까지 막지 않는다.

## 1. 먼저 확인할 것

Pi의 기존 `bgh` 그래픽 세션에서 터미널을 연다. VNC도 같은 로그인된 Wayland/labwc 세션을
사용한다. 다른 사용자·SSH 셸에서 임의 DISPLAY를 추측하지 않는다. 이미 켜진 BMO는 사용자가
그 앱의 메뉴로 정상 종료하고 아래 검사를 한다. 새 앱은 기존 공유 사용자 lock을 존중하며,
중복 실행이면 다른 프로세스를 종료하지 않고 거부한다.

```bash
cd /home/bgh/BMO_REAL_ROBOT
./launch-desktop --diagnose-startup
./launch-desktop --status
```

첫 명령은 세션/runtime 소유권·권한, 실행 파일, 원본 얼굴 SHA 및 lock metadata만 확인한다.
키·장치·카메라를 열거나 프로세스·포트·파일을 생성하지 않는다. `screen=ready`,
`screen_sha256_matches=true`, `chromium=ready`, 유효한 Wayland와 `runtime_directory=ready`를
확인한다. lock의 `occupied`는 실행 중인 앱을 지울 이유가 아니다.

`runtime_directory_permissions` 또는 `runtime_metadata.mode=0770`이면 **여기서 GUI 실행만
중단**한다. 자동 chmod나 OS/VNC/systemd 수정은 하지 않는다. 누가 권한을 바꾸는지는
`mode_change_actor=UNKNOWN`이다. 현재 소유권·mode와 재로그인 전후 시점, 사용자 세션 재현
조건만 기록해 별도 원인 조사 대상으로 넘긴다. 이 문서는 검수 없이 권한을 바꾸는 명령을
제공하지 않는다. 비밀정보·전체 환경·세션 원문 로그를 공개 이슈에 올리지 않는다.

런처는 기본 `.venv/bin/python`을 사용하며 `BMO_PYTHON`으로 새 작업 경로의 선택 interpreter만
명시할 수 있다. 기존 프로젝트의 가상환경·실행 파일을 변경하지 않는다. 일반 대화·테스트용
환경은 카메라 패키지와 독립적이다. 카메라 환경의 준비는 6절에서 따로 결정한다.

## 2. 키와 시작 인사는 최초 한 번만 준비

기존 비공개 OpenAI/Fish 저장소가 정상이라면 키를 다시 입력하지 않는다. 키가 없는 것으로
진단된 경우에만 사용자가 기존 숨김 입력 절차를 실행한다.

```bash
./launch-bmo --setup-credentials
```

이 명령은 명시적인 사용자 설정이다. 키를 CLI 인자·쉘 이력·스크린샷·보고서에 붙여넣지 않는다.
일반 Desktop 실행은 비대화형으로 저장된 키를 읽으며 매 실행 입력창을 띄우지 않는다.

환영 문장은 영어 `Oh! You're back! BMO missed you!`다. Fish 발화 경계에서는 BMO를 Beemo로 발음한다. 앱이 종료된 상태에서 아래 준비를 **사용자가
비용을 승인하고 직접 실행할 때만** 진행한다. 이미 승인된 welcome-v2 캐시가 정상이면
prepare를 반복하지 않는다. prepare는 Fish 한 번을 요청하는 작업이고 audition은 저장된
오디오를 실제 스피커에서 한 번 재생한다.

```bash
./launch-desktop --live --prepare-greeting
./launch-desktop --live --audition-greeting
./launch-desktop --live --approve-greeting WAV_SHA256_FROM_PREPARATION
```

마지막 명령의 값은 prepare 출력의 SHA-256을 사용자가 청취 후 확인해 넣는다. Aaron 음색·한국어
발음·음량과 잡음을 확인한다. 청취가 자동 승인이 되지 않는다. 캐시는 private welcome-en-v1이며
예전 `난 비모야~!` 캐시는 보존한다. 일반 앱 시작은 캐시를 읽을 뿐 자동 유료 합성하지 않는다.
캐시가 없거나 미승인이면 얼굴을 표시하고 인사 상태를 알려 주므로 대화 검수는 계속할 수 있다.

## 3. 첫 실기 명령: 카메라·팔 없이 GP8 대화부터

사전 점검이 통과하면 아래 **한 명령**으로 기본 전체 화면 앱을 시작한다. 실제 입력·출력과
각 대화의 유료 STT/AI/Fish 호출을 허용하므로 사용자가 준비된 상태에서 실행한다.

```bash
./launch-desktop --live --ptt-microphone --ptt-trace
```

이 명령이 제품 진입점이다. 사용자 URL 입력이나 개발 웹 페이지를 거치지 않는다. 내부 구조는
기존 Python AI 엔진 + 보호된 로컬 loopback 어댑터 + 앱이 소유한 전용 Chromium kiosk 창이다.
네이티브 Qt로 재작성한 앱이라고 설명하지 않는다. `--windowed`는 개발 창이 필요할 때만
선택하며 기본 제품 화면에 주소창·탭·개발 메뉴·가상 패드는 없어야 한다.

사용자는 다음 순서로 확인한다.

1. 긴 네모 Loading 바가 실제 로컬 준비 단계에 따라 0→100으로 진행한다. 브라우저 등록·엔진·
   얼굴 handshake가 완료돼야 끝나며, 100%는 유료 API 건강 검사 성공을 뜻하지 않는다.
2. 원본 R10이 800×480 전체 화면에서 잘리지 않고 표시된다. SVG·애니메이션·원본 배율을 변경하지
   않았는지 확인한다. labwc/VNC 실제 패널·스케일 검수는 headless 검사와 별도로 기록한다.
3. 승인된 인사가 있으면 실제 목소리·입모양과 함께 한 번 재생한다. 얼굴 복귀·새로고침·메뉴
   왕복으로 인사를 반복하지 않는다. 캐시가 없으면 준비 상태를 확인하고 얼굴로 계속한다.
4. **초록 GP8을 짧게 한 번 누르고 손을 뗀 뒤** 짧게 말한다. 기본은 상시 녹음이 아니라 1회
   버튼 녹음이다. 키를 뗐다는 이유로 즉시 취소하지 않아야 한다. 말하기가 끝나면 약 0.9초
   무음 후 자동 종료한다. 안전 상한은 12초다. 다시 누르기는 입력 종료/출력 취소 용도다.
5. 듣는 표정만 보고 마이크 성공이라고 판단하지 않는다. 실제 `microphone_open`, 첫 frame,
   speech 감지, STT, AI, Fish, playback 순서와 답변을 확인한다.
6. 동일한 동작을 일반 키보드 Enter로 비교한다. GP8의 실제 장치 구분은 HID Enter만으로
   확정하지 않으며 입력 증거에 `physical_identity=UNVERIFIED`가 남는다. GP7 빨간 버튼은
   기존 양팔 토글이므로 누르거나 TALK로 재매핑하지 않는다.
7. 말이 없으면 no_speech 안내와 재시도가 가능해야 하고 STT·AI·Fish 요청이 없어야 한다.
   게임 중 Enter는 게임/선택 동작을 보존하며 대화를 시작하지 않는다.

메뉴는 Esc 또는 얼굴 왼쪽 위 영역 1.2초 길게 누르기로 연다. **준비 상태**에서 원문 없는
`turn-diagnostics`와 Enter 입력 카운터를 확인한다. 메뉴를 여는 동작은 진행 중 입력을 취소할
수 있으므로, 대화가 끝난 뒤 진단을 읽는다. `--ptt-trace`는 opt-in이며 raw PCM/WAV·대사·
인물 정보·키·provider 예외 본문을 기록하지 않는다. 종료 후 사용자 음성 파일을 보관하지 않는다.

## 4. 답변이 없을 때 확인할 단계

아래 정보만 이슈에 적는다: 변경 SHA, 실행 옵션의 이름, 실패 stage/code, bounded RMS/시간,
입력 카운터, 정리 확인 여부. 명령행 전체 환경·키·개인 대사·음성/사진을 첨부하지 않는다.

| 관측 | 해석과 다음 검수 |
|---|---|
| `enter_presses`가 오르지 않음 | 포커스·iframe 전달·실제 HID 경로를 비교한다. 일반 Enter와 GP8을 구분해 기록 |
| `request_sent`만 증가, accepted 없음 | 앱 연결/요청 응답과 mode 확인. 게임·dialog에서 대화가 차단되는 것은 정상 |
| admission/audio_focus_busy | 인사·게임·음악·영상·이전 worker의 소유권이 남았는지 확인. 강제 중복 시작 금지 |
| microphone 미open/첫 frame 없음 | 도구 부재, 시작 실패, 장치/ALSA format 오류를 code로 구분. 얼굴 애니메이션은 성공 증거 아님 |
| `pcm_audio_format_failed` | 16kHz/S16_LE/mono 입력 계약을 사용자 감독하에 확인. 설정을 자동 바꾸지 않음 |
| `waiting_speech`/`no_speech` | 입력 수준·threshold·ambient 검증 여부 확인. 말이 없으면 유료 STT를 보내지 않음 |
| `calibration_unverified` | 주변 소음을 별도로 검증하지 않았다는 뜻. 첫 음성을 소음으로 학습해 버리지 않음 |
| `stt_empty`/전사 오류 | 캡처는 있었으나 STT가 빈 결과/실패. AI/Fish로 넘어가지 않아야 함 |
| ai stage 오류 | 현재 모델·인증·요청/연결/제한 code 확인. 원문/SDK 예외를 출력하지 않음 |
| fish stage 오류 | 합성 인증·잔액·속도 제한·네트워크·WAV 문제 확인. 자동 다른 성우/유료 재시도 없음 |
| playback 오류 | 합성과 실제 재생을 구분. 같은 소유 player의 시작/종료·speaker 장치·회수 확인 |
| `pcm_cleanup_pending`/close unknown | 새 녹음/새 앱을 연속 실행하지 않고 정상 종료 후 사용자 점검 |

이전 GP8 고장의 실물 원인은 한 가지로 확정하지 않았다. 이번에는 첫 발화 보존/preroll,
keyup 취소 방지, 포커스/반복 입력 처리와 단계별 실패 증거를 코드와 합성 검사로 확인한다.
**실제 GP8 녹음→답변 성공 여부는 이 절차에서 별도로 판정**한다.

주변 소음을 검증하려면 대화 앱을 먼저 종료한다. 팬은 평소대로 두고 사용자가 약 0.8초 조용히
기다리는 별도 녹음을 명시적으로 실행한다. 이 명령은 실제 마이크를 사용하지만 PCM/WAV·
STT·API는 저장/호출하지 않는다. 최대 2초의 유한 source이며 출력은 통계만 private 파일에 쓴다.

```bash
PYTHONPATH=software ./.venv/bin/python -B -m bmo_ai.ptt_diagnostics \
  --calibrate --supervised --output "$HOME/.config/bmo/ambient-usb-mic-v1.json"
./launch-desktop --live --ptt-microphone --ptt-trace \
  --ptt-ambient-profile "$HOME/.config/bmo/ambient-usb-mic-v1.json"
```

기존 profile은 덮어쓰지 않는다. 잡음/장치가 달라졌다면 새 이름으로 별도 검수하며, 다른 장치
fingerprint·위험한 권한·과도한 RMS이면 profile을 거부한다. 수동 threshold를 무작정 높이거나
팬을 끄는 방식으로 문제를 가리지 않는다.

## 5. 감정 있는 목소리·웃음·울음 청취

기본 제품 경로는 검증된 emotion과 host allowlist를 Fish S2.1 direction으로 연결한다. 얼굴
emotion만 바뀌었다고 실제 운율이 합격한 것은 아니다. Aaron Soft / Beemo / gain 0.25는
그대로다. 웃음은 happy, gasp는 surprised, 짧은 장난/울음은 사용자가 요청한 문맥에서만
허용하며 모든 문장에 붙이지 않는다. 반복은 실제 직전 WAV와 표정/입모양을 보존한다.

[VOICE_REAL_KR.md](VOICE_REAL_KR.md)의 여섯 고정 샘플을 neutral과 비교해 실제 청취·SHA 승인한다.
명시적 `--prepare SAMPLE --allow-paid-api`만 합성하며 캐시가 정상이면 재사용한다.
실제 청취 전 자산은 ASSET_PENDING/USER_APPROVAL이다. tag가 읽히거나 음색/감정이 과하면
승인하지 않고 다음 기본 음성 경로로 되돌린다.

```bash
./launch-desktop --live --ptt-microphone --ptt-trace --plain-voice
```

Piper는 원본을 보존하며 기존 `--offline-voice` 명시 opt-in만 사용한다. 자동 성우 교체·WAV
pitch shift·임의 SSML·다른 사람 voice clone을 추가하지 않는다.

## 6. 사용자 감독하의 실제 카메라 사진 한 장

먼저 대화/종료 검수에 통과한다. 카메라 모듈 검색 결과가 있다고 센서 동작을 주장하지 않는다.
Python 별로 Picamera2/libcamera 가시성이 다르므로 **실제로 launch에 쓸 interpreter**로 확인한다.

```bash
PYTHONPATH=software ./.venv/bin/python -B -c \
  'from bmo_ai.features.camera.live import probe_camera_capability; print(probe_camera_capability())'
```

이는 `find_spec` 정보만 읽으며 카메라 import·생성·센서 목록 조회·open을 하지 않는다.
`available=False`면 해당 환경만 unavailable로 기록한다. 시스템 Python에 모듈이 있다는 사실로
이 가상환경도 가능하다고 처리하지 않는다. 기존 시스템 패키지 의존성 문제를 해결하려고
OS 패키지를 설치·삭제하거나 다른 프로그램을 고치지 않는다.

사용자가 기존 시스템의 카메라 모듈을 별도 Python 환경에 노출하기로 선택한 경우에만 새
`.venv-camera`를 만든다. 아래는 사용자 선택 설치 절차이며 자동 작업에서 OS 변경을 하지 않는다.
기존 `.venv`/다른 checkout에 덮어쓰지 않는다. 시스템의 관련 모듈이 없다면 여기서 해당 기능만
BLOCKED로 남기고 별도 패키지 준비 검토를 한다.

```bash
python3 -m venv --system-site-packages .venv-camera
./.venv-camera/bin/python -m pip install -r software/requirements-lock.txt
PYTHONPATH=software ./.venv-camera/bin/python -B -c \
  'from bmo_ai.features.camera.live import probe_camera_capability; print(probe_camera_capability())'
```

다음은 사용자가 사진 저장 위치를 확인하고 카메라 앞의 사생활·케이블·렌즈 상태를 확인한 뒤
직접 실행한다. 예제는 새 private 사진 경로이며 앱은 기존 사진을 지우지 않는다.

```bash
BMO_PYTHON="$PWD/.venv-camera/bin/python" ./launch-desktop \
  --live --ptt-microphone --ptt-trace --camera-supervised \
  --photo-directory "$HOME/BMO_private/photos-real-v1"
```

앱 구성만으로 센서를 열지 않는다. 메뉴 **사진·시선 → 사진 촬영**을 사용자가 눌러 한 장을
요청한다. 같은 broker가 사진·preview·추적·영상 lease를 관리한다. 사진의 실제 화질·색·AF와
결과 `simulated=false / executed=true / completed=true`를 확인한다. 로그·Git에 사진을 올리지
않고 저장 SHA/성공 code만 남긴다. 사진은 로컬 private 파일이며 자동 업로드·얼굴 신원 인식은 없다.

preview/얼굴 위치 시선은 별도 버튼을 누를 때만 시작한다. 눈동자 화면 목표용이며 서보/IMU
제어가 아니다. tracking 모듈이 없으면 추적만 unavailable이고 사진을 무조건 막지 않는다.
카메라 끄기·취소·앱 종료 뒤 같은 센서를 다시 열 수 있는지는 실기 검수한다. SDK native
stop/close가 멈추면 hard deadline/실물 해제를 보장하지 않으므로 UNKNOWN을 성공으로 바꾸지 않는다.

사진 합격 후에만 별도 짧은 무음 영상을 검수한다. 먼저 preview·추적을 끄고 대화를 끝낸다.
앱을 정상 종료한 뒤 영상 경로도 명시하여 다시 시작한다.

```bash
BMO_PYTHON="$PWD/.venv-camera/bin/python" ./launch-desktop \
  --live --ptt-microphone --ptt-trace --camera-supervised \
  --photo-directory "$HOME/BMO_private/photos-real-v1" \
  --camera-video-supervised --video-directory "$HOME/BMO_private/videos-real-v1"
```

메뉴 **별도 영상 녹화**에서 저장 동의를 체크하고 사용자가 시작·종료한다. 기본 2fps의 짧은
무음 샘플 영상이며 H264/고속 녹화라고 설명하지 않는다. 자세한 시간/크기·private 저장·정리
제약은 [CAMERA_REAL_KR.md](CAMERA_REAL_KR.md)를 따른다. 사진/영상/추적은 한 센서 소유권을
공유하므로 사용 중 충돌이 거부되는지 확인한다. 실패 후 자동 재촬영이나 파일 삭제는 하지 않는다.

## 7. 팔은 현재 BLOCKED: 우회 실행 금지

읽기 전용으로 확인한 현 펌웨어에는 GP8 HID Enter, GP7 양팔 토글과 부팅 시 팔 설정이 있다.
검증된 host 개별 팔 프로토콜·watchdog·hard STOP이 없다. 제안 JSON wire codec을 실제 펌웨어
API로 간주하지 않는다. 따라서 이 milestone에는 사용자용 실제 팔 이동 명령을 제공하지 않는다.
체크리스트 flag를 모두 True로 해도 live 실행은 열리지 않는다.

다음 별도 입회 작업은 전원 무결성/독립 서보 전원/공통 GND/전류 제한/물리 차단 스위치,
좌우 기계 범위·속도·간섭 한계, 부팅 무동작, 검수된 명령/ACK/중단 규약부터 확정해야 한다.
그 뒤 사용자 감독하에 한 팔의 작은 저속 수동 범위를 검수한다. 포트 open/DTR/CDC REPL,
reset·펌웨어 재실행·GP7 토글을 조회나 안전 시험으로 우회하지 않는다. 기존 firmware/HID는
보존하며 IMU를 자세·팔 제어 또는 안전 판단에 사용하지 않는다.

## 8. 종료·재진입 검수 후 아이콘 설치

메뉴의 **종료** 또는 이 앱을 시작한 터미널의 Ctrl+C로 정상 종료한다. 앱이 소유한 Chromium
group·player·로컬 서버·worker만 정리한다. 다른 Chromium/SSH/VNC/팬/Ollama/서비스를 건드리지
않는다. `BMO desktop cleanup:`의 `resources_cleaned`와 자원별 `released/not_acquired/unknown`,
고정 code·소유권 stage를 확인한다. exit0·창 소멸·lock 해제만으로 실물 자원 해제를 주장하지 않는다.
`physical_release_verified=false`는 실제 장치 반환을 별도 검수해야 한다는 뜻이다.

정상 종료가 확인된 뒤 같은 명령으로 재진입한다. 이전 실패/worker/카메라/인사를 중복 취득하지
않아야 한다. unknown이면 연속 실행을 멈추고 해당 정리 단계만 기록한다. `killall`/광역 kill,
세션 재시작이나 잠금 파일 삭제를 복구 단계로 사용하지 않는다.

터미널 실행이 합격하면 사용자가 설치 계획의 경로/Exec를 확인한 뒤 바탕화면 아이콘을 설치한다.
이번 자동 개발에서는 실제 설치하지 않는다.

```bash
./launch-desktop --install-desktop --ptt-microphone --dry-run
./launch-desktop --install-desktop --ptt-microphone
```

이 아이콘은 새 checkout의 `launch-desktop --live --ptt-microphone`을 실행하며 autostart를
등록하지 않는다. 기본 R10 kiosk·감정 음성과 기존 승인된 인사 캐시를 사용한다. 기존 아이콘과
내용이 다르면 덮어쓰지 않고 중단한다. 파일 관리자 실행 허용/trust는 사용자 GUI 검수이며,
설치기가 기존 파일을 chmod하거나 VNC/OS 설정을 바꾸지 않는다. 카메라 옵션은 기본 아이콘에
넣지 않으며 위 감독 명령으로만 별도 선택한다.

복구는 새 앱을 정상 종료한 뒤 보존된 이전 checkout을 사용자가 다시 선택하거나, 검토 후 새
PR의 코드 커밋만 되돌리는 방식이다. 기존 checkout을 reset/clean하거나 원본 R10·private 키·
캐시·사진·백업을 삭제하지 않는다. 호스트 서비스/OS/펌웨어를 변경하지 않아 그 자동 복원
명령도 없다. 설치한 새 entry/icon은 설치 계획의 소유권·내용을 검토해 별도 사용자 정리한다.

## 합격 기록 표

| 항목 | 구현/자동 경계 검사 | 실제 Pi 검수 |
|---|---|---|
| GP8 녹음/VAD/STT/AI/Fish와 단계 진단 | 코드·합성/SDK double 검사 | NOT_RUN: GP8와 키보드 비교, 유료 1회 대화 필요 |
| 기본 kiosk·Loading·인사 1회·종료 | 코드·소유 child/브라우저 검사 | NOT_RUN: labwc/VNC 800×480·스피커·재진입 |
| Aaron 감정·짧은 반응음·repeat | allowlist·문맥·빈도·cache 검사 | ASSET_PENDING / USER_APPROVAL: 실제 청취 |
| Picamera2 사진/무음 영상/중단 | lazy backend·fake SDK·private 저장 검사 | NOT_RUN: 사용자 감독 사진 1장부터 |
| 양팔 host 이동 | readiness·기계 한계/enable 차단 구조 | BLOCKED: 실제 안전 프로토콜·전원/보정 미확정 |
| IMU/팬/SSH/VNC/기존 firmware | 보존, 제어 경로에 사용하지 않음 | 이번 실기 활성화 범위 밖 |

모든 하드웨어 완성을 선언하려면 위 실기 결과가 필요하다. Mock PASS나 사용자의 과거 대화
성공 보고를 이번 코드의 물리 검수 PASS로 옮겨 쓰지 않는다.
