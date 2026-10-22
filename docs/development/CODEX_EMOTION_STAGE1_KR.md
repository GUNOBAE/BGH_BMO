# Codex 실행 작업서 — BMO 자동 표정 v1

2026-10-22 KST. **계약 코드 준비 완료, Pi 런타임 연결은 이 작업에서 구현한다.**

## 결론과 사용자 승인

사용자는 기존 통합 앱으로 실제 답변이 잘 나온다고 확인했고, 자동 감정 표현의 다음 구현을 요청했다. 조사만 다시 하지 말고 아래 연결·오프라인 검증·커밋·push·기존 draft PR 갱신·보고까지 수행한다. 이번 작업의 목표는 **답변의 의미 → 승인 얼굴의 자동 표정**이다. 음성이 실제로 웃거나 우는 연기, 액션 실행, 새 성우 선택을 완료했다고 하지 않는다.

기존 실기 근거: 사용자 제공 로그에 listening→thinking→speaking/started→idle/ended가 세 번 있고, 사용자가 답변을 들었다고 확인했다. 화면의 실제 표시·립싱크·cancel/repeat/Piper 실기는 아직 사용자 합격을 받지 않았다. 자동 표정이나 통합 전체 합격으로 확대하지 않는다.

## 1. 출발점과 보존

- 저장소 `GUNOBAE/BGH_BMO`, 작업 브랜치 **`feature/bmo-auto-emotion-v1`**. 이 브랜치의 최신 HEAD에 준비한 코드와 작업서가 있다.
- 부모 기준 `feature/bmo-integration-v1`의 **`902b09739844ab4c78ea6234851b7bfb9cdb7634`**. 이 기준은 실기 대화의 복귀 경로다.
- 새 독립 clone **`/home/bgh/BMO_EMOTION`**에서 개발한다. 기존 `/home/bgh/BMO_INTEGRATION`, BGH_BMO, BMO_LUNA_AB, BGH_BMO_ACTIONS는 변경하지 않는다. 같은 목적의 새 clone이 이미 있으면 HEAD/status를 확인하고 이어간다. 덮어쓰기/reset --hard/clean/force-push/전체 브랜치 병합 금지.
- 적용 AGENTS.md를 읽는다. 전체 preflight를 반복하지 말고 경로·HEAD·변경·기존 앱 실행 여부의 변동만 확인한다. 권한/관리형 정책을 우회하는 변경은 하지 않는다.
- 승인 얼굴 `/home/bgh/Downloads/BMO_Screen_v13_2.html`, SHA-256 `38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`를 그대로 참조한다. HTML/SVG/입 프레임/비율/색/표정 재디자인 금지, 공개 업로드 금지.
- **Luna low, STT, Fish Aaron Soft, beemo, gain 0.25, 출력 상한 320, 원본 Piper 및 기존 opt-in/4.1 mini 복귀**를 유지한다. 프롬프트 원본 blob 검증과 english_voice 정책도 유지한다.
- 팬·SSH·서비스·OS·펌웨어·직렬·팔·카메라·게임·물리 버튼 변경 없음. 마이크/스피커/클라우드 API/Piper 모델 실행은 이번 자동 작업에서 하지 않는다. 실제 키·개인 로그·음성·원본은 출력/커밋하지 않는다.

먼저 이 파일, `docs/reports/EXPRESSION_SEED_20261022_KR.md`, `software/INTEGRATION_KR.md`, 기존 integration_profile/session/voice/launcher, core/cloud, 승인 HTML 연결 계약을 읽는다. 음성 정책 `docs/voice-policy-v1/README_KR.md`의 §5는 **Aaron Soft 고정 + 문장·얼굴 감정 일치가 먼저**라는 기준이다.

## 2. 이미 만든 코드를 런타임에 연결

`software/bmo_ai/expression_contract.py`와 대응 테스트는 실제 구현이다. 이 모듈을 검토·재사용하고 통째로 다시 만들지 않는다. 독립 계약 테스트 35개를 ChatGPT 도구 환경의 Python 3.13.5에서 통과시켰다. Pi 전체 회귀/실제 모델/얼굴 검증 완료를 뜻하지 않는다.

### A. 한 번의 LLM 응답에서 speech + emotion

자동 모드에서 **brain 전용** `ExpressionClient(real_client)`를 사용해 기존 `BMOBrain`에 주입한다. STT/TTS와 실제 client 소유권은 기존 session에 둔다. wrapper는 responses.create만 제공하며 원본 client를 닫지 않는다. policy_brain에 선택적인 연결점을 추가하되 일반 CLI와 기존 기본 모드는 보존한다.

- Responses API `text.format`의 strict JSON schema로 speech/emotion 두 값만 받는다. 감정은 neutral/happy/sad/surprised/angry/crying이다. schema 지원은 현재 Luna 계정에서 실제 검증 전이다.
- wrapper가 한 번의 create 호출을 위임하고, 검증된 **발화문만** 기존 BMOBrain으로 전달한다. history와 Fish/Piper에 JSON/감정값/자유 TTS 태그가 들어가지 않아야 한다.
- `last_reply`는 **임시 후보**다. `IntegrationApp.answer()`의 기존 취소/완료 검사가 성공한 뒤 현재 session/turn/utterance에 묶어 수용한다. 모델 응답 thread에서 곧바로 전역 얼굴을 바꾸지 않는다.
- 기존 cloud.call_cloud가 임의 ValueError를 generic cloud로 바꾸는 점을 처리한다. `ExpressionFormatError`만 명시적으로 `CloudError(code='expression_format', safe_message=고정 문구, retryable=False)`로 분류하거나 동등하게 안전한 오류 경계를 만든다. 잘못된 JSON을 오프라인 네트워크 문제로 취급하지 않는다. 실제 클라우드 오류 분류는 그대로 유지한다.
- malformed/partial/refusal/추가 필드/안전하지 않은 speech는 말하지 않고 정상 오류·idle 경로로 복구한다. 감정값만 누락/잘못된 경우 깨끗한 speech는 그대로, 표정은 neutral로 한다. 원문을 로그/예외에 출력하지 않는다.
- 감정 분류·JSON 복구용 두 번째 GPT 호출, 자동 모델 전환, 자동 재합성은 금지한다. 기존 SDK의 bounded transport retry와 추가 의미 판단 호출을 구분한다.
- API가 schema를 거절하면 결과를 숨기거나 즉시 다른 모델로 재시도하지 않는다. 이 턴을 안전하게 종료하고 `--emotion-mode manual`로 기존 plain 응답을 사용할 수 있게 한다.
- 추가 출력 형식 때문에 토큰/지연이 늘어날 수 있다. '호출 횟수 증가 없음'은 '지연·비용 증가 0'을 뜻하지 않는다. 실제 측정 전 성능 숫자를 쓰지 않는다.

### B. 자동 표정의 수명과 입 동작

새 CLI `--emotion-mode auto|manual`을 추가한다. **기본 manual**로 기존 실행을 보존하고 새 검수 명령에는 auto를 명시한다. 런타임 `/emotion auto`는 자동 복귀, 기존 `/emotion happy` 등은 수동 고정 override로 유지한다. `/reset`은 선택한 모드를 유지하되 이전 후보·표정·history·캐시를 정리하고 neutral로 돌아간다.

자동 모드의 첫 구현은 답변 한 번에 표정 하나다. 문장별 타이밍을 추측해 표정을 여러 번 바꾸지 않는다.

1. 새 질문: neutral에서 듣기·생각. 사용자 말만으로 성급하게 웃거나 울지 않는다.
2. 검증된 답변: emotion은 해당 turn에 staging만 한다. 합성 완료만으로 speaking을 켜지 않는다.
3. **player started**: 해당 발화의 표정을 적용하고 기존 입 시간표를 함께 시작한다. 감정 변경 때문에 setActivity가 speech를 끊지 않도록 기존 adapter 계약을 지킨다.
4. 정상 ended: 입을 닫고 표정은 **2초** 유지한 뒤 poll/monotonic 기반으로 neutral로 돌아간다. 이 값은 최초 조정값이지 사용자 시각 승인값이 아니다. 별도 무한 thread/timer를 만들지 않는다.
5. 다음 질문/reset/cancel/error/close가 오면 이전 후보·유지 타이머를 무효화한다. 오래된 turn·이전 브라우저 응답이 새 표정을 덮지 못하게 한다. 수동 override는 auto 복귀나 reset 전에는 유지한다.
6. `/repeat`: 마지막 성공 WAV에 대응하는 emotion도 **같은 캐시 원자적 교체 경계**에서 보존·재사용한다. 실패/취소한 새 답변이 이전 성공 WAV의 감정을 바꾸면 안 된다. 음성 재합성·새 감정 판정·gain 재적용 없음.
7. 동일 영어 답변의 Piper fallback이면 같은 emotion, 고정 장애/한국어 미지원 안내로 바뀌면 neutral을 사용한다. source가 piper라는 사실만으로 정상 답변과 안내를 혼동하지 않는다.

상태 스냅샷/비밀 아닌 로그에는 `emotion`, `emotion_mode`, 검증 결과 정도만 넣고 raw response·speech·키는 넣지 않는다. `effective-config`에 모드를 표시한다. 기존 GET-only loopback 서버를 유지한다.

### C. 표현 품질과 범위

정확한 답변·짧은 문장·영어 기본/명시 한국어 요청을 유지한다. 축하에는 happy, 위로에는 sad/neutral, 정보 질문에는 대체로 neutral, 진짜 놀람에는 surprised를 선택하도록 한다. angry/crying은 명시적 가벼운 연기 요청에 한정하도록 계약 지시가 들어 있다. **의미 분류 품질은 프롬프트 지침이지 자동 검사로 증명된 사실이 아니다.**

실제 laugh/cry/scream 태그나 excited/playful preset을 새로 Fish에 주입하지 않는다. 기존 Soft 지시만 사용한다. 표정이 웃는 것과 웃음소리 합성은 다르다. 필요 시 후속 청취 비교 작업으로 분리한다. Action v0의 5개 Mock과 모든 executed=False는 그대로다.

## 3. 검증 — 코드 연결까지 완료하고 기록

모의 client는 auto 요청의 schema에 맞는 JSON fixture를 반환하게 하되 manual은 기존 plain fixture를 유지한다. 동일 integration launcher/session/worker/voice 경로로 검사한다. mock 전용 별도 영구 앱을 만들지 않는다.

필수 검사:
- 기존 전체 회귀 + 계약 35개 + 새 연결 검사. 실제 SDK를 httpx.MockTransport로 검증해 Responses body의 text.format 구조와 요청 1회, 깨끗한 history를 확인한다.
- 서로 다른 두 턴 happy→neutral, sad→happy, 수동 override→auto, reset, malformed 및 incomplete 뒤 새 턴 성공.
- delayed LLM/TTS 뒤 cancel, 브라우저 reconnect, 종료: 늦은 emotion 미적용. 오래된 유지 타이머가 다음 발화를 초기화하지 않음.
- player 이전 외부 말하기 없음, player 시작 때 표정과 입 함께 시작, 정상 종료 2초 후 neutral, 취소 즉시 neutral(수동 override는 보존).
- `/repeat`가 원래 성공 WAV/입/표정을 재사용. 그 뒤 실패한 다른 감정의 답변이 캐시를 오염시키지 않음. fixed offline notice는 neutral.
- 실제 승인 HTML의 headless 검사: happy/sad/surprised/angry/crying/neutral 모두 연결하되 원본 전후 SHA 동일. 기존 감정/활동/입 제어 순서·중간 프레임 보존.
- JSON·잘못된 태그가 Fish/Piper로 넘어가지 않음. 모든 모의 검사에서 실제 backend/API 호출 0, 정상 자동 턴의 delegated create 1회.

테스트는 새 clone의 venv에서, 실제 키를 제외한 child 환경으로 수행한다. 기존 BMO가 켜져 있으면 강제 종료하지 않는다. 충돌 없는 임시 loopback 포트/자체 test lock으로 검사하거나 그 검사만 BLOCKED로 기록한다. 개발 패키지는 새 venv 범위만 설치한다.

## 4. GitHub 제출과 인계

코드·테스트·실행 안내·**`docs/reports/EMOTION_STAGE1_LATEST_KR.md`**를 작업 브랜치에 커밋·정상 push한다. 같은 head의 draft PR을 갱신한다. PR base는 `feature/bmo-integration-v1`. main이나 기존 통합 브랜치에 자동 merge하지 않는다.

보고서는 첫 문장에 PASS/PARTIAL/BLOCKED와 범위를 쓴다. 계약만 있던 seed와 이번 런타임 연결 완료를 구분하고 실제 커밋·검사 결과·실기 NOT_RUN·수동 실행/종료/복귀 명령을 기록한다. 키가 없는 새 terminal이면 재입력해야 한다는 점은 짧게 명시하고 키를 파일·명령행·Git에 저장하지 않는다.

완료 후 remote HEAD·보고서·PR을 직접 확인한다. 사용자에게는 결론과 링크만 전한다. 보고서 본문을 ChatGPT에 복붙하도록 요구하지 않는다. 새 기능이 live에서 검증되었다고 주장하지 않는다.

## 외부 API 계약 확인

- OpenAI Responses create: https://developers.openai.com/api/reference/resources/responses/methods/create
- Structured Outputs: https://developers.openai.com/api/docs/guides/structured-outputs

공식 문서에서 text.format과 strict schema 및 incomplete/refusal 예외를 확인했다. 현재 사용자 Luna 계정의 실제 schema 지원은 이 문서 열람만으로 검증되지 않는다. 모델·엔진을 바꾸지 않는다.
