# BMO 얼굴·음성 통합 구현 보고서

**PASS — 핵심 얼굴·음성 통합의 오프라인 회귀 273개와 실제 승인 HTML headless 8개 검사를 통과했다.** 실제 음성·Pi LCD 검수는 NOT_RUN이며, 이 첫 checkpoint에서는 후속 WAV 캐시와 Action v0 host 연결을 아직 구현하지 않았다.

기준일: 2026-10-22 KST. 작업 경로 `/home/bgh/BMO_INTEGRATION`, 브랜치
`feature/bmo-integration-v1`. 기존 독립 clone에 같은 목적의 미커밋 구현이 있어
23개 변경 파일을 비공개 timestamp 백업에 보존한 뒤 구현·검증을 이어갔다.
기존 체크아웃을 전환하거나 linked/shared worktree를 만들지 않았다.

## 출처와 코드 검증 SHA

| 기준 | SHA / 상태 |
|---|---|
| Luna 출발점 및 PR base | `054ced8f894f25f4cd0a178c2b097b5afe2c8576` |
| 핵심 구현·검증 코드 | `0b6ec7053abd714b6dd0dce0f613654c87492b51` |
| 야간/Stage 1/통합 기준 문서 HEAD | `ab6f20054f545ea07954fb4f74624a5e956a0198` |
| 음성 정책 출처 | `707c66b8876130be75ad216fa10aaabf2bdcdb56` |
| 완료된 Pi preflight 출처 | `0bfb8bd4b4d99bffb9ba479eab6b8b7f62ac25b4` |
| 최종 보고서 업로드 SHA | 본문 자체의 SHA를 미리 추정하지 않는다. 업로드 뒤 GitHub branch HEAD와 PR에서 별도 확인한다. |

main 및 기존 문서 브랜치를 통째로 merge하지 않았고 README 전체 개편도 하지
않았다. 정책의 네 원본 파일과 runtime prompt는 출처 blob과 같으며, loader가
고정 Git blob `a63a8e5c2c768b14b878aa1ca8468323a05fe554`를 검사한다.

## 원본과 비공개 백업

승인 얼굴은 `/home/bgh/Downloads/BMO_Screen_v13_2.html`, 159,892바이트다.
작업 전후 SHA-256은
`38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`로 같다.
원본 HTML/SVG/animation data는 수정하거나 공개 Git에 추가하지 않았다.

Piper 원본 모델은 `/home/bgh/bmo-voice-test/bmo-piper/en_US-bmo_voice.onnx`이며
SHA-256은 `2b323dfdd76efb36b096b93ef8a6f0a60a26886aaaeeba97e691c13641611fb8`,
JSON은 `0786c36ebf1ffc2b6979391941d64bd2ed0279bd0114654d980ad957ec914776`다.
이들은 현장 인수 해시이며 과거 승인 해시라고 확대하지 않는다.

기존 미커밋 구현과 승인 얼굴·Piper 모델/JSON/실행 파일은
`/home/bgh/BMO_backups/20261022_033859_660947_KST_integration_resume`의
새 비공개 폴더에 복사·해시 대조했다. 기존 백업을 덮어쓰거나 삭제하지 않았다.
백업은 같은 디스크이며 시스템 전체의 독립 복구 이미지가 아니다.

## 구현한 핵심 경로

- `python -m bmo_ai.integration`: 같은 session/brain/worker/voice/face를 사용하는
  명시적 `--live` 또는 완전 모의 `--simulate` 수동 실행기. 사용자별 중복 실행 방지.
- Luna low / STT mini / Fish Aaron `s2.1-pro-free` / soft / beemo / PCM gain .25 /
  출력 320의 실제 config 전달. `--baseline`은 4.1 mini와 reasoning None을 함께 적용.
- `english_voice`의 외부 원본 prompt를 단일 언어 지침으로 적용. 기존 일반 CLI는
  보존한다. 20개 acceptance case는 review data이며 실제 모델 합격 수가 아니다.
- session/turn/utterance ID, busy guard, bounded worker queue, 지연 결과 차단,
  합성·재생 중 취소·timeout·reset·종료와 다음 턴 복구.
- 소유 player만 실행 중 취소·종료·회수. tests에서는 무해 Python child만 사용.
- gain을 한 번 적용한 최종 WAV의 PCM envelope와 원본 bridge를 사용한 입 cue.
  Python/ALSA만 출력하며 browser 오디오 재생은 없다.
- 승인 파일 하나와 앱 정적 자원·상태만 제공하는 GET-only loopback 서버.
  same-origin iframe `?kiosk=1`, 감정/활동/재생/입 시간표를 분리한다.
- 실제 원본의 30fps 중간 프레임, 감정 보존, 무음 external+closed,
  종료 off+closed, stale event 거부, visibility/HTTP race 및 iframe 종료 정리.
- Fish/Piper 정상·실패가 동일 출력 이벤트 경로를 사용한다. Piper는 opt-in,
  기존 영어 제약·고정 안내를 유지하며 이미 생성된 답변을 재사용한다.

`on_start`는 player 프로세스 시작 근사값이며 가청 시작 보장이 아니다.
Python/JS monotonic 원점은 비교하지 않고 상대 재생 위치를 전달한다. 연결 복귀는
현재 위치 또는 idle을 적용하며 지난 소리나 cue를 처음부터 재생하지 않는다.

## 이번에 실행한 검증

테스트 child 환경은 PATH/HOME/locale/PYTHONPATH 등 명시 allowlist만 전달했다.
실제 클라우드·GitHub 자격증명은 상속하지 않았다. 개발 의존성은 기존에 마련된
이 독립 clone의 `.venv`를 사용했으며 시스템·원본 프로젝트 환경은 바꾸지 않았다.

| 검사 | 이번 실제 결과 |
|---|---|
| `pip check` | PASS, 의존성 오류 0 |
| `unittest discover -s tests -v` | 273 tests, OK, 실패·오류·skip 0 |
| 승인 HTML Chromium headless | 8 checks PASS, 원본 해시 전후 동일 |
| 원본 `_test.bridge` | 1,452 fixture 일치 |
| PCM envelope·cue 확장 | 실제 원본 알고리즘과 대조 PASS |
| 모의 launcher 연속 2턴 | history 2→4, 두 턴 playback ended |
| 취소·지연·reset·timeout·종료 | 무해 child 종료·회수와 다음 턴 복구 PASS |
| browser `playAudio`/`playBlob`/media play | 0 calls |
| stage 검토 | 명시한 소스·검사·문서 26개, 제한된 credential 형태 탐지 0 |

첫 핵심 검사 기록은 로컬 `/tmp/bmo-integration-core-checks-28svda6f/summary.json`에
있다. 임시 테스트 기록이 장기 보존된다고 보장하지 않으며 이 표는 실제 출력에서
요약했다. 개인 대화·녹음·원본 얼굴·모델·키·venv는 공개 stage하지 않았다.

## 수동 실행·취소·복귀

실행 폴더는 `/home/bgh/BMO_INTEGRATION/software`다.

```bash
# 장치·API 없는 두 턴 검사 후 종료
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B -m bmo_ai.integration --simulate --no-web --turns 2

# 승인 얼굴을 연결하는 수동 모의 앱
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B -m bmo_ai.integration --simulate \
  --face-file /home/bgh/Downloads/BMO_Screen_v13_2.html
```

출력된 `http://127.0.0.1:8765/`를 브라우저에서 열고 터미널 Enter로 질문한다.
`/cancel` 취소, `/reset` 문맥 초기화, `/emotion crying` 감정 주입,
`/quit` 또는 Ctrl-C/EOF로 종료한다. 실제 음성 검수는 사용자가 기존 인증
환경에서 `--simulate` 대신 `--live`를 선택한다. 전체 명령·장치 선택·Piper
옵션은 [수동 실행 안내](../../software/INTEGRATION_KR.md)에 있다.

브라우저 재접속은 같은 출력 URL로 하며 최신 상대 위치/idle로 복귀한다.
3초 이상 연결이 끊긴 활성 턴은 취소된다. 앱 종료 시 소유 작업만 정리하며
팬·SSH·다른 Chromium/오디오·서비스를 일괄 종료하지 않는다.

## 미실행·남은 범위

실제 mic/STT/GPT/Fish/Piper 합성/ALSA 재생, 실제 음질·볼륨·가청 지연,
Pi LCD, 카메라, 직렬, RP2040, 서보, IMU, 물리 버튼은 **NOT_RUN**이다.
서비스·OS 오디오·자동 시작·SSH·팬·펌웨어는 변경하지 않았다.

첫 checkpoint의 추가 기능 상태:

1. 최근 성공 WAV 한 개의 세션 캐시와 무재합성 `/repeat`: 다음 단계.
2. Action v0 기준 `b346cc30`의 5종 Mock host 연결: 다음 단계.

핵심 코드·보고서 checkpoint를 먼저 commit/push하고 위 순서로 별도 커밋한다.
실물 단계의 다음 한 가지는 사용자의 수동 launcher로 화면·음성을 함께 확인하는
검수다. main 병합·force-push·브랜치 삭제·서비스 배포는 하지 않는다.
