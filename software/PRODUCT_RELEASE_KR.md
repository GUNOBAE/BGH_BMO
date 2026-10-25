# BMO 제품 통합 v1 — Native 창 + 버튼 녹음 진단 + Fish 감정 + 카메라

## 현재 판정

이 코드는 기존 야간 PR #27에 대한 하나의 통합 Draft PR이다. 코드/CI 확인과 실제 Pi 수락은 다르다.
사용자의 기존 Cloud 대화 실기 성공을 새 Native/GP8/감정/카메라의 성공으로 주장하지 않는다.
승인 R10 HTML/SVG 원본 SHA는
8c3ef98ba0e0645213c95295a24d7353fc2865b5ad9bc85390360c4ec9b78c82
이며 변경하지 않는다. Qt GUI는 원본 HTML을 프로그램 내부에 그리기 위한 렌더러이고
Chromium 주소창으로 웹사이트에 접속하라는 뜻이 아니다.

## 1. OS/소프트웨어 준비 검증 (먼저 읽기 전용)

기존 코덱스가 만든 worktree는 /home/bgh/BMO_NIGHTLY에 있다. 새 release 브랜치는
별도 checkout/worktree로 받고, 기존 BMO_FEATURES와 BMO_NIGHTLY는 보존한다.
아래 APP_ROOT는 이 새 feature/bmo-product-release-v1 checkout을 가리킨다.
CLI에서 사용하는 가상환경은 기존 검증된 환경을 지정한다.

실물 Pi에서 Qt 선택을 시험하기 전에:

    /usr/bin/python3 -B -c 'from PySide6.QtWebEngineWidgets import QWebEngineView; print("NATIVE_QT_READY")'
    stat -c '%U %G %a' "$XDG_RUNTIME_DIR"

Qt import가 없다면 Debian 13 arm64 공식 python3-pyside6.qtwebenginewidgets 패키지
가용성을 먼저 확인하고 사용자 동의 후에만 설치한다. Codex는 무인 설치 금지.
기존 runtime directory는 UID 소유 0700이 필요하며 0770이 재발했던 원인은 미해결.
자동 chmod, VNC/OS 설정 변경, 전역 브라우저 kill 금지.
Qt native 앱은 /usr/bin/python3의 시스템 PySide6를 사용하고 BMO backend는
BMO_PYTHON으로 지정된 기존 가상환경을 사용한다.

## 2. Native BMO 단일 창, 진행률, 인사

    cd "$APP_ROOT"
    BMO_PYTHON=/home/bgh/BMO_FEATURES/.venv/bin/python ./launch-desktop --simulate --frontend native --windowed --demo-assets

합격: Chrome 주소창/툴바/탭 없이 BMO 전용 Qt 창, BMO Loading NN% + 긴 네모바,
원본 R10 화면, Esc 메뉴, 종료 시 자기 프로그램만 정리. QtWebEngine이 내부에서
HTML/JS를 렌더링하는 건 원본 R10 보존을 위한 의도된 구조다.
이 Native GUI의 실제 Wayland/VNC/800x480 작화와 부하 성능은 NOT_RUN.
Chrome fallback은 --frontend chromium. 사용자가 바탕화면 실행을 승인하기 전에는:

    ./launch-desktop --install-desktop --frontend native --ptt-microphone \
      --vad-profile sensitive --expressive-voice --dry-run

에서 정확한 새 파일·Exec를 검토하고, 실기 합격 뒤에만 명시적인
--install-desktop --frontend native로 사용자 홈의 BMO.desktop을 생성한다.
기존 BMO 아이콘이 있으면 덮어쓰지 않고 충돌을 보고한다.
부팅 자동실행은 기본으로 생성하지 않는다.

새 환영 문구는 "돌아왔구나! 비모가 기다렸어!"이며 Fish Aaron Soft의 기존
공통 cache-v2 prepare -> audition -> SHA 승인 절차만 사용한다.
승인되지 않은 WAV는 매 부팅마다 유료 합성하지 않고 안내만 표시한다.

## 3. 실제 대화, 음성 연기, 카메라를 각각 입회 검수

일반 대화: LIVE 앱에서 초록 GP8 또는 키보드 Enter를 누른 뒤 말하고 약 1초의
무음으로 녹음이 끝나는지 확인한다. 빨간 GP7은 기존 양팔 토글과 충돌하므로
누르지 않는다. 실제 USB/HID 포트와 Enter 키는 아직 구분 미확인이다.

    BMO_PYTHON=/home/bgh/BMO_FEATURES/.venv/bin/python ./launch-desktop --live --frontend native --windowed --ptt-microphone --vad-profile sensitive --expressive-voice

--vad-profile sensitive는 실기 음성이 잘리지 않는지 비교하는 명시적인 후보이며,
정상 오인식/팬 잡음 테스트 전 기본값을 바꾸지 않는다. normal이 이전 RMS 원본이다.
이제 no_speech / calibration_noisy / stream_closed / cleanup_failed 등이 외부 상태에
표시되므로 '듣는 표정만 했다'와 STT 실패를 구분할 수 있다. TTS는 verified
happy/sad/crying/surprised/angry metadata와 안전한 음성 지시를 합성 텍스트에만
추가한다. 기본은 기존 Aaron Soft, 표현 기능은 --expressive-voice opt-in.
원본 목소리 유지, 유료 Fish 추가 호출 없음. 부자연스러운 웃음/울음/삐침은
사용자가 2~3개 A/B 후 다듬는다. PTT 무응답의 실물 root cause는 아직 NOT_RUN.

녹음 중 `PTT` 공개 snapshot에는 안전한 `reason_code`, `vad_frames`,
`speech_frames`, `noise_level`, `input_level`, `effective_threshold`만
포함한다. PCM/WAV·문장·개별 프레임·마이크 장치 경로는 이 화면 상태에 실리지 않는다.
예를 들어 녹음 프레임은 증가하는데 speech_frames=0이면 RMS 감지 실패와
실제 발화를 구분할 수 있다. 실제 사용자 환경에서 실패 원인 코드를 확인하기
전에는 normal/sensitive 중 어느 쪽이 좋은지 확정하지 않는다.

사진/미리보기/사람 위치 추적 (별도 사용자가 허용한 실물 테스트):

    BMO_PYTHON=/home/bgh/BMO_FEATURES/.venv/bin/python ./launch-desktop --live --frontend native --windowed --camera-live --photo-directory /home/bgh/Pictures/BMO

이 옵션 없이 앱은 실물 카메라를 열지 않는다. Picamera2Backend는
사용자가 사진/미리보기/시선 추적 버튼을 누를 때만 lazy start하고,
PhotoStore는 명시한 private 0700 디렉터리에 저장하도록 구성된다.
Debian 13의 Picamera2/OpenCV 설치·IMX708 센서·AF·native close는 미검증이므로
실물 카메라 앞에서 작은 1회 사진부터 검사한다. 실물 video encoder는 별도
미구현이며 camera_video는 계속 denied다.

팔은 기존 GP10/GP11 RP2040-PWM 출력·펌웨어·좌우 기구 한계·STOP 경로가
검증되지 않았으므로 이 PR에서 실제 각도 명령을 열지 않았다.
무작정 90도 지정이나 GP7 버튼 재할당은 위험하다. next hardware supervised
step은 기존 firmware 백업과 servo 기구/전원 검증 → 좁은 시험각과 안전정지
프로토콜 → ACK/상태 확인이며, MPU6050 고장 센서는 제어와 분리한다.

실패 시 새 앱을 정상 종료하면 기존 /home/bgh/BMO_FEATURES/
launch-features --live 가 남아 있다. no reset --hard, no clean, no forced process kill.
