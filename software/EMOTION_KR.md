# 자동 표정 v1 실행과 복귀

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

- Enter: 질문. auto는 neutral에서 듣기/생각하며 답변 표정은 player started에 적용한다.
- 정상 ended: 입은 닫고 표정은 2초 유지 후 poll에서 neutral로 복귀한다.
- `/cancel`: 현재 턴과 유지 타이머 무효화. auto는 neutral, 수동 override는 유지한다.
- `/emotion happy` 등: 여섯 표정의 수동 고정 모드. `/emotion manual`도 수동 복귀다.
- `/emotion auto`: 수동 override를 해제하고 neutral로 자동 복귀한다.
- `/repeat`: 마지막 성공 WAV/입 cue/표정을 함께 재사용. GPT/합성/gain 재적용은 없다.
- `/reset`: 선택한 모드를 유지하고 neutral, history/후보/캐시를 정리한다.
- `/quit`, Ctrl-C, EOF: 이 앱 소유 작업과 서버를 정리한다.

감정은 답변 한 번에 하나다. 잘못된 감정값만 있으면 neutral로 낮추고 깨끗한
발화는 사용한다. malformed JSON, unsafe speech, refusal, incomplete는 발화하지
않고 해당 턴을 종료한다. 형식 복구용 추가 모델 호출이나 자동 모델 변경은 없다.
동일 영어 답변의 Piper fallback은 표정을 보존하고, 고정 장애/한국어 미지원
안내는 neutral이다. 실패한 새 음성이 이전 성공 캐시의 표정을 바꾸지 않는다.

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
Piper 실물 및 지연/비용은 이 모의 검사로 승인되지 않는다. 2초 유지도 초기값이다.

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
[EMOTION_STAGE1_LATEST_KR.md](../docs/reports/EMOTION_STAGE1_LATEST_KR.md)에 기록한다.
