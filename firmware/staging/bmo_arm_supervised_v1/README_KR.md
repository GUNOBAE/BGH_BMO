# BMO RP2040 + Raspberry Pi 통합 — 기존 버튼/팔 규칙 복구판

> **상태: GitHub 개발·자동 테스트판 / 이 브랜치의 최신 코드는 아직 실기 배포되지 않음.**
> R10 얼굴, Fish Audio Aaron 목소리, 카메라·PTT 코드는 변경하지 않는다.
> PR #34, JSON V1(#32)과 섞지 말고 이 V2 코어·코드·호스트를 **같은 커밋**으로 사용한다.

## 실기 관측 (2026-10-27)

- Pi에서 GP8 초록 버튼 Enter → PTT 입력 성공.
- `ttyACM1` = RP2040-Zero의 USB interface `02`, `CircuitPython CDC2 control`. `V2 HELLO`→`ACK ... UNCALIBRATED`, 이후 시험판에서 `READY` 실제 응답 확인.
- 당시 실기에 설치한 **구 시험판** `code.py`는 SHA-256 `95260ad44ae01da1bd05210f2d05c06fe4ea3b7b40b950a5e5afffa4d0d42229`, 좁은 `left 600..620/right 2380..2400`을 사용. **현재 신규 버전이 아님.**
- 첫 `--one-step left`에서 `HELLO READY` 이후 `invalid_arm_ack` 발생. 실제 모터 동작 성공/STOP 성공으로 기록하지 않는다. 이전 응답 대기 180ms와 ACK 프레이밍이 후보 원인이며, **정확한 원인은 미확정**. 신규 코드에서는 구분된 누락/불완전 응답 오류 및 더 넉넉한 유한 대기와 watchdog을 적용했다.
- 사용자는 서보 분리 전에 두 팔이 **최대 UP** 상태라고 확인했고, **구 원본 GP7 빨간 버튼으로 0↔180 왕복이 정상 작동**했다고 확인했다. 별도 각도·끝점 재보정이 아니라 이 기존 동작을 그대로 재사용한다.
- 사용자는 양팔의 3핀을 분리할 수 있음. 마지막 보고에는 **왼팔을 다시 연결했다**고 했으며 이후 실제 분리 완료 여부는 확인되지 않았다. 새 펌웨어를 쓸 때 반드시 모든 BMO 전원 OFF → 양쪽 3핀 분리부터 시작한다.

## 잠금 규칙 (실물 원본에서 검증된 것)

| 버튼 / GPIO | 동작 |
|---|---|
| GP2 | USB HID ArrowUp |
| GP3 | USB HID ArrowLeft |
| GP4 | USB HID ArrowRight |
| GP5 | USB HID ArrowDown |
| GP6 | USB HID KeyZ |
| **GP7 빨강** | **누를 때마다 양팔 위↔아래 토글**. X 키, 길게 누르기, STOP 버튼 아님 |
| **GP8 초록** | **USB HID Enter** — 평소 BMO 대화 PTT; 게임 진입 시 기존 Enter |
| GP10 | 오른팔 서보 PWM 50Hz |
| GP11 | 왼팔 서보 PWM 50Hz |

구 원본 펌웨어: `set_arms(angle)` → **왼팔 (180-angle), 오른팔 angle**. 50Hz `600..2400µs`:

| 자세 | 왼팔 GP11 | 오른팔 GP10 |
|---|---:|---:|
| **UP (원본 180°)** | 600µs | 2400µs |
| DOWN (원본 0°) | 2400µs | 600µs |

새 펌웨어 `code.py`는 모터를 **부팅 시 출력하지 않고**, GP7 첫 누름에서 **마지막으로 확인된 UP 위치**로 PWM을 초기화한 후 DOWN으로 서서히 이동한다. 그 다음 누름은 UP으로 복귀. 좌우 독립 호스트 명령은 기존 제한 안에서 따로 동작하고, GP7 새 입력은 호스트 움직임보다 우선한다.

### AI가 팔을 움직이는 기준

GPT는 PWM·각도·속도·횟수·임의 코드/경로를 절대 생성하지 않는다. 이미 확정된 제스처 이름만 제안하고 Pi 호스트가 고정된 동작표로 해석한다.

- `wave` (인사) = **오른팔만** 흔들기
- `celebrate` (명시적 축하/신남) = **양팔** 움직이기
- `shrug` (가벼운 모르겠어) = **양팔 작은 동작**
- 명시적 `raise_left_arm`, `raise_right_arm` / 양팔 UP·DOWN은 별도 사용자 동작으로만.
- 감정 `happy`만 보고 자동 팔 동작을 추가하지 않음. `FeatureReply.gesture`는 **응답 재생 시작에 한 번**, `cancel/stop`은 새 제스처보다 우선.
- 긴급 `V2 STOP`은 PWM 중단 시도 후 **재시작까지 래치**. 일반 제스처 취소용 `V2 HOLD`는 현재 pulse에서 명령 보류, 해제·다음 제스처를 허용. **둘 다 물리적 전원 차단이나 위치 도달 보증이 아님**.
- IMU는 건강 문제로 제어에 사용하지 않음.

## 안전한 프로젝트 구성

- `code.py`: 버튼 HID + 기존 GP7 토글 + CDC V2. **기본 `ARM_OUTPUT_BUILD_ENABLED=False`, `REVIEWED_CALIBRATION=None`**이어서 빌드만으로 모터는 움직이지 않는다.
- `safe_arm_core.py`: GPIO 없는 순수 상태기계, 소프트웨어 STOP, 800ms 호스트 watchdog / 수동 GP7 이동은 호스트 heartbeat 불필요. Host 이동은 한 tick 30µs (20ms), GP7 수동은 45µs (20ms)까지.
- `boot.py`: 콘솔 외 두 번째 전용 CDC data interface 활성화.
- `software/bmo_ai/features/motion/arm_host_v2.py`: ID·부팅 식별자·승인된 pulse 범위 확인, 한 요청 단위 ACK, 이상 응답 때 **중복 재전송 없음**.
- `gesture_rules_v2.py`: 고정 `wave/celebrate/shrug` 매핑 및 기존 600~2400µs 전체 범위.
- `live_feature_v2.py`: 음성 재생 시작/취소 이벤트, 실제 제스처 직렬화, heartbeat/HOLD/STOP.
- `arm_serial_posix_v2.py`: 사용자가 허용할 때만 실제 `ttyACM1` 장치 ID 검사 후 단독 소유. 구 코드·타코어·새 설치본 SHA 바이트 일치를 요구.
- `scripts/arm_up_bench_v1.py --prepare`: **Pi 로컬** `~/BMO_ARM_PREP/legacy_full_swing_v2_code.py`에 구 600~2400µs 구동 규칙을 넣은 후보 파일만 생성. **CIRCUITPY 자동 복사·서보 출력 없음**.
- `RuntimePolicy.arms_supervised`: 기본 False. `FeatureHost`는 명시적 실기 드라이버를 주입하지 않으면 거부.
- `launch-desktop --arms-supervised --arm-reviewed-code-sha <SHA> --arm-power-off-ready`: **명시적으로 opt-in한 실기만**. 파일/펌웨어 ID가 맞지 않으면 포트 열기 전에 중단. 기본 `launch-desktop --live`는 기존대로 팔 비활성화. **USB port, servo, GPT action 실제 물리 검증 완료 전에는 사용하지 않는다.**

## 다음 실기 단계

1. 전체 전원 OFF, **왼팔·오른팔 모두 3핀 분리** → Pi/RP2040 다시 켜기. 예전 백업 `/home/bgh/BMO_RP2040_BACKUPS/original_EWJ1G4sz/code.py` 보존.
2. 이 브랜치 CI가 통과한 **단일 커밋**에서 `scripts/arm_up_bench_v1.py --prepare` 실행. 출력 SHA를 기록.
3. SHA/구 버전 대조 후 펌웨어 `safe_arm_core.py` → 시험 `code.py` 순으로 복사, 기존 `boot.py` 유지, GP8 Enter와 `V2 HELLO READY` 확인.
4. 서보가 없는 상태에서 **GP7 눌림 토글과 STOP 및 호스트 독립 좌우 대상**을 검수. GPIO에만 PWM 출력이 있을 수 있으며 위치 피드백 없음.
5. BMO 전체 전원을 끈 후 연결을 바꾼다. 사용자 입회·즉시 전체 전원 차단 가능·움직임 범위에 손/케이블 없음. 구 전체 스윙이 검증됐으나 **새 프로그램의 물리 움직임과 비상 STOP은 아직 미검증**: 한쪽부터, 이후 양쪽 버튼/음성 제스처.
6. 정상 확인 후에만 데스크톱의 **명시적 실기 opt-in**을 사용할 것. 앱 R10 얼굴/목소리/카메라/GP8 PTT는 변경하지 않는다.

**인수 테스트: GitHub CI 통과 여부는 해당 커밋의 Actions run에서 확인. 물리 모터 구동 및 실제 중단 테스트는 아직 수행되지 않았다.**
