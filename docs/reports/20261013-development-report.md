# BMO 자율 개발 보고서 — 2026-10-13 KST

`feature/bmo-cloud-ai-mvp`의 PR #1 구현을 바탕으로 대화·음성 파이프라인을
보강하고 테스트·운영 문서·하드웨어 인터페이스를 추가했습니다. 개발 위치는
`/home/bgh/BGH_BMO`, 브랜치는 `feature/bmo-robust-conversation`입니다.

**전체 테스트 93개가 통과했습니다. 실제 클라우드 대화와 물리 오디오 동작은
검증하지 않았습니다.** 실행 환경에 `OPENAI_API_KEY`가 없어 실제 API 호출을
하지 않았으며, 서보 구동·RP2040 재시작·녹음·재생·카메라 캡처도 하지 않았습니다.
기존 스피커·OS·부팅·저장장치·전원 설정을 변경하지 않았습니다.

## 작업 결과

| 요청 항목 | 결과 | 검증 범위와 한계 |
| --- | --- | --- |
| 저장소/기존 구현 확인 | 완료 | GitHub 브랜치 `24fead9`에서 새 체크아웃; 기존 MVP와 테스트 및 로컬 IMU 보고서 검토 |
| GPT 대화, 영어 기본/한국어 지원 | 구현 완료 | Responses 요청·언어 지침·문맥·실패 복구를 모의 검증; 실제 답변 품질/계정 접근 미검증 |
| STT/TTS와 오디오 복구 | 구현 완료 | 실제 SDK+모의 HTTP, WAV·프로세스·파일 정리 테스트; 녹음/재생 미검증 |
| 오디오 장치 탐색 | 읽기 전용 실물 목록 확인 | USB 캡처 `Device`, MAX98357A 재생 등록 확인; 음질·배선·장치 열기 검증 아님 |
| 얼굴·카메라·컨트롤러·버튼·IMU·팔 구조 | 인터페이스 설계/구현 완료 | 얼굴은 NullFace, 카메라는 계약만, RP2040은 파서만, 팔은 항상 차단; 실제 통합 남음 |
| 테스트·로그·설정·문서 | 완료 | 93개 테스트, JSON 단계 로그, 비밀 제외, 순환 파일, 영/한 설치 문서 |
| 사용 가능한 검사 실행/수정 | 완료 | 전체 가상환경 테스트·의존성 검사·컴파일·SDK 없는 실행·공백/비밀 패턴 검사 |
| Raspberry Pi 하드웨어 확인 | 수동적 확인 완료 | Pi 5/Debian 13 및 커널/ALSA 장치 목록; 실제 센서/서보/카메라 동작 미검증 |
| 성능/지연 측정 | 모의 호스트 측정 완료 | 실제 클라우드·마이크→스피커 지연 미측정 |
| 개발 브랜치 커밋 | 로컬 완료 | 아래 코드 커밋 및 보고서/문서 커밋; 원격 push·PR 변경·main 병합 없음 |

## 구현된 동작

- **대화:** Responses API, 매번 persona/언어 지침 전달, `store=False`, 180 출력
  토큰 기본값. 영어 기본, 한국어 입력 대응, `ko`/`auto` 선택. 완성된 user/assistant
  쌍만 로컬 보관하며 기본 8턴·16,000자 문맥·4,000자 입력 제한을 적용합니다.
  빈 응답·불완전 응답·completion 상태 누락·네트워크 실패는 문맥을 바꾸지 않습니다.
- **음성:** Enter로 고정 길이 녹음, 자동 언어 STT, WAV TTS, 명시 장치 또는 기존
  ALSA 기본값 사용. 데이터 크기·RIFF·PCM·블록 정렬·실제 길이를 검증합니다.
  다운로드는 디스크에 쓰기 전에 24 MiB 상한을 검사하고 60초 협조적 청크 경계
  제한을 적용합니다. 실제 PCM 길이로 재생 제한을 계산합니다.
- **복구:** 인증·권한·모델·쿼터·연결·타임아웃·장치 오류를 안전한 메시지로
  알리고 프롬프트로 복귀합니다. SDK만 재시도하며 앱은 전체 턴을 자동 재실행하지
  않습니다. `/reset`은 문맥을 지우고 `/repeat`은 마지막 완성 응답을 다시 보여주거나
  재합성·재생합니다. `/repeat`은 GPT/STT를 다시 호출하지 않지만 TTS 비용은 발생합니다.
- **자원:** SDK 클라이언트는 초기화·ready 출력 실패까지 포함해 닫습니다. 녹음 파일은
  STT 뒤 정리하며 정리 실패가 기존 오류나 KeyboardInterrupt를 덮지 않습니다.
  TTS는 스트림·부분 파일을 성공/오류/인터럽트에서 정리합니다.
- **관측:** 고정 이벤트와 오류 분류, 녹음·STT·GPT·음성·총 턴 지연을 JSON으로
  기록합니다. 대화 원문·오디오·키·SDK 요청/응답 본문은 로그에서 제외합니다.
  로그 파일은 0600 권한, 1 MB, 백업 3개를 사용하며 회전 뒤 권한도 검증했습니다.
- **설정:** 검증된 불변 dataclass, BMO 환경 변수 및 CLI 우선 적용. 잘못된 값은
  원문을 보여주지 않습니다. 잘못된 내장 음성 선택은 클라우드 요청 전에 거부합니다.
  OpenAI 클라이언트는 공식 API origin을 명시해 `OPENAI_BASE_URL`을 따르지 않습니다.
- **하드웨어:** typed face/camera/button/IMU/arm 계약, strict proposed RP2040 v1
  telemetry, 유한 값·버전·필드·중복 키·프레임 크기 검사, 재전송·역순 거부,
  호스트 단조 시계 freshness, 제한된 버튼 큐. 서보 backend는 항상 거부합니다.
  실제 serial transport·camera driver·display renderer는 설치하지 않았습니다.

## 검증 증거

| 검사 | 결과 | 산출물 |
| --- | --- | --- |
| `.venv/bin/python -m unittest discover -s tests -v` | **93/93 통과, skip 0**, 마지막 실행 1.701초 | [전체 테스트](20261013-tests.txt) |
| 시스템 `python3` SDK 없는 테스트 | 93개 발견, **88 통과 + SDK 5개 skip**, 0.571초 | [SDK 없는 검사](20261013-tests-without-sdk.txt) |
| 실제 SDK 모의 HTTP 계약 | 5개 통과, 외부 네트워크 요청 0 | 위 전체 테스트의 `SDKContractTests` |
| SDK 의존성 | OpenAI 2.54.0; `pip check` 문제 없음 | [고정 의존성](../../software/requirements-lock.txt) |
| Python 컴파일 | `compileall -q bmo_ai tests` 성공 | 반환 코드 0 |
| CLI 오프라인 | 도움말·doctor·benchmark 실행 성공 | doctor/benchmark JSON 및 SDK 없는 테스트 |
| 임시 음성 파일 | 이번 테스트 후 repo와 `/tmp`의 `bmo_audio_*.wav` 없음 | 생성 파일은 테스트가 정리 |
| Git 검사 | staged whitespace 및 비밀 키/개인키 패턴 검사 통과 | 실제 비밀 값 출력/커밋 없음 |
| GitHub 호스팅 CI | 3.11/3.13 workflow 추가, **실행 안 함** | `.github/workflows/software-tests.yml` |

테스트는 실패한 대화의 문맥 롤백, complete-turn trimming, 언어 지침, 무음 모드,
잘못된 환경 값, 명령 동작, 실패 후 반복 음성, SDK 수명, 인증/타임아웃/재시도,
multipart STT, streamed TTS, 잘못된 WAV·다운로드 크기/시간, cleanup 실패,
telemetry fragmentation·CRLF 최대 길이·오버플로·순서·freshness, 팔 차단을 포함합니다.
프로세스 timeout 검사는 무해한 Python 자식 프로세스의 종료·수거만 실제 실행하며
하드웨어 프로세스를 테스트에서 실행하지 않습니다.

## 실제 Raspberry Pi 관측

커널 metadata에서 Raspberry Pi 5 Model B Rev 1.0, aarch64, Debian 13 (trixie),
Python 3.13.5, kernel `6.18.50+rpt-rpi-2712`를 확인했습니다. 제한 환경에서는
`/sys` 장치가 보이나 `/dev`가 숨겨져 있었으므로 물리 분리로 판단하지 않았습니다.
허용된 호스트 읽기 전용 doctor를 실행해 ALSA 장치 목록과 `/dev` 존재를 확인했습니다.

- 캡처: `hw:CARD=Device,DEV=0` — USB PnP Sound Device.
- 재생: HDMI 0/1 및 `hw:CARD=MAX98357A,DEV=0`.
- RP2040Zero USB audio/MIDI 카드와 `ttyACM0`의 kernel 등록을 관측했습니다.
  serial 포트는 **열지 않았으며**, controller identity와 데이터 프로토콜을
  검증하지 않았습니다. USB audio 카드가 실제 재생 장치라는 가정도 하지 않습니다.
- Pi ISP/codec/camera frontend video 등록은 확인했으나 Camera Module 3 Wide의
  실제 연결·autofocus·프레임 캡처를 입증하지 않습니다.
- 마이크/스피커 sample rate, 청취 음질, 실제 출력, 디스플레이, RAM 용량,
  저장장치 용량·배선·전압은 이번 작업에서 새로 물리 검증하지 않았습니다.

[수동적 sysfs snapshot](20261013-hardware-inventory.json),
[제한 환경 doctor](20261013-doctor-sandbox.json),
[호스트 doctor](20261013-doctor-host.json)에 관측을 보존했습니다.
장치 등록은 작동 검증과 구별합니다. 어떤 오디오 stream도 열지 않았습니다.

## 기존 IMU 이상과 보존

`/home/bgh/BMO_AI/imu_diagnostics/20261013_002849_KST/report.txt`와 `run.log`를
읽었습니다. 기존 진단은 정지 기준 큰 가속도/자이로 바이어스, 가속도 X 포화,
Self-Test 이상을 보고합니다. 예를 들어 GX 평균 약 +151.5°/s가 기록되어 있으며
정상 IMU로 사용할 근거가 없습니다. 센서 내부·실장·전원 등의 원인은 확정되지
않았습니다. 이번 작업은 진단을 재실행하거나 센서를 보정하지 않았습니다.

기존 진단은 RP2040을 REPL 정지 상태로 남겼으며 기존 `code.py`를 재시작하면
팔이 움직일 수 있다고 기록합니다. 포트 열기·REPL 조작·RP2040/센서 리셋·
펌웨어 설치·서보 명령을 수행하지 않았습니다. 기존 `/home/bgh/BMO_AI`,
`/home/bgh/be-more-agent`, `/home/bgh/bmo_tts/be-more-agent`의 코드·설정·
진단 자료도 수정하지 않았습니다. 기존 스피커 설정을 유지한 새 체크아웃 작업입니다.

## 성능과 현재 한계

[모의 성능 산출물](20261013-benchmark.json)은 200회 호스트 대화 처리만 측정합니다.

| 항목 | 결과 |
| --- | --- |
| 호스트 처리 중앙값 | 0.0514 ms |
| p95 | 0.0519 ms |
| 최대 | 0.0647 ms |
| traced peak | 29,224 bytes |
| 보관 message 수 | 16 |
| 외부 네트워크/하드웨어 호출 | 0 / 0 |

memory tracing overhead를 포함합니다. 전체 프로세스 RAM/RSS나 오디오/SDK/GPT
지연이 아닙니다. 수동적 inventory 1회는 3.292 ms였으며 역시 센서 응답 지연이
아닙니다. 실제 음성 왕복 지연은 API 키·사용자 참석하에 별도 측정해야 합니다.

타임아웃은 SDK 네트워크 단계/읽기 제한이며 재시도와 함께 전체 벽시계 상한을
보장하지 않습니다. TTS deadline은 청크 경계에서 확인하며 blocking read는 SDK
timeout을 따릅니다. 파일 크기 제한이 엄격한 end-to-end 요청 취소를 뜻하지
않습니다. 전원 차단·SIGKILL 때 파일 cleanup을 보장할 수 없습니다.
상시 청취·wake word·Realtime barge-in·echo cancellation·자동 시작 service는
추가하지 않았습니다. 의도적인 push-to-talk 흐름을 사용합니다.

## 실패·복구·미검증

- 초기 제한 환경 `git clone`과 pip 다운로드는 DNS/network sandbox 제약으로
  실패했습니다. 허용된 외부 실행으로 복제·가상환경 설치를 완료했고 OS 패키지는
  바꾸지 않았습니다.
- 제한 환경 ALSA hardware listing은 `/dev` visibility 때문에 실패 category를
  반환했습니다. 읽기 전용 호스트 doctor에서 capture/playback 목록 확인에 성공했습니다.
- 리뷰에서 발견한 **TTS 무제한 다운로드**, **cleanup이 인터럽트를 덮는 문제**,
  **startup 전에 잘못된 음성을 거부하지 못하는 문제**, **초기화 실패 시 SDK 미정리**,
  **512-byte CRLF framing 경계**를 수정하고 회귀 검증했습니다.
- `OPENAI_API_KEY` 부재는 실제 API 검증의 blocker입니다. 키를 찾으려고 비밀 파일을
  탐색하거나 사용자에게 키를 채팅으로 요구하지 않았습니다. API 비용이 드는
  요청·load test는 수행하지 않았습니다.
- 실제 영어/한국어 STT 정확도·GPT 응답 품질·TTS 발음, speaker routing·청취 출력,
  camera, display, physical buttons, serial 통합, IMU 정상화, 팔 동작은 미검증입니다.
- camera는 유지되는 libcamera/Picamera2 방향을 문서화했으나 upstream의 Raspberry
  Pi OS 지원 범위와 이 Debian 13의 실제 동작은 구별했습니다. 임의 pip 설치·
  firmware/boot 변경으로 호환성을 추측하지 않았습니다.

## 문서와 실행 시작점

- [한국어 설치/실행](../../software/README_KR.md) / [전체 설정 표와 English guide](../../software/README.md)
- [구조와 데이터/실패 흐름](../architecture.md)
- [오디오 설정과 복구](../audio.md)
- [하드웨어 계약/프로토콜/안전 한계](../hardware.md)

현재 Pi에서 클라우드 없이 확인:

```bash
cd /home/bgh/BGH_BMO/software
.venv/bin/python -m bmo_ai --doctor
.venv/bin/python -m bmo_ai --benchmark
.venv/bin/python -m unittest discover -s tests -v
```

나중에 사용자 참석하에 키를 설정한 뒤 먼저 `--text`로 짧은 대화를 검증하고,
확인된 오디오 장치로 한 번 녹음·재생을 점검하세요. 서보와 RP2040 firmware
재시작은 별도의 감독하에 검증해야 합니다. 이 소프트웨어는 팔을 구동하지 않습니다.

## Git 이력

- 출발점: `24fead9`, `feature/bmo-cloud-ai-mvp`.
- 코드/테스트/설정/CI 커밋: `d1b67198266d91a1f7c4d0475f3b840931a35ad1`
  — `Make BMO cloud conversations and speech robust with safe hardware boundaries`.
- 이 보고서와 설치·구조·관측 문서는 그 다음 별도 문서 커밋에 포함됩니다.
  `git log -2 --oneline`으로 두 커밋을 확인할 수 있습니다.
- 개발 브랜치: `feature/bmo-robust-conversation`. 커밋은 로컬에 있으며 **push하지
  않았고**, PR #1/원격 브랜치/main을 수정·병합하지 않았습니다.

## 확인한 공식 자료

2026-10-13 작업 중 원문을 확인하고 코드/문서 선택에 사용했습니다.

- [OpenAI Python SDK](https://developers.openai.com/api/reference/python): Responses,
  explicit timeout/retry 설정. 실제 설치 SDK 2.54.0의 코드 및 HTTP contract도 확인.
- [Responses create](https://developers.openai.com/api/reference/python/resources/responses/methods/create):
  completion/incomplete 상태, 출력 토큰, 로컬 context 요청.
- [Responses migration](https://developers.openai.com/api/docs/guides/migrate-to-responses):
  `store=False` API response storage 설정; 전체 보관 정책 면제를 주장하지 않음.
- [Transcription reference](https://developers.openai.com/api/reference/python/resources/audio/subresources/transcriptions/methods/create)
  및 [speech guide](https://developers.openai.com/api/docs/guides/text-to-speech):
  STT file upload, 언어 힌트, WAV 합성, 한국어 지원과 영어 중심 voice 한계.
- [ALSA PCM plugins](https://www.alsa-project.org/alsa-doc/alsa-lib/pcm_plugins.html):
  `hw`와 `plug` 변환 기능 구별.
- [Raspberry Pi camera software](https://www.raspberrypi.com/documentation/computers/camera_software.html)
  및 [Picamera2 upstream](https://github.com/raspberrypi/picamera2): 유지되는 camera stack과 OS 범위.
- [CircuitPython USB CDC](https://docs.circuitpython.org/en/latest/shared-bindings/usb_cdc/)
  및 [workflows](https://docs.circuitpython.org/en/latest/docs/workflows.html): REPL과 별도 데이터 channel 구별.
