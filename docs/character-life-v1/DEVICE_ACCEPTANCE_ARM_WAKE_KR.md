# 호출어·B1 팔 통신 실기 전 읽기 전용 점검

점검일: 2026-10-29 KST. 검토한 후보 HEAD는 `492472af425083ba7337abf3b577b092f051a5e5`다. [PR #40의 추가 검수 지시](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6473188150)에 따른 자료다. 아래 1차 준비 점검은 읽기 전용으로 수행했으며 실제 마이크·카메라·스피커·serial 포트를 열지 않았다. 이후 주 담당자가 단독 수행한 **B1 HELLO 1회**의 실제 결과는 문서 마지막 별도 단계에 기록했다.

이 문서는 현재 파일과 descriptor의 관찰 결과다. 기존 B1/HID/카메라 성공 기록을 다시 수행하거나, 그 성공을 이번 후보 HEAD의 실기 PASS로 바꾸지 않는다. 기존 실행본·가상환경·펌웨어·백업은 변경하지 않았다.

## 호출어 자산과 런타임

확인 범위는 `/home/bgh/BMO_CHARACTER_LIFE`, `/home/bgh/BMO_REAL_ROBOT`의 관련 파일명·모델 디렉터리, 알려진 `/home/bgh/.cache/bmo`의 파일명, Downloads의 관련 자산 파일명과 두 실행 환경의 package 디렉터리다. `.env`, API 키, 대화 기록과 개인 로그 내용을 읽거나 출력하지 않았다. 전체 home이나 루트 파일시스템을 검색하지 않았다.

| 항목 | 관찰 결과 | 판정 |
|---|---|---|
| Hey BMO 전용 manifest | 두 checkout의 관련 깊이와 알려진 BMO cache에서 실제 manifest를 찾지 못함. 저장소의 `wake_word.py`, 테스트와 문서는 모델이 아님 | `MODEL_MISSING` |
| 실제 ONNX classifier·melspectrogram·embedding | 확인 범위에서 `.onnx` 파일 없음 | `MODEL_MISSING` |
| 후보 `.venv` | Python 3.13.5, system-site-packages=false. package 디렉터리에서 `openwakeword`, `onnxruntime`, `numpy` 없음 | 선택적 호출어 런타임 미준비 |
| 기존 실행본 `.venv` | Python 3.13.5, system-site-packages=false. 해당 선택적 package 디렉터리 없음 | 기존 대화 환경 변경하지 않음 |
| 기존 `.venv-camera` | system-site-packages=true. 자체 package 디렉터리에서 호출어 런타임 없음 | 호출어 사용 가능을 증명하지 않음 |
| 시스템 Python package 디렉터리 | `/usr/lib/python3/dist-packages/numpy` 및 NumPy 2.2.4 metadata는 있음. 확인한 system/local 경로에 `openwakeword`, `onnxruntime` 없음 | NumPy 존재만으로 호출어 추론 불가 |
| 실제 모델 import·ONNX 추론 | 실행하지 않음 | `NOT_RUN` |
| 저장 WAV를 사용한 실제 호출어 인식률·오인식률 | 검증된 custom 모델이 없어 실행하지 않음 | `NOT_RUN` |

따라서 실제 `Hey BMO / Hey Beemo / 헤이 비모` 인식 완료를 주장할 수 없다. 임의의 pretrained 영어 호출어로 대신하지 않으며 모델·런타임을 자동 설치하거나 다운로드하지 않는다. 유효한 전용 모델·frontend·라이선스·SHA가 준비되면 [WAKE_WORD_SETUP_KR.md](WAKE_WORD_SETUP_KR.md)의 manifest 계약에 따라 별도 확인한다. 검증된 실제 모델이 있을 때만 주 담당자가 허용된 저장 WAV 추론부터 진행하며, 지금은 실행할 대상 모델이 없다. GP8 PTT는 모델 설치와 독립적으로 유지한다.

## B1 파일·USB descriptor 증거

`/dev/serial/by-id` symlink와 `/sys/class/tty/.../device`의 텍스트 descriptor만 읽었다. `/dev/ttyACM0`·`/dev/ttyACM1`을 열거나 ioctl·DTR·REPL·reset·쓰기 작업을 하지 않았다.

| endpoint | 읽은 interface 번호 | interface label | product / USB ID | 의미 |
|---|---|---|---|---|
| `/dev/ttyACM0` | `00` | `CircuitPython CDC control` | RP2040-Zero / `2e8a:101f` | console. 통신 점검 대상으로 사용하지 않음 |
| `/dev/ttyACM1` | `02` | `CircuitPython CDC2 control` | RP2040-Zero / `2e8a:101f` | 기존 코드가 요구하는 별도 data interface와 일치하는 descriptor |

`/media/bgh/CIRCUITPY`의 알려진 펌웨어 파일을 읽어서 바이트 SHA-256을 비교했다. 디스크에 있는 파일의 확인이며, 현재 MCU가 그 바이트를 이미 로드해 실행 중인지·서보 전원이 어떤 상태인지 증명하지 않는다.

| 파일 | SHA-256 | 결과 |
|---|---|---|
| 보존된 `original_code.py` | `fdc65bebef17b78b945281549ddce80fb749e1a94262751d8d2f0146c394a01e` | 기존 승인 원본 식별값과 일치 |
| 후보/마운트 `code.py` | `4e30cb8dd29f3af03867828da2fee7e1ea1c1e5099be725e7daa11b98f67ebae` | 두 파일 바이트가 같고 `REMOTE_ENABLED = False` |
| 후보/마운트 `bmo_arm_bridge.py` | `14fc42886a39f5b74fb7bc28e7eecf0914801b05e1fe5bd74cfdb3df0f0fcbfe` | 두 파일 바이트가 같음 |
| 마운트의 기존 `boot.py` | `f59e20f97feef6eab364bdb305cb3d7d0639c7f3aa88a0f5e3ff517912c122eb` | `usb_cdc.enable(console=True, data=True)` 텍스트 있음. 후보 예시와 다른 기존 파일을 보존 |

위 `code.py`는 원래 GP7 토글·GP8 ENTER와 GP10 오른팔/GP11 왼팔, 50 Hz, 600–2400 µs, 좌우 반전을 유지하는 B1 템플릿이다. **부팅의 `set_arms(arm_angle)` 자체는 존재한다.** 따라서 `REMOTE_ENABLED=False`는 원격 `DO` 차단이며, 보드 reset이나 전원 재인가가 모터에 아무 영향도 없다는 보장은 아니다. 전원·배선이 미확인인 상태에서 펌웨어 재시작으로 이 상태를 확인하지 않는다.

## 통신 코드가 하는 일과 하지 않는 일

검토한 경로는 기존 `OwnedCdcDataPort` → `LegacyArmLink` → `LegacyBridge`다. 파일명에 `arm_serial_posix_v2.py`가 남아 있어도 이번 검토에서 V2 프로토콜이나 새 보정값을 도입하지 않는다. B1 named-gesture 통신 경로만 확인했다.

`OwnedCdcDataPort.open()`은 정확한 `/dev/ttyACM1` data descriptor를 검사하고 `O_RDWR|O_NOCTTY|O_NONBLOCK|O_CLOEXEC`로 열어 flock을 잡은 뒤 termios를 보관하고 `tty.setraw()`를 수행한다. 소스에는 명시적 DTR 설정·console fallback·REPL 명령·Ctrl-D·baud 1200 reset·펌웨어 업로드가 없다. 닫을 때는 원래 termios를 복구하고 fd를 닫는다.

이는 **명시적 reset 코드가 없다는 소스 증거**다. Linux open/close의 실제 modem-control 상태 변화나 현재 MCU의 reset 부재를 측정한 결과는 아니다. data CDC라는 descriptor만으로 물리 reset이 절대로 없다고 보고하지 않는다. 최신 사용자 지시는 검토된 data interface의 비구동 통신을 허용했으며, 그 범위의 HELLO 1회는 아래 2차 결과로 구분한다. 실제 모터 구동에 필요한 연결·전원·현장 조건은 계속 `NEEDS_OPERATOR`다.

| B1 frame 형태 | firmware handler의 효과 | 현재 판단 |
|---|---|---|
| `B1 HELLO <새_request_id>\n` | boot token 및 READY/DISABLED/STOPPED를 ACK. handler에서 `set_side`·`set_both`·`DO` 호출 없음 | 명령 의미는 조회용. 포트 개방 영향·현재 runtime은 미검증이므로 실제 전송 `NOT_RUN` |
| `B1 PING <새_request_id> <HELLO_boot_token>\n` | enabled/non-stopped이면 `last_ping`을 갱신하고 ALIVE. disabled이면 DISABLED | 직접 PWM을 쓰지 않지만 진행 중 동작의 watchdog을 연장할 수 있음. **순수 읽기 전용 조회가 아님** |
| `B1 HOLD ...` | 현재 steps 취소 | 상태 변경. 전원 차단·각도 도달 증거가 아님. 이번 점검에서 전송하지 않음 |
| `B1 STOP ...` | steps 취소 후 stopped latch | 상태 변경. 전원 차단이 아님. 이번 점검에서 전송하지 않음 |
| `B1 DO ... <gesture>` | 검증 후 named steps를 예약하고 ACCEPTED 가능 | 모터 동작 요청. 이번 준비 절차에서 금지 |

ACK의 정확한 형식은 `B1 ACK <request_id> <boot_token> <code> POSITION_UNVERIFIED\n`이다. request ID는 새 bounded ASCII 식별자를 사용하고 ACK의 ID·boot token·코드를 일치시켜야 한다. `POSITION_UNVERIFIED`를 실제 위치 확인이나 움직임 완료로 바꾸지 않는다. 응답이 없거나 불완전·다른 boot이면 자동 재전송·reset·console 접속으로 우회하지 않는다.

`LegacyArmLink.hello()`는 READY만 정상 연결로 받아들이며 DISABLED면 `firmware_not_ready_disabled`로 fail closed 한다. 원격 차단 상태를 확인하는 별도 감독 점검에서는 **정확한 DISABLED ACK가 transport 응답의 증거**일 수 있다. 이를 READY 성공이나 모터 기능 PASS로 보고하지 않는다. 현재 마운트 파일은 disabled이므로 같은 파일이 실행 중이라면 DISABLED가 예상되지만 실제 응답을 추정하지 않는다.

또한 기존 `validate_legacy_install()`은 remote-enabled 후보 전체 SHA와 helper를 요구한다. 현재 disabled 파일에 enabled 해시를 넘겨 앱을 시작하거나 validation을 건너뛰지 않는다. 단순 HELLO 검토를 위해 `--arms-supervised`, `--emotion-arms`, `--arm-power-off-ready`를 무조건 켜는 방식도 사용하지 않는다.

## 다음 감독 단계와 중단 조건

1. 기존 BMO 대화·원본 펌웨어·GP7/GP8은 유지한다. 감독자가 실제 서보 연결·별도 전원 차단 수단·기존 serial 소유자가 없는지 확인한다. 연결·전원을 확인할 수 없으면 `NEEDS_OPERATOR`에서 멈춘다. descriptor 읽기만으로 물리 조건을 승인하지 않는다.
2. 현재 파일 SHA와 data interface가 이번 기록과 같은지 다시 확인한다. 다르면 변경 원인을 확인하며 파일을 자동 복원·수정하지 않는다. 기존 console 포트는 열지 않는다.
3. 주 담당자의 별도 통신 검수 범위에서만 data 포트 1개를 독점 개방하고 **HELLO 1회**의 정확한 ACK를 기록할 수 있다. 이 문서는 포트 개방이나 전송을 실행한 기록이 아니다. DISABLED/STOPPED면 그 상태를 보존한다. READY라면 기존 동작·전원 조건이 미확인인 채로 PING·DO를 진행하지 않는다.
4. PING은 원격 runtime·진행 중 동작 없음·전원 조건이 감독하에 확인된 경우에만 별도 검토한다. 본 단계에서는 heartbeat를 유지하거나 모터 기능을 살리는 목적으로 보내지 않는다. HOLD/STOP도 별도 상태 변경으로 구분한다.
5. 종료 시 이 점검이 연 data fd만 닫고 현재 termios 복원·fd 해제 결과를 남긴다. 기존 BMO 프로세스나 다른 USB 장치·서비스를 종료하지 않는다. 해제 여부가 불명확하면 새 owner를 열지 않는다.

| 실기 항목 | 1차 읽기 전용 준비 점검 당시의 결과 |
|---|---|
| 마운트 파일·descriptor 읽기 | 관찰 완료. 실제 runtime/전원 확인을 대신하지 않음 |
| data 포트 개방·reset 부재 확인 | `NOT_RUN / NEEDS_OPERATOR` |
| 현재 장치 HELLO·PING·HOLD·STOP ACK | `NOT_RUN` |
| 현재 장치 DO·서보 움직임·실제 위치 | `NOT_RUN / NEEDS_OPERATOR` |
| 실제 호출어 모델·저장 WAV 추론·실제 마이크 인식 | `MODEL_MISSING / NOT_RUN` |

이번 문서 작성만으로 모터나 호출어 실기를 PASS 처리하지 않는다. 실제 장치 결과가 생기면 주 담당자가 명령 종류·횟수·실제 ACK·시각·소유 자원 정리 결과를 별도 기록하고, 원본 파일 SHA와 포트/펌웨어 무변경 조건을 함께 확인한다.

## 2차 — 실제 B1 HELLO 1회 결과

위 읽기 전용 검토 이후, 주 담당자가 같은 후보 기준 HEAD `492472af425083ba7337abf3b577b092f051a5e5`의 기존 `OwnedCdcDataPort`와 `LegacyArmLink`를 사용해 data interface를 단독 개방했다. `motor_permission`은 False였으며, 아래 한 요청만 전송했다.

```text
LegacyArmLink._request("HELLO {rid}", is_hello=True)
```

수신 frame은 기존 `_request`의 ID·ASCII·종결 newline·boot token·ACK 형식 검사를 통과했다. 개별 request ID·boot token·그 해시 등 연결 식별값은 공개 보고에서 제외한다. `hello()`의 READY 요구를 우회하여 모터 동작을 허용한 것이 아니라, 원격 차단 상태의 정확한 transport 응답을 별도로 기록한 것이다.

| 관찰 항목 | 실제 결과 | 해석 |
|---|---|---|
| 전송 명령 | HELLO 정확히 1회 | named gesture나 heartbeat 요청 없음 |
| 검증된 ACK response code | `DISABLED` | 현재 장치가 B1 요청에 응답했고 원격 motion은 차단된 상태 |
| ACK 위치 상태 | `POSITION_UNVERIFIED` | 실제 서보 위치·각도 도달은 미확인 |
| 점검 코드의 elapsed | `0.0143 s` | 해당 단일 통신 점검에서 기록한 값. 대화·마이크·VAD·스피커 지연값이 아님 |
| serial 정리 | `serial_closed=True` | 이번 점검이 연 data 포트의 종료 확인 |
| 후속 명령 | 없음 | PING/HOLD/STOP/DO 전송하지 않음 |
| 펌웨어·reset | 쓰기/업로드/명시적 reset/REPL 명령 없음 | 물리 reset 부재를 별도 측정했다는 뜻은 아님 |

판정은 **`B1_TRANSPORT_PASS` — 비동작 HELLO/차단 응답만**이다. `READY` 응답, 팔 움직임, 모터 전원 차단, 각도 도달, watchdog 실기, 현재 후보의 완전한 팔 통합 PASS로 확대하지 않는다. 원래 기록했던 1차 단계의 `NOT_RUN`은 당시 상태로 남겼으며, 이번 단계에서 바뀐 항목은 실제 HELLO 응답과 소유 serial 정리뿐이다.

원시 증거는 이 세션의 비공개 장치 검수 batch에 있는 `b1_result.json`이며 저장소에 올리지 않는다. 문서 작성자가 이 파일의 비식별 결과 필드만 읽어 `commands=["HELLO"]`, `motion_commands_sent=0`, `runtime_remote_state="DISABLED"`, `serial_closed=true`와 위 통신 결과를 대조했다. 현재 서보 연결과 전원 차단 수단은 이 통신으로 확인하지 않았다. **모터 실기는 계속 `NEEDS_OPERATOR / NOT_RUN`**이고, 추가 B1 시험·firmware 변경·새 calibration은 진행하지 않는다. 호출어는 실제 모델이 없어 계속 `MODEL_MISSING / NOT_RUN`이다.
