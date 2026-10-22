**PASS — 자동 표정 v1의 brain/session/voice cache/CLI 연결, 전체 회귀와 승인 HTML headless 오프라인 검증을 완료했다. 실제 모델·음성·장치 검수는 NOT_RUN이다.**

2026-10-22 KST. 작업 경로 `/home/bgh/BMO_EMOTION`, 브랜치 `feature/bmo-auto-emotion-v1`.

## 기준과 구현 커밋

- 실기 대화 복귀 기준: `902b09739844ab4c78ea6234851b7bfb9cdb7634` (`feature/bmo-integration-v1`).
- 계약 seed/작업서 기준: `aaf8ec4985516475b2acecc73610c60ec4761a8a`.
- 이번 런타임 구현·테스트·실행 안내: [`2b0f03c4d1bbbece2b6202c12b5d548fe1f8da0a`](https://github.com/GUNOBAE/BGH_BMO/commit/2b0f03c4d1bbbece2b6202c12b5d548fe1f8da0a).
- 기존 [draft PR #13](https://github.com/GUNOBAE/BGH_BMO/pull/13)을 갱신했다. base는 `feature/bmo-integration-v1`이며 main/기존 통합 브랜치에 병합하지 않는다.
- 본 문서는 위 소스를 검증한 뒤 별도 문서 커밋으로 같은 브랜치에 제출한다. 문서 커밋 자체의 SHA를 본문에 순환 참조하지 않는다.

seed는 독립 계약 35개까지였고, 이번 변경에서 실제 통합 경로의 연결을 완료했다.
작업서에 기록된 기존 사용자 실기 대화 확인은 그대로 존중한다. 개인 로그를 열람하거나
그 확인을 자동 표정·LCD·립싱크·취소·Piper 전체 실기 합격으로 확대하지 않았다.

## 구현 결과

| 경계 | 동작 |
|---|---|
| brain | 기존 `ExpressionClient`를 검토·재사용해 자동 모드의 brain에만 주입한다. STT/TTS와 client 소유권은 원래 session에 둔다. 고정 prompt blob 및 english_voice를 유지한다. |
| 요청과 history | 기존 모델 요청 하나에 strict `text.format` speech/emotion schema를 추가한다. BMOBrain history와 음성에는 검증된 speech만 들어간다. 분류/복구용 두 번째 모델 요청과 자동 모델 전환을 추가하지 않았다. |
| 오류 | `ExpressionFormatError`만 고정 메시지의 비재시도 `expression_format`으로 분류하고 worker에서도 보존한다. 임의 ValueError·transport 분류는 유지한다. malformed/refusal/unsafe/extra field/incomplete는 발화하지 않는다. 감정값만 잘못되거나 누락되면 speech를 유지하고 neutral로 낮춘다. |
| 수용과 staging | wrapper.last_reply는 임시 후보다. `IntegrationApp.answer` 완료/취소 검사 후 현재 session·turn·발화에 묶인 경계에서 수용한다. generation과 모드를 함께 확인하며 모델 thread에서 얼굴을 직접 바꾸지 않는다. |
| 자동 표정 수명 | 새 질문은 neutral로 듣기/생각한다. prepared에서는 발화하지 않고 player started에서 표정과 기존 입 cue를 적용한다. ended는 입을 닫고 2초 유지 후 poll/monotonic으로 neutral로 복귀한다. 새 timer/thread는 추가하지 않았다. |
| 취소와 지연 | cancel/error/reset/close/다음 질문이 후보와 이전 hold를 무효화한다. 늦은 LLM/TTS 결과, 연결 단절 뒤 결과, 이전 발화/브라우저 응답은 새 표정을 덮지 않는다. |
| 반복 캐시 | 최종 gain 적용 WAV·cue·emotion·검증/안내 metadata를 성공 재생 뒤 같은 잠금 경계에서 교체한다. repeat는 그대로 재사용하며 GPT/합성/gain/cue 재생성은 없다. 실패/취소한 새 답변은 이전 성공 캐시를 보존한다. |
| 취소 경쟁 보강 | 캐시 교체 직전 취소 경쟁을 gate로 재현했다. cancel event와 pending generation 무효화를 cache lock 안에서 실행하고 promotion도 같은 lock 안에서 취소를 재검사한다. 이전 happy 캐시가 취소된 sad 대체에 오염되지 않는 회귀를 추가했다. |
| Piper | 같은 영어 답변 fallback은 emotion을 보존한다. 한국어 미지원/고정 장애 안내는 명시적 notice metadata로 neutral 처리한다. source=piper만으로 안내 여부를 추정하지 않는다. 실제 Piper는 실행하지 않았다. |
| 수동 복귀 | CLI `--emotion-mode auto|manual`, 기본 manual. `/emotion NAME`은 수동 고정, `/emotion manual`은 현재 표정으로 수동 복귀, `/emotion auto`는 neutral로 자동 복귀한다. busy 중 모드 변경은 오래된 후보를 무효화하고 다음 턴부터 brain 요청 형식을 전환한다. |
| reset/상태 | reset은 선택 모드를 유지하며 neutral/history/cache/후보/hold를 정리한다. effective-config와 공개 상태에 auto/manual을 표시한다. 내부 이벤트에는 허용된 표정/검증 상태만 추가하며 원문 응답·speech·키는 넣지 않는다. GET-only loopback 서버와 기존 adapter를 유지한다. |

Luna low, 기존 STT, 출력 상한 320, Fish Aaron Soft / beemo / PCM gain 0.25,
원본 Piper opt-in, 명시적 GPT-4.1 mini / reasoning None 복귀를 유지했다.
일반 CLI와 기본 plain 응답을 유지했다. Action v0의 5개 Mock 및 executed=False도
그대로 전체 회귀에 포함된다. 웃음/울음 태그나 새로운 voice preset은 넣지 않았다.

## 검증 결과

새 clone의 `.venv`에 `software/requirements-integration-dev.txt`만 설치했다.
Python 3.13.5 / ARM64, OpenAI SDK 2.54.0, httpx MockTransport, Playwright 1.64.0 및
기존 `/usr/bin/chromium`을 사용했다. OS/기존 venv/브라우저 패키지를 변경하지 않았다.
테스트 child에는 PATH/HOME/locale 등 고정 허용 환경만 전달하고 실제 인증은 제외했다.

| 검사 | 결과와 근거 |
|---|---|
| 전체 unittest | **398/398 PASS, skip 0**, 최종 소스 동결 후 22.562초. 기존 통합 323 + seed 계약 35 + 신규 연결/오류/캐시/CLI 40개다. 기존 358개 test method 삭제 0을 AST로 별도 확인했다. 기존 93개 회귀를 포함하는 누적 검사 전체를 보존한다. |
| 신규 검사 구성 | SDK/오류·정책 13, session 수명/지연/notice 15, cache 경쟁 4, 공개 얼굴 metadata 4, worker 오류 복구 1, launcher 3. |
| 실제 SDK 직렬화 | httpx.MockTransport에서 strict schema/Luna low/320/정책 보존, 정상 요청당 create 위임 1회와 clean history를 확인했다. 거부/incomplete/400 schema 거절 시 추가 분류·복구·모델 변경 없이 안전 종료한다. 일반 ValueError와 기존 transport 오류도 검사했다. |
| 자동 모드 순서 | happy→neutral, sad→happy, 1.999초 유지/2.0초 복귀, 이전 hold가 다음 턴을 지우지 않음, 수동 override→auto/reset, malformed/incomplete 뒤 새 턴 성공. |
| 늦은 결과/캐시 | 지연 LLM/TTS cancel·close·disconnect/reconnect, busy 중 모드 변경, promotion 직전 취소, reset/clear/close 후 late promotion 차단, 실패한 새 답변 뒤 이전 WAV/cue/emotion repeat를 확인했다. |
| 기존 승인 HTML 검사 | **8개 PASS**, 원본 bridge **1,452 fixtures** 및 30fps 중간 입 프레임 규칙 유지, browser media play 0. |
| 기존 수동 통합 E2E | **4개 PASS**, 실제 --simulate launcher/서버/승인 iframe의 문답·반복·취소·다음 턴·종료. brain 2, player 3, 회수 3, forbidden live IO 0. |
| 자동 표정 승인 HTML E2E | **13개 PASS = 기존 8 + 신규 5**로 중복을 구분한다. 실제 --simulate --emotion-mode auto launcher에서 6감정 원본 SVG, prepared neutral/off→started emotion+mouth, ended hold→neutral, 재연결 현재 위치, crying repeat/cancel/stale 거부, manual sad 유지→auto happy→repeat/reset을 확인했다. |
| 자동 E2E 호출 수 | 일반 문답 8 = auto 7 + manual 1. brain 8 / structured 7 / 합성 8 / gain 8 / cue 생성 8 / player 10 / 회수 10 / forbidden 0 / 브라우저 음성 0. 두 repeat에는 새 brain/합성/gain/cue 생성이 없었다. |
| GitHub CI | 구현 커밋 `2b0f03c4`의 push 및 PR 검사 모두 **PASS**, Python **3.11/3.13**의 unittest/pip check/compileall 성공. [push CI](https://github.com/GUNOBAE/BGH_BMO/actions/runs/39746727579), [PR CI](https://github.com/GUNOBAE/BGH_BMO/actions/runs/39746734417). 승인 원본 HTML은 CI에 업로드하지 않았으며 headless 결과는 Pi 로컬 검사다. |
| 개발 환경/정적 검사 | pip check PASS, compileall PASS, git diff --check PASS. 제출 경로·대형 자산·키 패턴 검사 PASS. |

headless checker의 150ms 상태 관측 지연과 worker 정리 여유는 합성 fixture다.
실제 API 지연·가청 지연 측정값이 아니다. 자동 검사에는 별도 임시 lock과 임시
loopback 포트를 사용해 기존 실행 앱과 충돌하지 않게 했고, 다른 프로세스를 종료하지 않았다.

재현 명령과 수동 실행 안내는 [software/EMOTION_KR.md](../../software/EMOTION_KR.md)에 있다.
실제 SDK 검사는 HTTP MockTransport이며 실제 paid backend가 아니다.
[공식 Structured Outputs 문서](https://developers.openai.com/api/docs/guides/structured-outputs)의
text.format/strict schema 및 refusal/incomplete 예외를 확인했다. 현재 사용자 계정의
Luna schema 지원은 문서 열람이나 MockTransport 성공만으로 검증되지 않는다.
SDK의 기존 bounded transport retry와 추가 의미 판단 호출을 구분한다.

## 보존 확인

- `/home/bgh/BMO_INTEGRATION`: 추적 파일 **128개 SHA-256 일치**, 변경 0, HEAD `902b09739844ab4c78ea6234851b7bfb9cdb7634` 유지, worktree clean.
- 승인 `/home/bgh/Downloads/BMO_Screen_v13_2.html`: 전후 SHA-256 **`38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`** 일치. 원본 HTML/SVG/입 프레임을 변경하거나 업로드하지 않았다.
- `expression_contract.py`와 seed 계약 35개, 고정 음성 정책 원본은 변경하지 않았다.
- 원본 Piper 모델 SHA-256 `2b323dfdd76efb36b096b93ef8a6f0a60a26886aaaeeba97e691c13641611fb8`, 모델 JSON `0786c36ebf1ffc2b6979391941d64bd2ed0279bd0114654d980ad957ec914776`, 실행 파일 `0c44a6360a21367b5d03b8656c3e274f4de715f6533245d8c1f17c242631912b`를 기존 기록과 대조해 일치 확인했다. 읽기만 했고 실행하지 않았다.
- BGH_BMO/BMO_LUNA_AB/BGH_BMO_ACTIONS의 기존 HEAD와 tracked clean 상태를 확인했고 수정하지 않았다. 개인 미추적 로그는 읽거나 업로드하지 않았다.
- 팬/SSH/systemd/OS/펌웨어/직렬/GPIO/IMU/서보/카메라/물리 버튼/게임/다른 프로젝트/기존 백업은 변경하거나 조작하지 않았다. 개발 환경·Git 설정 변경은 새 clone 내부로 한정했다.

## 실제 검수 NOT_RUN과 복귀

실제 마이크·스피커·Fish/GPT/STT 유료 호출·Piper 모델·Pi LCD·서보/센서 검수는
**NOT_RUN**이다. 실제 Luna schema 지원과 감정 의미 선택 품질, 음성의 웃음/울음 연기,
가청 립싱크, latency/cost는 미확인이다. 2초 hold도 초기 조정값이다.
정상 요청 1회 위임은 토큰·지연·비용 증가 0을 보장하지 않는다.

사용자가 live 검수할 때만, 기존 앱을 정상 종료하고 다음을 실행한다.
새 terminal에 키가 없다면 인증을 다시 입력해야 한다. 키는 파일·명령행·Git에 저장하지 않는다.

```bash
cd /home/bgh/BMO_EMOTION/software
../.venv/bin/python -B -m bmo_ai.integration --live --emotion-mode auto \
  --face-file /home/bgh/Downloads/BMO_Screen_v13_2.html \
  --mic-device plughw:CARD=Device,DEV=0 \
  --speaker-device plughw:CARD=MAX98357A,DEV=0
```

Enter로 질문, `/cancel`로 취소, `/repeat`로 마지막 성공 재생,
`/emotion happy`로 수동 고정, `/emotion auto`로 자동 복귀, `/reset`으로
선택 모드를 유지한 초기화, `/quit` 또는 Ctrl-C로 이 앱을 정상 종료한다.
plain 응답으로 돌아가려면 `/emotion manual` 또는 재실행 시 `--emotion-mode manual`을 쓴다.
필요한 경우에만 `--offline-voice`로 원본 Piper opt-in을 선택하며,
`--baseline`은 GPT-4.1 mini / reasoning None 비교를 명시적으로 선택한다.

현재 앱 자체를 이전 실기 기준으로 되돌리려면 새 앱을 정상 종료한 뒤
기존 `/home/bgh/BMO_INTEGRATION/software`에서 기존 manual live 명령을 사용한다.
기존 checkout을 덮어쓰거나 서비스/lock/프로세스를 강제 제거하지 않는다.
