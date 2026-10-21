# 승인 얼굴 v13.2와 음성의 수동 통합 실행기

이 문서는 `feature/bmo-integration-v1`의 실행 방법이다. 기존 음성·Luna·Action
체크아웃과 팬·SSH·서비스·펌웨어는 변경하지 않는다. 자동 검증은 모의 및
headless 검사이며 실제 소리·Pi LCD·물리 버튼의 검수가 아니다.

## 환경과 유효 설정

독립 clone의 `software/`에서 실행한다. 개발 환경이 없다면 이 clone 안에서만
다음과 같이 만든다. 시스템 Python이나 기존 프로젝트 venv는 바꾸지 않는다.

```bash
cd /home/bgh/BMO_INTEGRATION
python3 -m venv .venv
.venv/bin/python -m pip install -r software/requirements-integration-dev.txt
cd software
../.venv/bin/python -B -m bmo_ai.integration --effective-config
../.venv/bin/python -B -m bmo_ai.integration --baseline --effective-config
```

실효 프로필은 Luna `low`, STT `gpt-4o-mini-transcribe`, Fish Aaron reference
`106f41ea392c4cc48c6eee76f69ff17e`, engine `s2.1-pro-free`, delivery `soft`,
pronunciation `beemo`, PCM gain `0.25`, 출력 상한 320이다. 4.1 mini 비교에는
`--baseline`을 쓰며 reasoning은 None으로 제거된다. 엔진 가용성·가격을
추정하여 다른 엔진으로 바꾸지 않는다. 기존 일반 CLI의 기본값은 유지한다.

이 실행기는 `.env`를 읽지 않는다. 선택 프로필을 실제 `BMOConfig`에 전달하고,
기존 프로세스 환경의 인증만 live backend가 사용한다. 키를 명령행·URL·보고서에
붙이지 않는다. `--effective-config`는 명시한 비밀 아닌 필드만 출력한다.

`docs/voice-policy-v1/`의 출처는 `707c66b8`이다. 실행 prompt는 같은 원본의
고정 blob과 대조한 뒤 로드한다. `english_voice`가 기존 자동 한국어 응답
지침을 대체하므로 한국어 입력도 기본 영어 응답이며 명시 한국어 요청은
존중한다. 20개 acceptance case는 검수 자료이며 실제 모델 시험 결과가 아니다.

## 장치 없는 모의 실행

```bash
cd /home/bgh/BMO_INTEGRATION/software
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B -m bmo_ai.integration --simulate --no-web --turns 2
```

위 명령은 같은 session/brain/worker/voice 경로에서 두 번 대화하고 종료한다.
mic/STT/GPT/Fish/Piper/aplay를 호출하지 않는다. 입력·답변은 합성 fixture이며
PCM fixture의 envelope를 계산하고 소유한 무해 Python child의 수명만 재생에
대응시킨다. 실제 음질·가청 지연·립싱크 검증으로 해석하지 않는다.

얼굴까지 보는 모의 수동 실행:

```bash
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B -m bmo_ai.integration --simulate \
  --face-file /home/bgh/Downloads/BMO_Screen_v13_2.html
```

출력되는 `http://127.0.0.1:8765/`를 브라우저에서 연다. 원본은 정확한 SHA-256
`38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`인 한 파일만
허용한다. 서버는 loopback에만 바인딩하며 GET 정적 자원·상태만 제공한다.
디렉터리 열람·임의 파일·명령·인증·WAV 전송 API는 없다.

## 사용자가 수행할 실제 음성 검수

다음 명령은 실제 마이크·API·스피커를 사용한다. 이번 자동 작업에서는
실행하지 않았다. 기존 인증 환경을 준비한 사용자가 수동 검수할 때만 쓴다.

```bash
cd /home/bgh/BMO_INTEGRATION/software
../.venv/bin/python -B -m bmo_ai.integration --live \
  --face-file /home/bgh/Downloads/BMO_Screen_v13_2.html \
  --mic-device plughw:CARD=Device,DEV=0 \
  --speaker-device plughw:CARD=MAX98357A,DEV=0
```

필요하면 `--baseline`으로 기존 4.1 mini 비교 경로를 선택한다. `--offline-voice`는
기존 영어 Piper 원본을 opt-in fallback으로 사용한다. 이 옵션은 오프라인 GPT나
STT를 만들지 않는다. Fish 실패 시 이미 생성된 영어 답변을 재사용하며 추가
GPT 호출을 하지 않는다. 한국어·클라우드 실패에는 기존 고정 영어 안내와
영어 모델의 제약을 유지한다. 원본 Piper 경로는 기존 `BMOConfig`를 따른다.

## 시작·취소·종료·복귀

- 터미널 Enter: 한 턴 시작. live는 고정 길이 녹음부터, simulate는 합성 질문부터 진행.
- `/cancel`: 현재 턴 무효화, 소유한 player만 종료·회수. 늦은 합성·입 이벤트 폐기.
- `/repeat`: 세션의 마지막 성공 WAV 한 개를 재합성·추가 GPT·gain 재적용 없이
  같은 단일 플레이어와 입 cue로 재생. 캐시가 없거나 busy면 시작하지 않는다.
- `/reset`: 진행 중이면 먼저 취소하고 종료 뒤 대화 문맥 초기화.
- `/emotion crying` 등: neutral/happy/angry/sad/crying/surprised 수동 감정 확인.
- `/quit`, Ctrl-C, 터미널 EOF: 앱 소유 작업과 loopback 서버 정리. 다른 앱·서비스 유지.
- 새 질문은 worker가 정리된 뒤 시작한다. 정리 중 중복 입력은 거부한다.
- 브라우저만 끊겼다가 돌아오면 현재 상대 재생 위치 또는 idle 스냅샷으로 복귀한다.
  이전 음성 재생이나 지난 입 cue의 몰아 실행은 없다. 마지막 상태 조회 후 3초 이상
  연결이 끊긴 활성 턴은 취소된다. 새로 연 페이지는 출력된 같은 URL을 사용한다.
- launcher는 사용자별 lock으로 중복 실행을 거부한다. 강제 프로세스 일괄 종료나
  lock 파일 삭제 대신 기존 launcher의 `/quit`를 사용한다.

캐시는 비공개 임시 WAV 한 개이며 성공 재생 뒤에만 교체한다. 합성·재생 실패나
취소는 이전 성공 캐시를 유지한다. 반복 중에도 취소·중복 차단이 적용되며
문맥은 변하지 않는다. `/reset`과 앱 종료는 캐시를 정리한다. 음성을 장기 보관하는
DB가 아니며 강제 종료·시스템 장애 뒤의 정리는 정상 종료와 구분한다.

## Action v0는 모의 경로만 연결

`--simulate` 앱의 터미널에서 다음을 입력한다. 쉘 명령이 아닌 앱 입력이다.

```text
/actions-demo
/action {"action":"play_song","arguments":{"song_id":"demo_song"},"request_id":"manual-song-001"}
```

다섯 demo는 left/right arm, photo, demo_song, demo_game의 원본 Action v0 계약을
검사한 뒤 Mock 결과만 출력한다. 결과의 request_id/action/status/code/message/
simulated/executed 7개 필드를 보존하며 정상도 `status=simulated`, `code=mock_only`,
`executed=false`다. 팔·사진·노래·게임은 실제 실행하지 않는다. live LLM tool이나
실제 adapter를 추가하지 않았다. `--live`에서는 이 명령의 실행을 거부한다.

논리 요청의 request_id는 호출자가 안정적으로 전달한다. 같은 요청의 재시도는
같은 ID, 새 요청은 새 ID를 사용한다. `/actions-demo`의 고정 ID도 재실행 시
duplicate다. 같은 ID의 다른 내용은 conflict, 잘못된 입력과 용량 초과는 rejected로
구분한다. `/reset`은 음성·문맥만 초기화하며 Mock ledger를 재생성하지 않는다.
ledger는 launcher 프로세스 메모리에만 있고 재시작을 넘는 영속 중복 보장은 없다.
정확한 원본 계약과 출처는 `docs/action-system-v0.md`를 따른다.

Python/ALSA만 음성을 출력하며 browser의 playAudio/playBlob/media play를 쓰지
않는다. gain은 최종 WAV에 한 번 적용하고 해당 WAV의 envelope로 입 cue를 만든다.
입 움직임은 음량 기반이며 음소 분석이 아니다. `on_start`는 player 프로세스 시작
근사값이며 실제 가청 시작 보장이 아니다. Python과 JS monotonic 원점은 비교하지
않고 상대 위치와 최신 스냅샷을 사용한다.

## 자동 검증 명령

```bash
cd /home/bgh/BMO_INTEGRATION/software
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B -m unittest discover -s tests -v
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B checks/check_integration_browser.py \
  --face /home/bgh/Downloads/BMO_Screen_v13_2.html --chromium /usr/bin/chromium
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B checks/check_integration_end_to_end.py \
  --face /home/bgh/Downloads/BMO_Screen_v13_2.html --chromium /usr/bin/chromium
```

후자는 승인 원본 자체를 Chromium headless로 읽어 API·SVG·입 전환과 adapter를
검사한다. 승인 파일은 Git에 없으므로 새 checkout의 CI에 원본 검증까지 있다고
가정하지 않는다. 무음/종료/취소 때 closed와 감정 유지, 지연 이벤트 폐기,
소유 child 취소·회수, 연결 복귀를 테스트하되 실물 검수는 사용자에게 남긴다.

마지막 E2E 명령은 실제 `--simulate` launcher·자체 서버·승인 iframe을 함께 띄워
한 턴과 반복·취소·다음 턴·종료를 확인한다. 상태 관측용 150ms fixture 지연을
사용하므로 API·가청·실물 지연 측정으로 해석하지 않는다. launcher 검사는
중복 실행 lock을 사용하므로 다른 수동 launcher를 정상 종료한 뒤 실행한다.

최신 결과와 미실행 항목은 `docs/reports/INTEGRATION_STAGE1_LATEST_KR.md`를 따른다.
