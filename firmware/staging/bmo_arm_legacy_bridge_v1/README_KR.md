# BMO 기존 팔/버튼을 보존한 감정 팔 통합 — B1

**개발·검증용 Draft. 실물 RP2040에 아직 배포하지 않음.**

## 변경하지 않는 기준

- 원본 파일 `original_code.py` SHA-256: `fdc65bebef17b78b945281549ddce80fb749e1a94262751d8d2f0146c394a01e`
- GP2 UP / GP3 LEFT / GP4 RIGHT / GP5 DOWN / GP6 Z / GP8 ENTER HID 그대로
- GP7 RED 양팔 0°↔180° 토글 그대로
- GP10 오른팔 / GP11 왼팔, 50Hz, 600~2400 µs, `set_arms(angle)` 그대로
- 원본 부팅 `arm_angle = 0; set_arms(arm_angle)` **팔 아래**, 재시작에 실물 모터 움직일 수 있음
- 승인 R10, Fish Aaron Soft, GP8 PTT, Camera 3 코드 수정하지 않음
- MPU6050은 제어에서 제외

## 최소 추가

1. `code.py`: 원본 `servo_angle()`, `set_arms()` 및 버튼 HID를 유지하며 `LegacyBridge` 호출을 추가한다. `REMOTE_ENABLED = False`가 기본이다.
2. `bmo_arm_bridge.py`: CircuitPython에서 고정 동작 이름 `wave/celebrate/shrug/raise_left_arm/raise_right_arm/both_up/both_down`만 허용한다. raw PWM·각도·코드 실행 불허. heartbeat 0.9초에 추가 자동 동작 정지, 수동 GP7이 진행 중 자동 동작을 취소한다.
3. `boot.py`: 별도 `usb_cdc.data`를 허용하는 **선택적 예시**. 기존 `boot.py`가 있으면 먼저 검사하고 임의로 덮어쓰지 않는다.
4. Pi `LegacyArmLink` + `LegacyMotionFeature`: 기존 `FeatureHost`에 장치 옵트인으로 주입한다. GPT는 제스처 이름만 제안하고, 실제 player started에 한 번만 처리한다.
5. 감정 정책은 상위 PR #35 유지: happy→celebrate, surprised→shrug는 **제안 초기값**, R10 감정 화면 자체는 변경하지 않는다.

이 프로토콜은 구 V2 실험과 호환되지 않으며, 한 번에 명령 한 개/정확한 ACK/부팅 식별자/중복 요청 거부를 사용한다. 소프트웨어 HOLD/STOP은 **서보 전원 차단이 아니다**. 로봇은 관절 엔코더가 없으므로 실제 각도 도달은 여전히 확인되지 않는다.

## 단계 1 — Pi에서 확인만 하기 (서보 움직임 명령 없음)

```bash
sha256sum /media/bgh/CIRCUITPY/code.py
ls -l /media/bgh/CIRCUITPY/boot.py /dev/ttyACM* 2>&1
if [ -f /media/bgh/CIRCUITPY/boot.py ]; then cat /media/bgh/CIRCUITPY/boot.py; fi
```

원본 code.py SHA가 위의 값과 일치하면 GP7 복구는 디스크 기준으로 보존된 것이다. 이후 별도의 실기 검수로 GP7 버튼/GP8 PTT 재확인.

## 단계 2 — 실물 설치 이전 격리 테스트

```bash
git clone --single-branch --branch feature/bmo-legacy-arm-original-compatible-v1 \
  https://github.com/GUNOBAE/BGH_BMO.git ~/BMO_LEGACY_BRIDGE_TEST
cd ~/BMO_LEGACY_BRIDGE_TEST
PYTHONPATH=software python3 -m unittest -v \
  software.tests.test_legacy_arm_bridge software.tests.test_legacy_arm_host
./launch-desktop --simulate --dry-run --emotion-arms
```

테스트/드라이런은 모터를 건드리지 않는다. 기존 `BMO_REAL_ROBOT`, `BMO_ARM_STAGE` 작업 디렉터리를 덮어쓰지 않는다.

## 단계 3 — 감독자가 준비된 다음에만 실물 전환

실행을 멈추고 BMO 전체 전원을 끈 뒤 **양쪽 서보 3핀을 모두 분리**, 가능하면 별도 차단 가능한 서보 전원으로 준비한다. RP2040 `CIRCUITPY` 전체 백업 및 `code.py`/기존 `boot.py` SHA를 먼저 기록한다.

**모터를 연결하지 않고:** 원본에서 파생된 `code.py`(REMOTE_ENABLED=False)와 `bmo_arm_bridge.py`를 준비한 다음, 기존 boot 구성을 검토해 독립 CDC data를 활성화한다. HID GP8·GP7 이벤트와 B1 HELLO→DISABLED를 확인한다. USB endpoint 변화가 있으면 원본으로 즉시 복구한다.

그 다음 별도 감독 승인 아래에서만 `REMOTE_ENABLED = True`로 만든 **정확한 1바이트 변경 후보**를 작성하고 SHA-256을 기록한다. 앱은 단일 후보 전체 바이트·helper·boot.py를 확인하며 맞지 않으면 포트도 열지 않는다. 제스처는 반드시 전원 차단 수단 및 이동 공간을 확보한 상태에서 한쪽부터 테스트한다.

승인 이후의 앱 명령 예시(**지금 실행하지 말 것**):

```bash
./launch-desktop --live --ptt-microphone --emotion-arms \
  --arms-supervised --arm-legacy-bridge \
  --arm-reviewed-code-sha <REVIEWED_CODE_SHA256> --arm-power-off-ready
```

**현재 NOT_RUN:** Pi 실기 B1 USB handshake, helper 로딩, 실제 모터 움직임, 정지 검증, 전체 앱 통합 재검수. Linux/CI 합성 통과를 실물 검증과 혼동하지 않는다.
