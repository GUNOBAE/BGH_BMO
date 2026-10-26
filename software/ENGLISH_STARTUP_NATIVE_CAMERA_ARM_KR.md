# BMO English startup + local boot sound + native Qt + real camera + staged arms

**기준:** `feature/bmo-english-startup-hardware-v1` (기존 [PR #31](https://github.com/GUNOBAE/BGH_BMO/pull/31) 위).
사용자가 원한 것은 **영어 원작 분위기**다. 이전 한국어 `돌아왔구나! 비모가 기다렸어!`는
새 버전의 기본 인사가 아니다.

## 부팅 소리 조사 및 비용 정책

원작 `Adventure Time: Be More`에서 BMO의 전원이 켜질 때 Macintosh 계열 부팅 차임이
사용된 것으로 2차 자료에 기록돼 있다. 실제 성우는 Niki Yang이며 BMO는 영어로 말한다.
참고:
- https://adventuretime.fandom.com/wiki/Be_More
- https://press.wbd.com/us/bio/bmo?language_content_entity=en

**새 고정 인사**: `Oh! You're back! BMO missed you!`.
Fish 발화 경계에서 BMO의 라틴 문자를 **Beemo**로 바꿔 발음한다.
기존 Fish Aaron Soft / gain 0.25 / model `s2.1-pro-free` 보존.
새 캐시: `~/.cache/bmo/greeting-welcome-en-v1/greeting.cache`.
기존 한국어 welcome-v2 및 최초 `난 비모야~!` 자산은 읽거나 덮어쓰지 않는다.

**일상적으로 Fish API 0회**: Loading이 끝나 R10이 화면에 표시된 뒤
1.2초 내외의 짧은 original computer-style boot chime을 **로컬 WAV**에서
재생하고, 사전에 승인한 **영어 WAV**를 한번 재생한다. 두 음원을 Python ALSA 소유
재생기로 순차 재생한다. 시작 차임은 자연스러운 감정이 필요 없는 고정 SFX이므로
합성 API를 쓰지 않는다. BMO 자체 환영 인사는 한번만 Fish에 요청해 청취 승인 후
캐시로 재생. 일반 GPT 대화와 repeat WAV 캐시는 분리한다.

`startup_sound.py`는 구형 Macintosh 소리를 복사하지 않은 새로운 3음 전자 차임을
최초 실행 시 `~/.cache/bmo/startup-chime-v1/boot.wav`에 0600으로 만들어 이후 재사용한다.
사용자가 이용 권한을 가진 짧은 WAV(PCM 16-bit mono, 0.15~3초)로 대체하려면
`~/.config/bmo/startup_chime.wav`에 0600으로 저장하면 된다.
원작 방송의 오디오를 무단으로 재배포하거나 Git에 커밋하지 않는다.
파일이 유효하지 않으면 오류로 종료하며 조용히 다른 소리로 덮어쓰지 않는다.
**실제 Pi 스피커/영어 Aaron 청취는 이 코드 제출 시 NOT_RUN**이다.

## 최신 branch를 기존 작업을 지우지 않고 받기

기존 `BMO_FEATURES`, `BMO_NIGHTLY`, `BMO_REAL_ROBOT`를 덮어쓰지 않는다.

```sh
git -C /home/bgh/BMO_REAL_ROBOT fetch origin feature/bmo-english-startup-hardware-v1
git -C /home/bgh/BMO_REAL_ROBOT worktree add -b feature/bmo-english-startup-hardware-v1 \
    /home/bgh/BMO_FASTPATH origin/feature/bmo-english-startup-hardware-v1
cd /home/bgh/BMO_FASTPATH
export BMO_PYTHON=/home/bgh/BMO_FEATURES/.venv/bin/python
./launch-desktop --status
```

### 차임·영어 음성

기존 한국어 pending cache를 승인하지 말 것. 새 welcome-en-v1 디렉터리는 처음에는 비어 있다.
아래 첫 명령만 Fish 요청 **1회 비용**이 발생할 수 있다.

```sh
./launch-desktop --live --prepare-greeting
./launch-desktop --live --audition-greeting
./launch-desktop --live --approve-greeting ACTUAL_SHA_FROM_PREPARE
```

인사 생성 승인 후 모든 부팅에서는 **Fish API 추가 합성 없이** 승인된 음성 파일 재생.
이전 사용자 한국어 SHA를 재사용하면 안 된다.

### 네이티브 GUI로 시작

현재 Pi의 `QT_NOT_READY`는 **시스템 Qt WebEngine 바인딩이 설치되지 않았음**을 뜻한다.
Debian 13 ARM64에는 `python3-pyside6.qtwebenginewidgets` 공식 패키지가 존재한다.
- https://packages.debian.org/en/trixie/arm64/python3-pyside6.qtwebenginewidgets

```sh
sudo apt update
sudo apt install -y python3-pyside6.qtwebenginewidgets
/usr/bin/python3 -B -c 'from PySide6.QtWebEngineWidgets import QWebEngineView; print("QT_READY")'
```

설치 후 먼저 실제 UI 테스트:

```sh
cd /home/bgh/BMO_FASTPATH
export BMO_PYTHON=/home/bgh/BMO_FEATURES/.venv/bin/python
./launch-desktop --simulate --frontend native --windowed --demo-assets
./launch-desktop --live --frontend native --ptt-microphone --ptt-trace
```

`--frontend native`는 **Qt BMO 전용 프로그램 창**을 생성하고, Qt WebEngine은
기존 R10 HTML을 내부에서만 렌더한다. 기존 단일 Python brain/ALSA 그대로,
주소창·탭 없이 800×480 fullscreen. `--frontend chromium`은 기존 fallback이다.
R10 원본/브라우저 개인 프로필을 건드리지 않는다.
실제 Pi Wayland/7인치 렌더·Qt process cleanup은 사용자 입회 **NOT_RUN**.

바탕화면 프로그램은 파일 충돌을 먼저 확인한다:

```sh
./launch-desktop --install-desktop --frontend native --ptt-microphone --dry-run
# 대상과 기존 BMO.desktop이 동일하거나 비어 있을 때만:
./launch-desktop --install-desktop --frontend native --ptt-microphone
```

기존 다른 BMO 아이콘은 자동 덮어쓰지 않음. 데스크톱 파일 관리자의 실행 허용은 사용자 검수.
OS 자동실행/systemd/autostart 설치 없음.

## Camera3 Wide를 실물로 연결

앞서 사용자 Pi에서 `/usr/bin/python3`으로 picamera2/libcamera/cv2/PIL의 탐색 정보가
확인됐으나 기존 `.venv`는 해당 시스템 라이브러리를 상속하지 않아
`camera_backend_unavailable`로 차단됐다. **새 가상환경만 따로 만들면 되는 상황일
가능성이 높다.** 기존 BMO 대화 가상환경은 수정하지 않는다.

```sh
cd /home/bgh/BMO_FASTPATH
/usr/bin/python3 -B -c 'import picamera2, libcamera; print("SYSTEM_CAMERA_MODULES_READY")'
bash scripts/prepare_camera_venv.sh
```

시스템 Python import가 실패한다면 먼저 카메라 패키지만 준비한다.
Raspberry Pi OS 공식 권장 경로는 OS와 버전을 맞춘 apt 패키지다:

```sh
sudo apt install -y python3-picamera2 python3-libcamera python3-opencv python3-numpy
bash scripts/prepare_camera_venv.sh
```

이미 `.venv-camera`가 존재하면 스크립트는 덮어쓰지 않고 중단한다.
이 작업은 소프트웨어 가상환경만 생성한다. 실제 카메라를 열지 않는다.
공식 참고: https://www.raspberrypi.com/documentation/computers/camera_software.html

**실제 사진 한 장부터**. 저장 위치는 별도 private 디렉터리:

```sh
install -d -m 700 "$HOME/BMO_private"
BMO_PYTHON="$PWD/.venv-camera/bin/python" ./launch-desktop \
  --live --frontend native --ptt-microphone --camera-supervised \
  --photo-directory "$HOME/BMO_private/photos-real-v1"
```

앱 **사진·시선 → 사진 촬영**을 사용자가 눌러 한 장 촬영한다. 이후 정상 닫기·다시
열기/사진 색·AF를 확인하고 12초 저속 무음 영상으로 확대한다. 모델이 무단 촬영하지 않는다.

## 실제 양팔: staging firmware와 호스트 규약

기존 현재 RP2040 코드에는 GP7 양팔 토글과 **부팅 `set_arms(0)`**가 있어 바로
신규 호스트 팔 명령을 보내는 것이 불가능하다. 실제 servo 컨트롤이 없는 상태를
Mock 성공으로 부르지 않는다.

새 코드:
- `firmware/staging/BMO_SAFE_BOOT_V1.py` (USB CDC data endpoint)
- `firmware/staging/BMO_SAFE_CODE_V1.py` (GP2..GP6/GP8 HID 보존, GP7 PWM 정지,
  부팅 duty=0, 좌우 GP10/GP11, 전용 JSON/LF, boot-id, seq, 별도 min/neutral/max,
  제한된 3도 단계, 0.6초 heartbeat watchdog)
- `software/bmo_ai/features/motion/arm_control_v1.py`: 순수 메시지 encode/decode,
  임의 포트 오픈/리셋/프로세스 실행이 없다.

**이 두 staged 파일은 보드에 설치하지 않았다.** CircuitPython 환경에서 실제
USB endpoint, GPIO/HID, 서보 전압/방향/물리 간섭과 0700 private calibration을
확인하고 기존 firmware/라이브러리·`boot.py`를 백업한 다음 사용자 입회 bench만
허용한다. 실제 전원 분리 없이 RP2040 리셋·`boot.py` 교체 금지.
전원/서보 watchdog는 물리적인 전원 차단 스위치를 대신할 수 없다.

현재 움직임은 아직 **BLOCKED/NOT_RUN**, 하지만 실행 불가능한 Mock 설명 수준을
넘어 실제 controller-side firmware·host frame 코드까지 staging했다.
안전한 첫 물리 테스트에는 한 팔 servo power 분리, 손으로 확인한 neutral,
실제 개인 보정 파일, CDC enumeration 및 호스트 heartbeat 처리가 추가로 필요하다.
