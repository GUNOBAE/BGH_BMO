# 자동 표정과 감정 연속성 v2 실행·복귀

`feature/bmo-auto-emotion-v1`의 독립 clone `/home/bgh/BMO_EMOTION`에서 실행한다.
기존 `/home/bgh/BMO_INTEGRATION`과 승인 HTML 원본은 그대로 둔다.
기본은 **manual**이며 auto는 명시적으로 선택한다. 유효 설정은 비밀이 아닌
허용 필드만 출력하며 `.env`를 읽지 않는다.

```bash
cd /home/bgh/BMO_EMOTION/software
../.venv/bin/python -B -m bmo_ai.integration --emotion-mode auto --effective-config
```

Luna low / 출력 320 / 기존 STT / Fish Aaron Soft / beemo / gain 0.25 및
고정 prompt blob과 english_voice를 유지한다. 원본 Piper는 기존 opt-in이다.
표정마다 음성 preset이나 laugh/cry 태그를 새로 넣지 않는다.

## 장치와 API 없는 모의 실행

```bash
cd /home/bgh/BMO_EMOTION/software
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B -m bmo_ai.integration --simulate --emotion-mode auto \
  --face-file /home/bgh/Downloads/BMO_Screen_v13_2.html --port 0
```

출력된 loopback 주소를 사용한다. 실제 mic/STT/GPT/Fish/Piper/aplay는 호출하지
않으며 합성 WAV와 소유한 무해 Python child만 사용한다. 일반 실행은 기존 앱과
같은 사용자별 lock을 사용하므로, 이미 실행 중이면 새 실행이 거부된다.
기존 앱을 강제로 종료하거나 lock을 지우지 않는다. 자동 검사는 자체 lock을 쓴다.

- Enter: 질문. 마지막 표시 감정으로 듣기/생각/prepared를 이어가고 새 감정은 player started에 적용한다.
- 정상 ended: 입을 닫고 마지막 표정을 계속 유지한다. 시간 경과로 자동 neutral 초기화하지 않는다.
- `/cancel`: 소리·입·현재 후보를 중단하며 마지막 실제 표시 감정을 유지한다. 아직 시작하지 않은 후보는 적용하지 않는다.
- `/emotion happy` 등: 여섯 표정의 수동 고정 모드. `/emotion manual`도 수동 복귀다.
- `/emotion auto`: 수동 override를 해제하되 현재 표시 감정은 유지하며 다음 실제 발화부터 자동 결정한다.
- `/repeat`: 시작 전 현재 감정, 실제 player 시작에 캐시의 감정, 종료 뒤 그 감정을 유지한다. 마지막 성공 WAV/입 cue도 함께 재사용하며 GPT/합성/gain 재적용은 없다.
- `/reset`: 선택한 모드를 유지하고 neutral, history/후보/캐시를 정리한다.
- `/quit`, Ctrl-C, EOF: 이 앱 소유 작업과 서버를 정리한다.

감정은 답변 한 번에 하나다. 잘못된 감정값만 있으면 neutral로 낮추고 깨끗한
발화는 사용한다. malformed JSON, unsafe speech, refusal, incomplete는 발화하지
않고 해당 턴을 종료한다. 형식 복구용 추가 모델 호출이나 자동 모델 변경은 없다.
동일 영어 답변의 Piper fallback은 표정을 보존하고, 고정 장애/한국어 미지원
안내는 재생 중에만 neutral이며 끝나면 이전 대화 감정으로 복귀한다. 일반 cancel/error/disconnect도 마지막 표시 감정을 유지한다. 실패한 새 음성이 이전 성공 캐시의 표정을 바꾸지 않는다.

## 사용자가 수행할 실제 검수 — 이번 자동 작업에서는 NOT_RUN

새 터미널에 키가 없다면 인증을 다시 입력해야 한다. 키를 파일·명령행·Git에
저장하지 않는다. 아래 live 명령은 실제 마이크/유료 API/스피커를 사용한다.
기존 앱이 실행 중이면 사용자가 정상 종료한 뒤 검수한다.

```bash
cd /home/bgh/BMO_EMOTION/software
../.venv/bin/python -B -m bmo_ai.integration --live --emotion-mode auto \
  --face-file /home/bgh/Downloads/BMO_Screen_v13_2.html \
  --mic-device plughw:CARD=Device,DEV=0 \
  --speaker-device plughw:CARD=MAX98357A,DEV=0
```

수동 plain 응답으로 복귀하려면 `/emotion manual` 또는 종료 후 같은 명령의
`--emotion-mode manual`을 쓴다. schema 거절 시 다른 모델로 자동 재시도하지 않는다.
명시적 비교 실행만 `--baseline`으로 GPT-4.1 mini / reasoning None을 선택한다.
필요한 경우에만 `--offline-voice`로 원본 영어 Piper fallback을 켠다.
기존 실기 대화 기준 자체로 돌아가려면 새 앱을 정상 종료하고 기존
`/home/bgh/BMO_INTEGRATION/software`의 수동 실행을 이용한다.

실제 계정의 Luna schema 지원, 의미 분류 품질, LCD/가청 립싱크, 소리의 연기,
Piper 실물 및 지연/비용은 이 모의 검사로 승인되지 않는다. 기존 2초 초기화는 v2에서 제거했다.
승인 원본의 듣기 시선·끄덕임, 생각 시선·흠 입, 깜빡임·눈물은 유지하며, 별도의
카메라 추적·랜덤 시선·새 음성 연기 구현과 구분한다.

## 오프라인 검증

개발 의존성은 이 clone의 `.venv`에만 설치한다.

```bash
cd /home/bgh/BMO_EMOTION
python3 -m venv .venv
.venv/bin/python -m pip install -r software/requirements-integration-dev.txt
cd software
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B -m unittest discover -s tests -v
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B checks/check_integration_browser.py \
  --face /home/bgh/Downloads/BMO_Screen_v13_2.html --chromium /usr/bin/chromium
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B checks/check_integration_end_to_end.py \
  --face /home/bgh/Downloads/BMO_Screen_v13_2.html --chromium /usr/bin/chromium
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B checks/check_emotion_browser.py \
  --face /home/bgh/Downloads/BMO_Screen_v13_2.html --chromium /usr/bin/chromium
```

최신 실제 검사 결과와 미실행 범위는
[EMOTION_CONTINUITY_V2_LATEST_KR.md](../docs/reports/EMOTION_CONTINUITY_V2_LATEST_KR.md)에 기록한다.
이전 [v1 보고서](../docs/reports/EMOTION_STAGE1_LATEST_KR.md)는 당시 계약과 검증 이력으로 보존한다.
