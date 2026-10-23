# Codex 후속 작업서 — 감정 유지 듣기·생각 복원 v2

기준일: 2026-10-23 KST.
상태: **실기 피드백 수신·원인 확인·수정 계약 준비. 이 문서 커밋은 런타임 수정/배포 완료가 아니다.**

## 0. 이번 결론과 우선순위

사용자가 승인한 얼굴을 다시 만들지 않는다. 통합 session이 감정을 지우는 규칙을 수정한다.

**울면서 대답 → 울음 상태로 대기 → 울면서 듣기 → 울면서 생각 → 다음 검증된 답변의 감정으로 말하기.**

감정(emotion)은 대화 사이에 유지되는 상태이고, 듣기/생각/말하기/대기(phase)는 그 위에서 바뀌는 활동이다. 이전 작업서의 '새 질문 neutral', '답변 종료 2초 후 neutral'는 이번 사용자 피드백과 충돌한다. **`CODEX_EMOTION_STAGE1_KR.md` §2.B 및 이에 종속된 테스트 기대값은 이 문서가 대체한다.** 원본 화면·음성 설정·보안/취소/단일 출력 조건은 그대로 유지한다.

이번 우선 구현은 감정 연속성이다. 시선·리액션 전체 확장이나 새 웃음/울음 음성을 한꺼번에 끼워 넣지 않는다. §5에 기존 동작과 미연결 기능을 정확하게 구별한다.

## 1. 새 사용자 실기 근거

사용자 보고(2026-10-23):

- `/home/bgh/BMO_EMOTION`의 live auto 앱에서 얼굴이 표시되고 실제 대화가 된다.
- 답변에 맞는 자동 표정과 듣기/생각 활동 전환을 눈으로 확인했다.
- 답변 후 표정이 유지되는 것도 관찰했지만, 다음 듣기/생각은 이전 울음·슬픔을 유지하지 않고 일관된 표정으로 보인다고 지적했다.
- 승인 화면의 울면서 듣기/생각하기를 이어가고 싶으며, 눈/얼굴의 대각선·상하좌우 이동과 리액션을 왜 덜 사용하는지 질문했다.

이는 사용자 실기 확인이다. 새로 직접 촬영/계측한 결과나 모든 취소/반복/Piper/장치 검수 통과로 확대하지 않는다. 기존 보고서의 NOT_RUN은 작성 당시 이력으로 보존하고 새 보고서에 위 범위만 추가한다.

## 2. 실제 소스와 원인

검토한 코드 기준: `0b503ca7aef450464234d1a37e8d71b225f19346`, PR #13, `feature/bmo-auto-emotion-v1`.

`software/bmo_ai/integration_session.py`:

1. `_invalidate_expression()`이 pending 후보/generation 무효화와 화면 감정 초기화를 함께 한다. 자동 모드이면 `emotion='neutral'`까지 덮어쓴다.
2. `_start_turn()`이 위 함수를 호출하므로 새 질문을 시작할 때 이전 감정이 사라진다.
3. `emit('ended')`가 `_emotion_hold = (..., clock()+2.0)`를 만들고 `poll()`이 neutral로 돌린다. 다음 질문을 잠깐 늦게 해도 감정을 잃는다.

이는 HTML의 울음/감정 기능 누락이 아니라 **통합 정책 불일치**다. 기존 398개 검사 통과는 당시의 2초 복귀 계약에 대한 결과이지 이번 사용성 요구를 만족했다는 뜻이 아니다.

`software/bmo_ai/integration_web/adapter.js`는 감정과 활동을 분리해 적용하지만 backend가 이미 neutral로 보내면 이전 울음을 복원할 수 없다. 원본을 고치거나 frontend에서 별도 감정 추론으로 우회하지 않는다.

## 3. 작업 위치와 보존

- 저장소 `GUNOBAE/BGH_BMO`, 브랜치 `feature/bmo-auto-emotion-v1`, **기존 `/home/bgh/BMO_EMOTION`을 이어서 사용한다. 새 복제 폴더를 또 만들지 않는다.**
- 적용 AGENTS.md, 실제 경로·remote·HEAD·status·현재 앱 실행 여부의 변동만 확인한다. 전체 preflight 반복 금지.
- 사용자에게 BMO 터미널에서 `/quit`로 정상 종료하도록 안내한다. 아직 실행 중이면 임의 kill/lock 삭제/서비스 중단하지 않는다. 실행과 분리된 소스 검사·모의 검증은 계속할 수 있다.
- 현재 앱과 미커밋 변경을 보존한다. clean이면 정상 fetch 및 fast-forward로 이 문서를 인수한다. dirty/divergence는 사용자 변경을 보존하여 해결하고 reset --hard/clean/force-push를 쓰지 않는다.
- 기존 성공 기준 `0b503ca7`를 명시해 복귀 경로를 남기되 `/home/bgh/BMO_INTEGRATION` 및 다른 clone은 수정하지 않는다.
- 승인 얼굴 `/home/bgh/Downloads/BMO_Screen_v13_2.html`, SHA-256 `38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`를 읽기 전용으로 참조한다. 원본 HTML/SVG/표정/입 프레임/시선 크기/색/비율 수정·공개 업로드 금지.
- Luna low + 기존 STT + Aaron Soft / beemo / gain 0.25 / 출력 상한 320, 원본 Piper 및 opt-in, 기존 4.1 mini 복귀, 5개 Mock 계약을 보존한다.
- 새 GPT 분류 호출, 새로운 TTS 태그/preset, 카메라/시리얼/서보/펌웨어/팬/SSH/OS/서비스 조작은 하지 않는다. 검증은 실제 키를 제외한 오프라인·headless로 진행한다.

## 4. 감정 연속성 수정 계약

### A. 표시된 감정과 미확정 후보를 분리

`_invalidate_expression()` 또는 동등한 함수에서 **후보/오래된 결과를 버리는 일**과 **사용자가 보는 감정을 지우는 일**을 분리한다. generation/turn/utterance의 늦은 결과 차단은 약화하지 않는다.

- 앱 시작은 neutral.
- 정상 답변이 끝나면 현재 감정을 유지한다. 기본 경로의 고정 2초 복귀 타이머는 제거한다. 더 긴 임의 타이머로 바꿔 같은 문제를 숨기지 않는다.
- 다음 질문과 listening/thinking, 다음 답변의 prepared 동안은 이전 표시 감정을 유지한다. 입/활동은 기존 원본 규칙대로 바뀐다.
- 새 speech/emotion은 검증과 기존 취소 검사를 통과한 후보로만 staging한다. **새 player started**에 그 감정을 적용한다. 다음 정상 답변이 neutral이면 그때 neutral로 바뀌는 것은 정상이다.
- 예: crying → 다음 질문 듣기 crying → 생각 crying → 다음 prepared crying → 새 happy 음성 시작 happy. 새 답변의 감정이 늦게 계산되므로 듣는 즉시 그 감정으로 예측 전환하지 않는다.
- 일반 cancel/error/disconnect는 소리·입·후보를 중단하지만 마지막 실제 표시 감정은 보존한다. 아직 시작하지 않은 happy 후보를 취소했다면 이전 crying이 남아야 한다. happy 발화가 이미 시작된 뒤 취소했다면 표시 중 happy를 유지한다.
- `/reset`은 neutral/history/cache/후보를 명시적으로 초기화한다. `/emotion neutral`은 기존 수동 neutral 선택으로 유지한다.
- `/emotion auto`는 수동 고정을 해제하되 현재 표시 감정을 유지하고 다음 답변부터 자동 결정한다. `/emotion manual` 및 특정 감정 수동 고정은 기존 의미를 보존한다. busy 중 변경과 늦은 후보 차단 유지.
- `/repeat`는 성공 WAV/cue/emotion을 재사용한다. repeat 시작 전에는 현재 감정, 실제 반복 재생 시작 때 캐시의 감정, 완료 뒤에는 그 감정을 유지한다. 추가 합성/LLM/gain 적용 없음.
- 고정 장애 안내는 neutral로 말하는 기존 계약을 유지하되 이를 새로운 대화 감정 판단으로 오인하지 않는다. 안내 종료 후 이전 대화 감정으로 돌아갈지의 정책은 명시하고 테스트한다. 권장: neutral은 notice 동안만, 종료 뒤 이전 대화 감정 복원. 동일 영어 답변 Piper fallback은 기존 emotion 유지. notice와 일반 답변 metadata를 섞지 않는다.
- 자동 idle 감정 완화 기능이 필요하다면 이후 독립·명시 선택으로 논의한다. 이번에는 기본 경로에 새 자동 초기화 조건을 만들지 않는다.

### B. 원본 API와 타이밍

승인 원본을 이 대화의 도구 환경에서 다시 읽고 SHA 일치를 확인했다.

- `setActivity('listening'|'thinking'|'idle')`는 현재 감정 자체를 바꾸지 않는다. listening은 감정별 닫힌 입, thinking은 원본 흠 입과 감정별 눈/눈물을 사용한다.
- `setEmotion()`은 non-idle activity를 idle로 바꾼다. 감정이 안 바뀌면 다시 호출하지 말고 활동만 바꾼다. 감정+활동을 동시에 바꿀 필요가 있으면 감정 → 활동 순서다.
- `setActivity()`는 speech를 멈추므로 입 cue나 매 poll에 반복 호출하지 않는다. 발화 중 감정만 바꿀 때 기존 adapter 계약을 유지한다.
- speaking/stop/무음/중간 입 프레임/단일 Python 출력은 변경하지 않는다. `thinking`의 흠 입을 '소리 없이 입 움직임' 오류로 지우지 않는다.

### C. 검증 — 기대값은 수정하되 검증 범위를 줄이지 않기

먼저 현재 코드에서 재현되는 failing test를 추가하고 위 구현으로 통과시킨다. 과거 '새 질문 neutral/2초 neutral/cancel neutral' 검사는 새 계약으로 명시적으로 교체하고 변경 이유를 보고한다. 합계 숫자를 지키려고 불필요한 중복 검사를 추가하거나 실패 검사를 삭제해 통과로 만들지 않는다.

필수:

1. 모든 여섯 감정에서 정상 ended → 2초 이상 및 긴 모의 idle → 다음 listening → thinking → prepared까지 감정 동일.
2. 실제 승인 iframe에서 crying 상태의 위 흐름을 관찰해 `getState().selectedEmotion/emotion`, `activity`, 눈 SVG와 눈물 상태를 확인. 슬픔/행복/화남/놀람도 원본 규칙대로 유지.
3. 새 happy/neutral 응답은 prepared까지 이전 crying을 유지하고 started에만 교체.
4. 다음 답변이 지연/잘못된 JSON/refusal/오류/취소일 때 이전 표시 감정은 유지하고 새 발화·cue가 나오지 않음. 다시 시작 가능.
5. cancel before/after started, reset, close, disconnect/reconnect, busy 중 모드 변경, 오래된 SDK 응답·브라우저 응답이 감정을 덮지 않음.
6. `/repeat`의 WAV/cue/emotion 원자성 및 promotion 직전 취소 경쟁 회귀 유지. notice/동일 영어 Piper fallback 구분.
7. headless 검사에 기존 승인 듣기 시선·끄덕임, 생각 시선·흠 입, crying 눈물, surprised 눈·입 연동, 깜빡임이 살아 있는지 포함. 원본 해시 전후 동일.
8. 실제 API/장치 0 호출인 전체 회귀, 기존 수동 launcher E2E, 자동 감정 E2E를 실행. 테스트용 시간 진행은 제어된 clock으로 하고 실제 대기 sleep을 길게 늘리지 않는다.

## 5. 시선·웃음·리액션은 무엇이 이미 되고 무엇이 아닌가

현재 승인 원본의 동작:

- 자동 깜빡임.
- listening: `look(.95,-.80)`, `lookEyes(0,0)` 및 간헐적인 두 번 끄덕임.
- thinking: `look(-.82,-.7)`, `lookEyes(-.16,-.08)` 및 흠 입.
- crying: 눈물, surprised: 입 크기와 눈 크기 연동. 감정이 실제 전달돼야 사용된다.
- `look(x,y)` 및 `lookEyes(x,y)`는 각각 [-1,1] 범위의 두 축을 받는다. 얼굴 전체/눈만 이동을 제어할 수 있으며 원본에 보간이 있다.

현재 adapter는 상태/감정/입 제어를 연결했지만 **별도의 대화 문맥 기반 `look`/`lookEyes`/`blink` 연출은 호출하지 않는다**. 듣기·생각의 기본 시선은 원본 `setActivity` 내부에서 작동하므로 '시선 기능 전부 미연결'이라고 쓰면 부정확하다.

`happy` 웃는 얼굴과 실제 '하하' 웃음소리는 다르다. 현재 음성은 Aaron Soft 고정이며 전용 laugh/cry 연기는 추가하지 않았다. 카메라 시선 추적/화자 위치 추정도 구현돼 있지 않으므로 얼굴이 실제 사용자를 추적한다고 설명하지 않는다.

이번 수정에서는 승인 기본 동작을 검증·보존하고, **무작위 상하좌우 움직임·새 바운스·새 입 모양·무조건 미소를 삽입하지 않는다**. 이어질 별도 연출은 동일 감정을 유지한 짧고 제한된 시선 이동/눈 움직임을 상황별로 사용하고, speaking/listening/thinking의 원본 동작을 덮지 않도록 설계한다. 추가 웃음 음성는 Aaron 한 성우를 유지하는 별도 청취 검수 대상이다. 본 작업 완료를 시선·음성 연기 전체 완료로 표기하지 않는다.

## 6. 제출 및 사용자 인계

코드·새 검증·갱신한 `software/EMOTION_KR.md`와 `docs/reports/EMOTION_CONTINUITY_V2_LATEST_KR.md`를 같은 브랜치에 정상 push하고 기존 PR #13을 갱신한다. main/기존 통합 브랜치 병합 금지. 과거 EMOTION_STAGE1 보고서는 당시 기록을 보존한다.

보고서는 코드/자동 검사/사용자 실기 관찰/미실기를 분리한다. 실제 새 감정 연속성·음질·LCD 검수는 사용자 시험 전에는 NOT_RUN으로 남긴다. 시선·반응은 원본 유지와 신규 기능 미구현을 구분한다. 키/개인 대화/모델/얼굴 원본/음성 파일은 공개 커밋하지 않는다.

최종 응답: 결론, PR/보고서 링크, 기존 같은 경로에서의 정확한 수동 실행 명령과 다음 검사 하나만. 사용자에게 보고서 본문 복사·전달을 요청하지 않는다.
