# RP2040 기능 통합 통신 계약 초안 — staging 전용

**PARTIAL — 호스트의 메모리 모의 transport/ACK/중복 방지와 IMU trace 검사만 구현했다. 실제 펌웨어·직렬 transport·팔 구동은 구현·활성화하지 않았다.**

이 문서는 보드에 복사하는 펌웨어가 아니다. CIRCUITPY, 기존 code.py/boot.py,
USB endpoint, HID, GPIO, IMU 레지스터, 서보 또는 전원을 변경하는 명령을 제공하지
않는다. 현재 RP2040이 아래 새 규격을 구현한다고 가정하지 않는다.

기준 코드는 `software/bmo_ai/features/motion/`이다. 실행 경로는
`MotionFeature.submit → poll → FakeMotionTransport → 모의 ACK → FeatureEvent`다.
`simulated=False`와 검증된 보정 프로필을 지정해도 `real_disabled`로 거부한다.
환경 변수나 옵션으로 실물 backend를 해제하는 기능이 없다.

## 기존 보드와 분리해야 할 경계

- MPU6050은 기록상 RP2040 GP0/GP1과 INT GP9를 사용한다. Pi GPIO I2C에 직접
  연결된 센서로 취급하지 않는다.
- 팔은 기록상 GP10/GP11이다. 실제 좌우, 방향, 기구 충돌 범위, 부하 전류 및
  안전 정지는 미검증이다. 숫자 90도 복귀나 PWM off를 안전 동작으로 가정하지 않는다.
- GP7의 기존 양팔 토글과 TALK/취소 설계가 충돌하므로 새 버튼 매핑을 활성화하지 않는다.
- 기존 USB HID는 후속 설계에서도 보존 대상이다. endpoint 여유와 firmware 호환성은
  실제 구현 전에 별도 확인해야 한다.
- console/REPL은 데이터 transport로 사용하지 않는다. REPL 제어 문자, DTR 변경,
  bootloader 진입 또는 자동 controller reset을 이 모듈에서 수행하지 않는다.

## 제안되는 dedicated data 프로토콜

새 버전의 전용 데이터 채널을 사용한다. 다음은 **새 규격 제안**이며 현 펌웨어의
메시지를 추정한 것이 아니다. UTF-8 JSON 한 줄/LF, 최대 512바이트, 고정 필드,
유한 수치와 버전 검사를 기본으로 한다.

1. 호스트와 controller가 version, 전용 채널, controller boot ID, controller-side
   request dedup 지원, 보정 프로필 식별자, 안전 정지 검수 여부를 handshake한다.
   heartbeat나 USB 장치 이름만으로 이 계약을 충족했다고 하지 않는다.
2. 모델은 `none`, `wave`, `celebrate`, `shrug` 중 허용 ID만 제안한다. 새 host
   executor가 request/session/turn/mode generation을 발급한다. 모델의 각도/PWM/
   속도/셸 문자열/경로는 입력으로 받지 않는다.
3. 실제 호스트·controller는 독립적으로 검수한 좌우 제한과 방향, gesture 순서,
   속도 및 안전 정지 정책을 적용해야 한다. 현재 프로필 검증기는 min/neutral/max,
   direction, verified, safe_stop_verified를 검사하지만 실제 움직임을 구현하지 않는다.
4. controller는 같은 boot 내 같은 request ID/동일 내용에는 결과를 재전달하고
   다시 구동하지 않아야 한다. 같은 ID/다른 내용은 충돌이다. 저장 용량이 찼다면
   과거 ID를 조용히 지우고 재실행하지 말고 거부한다.
5. ACK는 명령 수신/처리의 근거일 뿐이다. 위치 피드백 센서가 확인되지 않았으므로
   실제 각도 도달이나 기계적 완료를 주장하지 않는다. 모의 결과도
   `executed=False`, `completed=False`, `completion=position_unverified`다.
6. ACK timeout, 연결 상실, controller boot 변경은 이미 실행됐는지 모르는
   `unknown` 경계다. 자동 재전송/재시작 뒤 명령 복원/과거 제스처 몰아 실행은 없다.
   새 boot가 확인되면 대기 중이던 이전 명령도 취소한다.

현재 FakeMotionTransport는 위 핵심 의미를 메모리에서만 검사한다. JSON wire
encoder, USB CDC reader/writer, firmware command handler 및 실제 모터 backend는 없다.
Python mock ACK 성공을 실물 안전 검수로 해석하면 안 된다.

## 호스트의 발화·취소·중복 정책

호스트가 먼저 `set_context(session_id, turn_id, mode_generation)`를 호출한다.
자동 장식은 현재 문맥에서 한 턴에 한 개, 실제 player started 시각의 기본
1초 TTL 안에만 수용한다. 기본 dispatch cooldown은 2초이며 큐는 최대 2개다.
큐에서 기다리는 동안 TTL이 지난 장식은 버린다. happy 표정만으로 팔 명령을 만들지 않는다.

명시적인 팔 intent는 음성 장식과 분리해 `explicit=True`로 전달할 수 있지만
host의 문맥·중복·큐·취소 검사는 그대로 적용한다. stop은 LLM을 기다리지 않고
대기 명령을 지우고 진행 중 명령은 위치 미확인으로 남긴다. 같은 턴의 늦은 장식은
거부하고 다음 새 문맥에서만 다시 수용한다. 실물의 stop/watchdog 동작은 아직
검증되지 않았으므로 이 모의 stop을 실제 팔 정지 보장으로 부르지 않는다.

논리 요청의 private journal은 공통 host가 소유한다. motion 모듈의 bounded
request/ACK ledger는 별도다. 어느 쪽도 controller feedback 없이 exactly-once
기계 움직임을 보장하지 않는다. 정상 팔 기능 준비를 IMU 존재 여부에 결합하지 않는다.

## IMU 진단과 제품 반응의 분리

IMUHealthMonitor는 이미 전달받은 기존 v1 IMU/heartbeat 관측만 검사한다.
센서/포트 polling, offset 적용, firmware reset 또는 자동 보정은 하지 않는다.

- 버전·필드·축·유한 값은 기존 hardware.parse_telemetry를 재사용한다.
- controller boot, 전역 uint32 seq/timestamp와 wrap, 역순·중복·누락, host monotonic
  freshness 및 controller/host 시간 차이를 검사한다.
- 범위 포화, 완전히 멈춘 수치, 명시적인 정지 참조에서의 바이어스를 분리한다.
  full-scale 기본 방어 범위는 16g/2000dps이며 실제 센서 설정을 입증하지 않는다.
  데이터 수집 환경의 실제 full-scale을 확인한 뒤 진단 설정에 맞춰야 한다.
- heartbeat/주소 응답만으로 healthy가 되지 않는다. 실제 관측이 수치 검사를
  통과해도 `unknown/physical_unverified`, `usable_for_control=False`다.
- `synthetic=True`인 trace만 healthy 판정 이후 duration/hysteresis/cooldown을
  거쳐 still/tilt/motion **후보**를 낸다. 후보는 들어올림의 확정 판정이 아니며
  실제 제품 반응이나 팔 안전장치 입력을 활성화하지 않는다.
- 실제 IMU의 건강 승인에는 정상 정지값·여러 자세·움직임·드리프트와 원인 확인이
  남아 있다. 기존 이상을 임의 offset으로 숨기거나 현재 고장을 단정하지 않는다.

실제 controller, 양팔 위치 도달, 안전 정지, 센서 정상성 및 장치 통합은 모두
NOT_RUN이다. 이 staging 자료만으로 실물 기능을 활성화할 수 없다.
