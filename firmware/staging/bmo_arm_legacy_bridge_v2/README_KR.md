# B1 explicit prepare 후보 — 2026-11-08

이 폴더는 **Pi 적용·실물 검수 전 후보**다. Cloud는 실제 보드, 서보, 전원을
조작하지 않았다. 기존 v1과 정상본 `BMO_REAL_ROBOT`은 그대로 보존한다.
현재 보드 버전·전원·입회 상태는 Pi가 다시 확인한다.

확인된 코드 원인은 v1의 STOP/GP7/watchdog latch와 재활성화 명령 부재다.
`Use arms` OFF, 앱 정상 종료의 STOP 뒤에는 ON 또는 같은 아이콘 재실행만으로
다시 움직일 수 없다. 이 보호를 자동으로 없애지 않고 **Prepare arms**라는
명시적인 사용자 동작으로 현재 상태를 확인하고 복귀한다.

## 변경 범위와 현재 동작

- `code.py`는 v1과 버전 주석 한 줄만 다르다. GP6=Z, GP8=Enter, GP7 수동
  토글, GP10/GP11, 좌우 반전, 600~2400µs, 50Hz, 원본 PWM/부팅 동작은 같다.
- HELLO는 현재 READY/STOPPED를 관측한다. 정지 상태에서 STATUS가 발급한
  2초 유효·단일 사용 token과 같은 boot에 대해서만 PREPARE를 받는다.
- PREPARE는 PWM·위치 초기화·과거 궤적·DO를 실행하지 않는다. 이전 DO의
  sequence를 모두 차단하고 새 사용자 요청만 기다린다.
- 중간 STOP/GP7/fault는 token을 무효화한다. 재접속, ON, 자동 몸짓, 반복,
  heartbeat는 PREPARE를 보내지 않는다. 요청/턴 중복 방지 기록도 유지한다.
- STOP/GP7 및 마지막 명령 위치가 남은 watchdog/gesture timeout만 명시적으로
  준비할 수 있다. servo/clock/CDC/unknown-position fault는 현장 점검이 필요하다.
  watchdog에서 마지막 **명령** 위치를 보존하며 실제 위치가 검증됐다는 뜻은 아니다.
- ACK는 통신 응답이다. 준비 완료·ACCEPTED는 실제 팔 움직임/도착 위치의 증명이 아니다.
  STOP은 전기적 전원 차단도 아니다.
- v1은 계속 지원한다. v1 STOP에서 Prepare를 누르면
  `arm_prepare_firmware_required`로 안내하고 STOP을 유지한다.

검토 대상 enabled `code.py` SHA256:
`71ff1f577a0108c780587e96bbde3b17482e925e3c78c2fd521d6608d4360e6c`

검토 대상 `bmo_arm_bridge.py` SHA256:
`6f4e6f4322bc04186cc5472f04c3a8c8636fabf82c5e907cfa1b612fb70dcc21`

저장소 원본 `REMOTE_ENABLED=False`는 유지한다. enabled 파일은 helper가 별도
private 검토 폴더에 만든다. boot.py는 보관/검증만 하며 덮어쓰지 않는다.

## Pi 준비와 명시적 적용

앱을 정상 종료한다. 먼저 읽기 전용으로 현재 v1 enabled 보드와 candidate
해시를 대조하고 private 후보를 준비한다. 기존 후보 폴더는 덮어쓰지 않는다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.arm_candidate --recovery-protocol
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.arm_candidate --recovery-protocol \
  --prepare --work-directory /home/bgh/BMO_private/arm-recovery-v2-review
```

Pi 담당이 두 파일 변경을 검토한다. **입회·서보 전원 실제 격리·공간 확보·즉시
전원 차단 수단·CircuitPython reload 위험 확인이 현재 충족된 경우에만** 다음을
직접 실행한다. 파일 쓰기는 auto-reload와 원래 부팅 PWM을 유발할 수 있다.
이 확인값을 일상 실행 설정이나 아이콘에 저장하지 않는다.

```bash
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.arm_candidate --recovery-protocol \
  --apply --work-directory /home/bgh/BMO_private/arm-recovery-v2-review \
  --reviewed-code-sha 71ff1f577a0108c780587e96bbde3b17482e925e3c78c2fd521d6608d4360e6c \
  --reviewed-helper-sha 6f4e6f4322bc04186cc5472f04c3a8c8636fabf82c5e907cfa1b612fb70dcc21 \
  --user-present --servo-power-isolated --clearance-verified \
  --physical-cutoff-ready --reload-risk-acknowledged
```

helper는 v1 code/helper/boot 전체와 SHA를 `backup-v1`에 먼저 동기 저장한다.
두 파일의 교체는 원자적이지 않다. 일부 쓰기 후 실패했다면 전원 격리를 유지하고
같은 명령의 `--apply`를 **`--rollback`**으로 바꿔 명시적으로 복구한다.
알 수 없는 제3자 변경 파일이나 바뀐 boot는 덮어쓰지 않는다.
정상 rollback도 현재 입회·전원 격리가 필요하다. USB reset은 하지 않는다.

## 최초 감독 검수 → 같은 아이콘의 일상 사용

1. Pi가 검토 SHA와 현재 파일 SHA를 확인한다. 이전 v1 commissioning receipt는
   v2를 자동 허용하지 않는다. 기존 세션용 supervised 실행 옵션으로 v2를 시험한다:
   `--arms-supervised --arm-legacy-bridge --arm-reviewed-code-sha <위 code SHA> --arm-power-off-ready`.
   기존 앱의 다른 승인 옵션은 유지하며 이 세션용 확인을 아이콘에 영구 저장하지 않는다.
2. 감독 조건을 실제 확인한 뒤 한 동작, Stop, Prepare arms, 새 동작을 확인한다.
   Prepare 자체는 움직이면 안 된다. GP7 이후 Prepare, 기존 GP6/GP8 입력,
   watchdog 정지, 명시적 취소/중복 방지도 확인한다. 오래된 요청은 재실행되면 안 된다.
3. 실물 검수 완료 후 기존 `bmo_ai.arm_usage --record-first-test`에 위 code SHA,
   실제 test-session-id 및 다섯 완료 항목을 기록한다. 이는 역사적 검수 기록이며
   현재 입회/전원 상태를 저장하지 않는다. 사용 선호 `Use arms`와
   `Conversation gestures`는 각각 메뉴에서 선택한다.
4. 같은 BMO Candidate 아이콘을 정상 종료·재실행한다. 상태가 Stopped이면
   **Arms → Prepare arms**를 누른 후 원하는 동작을 별도로 누른다.
   **Arms → Stop arms** 후에도 같은 순서로 복귀한다. 연결 문제는 팔에만 격리된다.

기존 실제 모델 대답·player-start·자동 몸짓 규칙은 Host가 결정한다. 이 후보는
그 빈도나 감정 모델을 바꾸지 않는다. 실물 승인과 통신 ACK를 별도로 기록한다.
