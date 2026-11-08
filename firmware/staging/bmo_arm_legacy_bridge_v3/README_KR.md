# B1 발화 연출 후보 — 2026-11-09

이 폴더는 **Pi 적용·실물 검수 전 후보**다. Cloud는 보드·서보·전원을 조작하지
않았다. 최신 Pi 인계(댓글 6722827839)는 v1 사용, 메뉴 네 동작 관찰이며
v2 미적용·자동 대화 몸짓 미검증이다. 후속6726186614에서 v1 HELLO 한 번의
READY/stopped=false를 관측했으며 DO/STOP/PREPARE/flash/reset은 하지 않았다.
이 관측은 현재 또는 향후 자세·실제 움직임의 보증이 아니다. 현장 위치·속도·동작 시간은 측정되지 않았다.
이 후보는 속도 개선으로 보고하지 않는다.

기존 v1/v2, 승인 핀·보정·좌우 반전·수동 동작과 BMO_REAL_ROBOT을 보존한다.
v3는 v2의 명시적인 Prepare arms와 별개로 발화 중 진입/유지/복귀를 추가한다.
`code.py`는 v1 앞에 버전 주석 한 줄만 붙였다. `REMOTE_ENABLED=False`가
저장소 기본이며 GP6=Z, GP8=Enter, GP7 수동, GP10/11, 600–2400µs, 50Hz는 같다.
최대 90°/s·한 업데이트 2°·20ms의 기존 상한도 그대로다.

## 제품 경로와 프로토콜

- 실제 LegacyMotionFeature/B1의 `start_performance`는 실제 player-start의
  검증된 현재 턴만 큐에 넣는다. Host가 한 답변 한 번·45초 간격·이동 5분 두 번과
  상황 억제를 적용한다. 도착을 기다려 음성/마이크/다음 대화를 지연시키지 않는다.
- `CAPS` 응답 `PERFORMANCE_READY`를 확인하고 `ACT <id> <boot> <profile>`로
  한 컨트롤러가 진입/유지/복귀를 실행한다. 임의 각도/시간은 외부 입력으로 받지 않는다.
- greeting: 오른팔 +45°, 유지 최대 1.4초. surprise: 양팔 +30°, 최대 0.45초.
  celebration: 양팔 +45°, 최대 0.9초. playful: 양팔 +20°, 최대 0.45초.
  단일 작은 상승이며 반복 흔들기는 없다. 프로필별 유지 상한 뒤에는 자연 복귀한다.
- 기준은 직전의 **명령 위치**다. 양팔이 60° 이하이고 명령 위치가 알려진 경우만
  허용한다. 시작 자세로 돌아오며 사용자가 높이 올린 팔을 임의로 내리지 않는다.
  총 4초 상한이며 ACK/phase는 실제 위치 센서나 실물 도착 확인이 아니다.
- 발화 종료/새 듣기/생각/일반 취소는 `END <new-id> <boot> <ACT-token>`으로
  같은 연출의 진입/유지를 원자적으로 짧은 복귀로 바꾼다. 별도 `both_down`이나
  busy 재시도는 없다. 늦은 이전 턴 END는 다음 연출 token에 영향을 주지 않는다.
- `PULSE`는 같은 token의 ENTER/HOLD/RETURN/IDLE 관측과 heartbeat다.
  STOP·GP7·fault는 token과 궤적을 제거한다. 그 뒤에는 자동 복귀/Prepare/재시작이 없다.
  연결 단절은 즉시 검출을 보장하지 않는다. 기존 watchdog 기한까지 제한된 현재
  궤적이 남을 수 있으며 검출되면 HOLD한다. 전기적 전원 차단은 아니다.
- v1/v2에는 자동 발화 연출을 기존 큰 동작으로 대체하지 않는다.
  `arm_performance_firmware_required`로 알리며 기존 명시적 메뉴/음성 동작은 유지한다.
  v3에도 기존 DO 명명 동작과 v2 STATUS/PREPARE 계약이 유지된다.

## 검토·설치·복귀

검토 대상 enabled `code.py` SHA256:
`d70323f51165edb2d4f6d9c4707ed508fe83a8ab19121ca4e1bba7b885254b9e`

검토 대상 helper `bmo_arm_bridge.py` SHA256:
`2e45a8f2cbf27919f4733896dd71ae44822bbd5add5251401a1263773e578c5c`

현재 v1 또는 v2의 검토된 enabled code/helper/boot 조합만 인수한다. 먼저 앱을
정상 종료하고 읽기 전용 대조와 private 후보 생성을 수행한다. 기존 검토 폴더는
덮어쓰지 않는다. 이 단계는 serial을 열거나 보드에 쓰지 않는다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.arm_candidate --speech-performance
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.arm_candidate --speech-performance \
  --prepare --work-directory /home/bgh/BMO_private/arm-speech-v3-review
```

Pi 담당이 차이와 두 SHA를 검토하고 **현재 입회·서보 전원 격리·공간 확보·즉시
전원 차단·auto-reload 위험 확인**이 실제 충족된 때만 적용한다. 원본 부팅 PWM이
실행될 수 있으므로 쓰기 동안 전원 격리가 필요하다. 이 확인은 아이콘/설정에 저장하지 않는다.

```bash
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.arm_candidate --speech-performance \
  --apply --work-directory /home/bgh/BMO_private/arm-speech-v3-review \
  --reviewed-code-sha d70323f51165edb2d4f6d9c4707ed508fe83a8ab19121ca4e1bba7b885254b9e \
  --reviewed-helper-sha 2e45a8f2cbf27919f4733896dd71ae44822bbd5add5251401a1263773e578c5c \
  --user-present --servo-power-isolated --clearance-verified \
  --physical-cutoff-ready --reload-risk-acknowledged
```

helper는 현재 정확한 v1/v2 파일 전체와 해시를 `backup-previous`에 먼저 동기 저장한다.
두 파일 교체는 원자적이지 않다. 일부 쓰기 실패 시 전원 격리를 유지하고 같은 명령의
`--apply`만 `--rollback`으로 바꿔 이전 정확한 조합으로 복구한다. 알 수 없는 파일이나
바뀐 boot는 덮지 않는다. rollback도 현재 감독 확인이 필요하며 USB reset은 하지 않는다.

## 최소 감독 검수

1. Pi가 목표 runtime SHA/설치 SHA/runner 인수 SHA/같은 아이콘 실행본과 보드
   code/helper SHA를 각각 기록한다. 기존 v1/v2 commissioning은 v3를 자동 허용하지 않는다.
   최초 감독 실행은 기존 다른 승인 옵션에
   `--arms-supervised --arm-legacy-bridge --emotion-arms --arm-reviewed-code-sha <위 code SHA> --arm-power-off-ready`
   를 사용한다. 현재 감독 선언을 일상 아이콘에 영구 저장하지 않는다.
2. 정상 준비 뒤 인사 한 번: 실제 발화 시작과 작은 한 팔 상승·짧은 유지·복귀를
   관찰한다. 놀람은 양팔 작은 상승과 승인 놀란 표정을 함께 관찰한다. 긴 답변에도
   유지 상한 뒤 복귀하고 음성·다음 입력이 기다리지 않는지 확인한다.
3. 정상 발화 종료·중간 취소·새 GP8 질문에서 제한된 복귀를 확인한다. STOP/GP7/
   watchdog/fault에서는 HOLD 우선이며 자동 복귀·재준비가 없어야 한다. Prepare
   자체는 움직이지 않으며 그 뒤 별도의 새 요청만 실행한다. 오래된 이벤트를 재생하지 않는다.
4. 실물 검수 후 기존 `arm_usage --record-first-test`에 이 code SHA·실제 세션과
   완료 항목을 기록한다. Use arms와 Conversation gestures는 별도 선호로 유지한다.
   정상 종료→같은 아이콘→Stopped 확인→명시적 Prepare→새 동작을 확인한다.

합성 포트·가짜 시계·파일 fixtures 검사는 프로토콜/복귀/중복/정지/설치 복구만
검증한다. 실제 움직임·속도·사용자 입회·안전 조건·발화와의 시각적 타이밍은 Pi 검수 대기다.
