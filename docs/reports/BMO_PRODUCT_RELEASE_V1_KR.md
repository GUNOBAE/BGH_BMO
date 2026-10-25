# BMO Product Release v1 — 실기 피드백 반영 작업 기록

- 2026-10-26 KST
- Master contract: [Issue #28](https://github.com/GUNOBAE/BGH_BMO/issues/28)
- One Draft PR: [#29](https://github.com/GUNOBAE/BGH_BMO/pull/29), branch `feature/bmo-product-release-v1`, base `feature/bmo-camera-video-and-qa-v1` (PR #27)
- R10 원본: `8c3ef98ba0e0645213c95295a24d7353fc2865b5ad9bc85390360c4ec9b78c82` (수정하지 않음)
- 기존 GPT Luna low/STT/Fish Aaron Soft gain .25/Beemo/Piper opt-in을 보존한다.

## 배경

사용자 실측: 일반 Live 대화는 가능하나 **GP8 물리 Enter 버튼으로 녹음 연출 후 답변 없음**, 감정 발화가 단조로움, Chromium App/127.0.0.1 경유 방식이 독립 실행 프로그램처럼 보이지 않음. Pi 로그인 후 XDG_RUNTIME_DIR 0770 재발; 사용자 수동 0700 복구 후 실행 가능. 이전 모의 실행 종료에서 "BMO owned cleanup needs review." 출력. 이번 변경 전 원인을 실제 음성 측정으로 확정할 수 없는 상태다.

## 실제 코드 변경

1. `desktop_native_window.py`: 시스템 Python `/usr/bin/python3`에서 optional PySide6 QtWebEngine을 활용하는 별도 **Qt BMO 창**을 준비. 로컬 호스트·포트 이외 네트워크 요청/메인 네비게이션을 거부, off-the-record 프로필 사용, 단일 800x480/전체화면, 원본 HTML을 Qt 내부에서만 렌더링. `DesktopBrowser(renderer="native")`와 `--frontend native`; 기존 `--frontend chromium`은 보존. Debian 13 arm64 패키지 여부/Wayland/GPU/실 LCD는 인간 검수 필요.
2. 데스크톱 설치는 `--install-desktop --frontend native --ptt-microphone --vad-profile sensitive --expressive-voice --dry-run`을 명시적으로 지원. 파일 덮어쓰기/부팅 자동 실행 없음. 사용자가 dry-run을 보고 승인할 때에만 설치.
3. `PttRecorder`는 기존 normal 설정을 보존하고 `sensitive` opt-in (.007 RMS, 0.16s 발화, 1s 무음, 5s 무음 시작 timeout, .3s calibration, noise multiplier 1.6)을 제공한다. `no_speech`/calibration/녹음 오류를 PTT UI에서 숨기지 않고 보여 준다. PCM 데이터·음성 원문 없이 frame/speech frame counts, coarse level bands, calibrated threshold만 공개 상태에 남겨 현장 PTT 무응답 원인을 좁힐 수 있다.
4. `voice_performance.py`: GPT 한번의 validated `FeatureReply.emotion`을 Fish S2 allowlist 지시로 변환하는 `--expressive-voice` 실험 옵션. 같은 Aaron Soft, 동일 Fish API 호출 수·캐시/반복 재생 유지. 보이스 태그는 Piper로 전달하지 않음. 추가 웃음/울먹임/뿌우 interjection은 대화당 최대 0~1개를 자연스러울 때만 추천하는 시스템 정책으로 수정. 실제 품질은 미검수.
5. `--camera-live --photo-directory`: 사용자가 허용할 때에만 기존 `Picamera2Backend` + `OpenCVFaceDetector` + `PhotoStore`로 연결. 기본 OFF, camera open은 사용자가 preview/촬영/추적을 요청할 때만. 실제 사진 영구 보존 경로 선택 필요. 실제 video encoder와 RP2040 servo motion은 이번 코드에서 아직 비활성; Mock 결과를 실물 검수로 주장하지 않는다.
6. `desktop_app.main`의 종료 실패는 `stage=cleanup`과 제한된 오류 코드를 표시한다. 임의 시스템 프로세스 kill/autostart/chmod 수리 없음.

## 자동검증 증거와 한계

- 누적 baseline: PR #27 Python **1076** PASS.
- 이 PR의 검증 코드 `e1f837273f2e2dde44d374debcdbc8a83d91418a` 단계 [GitHub PR CI](https://github.com/GUNOBAE/BGH_BMO/actions/runs/40216747813): **Python3.11 + Python3.13 각각1088 tests 성공, public-browser 성공**. 최근 추가 문서 커밋 이후 최종 HEAD 검사 상태는 PR의 Actions를 다시 확인해야 한다.
- fake Qt native launcher arguments/loopback, desktop icon dry-run, native camera 권한/idle no-device import, Fish annotated text/fallback, PTT mode/counters, 기존 headless browser를 검사했다.
- **실제 Pi Native Qt 패키지 import/창 실행, GP8 버튼 응답, 실제 카메라/사진, Fish 감정 발화/환영 인사, servo/IMU/영상 실물, XDG runtime 0770 재발 원인과 cleanup 실기**는 `NOT_RUN / HUMAN_TEST_PENDING`.
- GP7 빨간 버튼은 양팔 기존 토글 위험. 기존 RP2040 `code.py`가 저장소에 없으므로 실제 서보 방향/명령 프로토콜 없이 임의 각도로 구동하지 않는다. MPU6050 고장 센서는 제어·안전에 사용하지 않는다. 팬/전원 과거 0x50000 비트 해석만 읽기 전용으로 유지한다.
- 승인 R10 및 기존 얼굴/음성/Piper/키/프로젝트/OS/SSH/VNC/서비스/팬/펌웨어/main을 변경·자동 병합하지 않았다.

## 단일 실기 검수 순서

[사용 가이드](../../software/PRODUCT_RELEASE_KR.md)를 따른다.
1. 기존 `/home/bgh/BMO_FEATURES`, `/home/bgh/BMO_NIGHTLY` 보호: 새 branch를 다른 worktree로 로드, 시스템 Python3 Qt WebEngine import와 runtime0700 확인 후 **native simulate**에서 실제 R10/로딩/인사/종료.
2. 사용자 유료/API/마이크 입회: native live `--ptt-microphone --vad-profile sensitive --expressive-voice`; GP8 질문 직후 voice answer 또는 공개 PTT 종료 코드·에너지 밴드를 보고 normal과 비교. 환영 Fish WAV는 별도 1회 prepare→audition→SHA 승인.
3. 카메라 사용자 1회 사진/추적은 명시 `--camera-live --photo-directory`. 양팔은 firmware/profile/STOP/PSU 장비를 검증한 뒤 별도 bench 최초 제한각 실기. 실제 영상/게임/음원은 독립 콘텐츠/드라이버 준비 후 검수.

### 완료 정의

**진짜 native 단일 아이콘 → 실제 checkpoint 로딩 바 → 승인 R10 얼굴 → 1회 환영 → 물리 GP8 질문 → 실제 VAD 종료 → 감정 있는 동일 Aaron 음성으로 1회 답변**이 사용자 Pi에서 관측되어야 제품 대화 경로 PASS. 현재는 이 요건이 전부 실기 PASS라고 보장하지 않는다. Draft PR 유지, main merge 금지.
