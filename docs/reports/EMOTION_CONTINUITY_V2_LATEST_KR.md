**PASS — 작업 A의 감정 연속성 v2 코드·오프라인 회귀·승인 HTML 검증을 완료했다. 이번 변경의 실제 API/음성/LCD 검수는 NOT_RUN이다.**

2026-10-23 KST. `/home/bgh/BMO_EMOTION`, `feature/bmo-auto-emotion-v1`, 기존 [draft PR #13](https://github.com/GUNOBAE/BGH_BMO/pull/13).

## 인수 기준과 입력 제약

- 수정 전 GitHub HEAD `dd12c4a2235d97da7efbf3f460c39a84c7ed609a`. 이 커밋은 v2 작업서 추가이며 런타임 완료본이 아니었다. 로컬 clean `0b503ca7`에서 fetch/fast-forward로 인수했다.
- 구현·검증 커밋: [`5545880fedf1edf35268a5e1b935bd9cabb6ce5e`](https://github.com/GUNOBAE/BGH_BMO/commit/5545880fedf1edf35268a5e1b935bd9cabb6ce5e). 본 보고서는 이후 별도 문서 커밋으로 제출한다.
- 상세 계약: `docs/development/CODEX_EMOTION_CONTINUITY_V2_KR.md`. 이전 v1의 새 질문 neutral·2초 neutral·cancel neutral 기대값을 이 계약으로 교체했다. 과거 `EMOTION_STAGE1_LATEST_KR.md`는 당시 결과로 보존했다.
- 시작 시 main `161a2ebd99b21ddc31e4cbf6b1cbd4452967e264`, PR #13 open/draft, base `feature/bmo-integration-v1`, 당시 HEAD CI Python 3.11/3.13 성공을 확인했다. main에는 병합하지 않았다.
- 지정 `BMO_R10_CODEX_INTEGRATION_HANDOFF_2026-10-23.zip` 및 `BMO_CODEX_INTEGRATION_MASTER_ORDER_2026-10-23.md`는 현재 접근 가능한 Pi 첨부 경로와 GitHub 관련 브랜치/PR에서 찾지 못했다. 경로/링크를 요청했다. **ZIP 전체와 master order를 읽었다고 주장하지 않는다.** 사용자 메시지와 확보된 GitHub v2 작업서의 명확한 A 범위를 먼저 완료했다. 이후 B/C도 같은 입력 제약을 구분한다.

## 변경 동작

- 후보/generation 무효화와 표시 감정 초기화를 분리했다. `_emotion_hold` 및 2초 neutral poll 코드를 제거했다. 임의로 더 긴 타이머를 넣지 않았다.
- 앱 시작은 neutral. ended 뒤 감정을 유지하고, 다음 idle/listening/thinking/prepared도 이전 표시 감정으로 이어간다. 새 검증 후보는 **실제 player started**에서만 표시한다. 새 정상 응답이 neutral이면 이 경계에서 neutral로 바뀐다.
- 일반 cancel/error/disconnect/close는 소리·입·후보를 정리하고 마지막 표시 감정을 보존한다. 시작 전 happy 후보를 취소하면 이전 crying, happy가 이미 시작됐다면 happy가 남는다.
- `/emotion auto`는 현재 표정을 보존하며 수동 고정을 해제한다. 특정 감정 수동 고정과 manual 복귀를 유지한다. `/reset`과 수동 neutral 선택은 명시적 초기화다.
- repeat는 시작 전 현재 표시 감정, 실제 재생 시작에 성공 캐시의 감정, 완료 뒤 그 감정을 유지한다. WAV/cue/emotion 원자적 캐시와 취소 직전 promotion 경쟁 보호는 그대로다.
- 고정 장애/한국어 미지원 안내는 재생 중에만 neutral. ended/cancel/error 뒤 이전 대화 감정과 검증 상태를 복원한다. generation/turn/mode 검사로 reset/수동 변경/오래된 안내 callback이 새 선택을 덮지 못한다. 같은 영어 Piper fallback은 답변 감정을 유지한다.
- 제품 소스 변경은 `integration_session.py` 한 파일이다. 기존 voice의 명시 notice metadata, cache lock/generation, late result 차단 및 단일 Python 출력 경계를 재사용했다. 얼굴 원본과 adapter 아트는 수정하지 않았다.

## 실행한 검사

| 검사 | 실제 결과 |
|---|---|
| 수정 전 실패 재현 | 신규 기본 검사 2 methods 실행, **6개 실패**(6감정 중 비중립 5개에서 2초 neutral + notice 이전 감정 소실), 0.317초. 원인이 v1 session 정책임을 확인한 뒤 수정했다. |
| 최종 전체 회귀 | **415/415 PASS, skip 0**, 27.019초. 기존 398개 누적 검사 + 신규 연속성 12 + 독립 notice 검토 5. 기존 정책 테스트 4개는 의미에 맞게 이름을 변경하고 neutral 기대값을 연속성 기대값으로 교체했다. 검사를 삭제/임의 skip하거나 검증 범위를 축소하지 않았다. |
| 연속성 경계 | 6감정 long idle, listening/thinking/prepared 유지, 새 happy/neutral started 전환, malformed/refusal/오류 복구, started 전후 cancel, delayed LLM/TTS/close/disconnect, busy 모드 변경, repeat/cache 경쟁, notice 정상·취소·오류·reset·이전 callback을 확인했다. |
| 승인 HTML headless | **14 checks PASS = 기존 8 + v2 자동 6**. 같은 실제 모의 launcher/FaceServer/승인 iframe 경로를 사용했다. |
| headless 수량 | 일반 문답 9(auto 8/manual 1), 합성/gain/cue 각 9, player 11, 회수 11, forbidden API/장치 0, browser media play 0. repeat에 추가 GPT/합성/gain/cue 생성 없음. |
| 원본 활동 | 제어 clock으로 listening 시선·끄덕임, thinking 시선·흠 입, crying 눈물, 자동 깜빡임, surprised 눈·입 연동을 확인했다. 기존 bridge 1,452 fixtures와 30fps 중간 입 규칙도 보존했다. |
| 긴 시간 검사 | 6감정마다 host clock을 3,600초 진행시켜 시간 경과에도 유지됨을 확인했다. 긴 실제 sleep을 넣지 않았다. 원본 동작은 별도 Playwright clock으로 진행했다. |
| 기존 수동 통합 E2E | **4 checks PASS**, 질문 2/소유 player 3/회수 3/API·장치 0. 취소 후 다음 턴 및 정상 서버 종료를 확인했다. |
| 정적 검사 | compileall / git diff --check PASS. 실제 키를 제외한 고정 허용 child 환경으로 검사했다. |

재현은 `software/EMOTION_KR.md`의 unittest 및 세 headless 명령을 따른다.
headless의 합성 지연은 관측 fixture이며 실제 API·가청 지연 측정이 아니다.
테스트에는 독립 임시 lock/loopback port를 써 기존 앱을 종료하거나 lock을 지우지 않았다.

## 보존·사용자 실기·미실기

승인 v13.2 SHA-256 전후 `38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932` 일치.
Luna low, 기존 STT, Aaron Soft / beemo / gain 0.25 / 출력 320, 고정 정책 blob,
원본 Piper opt-in, 4.1 mini / reasoning None 복귀, 5개 Mock을 유지했다.
기존 BMO_INTEGRATION/다른 checkout, 팬·SSH·서비스·OS·펌웨어·IMU·서보·카메라와 백업은 변경하지 않았다.

작업서가 전달한 사용자 실기 근거는 기존 앱의 실제 얼굴/대화/자동 표정 및 활동 표시 확인이다.
이번 수정 뒤 감정 연속성·가청 립싱크·LCD·취소·Piper 실기 합격을 새로 받았다는 의미는 아니다.
카메라 얼굴 위치 추적이나 대화 문맥별 신규 시선 연출/웃음·울음 음성도 이번 A의 구현 범위가 아니다.
승인 원본 내부의 기본 활동 시선/깜빡임/눈물은 이미 있으며 이번에 보존 검증했다.

## 수동 검수와 안전한 복귀

현재 앱이 실행 중이면 사용자가 해당 터미널에서 `/quit`로 정상 종료한다. 자동 개발에서는 실행하지 않은 live 검수 명령이다.

```bash
cd /home/bgh/BMO_EMOTION/software
../.venv/bin/python -B -m bmo_ai.integration --live --emotion-mode auto \
  --face-file /home/bgh/Downloads/BMO_Screen_v13_2.html \
  --mic-device plughw:CARD=Device,DEV=0 \
  --speaker-device plughw:CARD=MAX98357A,DEV=0
```

다음 검사 하나: `/emotion crying` → `/emotion auto` → Enter로 질문하고 **crying으로 듣기/생각한 뒤 새 음성 시작에서만 답변 감정으로 바뀌는지** 확인한다.
`/cancel`·`/repeat`·`/reset`·`/quit` 경로는 유지한다. 원래 plain 응답은 `/emotion manual` 또는 `--emotion-mode manual`이다.

롤백은 새 앱을 정상 종료한 뒤 보존된 `/home/bgh/BMO_INTEGRATION` 실행 경로를 사용하면 된다.
정확한 이전 emotion 소스는 `0b503ca7aef450464234d1a37e8d71b225f19346`; 필요 시 clean 여부를 확인하고
해당 커밋을 별도로 선택할 수 있다. 자동 reset/clean/force-push나 기존 백업 복원은 수행하지 않았다.
