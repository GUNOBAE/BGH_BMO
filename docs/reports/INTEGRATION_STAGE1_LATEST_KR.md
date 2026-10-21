# BMO 얼굴·음성 통합 최종 보고서

**PASS — 수동 통합 실행기, 승인 얼굴·단일 음성 출력·취소, 세션 WAV 반복 캐시, 5종 Mock Action 연결을 구현했다. 회귀 323개, 승인 HTML headless 8개, 단일 launcher E2E 4개 검사를 통과했으며 실제 음성·Pi LCD 검수는 NOT_RUN이다.**

기준일: 2026-10-22 KST. 작업 경로 `/home/bgh/BMO_INTEGRATION`, 브랜치
`feature/bmo-integration-v1`, draft PR [#12](https://github.com/GUNOBAE/BGH_BMO/pull/12),
base `feature/bmo-luna-low-voice-ab`다. main 병합·force-push·브랜치 삭제·서비스
배포는 하지 않았다. 기존 독립 clone의 같은 목적 미커밋 구현을 보존한 뒤 이어갔다.

## 출처와 검증 커밋

| 기준 | SHA / 의미 |
|---|---|
| Luna 출발점 / PR base | `054ced8f894f25f4cd0a178c2b097b5afe2c8576` |
| 야간·Stage 1·통합 기준 문서 HEAD | `ab6f20054f545ea07954fb4f74624a5e956a0198` |
| 음성 정책 원본 | `707c66b8876130be75ad216fa10aaabf2bdcdb56` |
| Pi preflight 보고서 원본 | `0bfb8bd4b4d99bffb9ba479eab6b8b7f62ac25b4` |
| 핵심 코드 / 첫 업로드 checkpoint | `0b6ec7053abd714b6dd0dce0f613654c87492b51` / `78c684dadb64ab95052299d8380ac7d4d2fe7c07` |
| 녹음 취소 수정 | `7f47bd6893c7f5a3cee1cab037ec20cd8ecdeca9` |
| 마지막 성공 WAV 캐시 | `c6eb20fb93e8f3d5cf6db2dab817df1116354ead` |
| 5종 Mock Action 연결·323개 회귀 | `757b53595d58b1b4f393ca2a0674c6bb2cc95579` |
| E2E 검사·검증 소스 고정 | `0042e4b5c586cfb60f060f2c33cbf93e9d0328d6` |

보고서 자체의 커밋 SHA를 미리 추정하지 않는다. 코드 검증 SHA는 위와 같고,
업로드 뒤 직접 확인한 최종 branch/PR HEAD는 PR 본문의 업로드 검증에 별도로
기록한다. main·문서 브랜치를 통째로 merge하거나 README 전체를 개편하지 않았다.

## 원본 보존과 비공개 백업

| 로컬 인수 원본 | bytes | 작업 전후 SHA-256 |
|---|---:|---|
| `/home/bgh/Downloads/BMO_Screen_v13_2.html` | 159892 | `38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932` |
| `/home/bgh/bmo-voice-test/bmo-piper/en_US-bmo_voice.onnx` | 63516050 | `2b323dfdd76efb36b096b93ef8a6f0a60a26886aaaeeba97e691c13641611fb8` |
| 같은 모델의 `.onnx.json` | 7098 | `0786c36ebf1ffc2b6979391941d64bd2ed0279bd0114654d980ad957ec914776` |
| `/home/bgh/bmo-voice-test/piper/piper` | 5283040 | `0c44a6360a21367b5d03b8656c3e274f4de715f6533245d8c1f17c242631912b` |

Piper 해시는 이번 현장 인수값이며 과거 승인 해시라고 확대하지 않는다. 승인
얼굴의 HTML/SVG/animation data는 변경하지 않았다. 네 파일의 원본·새 사본
해시를 작업 종료 때 다시 대조해 일치를 확인했다. 원본·모델은 공개 Git에 없다.

새 비공개 백업은
`/home/bgh/BMO_backups/20261022_033859_660947_KST_integration_resume`이다.
기존 미커밋 구현 23개 파일과 위 인수 파일 4개를 복사·해시 대조했다. 기존 백업을
덮어쓰거나 삭제하지 않았다. 같은 디스크의 사본이며 전체 시스템 복구 이미지는 아니다.

종료 시 기존 체크아웃의 HEAD·변경 상태도 시작 시점과 같았다.

- `/home/bgh/BGH_BMO`: Piper `7bed6d02`, clean.
- `/home/bgh/BMO_LUNA_AB`: Luna `054ced8f`, 기존 미추적 logs만 보존. 내용 미열람.
- `/home/bgh/BGH_BMO_ACTIONS`: Action v0 `b346cc30`, clean.

API 키·인증 설정·실제 `.env`·개인 로그·녹음·모델·원본 얼굴·게임·venv는
공개 stage하지 않았다. 팬·SSH·펌웨어·서보·다른 서비스와 OS 설정도 변경하지 않았다.

## 구현 결과

### 하나의 수동 실행 경로와 선택 프로필

`python -m bmo_ai.integration`는 `--simulate` 또는 `--live`를 명시해야 한다.
모의 모드도 같은 session/brain/worker/voice/server를 사용하며 실제 backend는
호출하지 않는다. 사용자별 lock으로 중복 launcher를 거부한다.

선택값을 실제 `BMOConfig`에 전달한다: `gpt-6-luna`/`low`,
`gpt-4o-mini-transcribe`, Fish Aaron `106f41ea392c4cc48c6eee76f69ff17e`,
engine `s2.1-pro-free`, delivery `soft`, pronunciation `beemo`, PCM gain `0.25`,
응답 상한 320. soft의 기존 연기 지시를 보존했고 gain은 한 번만 적용한다.
`--baseline`은 기존 4.1 mini와 reasoning None을 함께 적용한다. 가격·가용성을
추측해 모델이나 엔진을 변경하지 않았다. 실제 클라우드 성공은 이번 검사 범위 밖이다.

`.env`를 새로 만들거나 읽지 않는다. `--effective-config`는 비밀 아닌 allowlist만
출력한다. 정책 네 원본과 runtime prompt를 `707c66b8`에서 인수하고 고정 Git blob
`a63a8e5c2c768b14b878aa1ca8468323a05fe554`를 검사한다. `english_voice`는 기존
자동 한국어 응답 지침을 대체하며 일반 CLI는 보존한다. 20개 acceptance case는
review data이고 실제 모델 합격 20건이라고 표현하지 않는다.

### 취소·단일 출력·승인 얼굴

- 기존 worker의 busy guard·bounded queue·취소·늦은 결과 차단을 재사용하고
  session/turn/utterance 식별자를 추가했다. 같은 brain에서 문맥을 유지·reset한다.
- Python/ALSA만 출력한다. browser의 playAudio/playBlob/media play는 호출하지 않는다.
- 합성 완료는 prepared이며 player 시작 뒤에만 speaking과 입 cue를 활성화한다.
  `on_start`는 프로세스 시작 근사값이지 실제 가청 시작 보장이 아니다.
- 최종 gained WAV의 PCM envelope와 원본 bridge를 사용한다. 30fps 중간 프레임,
  low→body→wide와 tight→round를 유지하며 음소 분석이라고 부르지 않는다.
- 감정·활동·재생·입 시간표를 분리하고 말하는 중 감정 변경에 activity를 덧붙이지 않는다.
  무음은 external+closed, 종료·취소·오류는 off+closed와 감정 보존을 적용한다.
- localhost GET-only 서버는 승인 파일 하나와 앱 정적 자원·상태만 제공한다.
  임의 파일·명령·키·WAV 제공 API나 directory root를 열지 않는다.
- Python/JS monotonic 원점을 비교하지 않고 상대 재생 위치와 최신 스냅샷을 쓴다.
  visibility·HTTP race·stale session/utterance를 차단하고 재연결 시 과거 소리나
  cue를 몰아서 재실행하지 않는다. iframe/앱 종료 시 소유 자원만 정리한다.
- 실제 재생 중 취소뿐 아니라 추가 검토에서 발견한 녹음 취소 전달 누락도 고쳤다.
  녹음도 소유 child를 종료·회수하며 기본 호출의 호환성은 유지했다.
- Fish/Piper는 같은 출력 이벤트 경로다. Piper는 opt-in·영어 제약·기존 고정 안내를
  유지하고 생성된 영어 답변을 재사용한다. 추가 GPT나 오프라인 두뇌/STT가 생기지 않는다.

### 세션 캐시와 Mock Action

캐시는 마지막 성공 gained WAV 한 개와 그 cue/info만 보관한다. `/repeat`는
동일 WAV·gain·시간표를 재사용해 GPT/Fish/Piper/attenuate/cue 계산을 다시 하지
않는다. 새 turn/utterance ID, 같은 worker/player 취소 경로를 사용하고 listening이나
문맥 변경도 없다. 실패·취소 때 이전 성공 캐시를 보존하고 새 성공에만 교체한다.
reset/close는 삭제하며 generation/closed guard가 늦은 저장을 막는다. 비공개
임시 파일은 0600이며 장기 녹음 DB가 아니다. 강제 프로세스/시스템 장애는 정상
종료 정리와 구분한다.

Action은 원본 `b346cc3017ca0ee4cb2162384d1fa1499659739a`의 actions.py,
27개 tests, 계약 문서를 바이트 그대로 인수했다. launcher에 한 번 만든 Mock host가
구조화 요청의 `action/arguments/request_id`를 검사하고 7개 결과 필드를 보존한다.
다섯 정상 결과는 `status=simulated`, `code=mock_only`이고 **모든 결과 executed=False**다.

| Action | arguments |
|---|---|
| raise_left_arm | `{}` |
| raise_right_arm | `{}` |
| take_photo | `{}` |
| play_song | `{"song_id":"demo_song"}` |
| launch_game | `{"game_id":"demo_game"}` |

live 모드는 차단하고 LLM tool·실제 장치 adapter는 없다. 안정적 논리 request ID,
중복·충돌·인자·용량 제한을 유지한다. `/reset`도 ledger를 재생성하지 않는다.
ledger는 프로세스 메모리이며 재시작을 넘는 중복 방지가 아니다. 결과는 JSON으로
표시하며 팔·사진·음악·게임이 실제 실행됐다는 발화를 만들지 않는다.

## 이번에 실행한 검사와 오류 수정

모든 검사 child에 실제 키를 전달하지 않았다. PATH/HOME/locale/PYTHONPATH 등
명시 allowlist만 전달했다. 기존 독립 clone의 `.venv`와 Chromium을 사용했으며
시스템·원본 프로젝트의 환경이나 패키지를 변경하지 않았다.

| 검사 | 실제 결과 |
|---|---|
| pip check | PASS, 의존성 오류 0 |
| 핵심 첫 checkpoint | 273 tests OK, headless 8 checks PASS |
| 녹음 취소 수정만 stage/export한 회귀 | 277 tests OK |
| 캐시 포함 회귀 | 286 tests OK, 새 cache 8개 포함 |
| Action 포함 최종 회귀 | **323 tests OK, 실패·오류·skip 0** |
| 승인 HTML headless | **8 checks PASS**, 원본 해시 전후 일치 |
| 원본 bridge 대조 | 1,452 fixture 및 PCM envelope/cue 확장 일치 |
| 실제 launcher+승인 iframe 모의 E2E | **4 checks PASS**, 일반 턴→repeat→cancel→다음 턴→quit |
| E2E counters | listen 2, brain 2, player 3, reaped 3, forbidden 0 |
| browser 오디오 | HTMLMediaElement.play / playAudio / playBlob 모두 0 calls |
| Mock host CLI | 15개 결과: 처음 5개 simulated, 재시도·reset 뒤 10개 duplicate, executed 0 |
| 첫 checkpoint GitHub CI | Python 3.11·3.13 모두 SUCCESS. 최종 push CI는 PR checks와 업로드 검증에서 구분 |

E2E의 150ms 지연은 합성 단계 관측 fixture이며 가청·API 지연 측정이 아니다.
실제 승인 원본의 stop 결과는 closed라서 rest를 기대하던 검사를 고쳤다.
visibility 이전 HTTP 응답이 늦게 발화를 재개하던 경계, retired cue·종료 정리,
녹음 취소 전달 누락을 수정하고 회귀를 남겼다. 외부 환경 차단은 없었다.

로컬 검사 근거는 `/tmp/bmo-integration-core-checks-28svda6f/summary.json`,
`/tmp/bmo-integration-record-fix-0xlihl60/regression.txt`,
`/tmp/bmo-integration-cache-checks-__6mktaf/summary.json`,
`/tmp/bmo-integration-final-checks-zk8l5hw5/summary.json`이다. 임시 경로의 장기
존재를 보장하지 않으며 원본 개인 로그 대신 실제 검사 결과만 이 보고서에 요약했다.
E2E는 `software/checks/check_integration_end_to_end.py`의 직접 실행 결과다.

## 정확한 수동 실행·취소·복귀

```bash
cd /home/bgh/BMO_INTEGRATION/software

# 장치/API 없는 2턴 후 종료
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B -m bmo_ai.integration --simulate --no-web --turns 2

# 승인 얼굴을 연결하는 모의 수동 앱
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B -m bmo_ai.integration --simulate \
  --face-file /home/bgh/Downloads/BMO_Screen_v13_2.html

# 사용자가 실제 화면·음성 검수할 때만 실행; 이번 작업에서는 실행하지 않음
../.venv/bin/python -B -m bmo_ai.integration --live \
  --face-file /home/bgh/Downloads/BMO_Screen_v13_2.html \
  --mic-device plughw:CARD=Device,DEV=0 \
  --speaker-device plughw:CARD=MAX98357A,DEV=0
```

출력된 `http://127.0.0.1:8765/`를 브라우저에서 열고 터미널 Enter로 질문한다.
`/cancel` 취소, `/repeat` 마지막 성공 WAV 반복, `/reset` 문맥·캐시 정리,
`/emotion crying` 감정 주입, `/quit` 또는 Ctrl-C/EOF로 종료한다.
`--baseline`은 4.1 mini 복귀, `--offline-voice`는 원본 영어 Piper opt-in이다.

모의 앱 안의 `/actions-demo`는 고정 ID의 5개 요청을 전달한다. `/action JSON`에는
정확한 구조화 요청을 입력한다. 예시는 [수동 실행 안내](../../software/INTEGRATION_KR.md)에
있다. live에서는 거부한다. 캐시가 없거나 busy일 때 repeat는 시작하지 않는다.

브라우저 복귀는 같은 URL에서 최신 위치 또는 idle로 이루어지고, 3초 이상 연결이
끊긴 활성 턴은 취소된다. 새 질문은 worker 정리 뒤 가능하다. 종료는 앱 소유
자원만 정리하며 팬·SSH·다른 앱을 일괄 종료하지 않는다. 모든 검증 재실행 명령은
수동 실행 안내에 있으며, 원본 승인 파일이 없는 CI에 headless 검증까지 있다고
가정하지 않는다.

## 미실행 항목과 다음 한 단계

실제 mic/STT/GPT/Fish/Piper 합성/ALSA 재생, 실제 음질·볼륨·가청 지연,
Pi LCD, 카메라, 직렬, RP2040, 서보, IMU, 물리 버튼은 **NOT_RUN**이다.
호출어/VAD·스트리밍·실제 게임·패드·시리얼 ACK·제스처는 이후 개발 범위다.
GP7 충돌을 펌웨어 수정으로 해결하지 않았고 IMU를 제어 입력으로 사용하지 않았다.

추가 기능인 WAV 캐시와 5종 Mock host까지 완료했으며 미해결 코드·환경 차단은
없다. **사용자의 다음 한 단계는 수동 live launcher로 실제 화면·음성 한 턴과
취소·종료를 함께 확인하는 것**이다. 이 결과를 자동 검사 통과와 구분해 기록한다.
