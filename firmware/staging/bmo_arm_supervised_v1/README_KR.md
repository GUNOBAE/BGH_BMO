# BMO RP2040-Zero 양팔 제어 v2 — *배포 금지* 펌웨어 초안

**상태: STAGING / OFFLINE_TEST_ONLY. 이 디렉터리는 설치 스크립트가 아니다.**
현재 RP2040의 `CIRCUITPY/code.py`·`boot.py`·USB 구성·서보 출력은 **변경하지 않았다**.
사용자가 직접 기존 펌웨어의 백업·전원·기구 간섭·물리 차단 스위치와 통신 규약을 확인할 때까지 이 파일을 복사하거나 실행하지 마라. 현장의 GP8 대화/카메라 성공을 보존해야 한다.

## 작업 이유

2026-10-26 사용자 보고: GP8 음성 대화, Camera Module3 Wide 실제 사진과 저속 무음 영상이 작동함. 다음 목표는 양팔을 **각각** 안전하게 제어하는 것.

읽기 전용 기존 펌웨어 조사에서 다음이 확인됐다.

- GP2 UP / GP3 LEFT / GP4 RIGHT / GP5 DOWN / GP6 triangle(Z) / GP8 green(Enter): USB HID 입력
- GP7 빨간 버튼: **기존 펌웨어에서 양팔 0°↔180° 토글**. 새로운 staging 코드에서는 별도 소프트웨어 STOP으로 **바뀔 예정**이라 사용자의 물리 승인과 기존 매핑 대조 전에는 업로드하면 안 됨
- right PWM GP10 / left PWM GP11, 50 Hz, 부팅 `set_arms(0)` 호출이 기존 펌웨어에 있음
- 현재 컨트롤러에는 안전한 개별 팔 host command/ACK/STOP/watchdog 규약이 확인되지 않음
- IMU MPU6050은 건강 문제 기록이 있어 팔 제어/안전 피드백에 사용하지 않음

## 이번에 실제 작성한 소스

- `safe_arm_core.py`: GPIO/장치 import가 **전혀 없는** 제한된 `V2` ASCII/LF 명령 파서, boot-id gate, 좌우 별도 reviewed pulse bounds, 중복 ID 방어, 느린 pulse slew, 350ms heartbeat watchdog, GP7 software STOP에 대응하는 latch, 위치 피드백 **없음**
- `code.py`: 새 CircuitPython 펌웨어 **참고 구현**, 기존 방향/Z/Enter HID 유지, GP7은 소프트웨어 STOP으로 변경. 기본 `ARM_OUTPUT_BUILD_ENABLED = False` + `REVIEWED_CALIBRATION = None`이어서 **서보 PWM을 절대 생성하지 않음**. GPIO GP12는 *가정한* 별도 활성 입력으로 Pull.UP이며 핀/배선 가능 여부는 **미검증**
- `boot.py`: 명시적 테스트 시의 `usb_cdc.enable(console=True, data=True)` 예시. 전용 `usb_cdc.data`만 사용하며 `usb_cdc.console` (REPL)로 모터 명령을 보내지 않음. RP2040의 USB endpoint 예산 확인/재열거 전엔 설치 금지
- `software/tests/test_arm_firmware_v2_stage.py`: pure CPython fake PWM으로 부팅 무동작, 부정 frame, physical gate, boot ID, 중복, 2개의 pulse 한계, slew, timeout, STOP latch 확인. **실제 하드웨어/펌웨어 테스트가 아님**
- `software/bmo_ai/features/motion/arm_host_v2.py`: Pi 호스트의 **같은 V2 프로토콜**을 encode/decode한다. `HELLO/ARM/PING/MOVE/STOP`과 `ACK`의 ID·boot 불일치, 미승인 물리 조건, heartbeat 만료를 차단한다. `BoundedCdcExchange`는 **외부에서 이미 열고 식별·검수한 전용 포트 객체만 받는다**. 포트 자동 검색, 시리얼 open, DTR/RTS 변경 또는 기존 HID 제어를 하지 않는다.
- `software/tests/test_arm_host_v2.py`: 호스트 → 실제 staged firmware parser/controller core → ACK 왕복과 둘 다 좌우 개별 target, STOP, heartbeat, 중복/실패/이전 boot를 오프라인에서 검사. **현재 보드와 통신하는 실기 드라이버는 아님**.

### 왜 기본 실행이 막혀 있는가

세 단계가 모두 필요하다.

1. 펌웨어 배포 변경을 사용자가 감독하고, 기존 `code.py` 및 `boot.py`·키/HID·USB 열거·서보 전원 케이블을 백업·대조
2. 실제 좌우 서보의 물리 home과 pulse(µs) min/max를 각각 측정해 작성. **예제 테스트 범위 `1450..1550us`는 실기 값이 아니다.** 최초 PWM 부여는 현재 관절 위치를 모르면 크게 움직일 수 있음
3. 별도 GPIO GP12 물리 enable 신호 가능 여부를 보드 배선으로 확인하고 **서보 전원 독립 차단 스위치**를 설치. GP7 software STOP 및 `PWMOut.deinit()`은 물리적으로 전원 차단하는 **hard STOP이 아니다**

`ARM_OUTPUT_BUILD_ENABLED`를 단순히 True로 바꾸거나 임의로 1500us를 전송하지 말 것. 리뷰 후 원본과 합쳐야 하는 staging 코드이지 지금의 RP2040 코드를 즉시 대체하는 제품 릴리스가 아니다.

## 프로토콜 v2 초안 (전용 data CDC만; 기존 JSON/LF v1과 의도적으로 **호환되지 않음**)

LF로 끝나는 ASCII 한 줄, 128바이트 상한, 1개 공백 구분. 리플레이/요청 ID는 한 부팅 안에서 32개까지만 저장하며 오래된 ID를 지우고 재실행하지 않는다.

```text
V2 HELLO check1
V2 ARM begin1 BOOT_ID
V2 PING beat1 BOOT_ID 1
V2 MOVE req001 BOOT_ID left REVIEWED_PULSE_US
V2 MOVE req002 BOOT_ID right REVIEWED_PULSE_US
V2 STOP stop1
```

응답은 `V2 ACK ID BOOT_ID CODE POSITION_UNVERIFIED` 이다. `ACK`는 수신·요청 검사 결과이고 실제 관절 위치나 각도 도달 보증이 아니다. 별도 feedback encoder가 없다.

- `STOP`: 명령 도착 및 GP7 눌림에 의해 software stop latch. 같은 세션에서 다시 arm 불가, `PWM` 비활성화 시도
- `ARM`: *물리 활성 신호 + 검수 설정 + 맞는 BOOT_ID*를 모두 요구. 처음 `PWMOut`을 만들며 **home pulse에서 실제 서보가 움직일 수 있음**. 이 위험 때문에 기본 source build flag는 False
- `MOVE`: 각각의 검수된 `min_us..max_us`로만 target을 등록. 20ms 단위 최대 10us 순차 변화. 실제 angle feedback은 없음
- `PING`: 350ms 간격 이상의 통신 상실 시 latched software stop. Host가 주기적으로 보내야 하나 **현재 제품 host transport는 아직 미구현**
- 부팅에는 어떤 서보 PWM이나 명령도 출력하지 않는다. 전원이 공급된 서보의 안전이 자동 보장되는 것은 아니다

## 개발·실물 검수 순서

1. 먼저 코드/CI 전수 검사. **현재 장치에 복사 금지**.
2. 운영자가 **서보 전원 분리** 상태에서 기존 RP2040 `code.py`와 `boot.py`를 백업하고 GP2~GP8 USB HID 동작, USB endpoint/CDC data port 가용성을 검수. REPL/DTR/open 동작이 자동 리셋·부팅 팔 움직임을 일으킬 수 있으므로 무인 포트 접속 금지
3. GP12 물리 인터록 접근이 가능한지 설계/PCB와 실제 보드 확인. 불가능하면 GPIO 가정을 수정, 대체 스위치 검토. 외부 서보 전원 kill·퓨즈/전류제한·공통 GND 확인
4. 2개 실제 서보가 부하 없는 지그에서 어느 방향으로 어느 pulse에서 어느 위치에 있는지 각각 확인. 임의 0°·90°·180° 명령 금지
5. 테스트 장비·운영자·물리 컷오프를 준비한 후 **별도 검수된 펌웨어**만 수동 적용. 먼저 모든 팔 출력 OFF/HID·data handshake 검수, 다음 한 팔의 아주 작은 이동, 응급 STOP/heartbeat failure/전원 분리 확인
6. Pi 측 V2 host protocol은 현재 **코드 구현 및 fake-controller 검증 단계**이다. 실제 `usb_cdc.data` endpoint를 열고 유지할 승인된 Pi 포트 소유자는 아직 없으며, `MotionFeature`와 BMO AI action 라우팅은 물리 검증 전 자동 이동을 허용하지 않는다. 사용자 입회 하드웨어를 확인한 뒤 포트 식별·한 소유자·부팅 ID·주기적 heartbeat·우선 STOP·수동 조작/소각 테스트를 검수한 다음에만 이 인터페이스에 연결한다. LLM은 팔 angle/pulse를 직접 생성하지 않고 검수된 제스처만 제안한다.

## 공식 API 참고

- CircuitPython USB CDC 문서: https://docs.circuitpython.org/en/latest/shared-bindings/usb_cdc/ — data 포트는 `boot.py`에서 별도 활성화해야 하며 USB endpoint 부족 가능
- CircuitPython PWM 문서: https://docs.circuitpython.org/en/latest/shared-bindings/pwmio/ — `duty_cycle`는 16비트 값, `frequency=50` 선택은 실제 기구각 범위를 보증하지 않음
- Adafruit HID: https://github.com/adafruit/Adafruit_CircuitPython_HID — 키보드 키 전송

## 이번 실기 제약 (사용자 확인 2026-10-26)

양팔 서보에 **상시 전원**이 들어오며 사용자는 서보 전원을 별도로 차단하거나 커넥터를 분리할 수 없다. 현 보드 `/media/bgh/CIRCUITPY/code.py` 원본은 로컬 `/home/bgh/BMO_RP2040_BACKUPS/original_EWJ1G4sz/code.py`에 보존됐고 SHA-256은 `fdc65bebef17b78b945281549ddce80fb749e1a94262751d8d2f0146c394a01e`로 일치한다. 기존 `code.py`는 부팅 시 `set_arms(0)`을 호출하므로, 저장·리로드·boot.py 교체·USB CDC 재열거가 **의도하지 않은 움직임**을 만들 수 있다. **이 조건에서 새 staged 파일을 CIRCUITPY에 복사하지 않는다.** GPIO GP12은 현재 PCB 배선/스위치로 검증되지 않았다. 이 문서에서 물리적 전원 차단을 소프트웨어 STOP으로 대체할 수 있다고 주장하지 않는다.

### Pi의 안전한 코드 검사 (장치 접근 없음)

```bash
cd /home/bgh/BMO_REAL_ROBOT
# 해당 GitHub 브랜치가 별도 worktree에 반영된 경우에만:
PYTHONPATH=software python3 -B -m unittest software.tests.test_arm_firmware_v2_stage software.tests.test_arm_host_v2 -v
```

이때 `BoundedCdcExchange`는 **포트를 여는 코드가 아니며**, 실제 사용하려면 별도의 안전 검수 후 구성한 *이미 열린* 데이터 포트 객체를 받아야 한다. `ttyACM0`이 보인다고 전용 데이터 포트라고 단정하지 않는다. 기본 BMO 앱은 신규 팔 이동 기능을 여전히 비활성화한다.

**최종 수락 상태: firmware & host V2 STAGED / GitHub CI 검사 필요 / USB_CDC_PHYSICAL_NOT_RUN / MOTOR_PHYSICAL_NOT_RUN / POSITION_UNVERIFIED.**
