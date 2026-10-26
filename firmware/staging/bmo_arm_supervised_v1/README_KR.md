# BMO RP2040-Zero 양팔 제어 v2 — *배포 금지* 펌웨어 초안

**상태: STAGING / MOTOR_OUTPUT_DISABLED.** 실제 RP2040에는 이전 commit `a6d7c9f`의 `code.py`/`safe_arm_core.py`/`boot.py`가 이미 사용자 감독하에 설치됐다. 두 서보 3핀 분리 상태에서 GP8 HID와 `ttyACM1` 전용 CDC `HELLO` 실제 왕복이 성공했다. **이 브랜치의 이후 수정본은 아직 RP2040에 복사되지 않았다.**
기존 원본 백업은 그대로 보존. GP7 버튼 의미가 이전 firmware와 달라지므로 업데이트 시 주의. 어떠한 펌웨어에서도 실제 모터 출력 활성화는 사용자 승인 전 금지.

## 작업 이유

2026-10-26 사용자 보고: GP8 음성 대화, Camera Module3 Wide 실제 사진과 저속 무음 영상이 작동함. 다음 목표는 양팔을 **각각** 안전하게 제어하는 것.

읽기 전용 기존 펌웨어 조사에서 다음이 확인됐다.

- GP2 UP / GP3 LEFT / GP4 RIGHT / GP5 DOWN / GP6 triangle(Z) / GP8 green(Enter): USB HID 입력
- GP7 빨간 버튼: 원본 펌웨어의 양팔 0°↔180° 토글을 사용하지 않는다. **현재 Pi 실기 버전(a6d7c9f)은 누르면 STOP**, 새 코드에서는 **누르고 있을 때만 소프트웨어 ARM 허용, 놓는 즉시 STOP 래치** 방식이다. 둘 다 물리적 전원 차단은 아니다.
- right PWM GP10 / left PWM GP11, 50 Hz, 부팅 `set_arms(0)` 호출이 기존 펌웨어에 있음
- 현재 컨트롤러에는 안전한 개별 팔 host command/ACK/STOP/watchdog 규약이 확인되지 않음
- IMU MPU6050은 건강 문제 기록이 있어 팔 제어/안전 피드백에 사용하지 않음

## 이번에 실제 작성한 소스

- `safe_arm_core.py`: GPIO/장치 import가 **전혀 없는** 제한된 `V2` ASCII/LF 명령 파서, boot-id gate, 좌우 별도 reviewed pulse bounds, 중복 ID 방어, 느린 pulse slew, 350ms heartbeat watchdog, GP7 software STOP에 대응하는 latch, 위치 피드백 **없음**
- `code.py`: 실제 CircuitPython의 GP2~6/8 HID 유지. **미연결 GP12 사용을 제거**하고 실물 GP7 빨간 버튼을 **누르는 동안만 활성화되는 소프트웨어 deadman**으로 사용한다. GP7을 놓으면 STOP 래치. 기본 `ARM_OUTPUT_BUILD_ENABLED = False` + `REVIEWED_CALIBRATION = None`이므로 **서보 PWM은 비활성화된다.** 두 플래그는 임의로 해제하지 말 것.
- `boot.py`: 명시적 테스트 시의 `usb_cdc.enable(console=True, data=True)` 예시. 전용 `usb_cdc.data`만 사용하며 `usb_cdc.console` (REPL)로 모터 명령을 보내지 않음. RP2040의 USB endpoint 예산 확인/재열거 전엔 설치 금지
- `software/tests/test_arm_firmware_v2_stage.py`: pure CPython fake PWM으로 부팅 무동작, 부정 frame, physical gate, boot ID, 중복, 2개의 pulse 한계, slew, timeout, STOP latch 확인. **실제 하드웨어/펌웨어 테스트가 아님**
- `software/bmo_ai/features/motion/arm_host_v2.py`: Pi 호스트의 **같은 V2 프로토콜**을 encode/decode한다. `HELLO/ARM/PING/MOVE/STOP`과 `ACK`의 ID·boot 불일치, 미승인 물리 조건, heartbeat 만료를 차단한다. `BoundedCdcExchange`는 **외부에서 이미 열고 식별·검수한 전용 포트 객체만 받는다**. 포트 자동 검색, 시리얼 open, DTR/RTS 변경 또는 기존 HID 제어를 하지 않는다.
- `software/tests/test_arm_host_v2.py`: 호스트 → 실제 staged firmware parser/controller core → ACK 왕복과 둘 다 좌우 개별 target, STOP, heartbeat, 중복/실패/이전 boot를 오프라인에서 검사. **현재 보드와 통신하는 실기 드라이버는 아님**.

### 왜 기본 실행이 막혀 있는가

세 단계가 모두 필요하다.

1. 펌웨어 배포 변경을 사용자가 감독하고, 기존 `code.py` 및 `boot.py`·키/HID·USB 열거·서보 전원 케이블을 백업·대조
2. 실제 좌우 서보의 물리 home과 pulse(µs) min/max를 각각 측정해 작성. **예제 테스트 범위 `1450..1550us`는 실기 값이 아니다.** 최초 PWM 부여는 현재 관절 위치를 모르면 크게 움직일 수 있음
3. 첫 벤치 시험은 **서보 3핀 커넥터를 통째로 뽑은 상태**에서 시행했고, 이 조건으로 실기 GP8·CDC HELLO 성공을 확인했다. GP7 deadman은 **소프트웨어 게이트**일 뿐 실제 서보 전원을 끊는 스위치가 아니다. 서보 재연결·움직임 시험은 BMO 전체 전원을 끈 다음 한쪽 커넥터만 연결하고, 전원을 즉시 끌 수 있게 준비한 상태에서 검수된 home/범위로 수행해야 한다.

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

- `STOP`: 명령 도착 또는 **GP7 deadman 놓기**에 의해 software stop latch. 같은 세션에서 재arm 불가, PWM 비활성화 시도. GP7은 현재 장치 버전에서는 *누르면 STOP*이므로 두 버전의 차이를 분명히 확인한다.
- `ARM`: *물리 활성 신호 + 검수 설정 + 맞는 BOOT_ID*를 모두 요구. 처음 `PWMOut`을 만들며 **home pulse에서 실제 서보가 움직일 수 있음**. 이 위험 때문에 기본 source build flag는 False
- `MOVE`: 각각의 검수된 `min_us..max_us`로만 target을 등록. 20ms 단위 최대 10us 순차 변화. 실제 angle feedback은 없음
- `PING`: 350ms 간격 이상의 통신 상실 시 latched software stop. Host가 주기적으로 보내야 하나 **현재 제품 host transport는 아직 미구현**
- 부팅에는 어떤 서보 PWM이나 명령도 출력하지 않는다. 전원이 공급된 서보의 안전이 자동 보장되는 것은 아니다

## 개발·실물 검수 순서

1. 먼저 코드/CI 전수 검사. **현재 장치에 복사 금지**.
2. 운영자가 **서보 전원 분리** 상태에서 기존 RP2040 `code.py`와 `boot.py`를 백업하고 GP2~GP8 USB HID 동작, USB endpoint/CDC data port 가용성을 검수. REPL/DTR/open 동작이 자동 리셋·부팅 팔 움직임을 일으킬 수 있으므로 무인 포트 접속 금지
3. 새 GP7 deadman 동작을 서보 커넥터 분리 상태에서 검증한다. 원본에서 GP7은 양팔 토글, 기존 staged 실기는 누르면 STOP, 이번 새 staging은 **놓으면 STOP**이다. USB HID 키 GP8이 계속 Enter를 보내는지 검수한다. BMO 전체 전원을 끄는 동작과 서보 외부 전원/배선·공통 GND 확인.
4. 2개 실제 서보가 부하 없는 지그에서 어느 방향으로 어느 pulse에서 어느 위치에 있는지 각각 확인. 임의 0°·90°·180° 명령 금지
5. 테스트 장비·운영자·물리 컷오프를 준비한 후 **별도 검수된 펌웨어**만 수동 적용. 먼저 모든 팔 출력 OFF/HID·data handshake 검수, 다음 한 팔의 아주 작은 이동, 응급 STOP/heartbeat failure/전원 분리 확인
6. Pi 측 V2 host protocol은 현재 **코드 구현 및 fake-controller 검증 단계**이다. 실제 `usb_cdc.data` endpoint를 열고 유지할 승인된 Pi 포트 소유자는 아직 없으며, `MotionFeature`와 BMO AI action 라우팅은 물리 검증 전 자동 이동을 허용하지 않는다. 사용자 입회 하드웨어를 확인한 뒤 포트 식별·한 소유자·부팅 ID·주기적 heartbeat·우선 STOP·수동 조작/소각 테스트를 검수한 다음에만 이 인터페이스에 연결한다. LLM은 팔 angle/pulse를 직접 생성하지 않고 검수된 제스처만 제안한다.

## 공식 API 참고

- CircuitPython USB CDC 문서: https://docs.circuitpython.org/en/latest/shared-bindings/usb_cdc/ — data 포트는 `boot.py`에서 별도 활성화해야 하며 USB endpoint 부족 가능
- CircuitPython PWM 문서: https://docs.circuitpython.org/en/latest/shared-bindings/pwmio/ — `duty_cycle`는 16비트 값, `frequency=50` 선택은 실제 기구각 범위를 보증하지 않음
- Adafruit HID: https://github.com/adafruit/Adafruit_CircuitPython_HID — 키보드 키 전송

## 이번 실기 제약 (사용자 확인 2026-10-26)

양팔 서보의 전원에 별도 스위치는 없지만, 사용자가 **전체 전원을 끄고 양팔의 각 3핀 커넥터를 뽑을 수 있음**을 확인했다. 실제로 이를 분리했다고 보고한 상태에서 펌웨어/HID/USB CDC 실기 테스트에 성공했다. 현 보드 `/media/bgh/CIRCUITPY/code.py` 원본은 로컬 `/home/bgh/BMO_RP2040_BACKUPS/original_EWJ1G4sz/code.py`에 보존됐고 SHA-256은 `fdc65bebef17b78b945281549ddce80fb749e1a94262751d8d2f0146c394a01e`로 일치한다. 기존 `code.py`는 부팅 시 `set_arms(0)`을 호출하므로, 저장·리로드·boot.py 교체·USB CDC 재열거가 **의도하지 않은 움직임**을 만들 수 있다. **새 소스는 아직 RP2040의 설치판과 다르다.** 이번 버전은 GPIO GP12 가정을 제거하고 GP7 deadman을 사용한다. 하지만 기본 PWM 출력을 여전히 비활성화하며, 실제 서보 연결 및 움직임을 승인하지 않는다. GPIO 입력 기반 소프트웨어 STOP을 물리적 전원 차단으로 부르지 않는다.

### Pi의 안전한 코드 검사 (장치 접근 없음)

```bash
cd /home/bgh/BMO_REAL_ROBOT
# 해당 GitHub 브랜치가 별도 worktree에 반영된 경우에만:
PYTHONPATH=software python3 -B -m unittest software.tests.test_arm_firmware_v2_stage software.tests.test_arm_host_v2 -v
```

이때 `BoundedCdcExchange`는 **포트를 여는 코드가 아니며**, 실제 사용하려면 별도의 안전 검수 후 구성한 *이미 열린* 데이터 포트 객체를 받아야 한다. `ttyACM0`이 보인다고 전용 데이터 포트라고 단정하지 않는다. 기본 BMO 앱은 신규 팔 이동 기능을 여전히 비활성화한다.

**실기 수락: 기존 staged `a6d7c9f`에서 GP8 HID / dedicated CDC V2 HELLO(\`UNCALIBRATED\`) 실제 성공. 최신 GP7 deadman 브랜치의 실물 적용·STOP·모터 시험은 NOT_RUN. 최종 수락: firmware & host V2 STAGED / MOTOR_PHYSICAL_NOT_RUN / POSITION_UNVERIFIED.**
