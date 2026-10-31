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
2. `bmo_arm_bridge.py`: CircuitPython에서 고정 동작 이름 `wave/celebrate/shrug/raise_left_arm/raise_right_arm/both_up/both_down`만 허용한다. raw PWM·각도·코드 실행 불허. heartbeat 0.9초에 추가 자동 동작 정지, 수동 GP7은 대기 중 CDC 명령을 포함해 자동 동작을 취소하고 latch한다. 수동 토글은 그대로 가능하다.
3. `boot.py`: 별도 `usb_cdc.data`를 허용하는 **선택적 예시**. 기존 `boot.py`가 있으면 먼저 검사하고 임의로 덮어쓰지 않는다.
4. Pi `LegacyArmLink` + `LegacyMotionFeature`: 기존 `FeatureHost`에 장치 옵트인으로 주입한다. GPT는 제스처 이름만 제안하고, 실제 player started에 한 번만 처리한다.
5. 감정 정책은 상위 PR #35 유지: happy→celebrate, surprised→shrug는 **제안 초기값**, R10 감정 화면 자체는 변경하지 않는다.

이 프로토콜은 구 V2 실험과 호환되지 않으며, 한 번에 명령 한 개/정확한 ACK/부팅 식별자/중복 요청 거부를 사용한다. 소프트웨어 HOLD/STOP은 **서보 전원 차단이 아니다**. 로봇은 관절 엔코더가 없으므로 실제 각도 도달은 여전히 확인되지 않는다.

## PR #36 리뷰 보완

- motor DO의 request ID는 `bmo_<양의 ASCII 십진수 sequence>`(숫자 최대 20자리)다.
  host는 `monotonic_ns`와 자체 증가값으로 ID를 만들고, 펌웨어는 ACK cache 24개와 별도로
  motor sequence 상한을 유지한다. cache 밖의 과거/동일 숫자·다른 zero padding 요청은
  `STALE_REQUEST`로 거부한다. BUSY/INVALID로 거절된 DO도 나중에 살아나지 않는다.
  같은 Pi boot의 host 재생성은 단조 시계를 이어 쓰지만 Pi만 재부팅해 sequence가 뒤로 가면
  거부될 수 있다. 자동 상한 초기화·자동 보드 reset·재전송으로 우회하지 않는다.
- remote named gesture만 90°/s 이하, 최소 20ms 간격, 최대 2°/update로 보간한다.
  늦은 poll에 밀린 각도를 몰아서 적용하지 않으며 firmware 총 한도는 15초다.
  host는 DO ACK 뒤 최대 15.2초 heartbeat를 유지하고 한도 끝에 HOLD한다.
  GP7 원본 즉시 토글과 0~180°/600~2400µs 변환은 바꾸지 않았다.
  이 수치는 소프트웨어 명령 상한이며 실제 속도·토크·기계 안전을 보정/확인한 값이 아니다.
- PWM 생성·boot write·GP7 write·자동 step·CDC 오류는 remote 취소/latch로 격리한다.
  fault 처리 자체는 PWM을 추가로 쓰지 않는다. GP8와 나머지 HID scan은 계속된다.
  자동 보간은 20ms마다 원본 `set_arms()`의 console print를 호출하지 않는다.
- 발화 시작 timestamp/context를 큐에 보존하고 HELLO 전후와 실제 DO write 직전에 TTL을
  재검사한다. 만료 결과는 rejected/not_started이며 DO를 보내거나 자동 retry하지 않는다.

자세한 검증·남은 실기는 [리뷰 수정 보고서](../../../docs/reports/PR36_B1_REVIEW_FIXES_KR.md)를 따른다.

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

현재 사용자는 별도 전기적 차단 스위치가 없다고 확인했다. 따라서 **모터 연결 시험은 현재 차단**한다.
`--arm-power-off-ready`, software STOP 또는 PWM duty=0으로 이 물리 조건이 충족되지 않는다.
기존 live `boot.py`에는 이미 data CDC 설정이 있으므로 예시로 덮어쓰지 않는다.

그 다음 별도 감독 승인과 실제 전원 차단/독립 분리 검토가 끝난 뒤에만 `REMOTE_ENABLED = True`로 만든
**단일 flag 치환 후보**를 작성하고 SHA-256을 기록한다. 앱은 단일 후보 전체 바이트·helper·기존 boot 설정을
확인하며 맞지 않으면 포트도 열지 않는다. 제스처는 이동 공간과 차단 수단이 검토된 상태에서 한쪽부터 테스트한다.

승인 이후의 앱 명령 예시(**지금 실행하지 말 것**):

```bash
./launch-desktop --live --ptt-microphone --emotion-arms \
  --arms-supervised --arm-legacy-bridge \
  --arm-reviewed-code-sha <REVIEWED_CODE_SHA256> --arm-power-off-ready
```

**현재 NOT_RUN:** Pi 실기 B1 USB handshake, helper 로딩, 실제 모터 움직임, 정지 검증, 전체 앱 통합 재검수. Linux/CI 합성 통과를 실물 검증과 혼동하지 않는다.

## 2026-10-31 현재 후보의 OFF → 검토 후보 → 감독 전환 도구

위 문서의 과거 전원·연결 기록은 현재 조건을 확인한 증거가 아니다. 이번
현장 관측은 `REMOTE_ENABLED=False`, `transport=NOT_OPENED`이고, 아래 절차는
**Cloud에서 실행하지 않았다**. `arm_prepare`는 읽기 전용 확인 도구이며
활성화 도구가 아니다. 새 `bmo_ai.arm_candidate`는 별도의 검토·백업·복귀
경로를 제공한다. 기본 실행은 dry-run이며 보드 쓰기·포트 열기·USB reset을
하지 않는다. 저장소의 `REMOTE_ENABLED=False` 원본도 그대로다.

정확한 입력/출력 전체 파일 SHA256:

- 현재 OFF 템플릿: `4e30cb8dd29f3af03867828da2fee7e1ea1c1e5099be725e7daa11b98f67ebae`
- 단일 flag 치환 enabled 후보: `0f69420e2e19500e40a925f0ad71033d86780b8c5933302713795103d1fa4a2e`
- helper: `14fc42886a39f5b74fb7bc28e7eecf0914801b05e1fe5bd74cfdb3df0f0fcbfe`

후보는 `REMOTE_ENABLED = False` 한 곳만 `True`로 바꾼다. 다른 바이트,
보정, GP7/GP8, GPIO, PWM, 좌우, B1, `boot.py`, helper를 수정하지 않는다.
현재 board code/helper가 위 바이트와 다르면 중단하며 임의 마이그레이션을
하지 않는다. 기존 `boot.py`는 CDC 설정을 검사하고 정확한 SHA를 인수
기록에 보관하며 덮어쓰지 않는다.

### 1. 실제 장치에 쓰지 않고 후보와 검토 기록 준비

Pi의 후보 앱을 정상 종료한다. 아래 `--prepare`는 보드를 읽고 private
후보/인수 기록만 만든다. 이미 존재하는 후보 파일은 덮지 않으므로 재검토는
새 디렉터리를 쓴다. dry-run에는 `--prepare`를 생략한다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.arm_candidate \
  --prepare --mount /media/bgh/CIRCUITPY \
  --work-directory /home/bgh/BMO_private/arm-candidate-20261031

diff -u firmware/staging/bmo_arm_legacy_bridge_v1/code.py \
  /home/bgh/BMO_private/arm-candidate-20261031/enabled_code.py
sha256sum /home/bgh/BMO_private/arm-candidate-20261031/enabled_code.py
```

diff의 종료값 1은 위 한 줄의 예상 차이를 뜻한다. 후보 생성은 청취·실물
승인이나 움직임 PASS가 아니다. `review.json`은 당시 board code/helper/boot
SHA와 mount를 고정한다. 보드 내용이 그 뒤 달라지면 apply가 중단한다.

### 2. 현재 물리 조건을 확인한 Pi 감독자만 apply

**보드 쓰기는 자동 reload를 일으켜 `set_arms(0)`를 실행할 수 있다.**
사용자 입회, 양팔 서보 전원 분리/전기적 격리, 움직일 공간, 즉시 물리
전원 차단 수단을 현재 세션에서 확인한다. 아래 플래그를 자동으로 채우는
wrapper나 서비스는 만들지 않는다. 확인되지 않은 조건은 해당 물리 단계만
보류한다. 앱/기존 serial owner를 종료한 상태에서 진행한다.

아래 명령은 다섯 조건을 모두 실제 확인하고 위 diff/SHA를 검토한 뒤에만
Pi 감독자가 실행한다. backup에는 적용 직전 code/helper/boot와 개별 SHA가
보관되며, 백업·재검사 후 `code.py`만 변경한다. 자동 reset/reload 명령은
보내지 않는다. 쓰기/검증 실패 시 재시도나 자동 복귀 쓰기를 하지 말고,
서보 전원을 격리한 채 보관된 backup과 현재 파일을 먼저 대조한다.

```bash
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.arm_candidate \
  --apply --mount /media/bgh/CIRCUITPY \
  --work-directory /home/bgh/BMO_private/arm-candidate-20261031 \
  --reviewed-code-sha 0f69420e2e19500e40a925f0ad71033d86780b8c5933302713795103d1fa4a2e \
  --user-present --servo-power-isolated --clearance-verified \
  --physical-cutoff-ready --reload-risk-acknowledged
```

### 3. 감독 세션, named action 한 번, 정지, GP7 보존

정적 검증 성공은 실제 연결/모터 도달 성공이 아니다. reload와 현재 보드
상태를 감독자가 확인하고, 물리 차단 수단과 공간을 유지하며 서보 재연결을
별도로 판단한다. 아래 앱은 기존 보정/단일 B1 adapter를 쓰고 자동 감정 팔
옵션은 켜지 않는다. 메뉴/명시 요청의 named action 한 번, HOLD/STOP,
GP7 수동 토글과 GP8 복귀를 감독하에 확인한다. software STOP은 전원
차단이 아니며 ACK는 관절 위치 도달 증거가 아니다.

```bash
./launch-character-life --arms-supervised --arm-legacy-bridge \
  --arm-reviewed-code-sha 0f69420e2e19500e40a925f0ad71033d86780b8c5933302713795103d1fa4a2e \
  --arm-power-off-ready
```

### 4. OFF 복귀

앱을 정상 종료하고 다시 현재 사용자 입회·서보 전원 격리·공간·물리 차단
수단을 확인한다. 위 apply 명령의 `--apply`만 `--rollback`으로 바꾸면
보관한 OFF code를 복원한다. rollback에도 같은 다섯 확인과 정확한
reviewed enabled SHA가 필요하다. 현재 enabled code/helper/boot 또는
백업이 달라지면 조용히 덮지 않고 중단한다. backup은 복귀 뒤에도 남는다.
`BMO_REAL_ROBOT`와 OS/서비스는 이 도구가 수정하지 않는다.
