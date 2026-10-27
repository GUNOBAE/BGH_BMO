# PR #36 — 기존 원본 호환 B1 리뷰 수정·검수 인계

대상은 [PR #36](https://github.com/GUNOBAE/BGH_BMO/pull/36)의
`feature/bmo-legacy-arm-original-compatible-v1` 브랜치다. 검토 기준 commit은
`afc75c2690237e7a4d93736cf130a734989c2234`다. 다른 V2 구현을 새로 만들거나
`BMO_REAL_ROBOT`, `BMO_ARM_STAGE`, 원본 펌웨어/비공개 백업을 교체하지 않는다.

## 리뷰 네 항목

| 리뷰 | 수정·회귀 확인 대상 |
|---|---|
| [P1: ACK cache 축출 뒤 재실행](https://github.com/GUNOBAE/BGH_BMO/pull/36#discussion_r4388742630) | bounded ACK cache와 별도의 단조 요청 sequence 상한으로 과거 motor 요청 거부. heartbeat로 cache가 밀려도 동일 동작 재실행 금지 |
| [P1: servo 오류가 전체 버튼 loop 종료](https://github.com/GUNOBAE/BGH_BMO/pull/36#discussion_r4388742642) | remote PWM/CDC/시계 오류는 자동 동작만 취소·latch. boot·GP7 PWM 예외도 GP8 및 나머지 HID loop에 전파하지 않음 |
| [P1: endpoint로 순간 이동](https://github.com/GUNOBAE/BGH_BMO/pull/36#discussion_r4388742654) | remote named gesture에 시간·각도 상한을 둔 중간 명령 적용. 늦은 poll에서 한꺼번에 따라잡지 않음. 원본 endpoint·pulse 함수·수동 GP7 즉시 토글 보존 |
| [P2: 큐 대기 중 playback TTL 만료](https://github.com/GUNOBAE/BGH_BMO/pull/36#discussion_r4388742662) | speech 시작 시점과 context 보존, poll/HELLO 전후/실제 DO 직전 재검증. 오래된 제스처는 실행하지 않고 terminal 결과로 끝냄 |

최종 수정 SHA와 해당 HEAD의 Python 3.11/3.13 Actions 링크는 PR #36 완료 댓글에 기록한다.
이전 commit의 CI를 현재 commit의 성공으로 쓰지 않는다.

## 구현 상세

- ACK cache는 24개로 유지하지만 motor DO sequence 상한은 별도 정수로 보존한다.
  `bmo_<1~20자리 양의 ASCII 십진수>`만 motor ID로 받으며 zero padding이 달라도 같은 정수는
  같은 명령이다. cache에 남은 정확한 재전송은 ACK만 돌려주고, cache 밖 과거 요청은
  `STALE_REQUEST`로 거부한다. BUSY/INVALID로 거절된 DO도 나중에 새 동작으로 변하지 않는다.
  PING·HELLO cache 축출은 이 상한을 지우지 않는다.
- host ID는 `monotonic_ns`와 local 증가값 중 큰 값을 사용한다. 같은 Pi boot의 새 Desktop
  instance가 1부터 ID를 재사용하지 않는다. Pi만 재부팅해 MCU보다 시퀀스가 뒤로 간 경우
  fail-closed 거부될 수 있다. 이를 자동 reset/retry/펌웨어 재시작으로 해제하지 않는다.
- 자동 동작만 **90°/s 이하 / 최소 20ms 간격 / 최대 2° per update**로 보간한다.
  밀린 시간의 credit은 쌓지 않고 한 poll에 한 번만 갱신한다. 기존 wave waypoint와 최종
  0/180°, 좌우 역방향 pulse mapping은 그대로다. firmware 전체 예산은 15초다.
  host는 DO ACK 뒤 최대 15.2초 heartbeat를 유지하고 끝에 HOLD한다. 완료 위치는 추정하지 않는다.
  자동 양팔 보간은 같은 원본 `servo_angle` mapping을 쓰되 매 tick console print를 만들지 않는다.
- PWM 생성·boot·GP7·remote callback·CDC·clock 예외를 격리한다. 자동 trajectory를 비우고
  latch하며, fault 대응을 위해 또 PWM을 쓰지 않는다. GP7은 idle에 대기 중인 CDC까지
  차단하는 manual override이고 정상 수동 토글은 즉시 원본 함수로 처리한다.
- queued `started_at/context/explicit/queued_at`를 보존한다. admission 외에 poll, HELLO 전후,
  권한 검사와 sequence 생성 뒤 **DO write 바로 직전**까지 TTL·context를 검사한다.
  만료 요청은 rejected/not_started로 끝나며 DO 0회, 자동 retry 0회다.

## 자동 검증 결과

로컬 격리 venv / Python **3.13.5**, 최종 동결 소스:

```text
Ran 1268 tests in 75.114s
OK
failures=0 errors=0 skipped=0
```

B1 관련 53개를 포함한다: controller 18, 기존 host 6, 새 host 경계 16,
실제 staged button loop 10, 실제 Desktop/PTT→B1 integration 3이다.
기존 controller 7개는 waypoint·속도 상한을 함께 검사하도록 갱신했고 삭제하지 않았다.
추가 회귀는 총 40개(+11 controller, +16 host, +10 button, +3 integration)다.

| Headless 프로필 | Offline | Desktop | PTT | Camera/Media | 합계 |
|---|---:|---:|---:|---:|---:|
| 공개 fixture | 6 | 7 | 12 | 7 | 32 PASS |
| R10 | 6 | 7 | 12 | 7 | 32 PASS |
| v13.2 | 6 | 7 | 12 | 7 | 32 PASS |

총 **96 PASS**, forbidden 호출 0, 원본 SHA before=after다.
R10 `8c3ef98ba0e0645213c95295a24d7353fc2865b5ad9bc85390360c4ec9b78c82`,
v13.2 `38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`를 유지했다.
`pip check`, Python/선택된 staged firmware의 `compileall`, `git diff --check`도 통과했다.
CI에는 B1 staging 경로 변경도 회귀를 시작하도록 path와 syntax-only compile을 추가했다.
이는 firmware를 import하거나 배포하는 작업이 아니다.

Desktop 통합 검사는 실제 FeatureHost/PTT worker·voice owner·LegacyMotionFeature·B1 link·staged
controller를 이어 실행하고 장치/provider 경계만 대체한다. GP8 입력→답변 시작에 celebrate 한 번,
repeat/오래된 speech-start에는 DO 0회, 실제 host heartbeat 30개 뒤 옛 frame 재실행 금지를 확인한다.
단순 selector 검사나 현재 firmware를 바꾸지 않는 문서 작업만으로 끝내지 않았다.

## 보존 기준과 검사 의미

- 보존된 `original_code.py` SHA-256:
  `fdc65bebef17b78b945281549ddce80fb749e1a94262751d8d2f0146c394a01e`.
- 원본 `servo_angle()`·`set_arms()` 소스, 0~180° clamp, 600~2400 µs,
  왼쪽 역방향과 GP10 오른팔/GP11 왼팔 50 Hz를 보존한다. 새 보정값이나 offset을 넣지 않는다.
- GP7은 기존 양팔 0↔180° 수동 토글이며 자동 동작의 수동 override다.
  GP8 Enter PTT, GP2~6 HID, R10 얼굴, Aaron Soft/GPT, 실제 카메라 사진·영상과
  Desktop의 기존 단일 엔진 구조는 유지한다. IMU는 제어/안전 판단에 쓰지 않는다.
- staged `code.py`의 기본 `REMOTE_ENABLED=False`와 원본 부팅 DOWN 설정을 보존한다.
  부팅 DOWN은 물리적으로 팔을 움직일 수 있으므로 "서보가 꺼진다"는 뜻이 아니다.
- 자동 제스처의 속도 상한은 **명령 각도 변화의 소프트웨어 상한**이다. encoder가 없으므로
  실제 관절 위치·속도·토크·기계 안전을 확인했다는 뜻이 아니다.
- `software/tests/test_b1_button_regression.py`는 저장소의 실제 staged `code.py`와
  `bmo_arm_bridge.py`를 메모리의 board/PWM/CDC/HID/time 대체 모듈로 실행한다.
  PWM 예외 뒤에도 실제 while loop가 GP8/모든 HID press-release와 수동 GP7을 처리하는지 검사한다.
  원본 함수와 실제 endpoint pulse 값도 검사한다. 실제 CircuitPython 보드/USB/서보 실행은 아니다.

이번 작업의 자동 검증은 장치·유료 API를 사용하지 않는다. 실제 RP2040 flash/port open/DTR/REPL,
펌웨어 재실행, 카메라/마이크/서보 동작, Pi 재부팅·서비스 변경·main 병합을 하지 않는다.
사용자가 이미 확인한 GP8 실대화와 카메라 사진/영상 성공은 보존해야 할 **사용자 확인 baseline**이다.
이번 수정의 물리 검수 PASS로 옮겨 쓰지 않는다.

## 현재 안전 차단 조건

사용자 인계에 따르면 BMO 전원과 서보 전원이 묶여 있고 **별도의 물리 차단 스위치가 없다**.
`--arm-power-off-ready` 같은 CLI flag, software HOLD/STOP 또는 PWM duty=0은 실제 전원 차단을
만들지 않는다. 이 조건을 임의로 승인된 것으로 간주하지 않는다. 모터를 연결한 새 B1 후보
배포·시험은 접근 가능한 전기적 차단/독립 분리와 사용자 감독 계획이 별도로 검토되기 전까지 차단한다.

실물의 현재 `code.py`는 사용자가 복원한 원본이며 아직 B1 명령을 구현하지 않는다.
`boot.py`는 이미 `usb_cdc.enable(console=True, data=True)`를 포함한다고 인계됐다.
기존 boot 파일을 예시로 덮어쓸 이유가 없다. `/dev/ttyACM0`·`ttyACM1` 존재만으로 어느 포트가
data인지 확정하거나 아무 포트나 열지 않는다.

## 사용자 감독하의 단계별 검수

각 단계에서 실패·불명확한 상태가 나오면 그 하드웨어 단계만 중단하고 결과를 기록한다.
자동 retry, 광역 kill, 보드 reset, 재보정으로 실패를 숨기지 않는다.

1. **장치 없는 코드 확인.** 이 브랜치의 offline 회귀와 CI를 확인한다.
   `software`에서 `python -B -m unittest discover -s tests -v`를 실행한다.
   dry-run/status는 실제 port·센서·유료 API를 열지 않는다는 회귀도 함께 확인한다.
2. **전원 분리.** 사용자가 정상 앱 종료 후 BMO 전체 전원을 끄고 양쪽 서보의 3핀 connector를
   모두 분리한다. 전원이 켜진 상태에서 connector를 뽑지 않는다. 별도 차단이 없는 현재 상태에서는
   원본 부팅 동작을 포함해 어떤 재시작도 모터 분리 전 시험하지 않는다.
3. **원본 전체 보존.** 사용자 감독하에 CIRCUITPY 전체 및 기존 boot/helper/library를 새 날짜의
   비공개 백업에 보존하고 파일 수·SHA를 비교한다. 기존 백업을 덮어쓰거나 지우지 않는다.
   현재 원본 `code.py` SHA가 위의 baseline과 다르면 이유를 먼저 확인한다. 기존 live boot는 보존한다.
4. **선택적인 무서보 B1 시험.** 양쪽 서보가 분리된 상태에서만 사용자가 별도로 검토한
   staged disabled 후보/helper를 배포하는 다음 작업을 승인할 수 있다. 이번 자동 작업은 배포하지 않는다.
   data interface identity를 읽기 전용 metadata로 확인한 후 별도 승인된 도구만 사용한다.
   HELLO→DISABLED와 GP2~6/GP8 HID press-release를 먼저 확인한다. GP7 역시 모터가 분리된 상태에서
   원본 토글 상태만 확인한다. CDC 오인/부분 ACK/불명 종료가 있으면 다음 단계로 가지 않는다.
5. **훗날의 한 팔 시험.** 물리 차단·독립 전원 분리, 이동 공간·간섭·기존 끝점 검수와 사용자 감독이
   별도로 승인된 뒤에만 검토된 motor-enabled 후보/helper의 정확한 SHA로 진행한다.
   새로운 보정을 하지 않고 기존 범위 안에서 한 팔부터 검수한다. GP7 override·software HOLD/STOP·
   heartbeat 상실·전체 전원 차단을 각각 다른 기능으로 확인한다. 명령 ACK는 실제 위치 도달 증명이 아니다.
6. **감정 통합 검수.** 앞 단계가 합격한 뒤 한 번의 실제 음성 시작에 한 번만 named gesture가
   연결되는지 확인한다. listening/thinking/idle/repeat에는 새 제스처가 없어야 한다.
   지연된 speech-start, mode/turn 변경, 취소·GP7 override, 중복 요청은 재실행되지 않아야 한다.
   이후 GP8 실대화·카메라 사진/영상·R10·Aaron·정상 종료/재진입을 함께 다시 확인한다.

## 남은 실기 항목과 복구

| 항목 | 이번 자동 작업 |
|---|---|
| B1 data interface 식별·실제 HELLO/DISABLED·CircuitPython helper 호환 | NOT_RUN |
| GP7·GP8와 나머지 HID의 실제 버튼/USB 오류 후 유지 | NOT_RUN |
| 실제 제스처 속도·토크·간섭·물리 전원 차단 | NOT_RUN / 차단 조건 미충족 |
| RP2040 firmware 변경·Pi 재부팅·서보 동작 | 실행하지 않음 |
| 최신 수정과 실제 GP8/GPT/Fish·Camera3·Desktop 재검수 | NOT_RUN; 기존 사용자 baseline 보존 |

이번에는 live firmware나 호스트 서비스를 바꾸지 않아 장치 rollback을 실행할 대상이 없다.
후속 배포 시험의 복구는 BMO 전체 전원 OFF·양쪽 서보 분리 후 원본 전체 백업과 정확한 SHA를
기준으로 사용자가 감독한다. 원본/개인 백업 삭제, V2 firmware 자동 복원, 다른 서비스를 종료하는
방법을 rollback으로 사용하지 않는다. Git 변경은 검토 후 이 PR의 수정 commit만 되돌리며
reset/clean/force push/main 병합을 하지 않는다.
