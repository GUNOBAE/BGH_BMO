# BMO Luna Low + Fish Aaron Soft 실제 음성 A/B (2026-10-21)

## 목적과 안전 범위

이 브랜치는 `feature/bmo-luna-low-voice-ab`이다. **현재 Pi에서 성공한**
`feature/bmo-local-piper-fallback`에서 별도 분기했으며, 기존 로봇
`/home/bgh/BGH_BMO`는 그대로 둔다. 별도 진행 중인 BMO 얼굴,
Piper 원본 음성 프로젝트, Codex `BGH_BMO_ACTIONS`, Game Center, RP2040
모터/IMU/카메라/서비스/부팅 설정은 전혀 수정하지 않는다.

기본 GPT는 여전히 `gpt-4.1-mini`, Fish 목소리는 여전히 Aaron Soft
`s2.1-pro-free` + Beemo 발음 + 소프트웨어 게인 0.25다. 명시적 CLI
옵션 `--chat-model gpt-6-luna --reasoning-effort low`를 전달할 때만
새로운 GPT를 사용한다. 피시 TTS 기능과 로컬 Piper 백업 기능도
기존 동작 그대로 유지한다. **별도 음성 프로세스 두 개를 동시에
켜서 같은 마이크/스피커를 공유하지 않는다.**

변경한 점:
- `core.py`: Responses API `reasoning={"effort":"low"}` 전달 지원.
  옵션을 지정하지 않은 기존 GPT-4.1 mini 요청은 변경 없음.
- `config.py`: 설정 검증. GPT-4.1 mini에 추론 옵션을 잘못 전달하거나
  Luna에 미지원 추론 값을 전달하면 API 호출 전에 즉시 거부.
- `__main__.py`: 안전한 모델/추론/출력 토큰 CLI 선택 및 설정 로그.
- `app.py`: 녹음 중지 시점부터 실제 재생 프로그램 시작 콜백까지
  경과시간 기록. **스피커의 실제 가청 시작을 직접 측정한 값은 아님.**
  Fish 실패로 Piper가 대신 재생한 경우 `tts_source=piper`라고 기록.
- `voice_ab_report.py`: 별도 두 번의 음성 테스트 JSONL 로그를 읽기만
  하고 Fish 경과시간 중앙값 비교. 대화 원문·API 키는 로그에 남기지
  않으며 파일을 고치지 않는다.

## 0. 독립 폴더에 받기 (기존 BMO에는 체크아웃·병합하지 않음)

터미널에서:

```bash
cd /home/bgh
git clone --single-branch --branch feature/bmo-luna-low-voice-ab \
  https://github.com/GUNOBAE/BGH_BMO.git BMO_LUNA_AB

cd /home/bgh/BMO_LUNA_AB/software

# 새 패키지를 설치하지 않고 기존에 잘 작동한 BMO venv만 실행 인터프리터로 사용
PY=/home/bgh/BGH_BMO/software/.venv/bin/python
"$PY" -m unittest discover -s tests -p 'test_luna_voice_ab.py' -v
```

`BMO_LUNA_AB` 폴더가 이미 있으면 `git clone`을 재실행하지 말고
기존 폴더 상태를 먼저 확인할 것. 두 세션의 독립 파일에는 영향을
주지 않는다. Python은 현재 작업 디렉터리의 `bmo_ai`를 가져오므로
기존 venv 인터프리터를 사용해도 이번 실험 브랜치 코드가 실행된다.

## 1. 키 점검 (같은 터미널에서만 유효)

```bash
test -n "$OPENAI_API_KEY" && echo "OpenAI OK" || echo "OpenAI missing"
test -n "$FISH_API_KEY" && echo "Fish OK" || echo "Fish missing"
```

키가 없으면 다음처럼 **보이지 않게 입력**한다. 채팅이나 스크린샷에
키를 붙이지 않는다.

```bash
read -rsp 'OpenAI API key: ' OPENAI_API_KEY; echo
export OPENAI_API_KEY
read -rsp 'Fish API key: ' FISH_API_KEY; echo
export FISH_API_KEY

# 전에 설정한 추론 옵션이 baseline에 섞이지 않게 이 셸에서만 해제
unset BMO_REASONING_EFFORT
```

## 2. 기존 GPT-4.1 mini + Fish Aaron Soft 비교 기준

```bash
cd /home/bgh/BMO_LUNA_AB/software
PY=/home/bgh/BGH_BMO/software/.venv/bin/python

"$PY" -m bmo_ai \
  --chat-model gpt-4.1-mini \
  --max-output-tokens 320 \
  --tts-provider fish \
  --fish-model s2.1-pro-free \
  --fish-delivery soft \
  --fish-name-mode beemo \
  --fish-gain 0.25 \
  --offline-voice \
  --mic-device 'plughw:CARD=Device,DEV=0' \
  --speaker-device 'plughw:CARD=MAX98357A,DEV=0' \
  --log-file /home/bgh/BMO_LUNA_AB/logs/voice_41.jsonl
```

`BMO ready`가 뜨면 Enter를 누르고 짧은 영어 문장으로 말한다.
한 번 녹음은 기본 5초. 예: `Hey BMO, how are you today?`,
`Tell me a tiny joke.`, `What game should we play?`,
`BMO, what makes you happy?`, `Can you cheer me up?`.
순서대로 5번 대화 후 `/quit` 입력. 컴퓨터 키보드에 질문을 직접
입력하는 게 아니라 **Enter → 5초 말하기**임.

## 3. Luna low + 똑같은 Fish Aaron Soft

기존 앱이 `/quit`로 완전히 끝난 후 실행한다.

```bash
cd /home/bgh/BMO_LUNA_AB/software
PY=/home/bgh/BGH_BMO/software/.venv/bin/python

"$PY" -m bmo_ai \
  --chat-model gpt-6-luna \
  --reasoning-effort low \
  --max-output-tokens 320 \
  --tts-provider fish \
  --fish-model s2.1-pro-free \
  --fish-delivery soft \
  --fish-name-mode beemo \
  --fish-gain 0.25 \
  --offline-voice \
  --mic-device 'plughw:CARD=Device,DEV=0' \
  --speaker-device 'plughw:CARD=MAX98357A,DEV=0' \
  --log-file /home/bgh/BMO_LUNA_AB/logs/voice_luna.jsonl
```

기존과 **같은 다섯 질문, 같은 순서**로 말하고 `/quit` 입력.
두 실행은 OpenAI STT 및 Fish API를 실제로 호출하므로 각 단계에
API 사용료 또는 무료 모델 할당량이 적용될 수 있다. 이번 테스트는
유료 모델로 자동 업그레이드하지 않는다.

## 4. 로그 결과만 비교 (API 호출 없음)

```bash
cd /home/bgh/BMO_LUNA_AB/software
PY=/home/bgh/BGH_BMO/software/.venv/bin/python

"$PY" -m bmo_ai.voice_ab_report \
  --baseline /home/bgh/BMO_LUNA_AB/logs/voice_41.jsonl \
  --luna /home/bgh/BMO_LUNA_AB/logs/voice_luna.jsonl
```

결과 예시(측정값 아님): `Luna low minus baseline: -250.0 ms`는
녹음 중지 ~ 오디오 재생 프로그램 시작까지 Luna가 0.25초 빠르다는
뜻이다. 플러스는 느리다는 뜻이다. **이 수치는 GPT 텍스트 TTFT와
같지 않다.** Fish가 전체 WAV를 생성하는 시간과 STT가 포함된다.
`tts_source=piper`로 동작한 샘플은 비교에서 제외하므로 실제
Fish 문제가 발생했을 때 지표 왜곡을 피한다.

각 모델 5개 샘플로는 품질을 확정하기 어렵다. 동일한 길이의
문장으로 여러 차례 반복해서 확인하고, 목소리가 끊기지 않는지,
실제로 귀에 들리는 대기 체감도 함께 비교한다.

두 파일만 이 채팅에 보내주면 GPT/Fish 지연을 더 자세히 분석할
수 있다. 로그에 입력 원문·출력 문구·API 키·오디오 자체는 없다.

## 실패 시 복구와 다음 단계

- 새 BMO에서 음성이 실패하면 이 테스트를 종료하고 **기존**
  `/home/bgh/BGH_BMO/software`의 정상 음성 실행으로 되돌리면 된다.
  원래 설치 프로그램/키/ALSА/부팅 서비스 변경이 없다.
- `incomplete`와 같은 GPT 오류가 나면 Luna 추론 토큰+답변 토큰이
  `max_output_tokens=320`을 초과했을 수 있다. 무턱대고 제한을
  크게 올리지 말고 로그·오류를 확인할 것.
- 이번 단계에서는 VAD, GPT/Fish 스트리밍, 게임/카메라/팔
  Function Calling을 **추가하지 않았다**. 먼저 순수 GPT 모델
  전환 시 성능과 안정성을 확인한 뒤, 별도 단계에서 연동한다.
- 실제 Action System v0는 Codex의 독립 작업물만 기준으로 나중에
  통합한다. 이 실험 브랜치를 현재 로봇 메인에 임의로 병합하지 않는다.

참고: https://developers.openai.com/api/docs/guides/reasoning
