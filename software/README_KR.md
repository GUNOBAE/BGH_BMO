# BMO 대화 소프트웨어 — Raspberry Pi 5

PR #1의 클라우드 MVP를 보강한 개발 브랜치입니다. **영어를 기본으로 대화하며
한국어 입력에는 한국어로 답합니다.** Enter로 시작하는 Push-to-Talk 녹음,
음성 인식, GPT 응답, WAV 음성 합성을 구현했습니다. 기본 STT는 언어를 자동
감지하며 `--language ko`는 한국어 응답과 인식 힌트를 선택합니다.

Raspberry Pi 5 / Debian 13 / Python 3.13.5에서 모의 테스트와 실제 SDK 요청
형식 검증을 수행했습니다. **API 키가 없어 실제 클라우드 대화는 미검증입니다.**
마이크·스피커는 장치 등록 목록만 확인했으며 실제 녹음·재생, 화면 애니메이션,
카메라 캡처, RP2040 통합은 검증하지 않았습니다. 서보 구동은 코드에서 차단합니다.
기존 MPU6050 진단에는 큰 바이어스와 Self-Test 이상이 있어 동작 제어에 쓰지 않습니다.

## 준비와 실행

이 Pi에는 `.venv`와 의존성을 이미 설치했습니다. 다른 체크아웃에서는:

```bash
cd /home/bgh/BGH_BMO/software
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m pip check
.venv/bin/python -m bmo_ai --doctor
.venv/bin/python -m unittest discover -s tests -v
```

`alsa-utils`, `python3-venv`가 필요합니다. 시스템 Python이나 기존 스피커 설정을
교체하지 않습니다. Python 3.11 이상을 대상으로 CI를 추가했으며, 로컬에서 실제
실행한 버전은 3.13.5입니다. GitHub 호스팅 CI는 이번 작업에서 실행하지 않았습니다.

키를 Bash 히스토리에 남기지 않도록 직접 입력합니다:

```bash
read -rsp 'OpenAI API key: ' OPENAI_API_KEY
export OPENAI_API_KEY
.venv/bin/python -m bmo_ai --text
```

`.env.example`은 설정 설명용이며 앱이 자동으로 읽지 않습니다. 설정은 환경 변수로
export해야 합니다. 실제 키는 Git·로그에 넣지 마세요. `--help`, `--doctor`,
`--list-audio`, `--benchmark`에는 API 키와 OpenAI SDK가 필요하지 않습니다.

```bash
# 텍스트 대화: 녹음·음성 합성·재생 없음
.venv/bin/python -m bmo_ai --text
# 한국어 선호
.venv/bin/python -m bmo_ai --text --language ko
# Enter로 5초 녹음하는 음성 대화
.venv/bin/python -m bmo_ai --seconds 5
# 녹음·GPT만 사용하고 음성 출력 생략
.venv/bin/python -m bmo_ai --mute
# 읽기 전용 장치 목록 / 모의 성능 측정
.venv/bin/python -m bmo_ai --list-audio
.venv/bin/python -m bmo_ai --benchmark
```

`/quit` 또는 `/exit`로 종료하고 `/reset`으로 로컬 대화 문맥을 지웁니다.
`/repeat`은 마지막 완성 응답을 다시 표시하고 음성 모드에서는 재합성·재생합니다.
GPT 재요청·마이크 녹음은 없지만 TTS 비용은 발생합니다. 음성 모드에서는 빈 Enter만
녹음을 시작하며 임의의 입력은 녹음을 시작하지 않습니다. 상시 청취·깨우기 단어·
실시간 끼어들기 기능은 구현하지 않았습니다.

## 오디오 설정 보존

장치 이름을 생략하면 **현재 ALSA 기본값을 유지**합니다. 자동으로 카드를 고르거나
ALSA/PipeWire·믹서·I2S·부팅 설정을 바꾸지 않습니다. 실제 호스트 목록에서
USB 캡처 `Device`, MAX98357A 재생 장치를 관측했습니다. 다음은 확인된 이름의
선택 예시이며 실제 녹음·재생 검증을 대신하지 않습니다:

```bash
.venv/bin/python -m bmo_ai \
  --mic-device 'plughw:CARD=Device,DEV=0' \
  --speaker-device 'plughw:CARD=MAX98357A,DEV=0'
```

현재 잘 작동하는 스피커 선택이 다르면 그 값을 유지하세요. 숫자 카드 번호는
재연결 후 달라질 수 있습니다. USB 마이크가 48 kHz만 지원하면 확인된 `plughw`
장치로 ALSA 변환을 사용합니다. 배선이나 overlay를 추측해서 고치지 않습니다.

## 오류·로그·성능

대화는 완성된 턴과 글자 수 기준으로 제한합니다. 실패·빈 응답·불완전 응답은
기존 문맥을 보존하며, SDK만 제한된 재시도를 수행합니다. 기본 모델은 기존의
`gpt-4.1-mini` / `gpt-4o-mini-transcribe` / `gpt-4o-mini-tts`를 유지합니다.
실제 계정에서의 사용 가능 여부와 영·한 응답 품질은 확인하지 않았습니다.

네트워크·인증·쿼터·오디오 오류는 원문 SDK 예외를 출력하지 않고 안전한 메시지로
알립니다. 녹음·합성 임시 파일은 완료·오류·인터럽트에서 정리하며 정리 실패가
원래 오류를 덮지 않습니다. TTS 다운로드는 24 MiB 제한과 청크 경계의 협조적
60초 제한을 사용합니다. SDK 타임아웃은 네트워크 단계/읽기 제한이므로 전체 턴의
엄격한 시간 상한은 아닙니다. 전원 차단·강제 종료 시 파일 정리는 보장하지 않습니다.

```bash
.venv/bin/python -m bmo_ai --text --log-file logs/events.jsonl
```

로그에는 고정 이벤트·오류 분류·단계 지연만 기록합니다. 키·녹음·대화 원문·요청
본문은 기록하지 않으며, 파일 권한 0600과 1 MB/백업 3개 순환을 적용합니다.
터미널에 표시되는 대화는 외부 세션 기록기에 남을 수 있습니다.
`--benchmark`는 클라우드·하드웨어 호출 없이 호스트 처리 시간만 측정합니다.
실제 대화 지연이나 마이크→스피커 지연으로 해석하면 안 됩니다.

전체 설정 표와 자세한 설치 설명: [English guide](README.md).
구조: [architecture](../docs/architecture.md).
오디오 복구: [audio](../docs/audio.md).
하드웨어 계약과 IMU 한계: [hardware](../docs/hardware.md).
완료·실패·미검증 및 커밋: [개발 보고서](../docs/reports/20261013-development-report.md).

## 선택형 Fish Audio — Aaron BMO 목소리 (2026-10-19)

기본값은 이전과 동일하게 OpenAI의 coral 목소리입니다. GPT 텍스트 응답과
OpenAI STT는 바꾸지 않고, **BMO의 응답을 읽는 TTS만** Fish Audio로
선택적으로 변경할 수 있습니다. 별도 브랜치 `feature/bmo-fish-aaron-tts`에서
개발 중이며, 실물 Raspberry Pi 및 Fish API 키를 사용하는 통합 테스트는
아직 수행하지 않았습니다.

- Fish 공개 BMO 음성: https://fish.audio/m/106f41ea392c4cc48c6eee76f69ff17e/
- Aaron의 voice reference ID: `106f41ea392c4cc48c6eee76f69ff17e`
- `s2.1-pro-free`: 개발자 무료, Fair Use 적용. Fish의 2026-10 공지에서 **2026-11-30까지** 무료.
- `s2.1-pro`: $15/100만 UTF-8 바이트 (영어 ASCII는 약 100만 글자).
- `drama-3`: **2026-11-12까지 $10/100만 글자**, 이후 정가 $50/100만 글자.
- 공개 웹 Aaron 샘플은 Drama 3로 렌더링됨. S2.1 무료 버전과 느낌이
  다를 수 있으며 이 reference_id를 해당 계정에서 호출 가능한지 **검증 필요**.
- 비용은 Fish API와 OpenAI API에서 각각 청구됩니다. 단, 한 답변에 TTS는
  Fish 또는 OpenAI 중 **하나만** 사용하며 자동 유료 fallback은 없습니다.
- Fish API 가격과 Creator 웹 구독 크레딧은 별도 체계입니다.

[Fish API 키 발급](https://fish.audio/app/api-keys) 후, 키를 복붙해
명령줄 기록에 남기는 대신 다음처럼 안전하게 입력합니다.

```bash
cd /home/bgh/BGH_BMO/software
read -rsp 'Fish Audio API key: ' FISH_API_KEY; echo
export FISH_API_KEY
# 기존 OPENAI_API_KEY 또한 환경변수로 필요함
.venv/bin/python -m bmo_ai --tts-provider fish --fish-model s2.1-pro-free \
  --mic-device 'plughw:CARD=Device,DEV=0' \
  --speaker-device 'plughw:CARD=MAX98357A,DEV=0'
```

위 호출은 실제 Fish 요청이므로 현재 무료 모델 정책 및 계정 접근
가능 여부를 Fish 대시보드에서 먼저 확인하세요. 유료 Drama 3를
검증하려면 `--fish-model drama-3`를 명시합니다. `--text`와
`--mute`는 TTS를 호출하지 않습니다. 한 번에 많은 요청을 보내거나
응답을 무한 반복하지 마세요. `/repeat`은 매번 별도 Fish 생성
요청이므로 비용을 다시 낼 수 있습니다.

현재 REST JSON → Fish PCM WAV → 기존 오디오 검증/ALSA aplay 경로를
사용합니다. 기존 마이크·스피커 장치나 시스템 설정을 자동으로 변경하지
않습니다. WAV가 잘못되거나 Fish가 오류를 반환하면 발성을 중단하며,
다른 유료 공급자에게 말없이 우회하지 않습니다.

시연 추정 단가(실측이 아닌 가정): 영어 150바이트 답변, 사용자 음성 5초,
GPT-4.1-mini 입력 1,200/출력 100 토큰. GPT $0.00064 + STT $0.00025
+ Fish S2.1 유료 $0.00225 = 턴당 약 $0.00314. Drama 3 할인기간이면
약 $0.00239, 정가라면 약 $0.00839. 이 값에는 세금, 환전 수수료,
재시도/반복, 다른 도구 API, 오디오 플랫폼 무료/유료 조건 변화가
반영되어 있지 않습니다. 실제 금액은 각 대시보드에서 검증해야 합니다.

가격 근거:
- https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits
- https://fish.audio/blog/fish-audio-drama-3/
- https://fish.audio/ar/blog/s2-1-pro-free-api/?articleLocale=en
- https://developers.openai.com/api/docs/pricing
### Aaron BMO 목소리 A/B 비교 — 무료 vs Drama 3

완성된 BMO 대화 앱과 별도로, 동일한 Aaron 음성과 영어 대사를 Fish
S2.1 무료 모델·Drama 3에 넣고 결과 WAV 파일을 나란히 들어볼 수
있습니다. 비교 도구는 GPT, STT, 마이크, Pi 스피커를 사용하지 않습니다.

```bash
cd /home/bgh/BGH_BMO/software
# 비용 0원, 네트워크 요청 없이 미리 확인
.venv/bin/python -m bmo_ai.fish_compare

# Fish Audio API 키를 안전하게 입력
read -rsp 'Fish Audio key: ' FISH_API_KEY; echo
export FISH_API_KEY

# 무료 Aaron 음성 테스트: 인사 대사 한 번
.venv/bin/python -m bmo_ai.fish_compare --mode free --sample hello

# 동일 대사 무료 vs 유료 Drama 3 비교: 유료 요청 명시적 허용
.venv/bin/python -m bmo_ai.fish_compare --mode compare --sample hello --confirm-paid

# 감정 4종 비교: 무료 네 번 + 유료 네 번, 총 8개 WAV 생성
.venv/bin/python -m bmo_ai.fish_compare --mode compare --sample all --confirm-paid
```

결과는 `software/fish_aaron_tests/bmo-aaron-*/report.html`에서 열어 볼
수 있습니다. WAV·HTML·JSON이 함께 저장됩니다. Pi에서 실행했다면
비교 폴더를 PC로 복사해 웹 브라우저에서 HTML을 열면 됩니다.

기본값은 API 호출 없는 `plan`입니다. `compare`와 `--confirm-paid`를
동시에 지정해야만 Drama 3를 호출합니다. 유료 예상액 0.05달러를 넘으면
차단하지만 이는 **실제 결제 한도가 아니라 사전 추정액 기준**입니다.
무료 모델이 실패하면 유료 호출도 중단합니다. 자동 재시도·유료 fallback
없음. 무료 기간이 종료된 2026년 11월 30일 이후에는 정책을 확인하고
`--reviewed-free-terms` 옵션을 명시해야 요청이 진행됩니다.

판단 기준: Aaron 원본과의 음색 유사도, 행복·슬픔·분노의 연기,
발음 정확도, 목소리 일관성, 유료 모델의 가격 대비 개선 정도.
코드·모의 테스트는 완료했지만 실제 Fish 계정으로 생성된 비교 음성은
아직 검증되지 않았습니다.

### 무료 음성 생성이 "nonempty PCM WAV"로 실패했을 때

2026-10-21 Pi 테스트에서 Fish 서버 응답을 기존 엄격한 PCM 파서가
거부하는 문제가 확인됐습니다. Fish 공식 호환성 문서에 따르면 WAV에
RIFF(4294967076)/data(4294967040) 크기를 임시로 기록하는 경우가
있습니다. Fish 어댑터는 알려진 이 두 값의 조합만 실제 다운로드된
바이트 길이로 고치고, 이후 동일한 엄격한 PCM 검증을 수행하도록
수정했습니다. 오프라인 회귀 테스트 통과, 실제 계정 재검증 필요.

브랜치가 원격을 추적하지 않는 Git 설정에서도 작동하는 업데이트 절차:

```bash
cd /home/bgh/BGH_BMO
git status --short
git fetch origin refs/heads/feature/bmo-fish-aaron-tts:refs/remotes/origin/feature/bmo-fish-aaron-tts
git merge --ff-only refs/remotes/origin/feature/bmo-fish-aaron-tts
cd software
.venv/bin/python -m unittest tests.test_fish_wav_header -v
# 키는 같은 셸에 이미 export돼 있어야 합니다.
.venv/bin/python -m bmo_ai.fish_compare --mode free --sample hello
```

비교 프로그램은 **소리를 자동으로 재생하지 않습니다.** WAV가
정상 생성되면 Pi의 현재 MAX98357A ALSA 장치에서 직접 듣거나,
`fish_aaron_tests/bmo-aaron-*/report.html`을 PC에서 열어 들을 수 있습니다.

```bash
wav_file=$(find fish_aaron_tests -type f -name hello_free.wav -print -quit)
if [ -n "$wav_file" ]; then
  aplay -q -D 'plughw:CARD=MAX98357A,DEV=0' "$wav_file"
else
  echo "No WAV saved yet; check the previous Fish API error"
fi
```

출처: https://docs.fish.audio/developer-guide/compat/capabilities

## Fish BMO 하이톤/볼륨 조정과 무료 후보 비교 (2026-10-21)

### 볼륨: MAX98357A raw ALSA 우회 방지

`aplay -D plughw:CARD=MAX98357A,DEV=0`은 직접 PCM 장치에
출력하므로 PipeWire/데스크톱 시스템 음량 조절을 우회할 수 있습니다.
시스템과 연결된 출력은 `default` 또는 `pipewire`일 수 있지만,
실제 Pi에서 `wpctl status`, `aplay -L`을 읽고 확인해야 합니다.
ALSA, PipeWire, 시스템 전체 볼륨 및 부팅 설정은 자동 변경하지 않습니다.

BMO Fish 앱은 기본적으로 `BMO_FISH_GAIN=0.30`을 적용해 임시 WAV의
PCM 진폭을 30%로 줄여서 보냅니다(출력 전력 30%를 뜻하지 않음).
원본 WAV 파일에는 손대지 않으며 믹서 설정도 바꾸지 않습니다.
`--fish-gain 0.2` 처럼 BMO 앱의 TTS만 조절할 수 있습니다.
기존 OpenAI TTS나 YouTube의 재생 상태는 변경되지 않습니다.

시연용 WAV는 이제 직접 `aplay` 하지 말고 안전 재생기를 사용하세요:

```bash
cd /home/bgh/BGH_BMO/software
wav_file=$(find fish_aaron_tests -type f -name hello_free.wav -print -quit)
if [ -n "$wav_file" ]; then
  # Pi default ALSA가 desktop PipeWire/Pulse에 연결된 경우 시스템 볼륨도 따라감
  .venv/bin/python -m bmo_ai.safe_playback "$wav_file" --device default --gain 0.25

  # default가 소리 나지 않으면 확인된 I2S 카드로 25% 소프트웨어 출력
  # .venv/bin/python -m bmo_ai.safe_playback "$wav_file" --device 'plughw:CARD=MAX98357A,DEV=0' --gain 0.25
fi

# 읽기 전용 상태 확인
aplay -L | grep -E '^(default|pipewire|pulse)$'
command -v wpctl >/dev/null && wpctl status
```

BMO 대화 실행 예시:

```bash
.venv/bin/python -m bmo_ai --tts-provider fish --fish-model s2.1-pro-free \
  --fish-gain 0.30 --speaker-device default
```

시스템 기본값이 실제 BMO 스피커를 가리키는지는 먼저 재생으로 확인.
`--speaker-device plughw:CARD=MAX98357A,DEV=0`를 쓰더라도
새 30% 소프트웨어 게인은 적용되지만 시스템 볼륨 노브와는 별개입니다.

### 새로운 무료 BMO 음색 비교: Aaron / Studio Coille / Nikkooo

같은 영어 대사를 4개 공개 BMO 보이스에 넣어 선택하는 별도
테스터를 추가했습니다. 같은 성우를 감정별로 교체하지 않으며, 한
목소리에 낮은 에너지의 연기 지시를 추가한 대안도 제공합니다.

Fish 음성 페이지(기본 샘플은 각 사이트에서 별도 청취):
- Aaron Hinojosa: https://fish.audio/m/106f41ea392c4cc48c6eee76f69ff17e/
- Studio Coille: https://fish.audio/m/323847d4c5394c678e5909c2206725f6/
- Nikkooo: https://fish.audio/m/1ac90ca23d4a45a4b78b0dcc780069c7/
- Zaccharie Jacquat: https://fish.audio/m/35da70940483488a8f95ade873bf5d20/

```bash
cd /home/bgh/BGH_BMO/software
# 무료 후보와 Voice IDs 확인; API 호출 0회
.venv/bin/python -m bmo_ai.fish_voice_lab --profile all

# 이미 FISH_API_KEY가 export됐을 때, 기본 Aaron 원본 무지시 1회
.venv/bin/python -m bmo_ai.fish_voice_lab --mode free --profile aaron_plain

# Aaron을 조금 차분하게, 지나친 감탄/흥분 지시 없이
.venv/bin/python -m bmo_ai.fish_voice_lab --mode free --profile aaron_soft

# 예전에 음색이 좋았던 Coille에 은은한 장난기를 부여
.venv/bin/python -m bmo_ai.fish_voice_lab --mode free --profile coille_playful

# 전체 6개 프로필을 같은 문장으로 비교 (무료 S2.1 전용, 요청 6회)
.venv/bin/python -m bmo_ai.fish_voice_lab --mode free --profile all
```

결과: `fish_voice_trials/bmo-voices-*/listen.html`. Pi에서 만든 WAV를
PC로 옮기거나 위의 안전 플레이어로 하나씩 들어보면 됩니다.
음색 비교 대사는 동일하고, `*_soft`/`*_playful`에서만 연기 방향 태그가
추가됩니다. 태그 없는 버전이 기준입니다. 무료 S2.1만 호출할 수
있으며 Drama 3 유료 호출 기능은 없습니다. 추가 생성도 사용자가
명시적으로 실행한 경우에만 진행됩니다. 11월 30일 이후 무료 정책은
반드시 재확인해야 합니다.

`[excited]`를 연속으로 붙이거나 `Yay!!!`같은 강한 감탄을 반복하면
원작 BMO의 자연스러운 톤에서 멀어질 수 있습니다.
예전 Aaron 선택 이유는 장난기/감정 연기였으며, Studio Coille는
음색이 가깝지만 차분했습니다. 이번 샘플은 이 두 장점을 비교하려는
것이지 둘을 번갈아 쓰려는 설계가 아닙니다.

## Aaron 2번 피드백 반영: "BMO" 발음 수정 (2026-10-21)

실제 6개 샘플 청취 결과 2번 Aaron Soft를 1순위로 유지합니다.
Coille는 억양 하강/느린 말투, Nikkooo는 차가움, Zaccharie는 지나친
톤 다운으로 이번 단계에서 제외합니다. 새로운 유료 모델이나 성우를
자동으로 전환하지 않습니다.

**중요: 화면/대화에는 BMO를 그대로 표시합니다.** Fish TTS 요청
경계에서만 영어 단어로 독립한 `BMO`를 `Beemo`로 치환합니다.
Fish의 공개 원작 BMO 샘플에도 "Beemo"라는 발음 표기가 쓰이고 있습니다.
이번 표기가 Aaron에서 실제로 "비모"로 들리는지는 음성으로 검증할
것이며, 발음이 이상하다면 `--fish-name-mode bee-mo`를 선택할 수
있습니다. 기존 방식은 `--fish-name-mode literal`입니다.
한글 조사와 붙어 있는 `BMO는` 등은 아직 변경하지 않습니다.

일상 대화용 선택 예시:
```bash
cd /home/bgh/BGH_BMO/software
.venv/bin/python -m bmo_ai --tts-provider fish --fish-model s2.1-pro-free \
  --fish-name-mode beemo --fish-gain 0.25 \
  --speaker-device 'plughw:CARD=MAX98357A,DEV=0'
```

### Aaron 발음 및 감정만 좁혀서 다시 테스트하기

이미 비교한 여섯 커뮤니티 음성을 전부 재생성할 필요 없습니다. Aaron
한 목소리만 사용합니다. 무료 Fish S2.1 Pro 모델로 3개 대사를 합성:
1. `soft_beemo`: 기존 2번 Aaron Soft의 지시는 그대로, BMO→Beemo
2. `soft_bee_mo`: 나머지는 똑같고 이름만 Bee-mo
3. `playful_beemo`: Beemo로 발음하고 과장 없이 약간 생기 있게

```bash
cd /home/bgh/BGH_BMO/software
.venv/bin/python -m bmo_ai.fish_aaron_refine
# PLAN ONLY -> 0원, Fish 호출 없음

# 키가 현재 셸에 export돼 있다면:
.venv/bin/python -m bmo_ai.fish_aaron_refine --mode free --variant all
```

생성 폴더는 `fish_name_trials/aaron-name-*/listen.html`입니다.
브라우저에서 들어보거나 Pi 스피커로 들을 때 이전에 만든
`bmo_ai.safe_playback`을 이용해 25% 소프트웨어 게인을 적용하세요.
모든 샘플은 같은 Aaron Voice ID이며, 유료 Drama 3 요청이 불가능합니다.
실제 결제 조건/음색은 계정과 무료 정책에 따라 다를 수 있습니다.
