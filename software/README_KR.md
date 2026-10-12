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
