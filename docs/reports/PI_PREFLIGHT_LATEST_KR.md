# BMO 통합 전 Pi 현황 점검 보고서

기준일: **2026-10-22 KST**. 관찰 시각: **02:48–02:58:28 KST**. 아래 내용은 이번 읽기 전용 관찰과 명시한 커밋의 소스 대조 결과다. 업로드 직전 마지막 Git 재조회에서도 아래 HEAD·변경 상태가 동일했다.

## 1. 결론

**기존 음성·Mock Action 코드는 보존되어 있으나, 승인 얼굴을 포함한 단일 BMO 통합이 완료된 상태로 판단할 수 없다.** 세 Git 체크아웃의 HEAD는 각각 원격 브랜치와 일치한다. Luna 폴더에는 미추적 로그 2개가 있으며 내용을 열지 않았다.

**첫 통합의 선행 조건은 승인 얼굴 원본 확보·해시 확인, 선택 음성 프로필 명시, 실제 재생 중 취소와 얼굴 이벤트 연결이다.** 승인 `BMO_Screen_v13_2.html`은 한정 탐색 범위에서 찾지 못했다. Piper 원본 모델·설정은 존재한다. Action은 5종 Mock이며 모든 결과의 `executed=False`를 유지한다.

**이번 작업에서 앱·테스트·모델·추론/음성 API·녹음·재생·장치·펌웨어를 실행하지 않았다.** 기존 소스·설정·Git 인덱스/브랜치·로그·백업·서비스를 변경하지 않았다. 해결책은 아래 제안에 그치며 Stage 1 구현은 수행하지 않았다. 과거 실음성 성공·CI·백업 검증 기록은 이번 실기 검증으로 계산하지 않는다.

## 2. 작업 경로와 Git 상태

### 임시 작업 공간과 실제 프로젝트

이번 임시 경로는 `/tmp/bmo-preflight.3PqHT9`이며 보고서·점검 메모·업로드 JSON을 위한 공간이다. Git 저장소가 아니며 초기 이 경로에서 나온 Git 오류/파일 검색 결과는 프로젝트 이상으로 판단하지 않았다. 실제 프로젝트의 Git 상태·브랜치·HEAD 조회는 `GIT_OPTIONAL_LOCKS=0`과 `git -C <실제 프로젝트 경로>`를 사용했다. Action blob 계산 1회는 같은 환경변수와 실제 프로젝트를 명시한 작업 디렉터리에서 실행했다. 기존 저장소에서 fetch/pull/checkout/add/commit/push 등 변경 명령은 실행하지 않았다.

| 실제 경로 | 존재 / `rev-parse --show-toplevel` | 현재 branch | 전체 HEAD | 변경 상태 |
|---|---|---|---|---|
| `/home/bgh/BGH_BMO` | 있음 / 같은 절대 경로 | `feature/bmo-local-piper-fallback` | `7bed6d02c4ac239671b56d601e7fd2845b5fe0c0` | clean |
| `/home/bgh/BGH_BMO_ACTIONS` | 있음 / 같은 절대 경로 | `feature/bmo-actions-mock-v0` | `b346cc3017ca0ee4cb2162384d1fa1499659739a` | clean |
| `/home/bgh/BMO_LUNA_AB` | 있음 / 같은 절대 경로 | `feature/bmo-luna-low-voice-ab` | `054ced8f894f25f4cd0a178c2b097b5afe2c8576` | tracked 변경 없음. `logs/voice_41.jsonl`, `logs/voice_luna.jsonl` 미추적 |
| `/home/bgh/bmo-voice-test` | 있음 / Git 저장소 아님 | 해당 없음 | 해당 없음 | Git status 적용 불가. 모델·설정 파일만 읽기 전용 조사 |

세 저장소의 인증정보 없는 remote는 모두 `origin https://github.com/GUNOBAE/BGH_BMO.git`이며 fetch/push URL도 동일하다. `BGH_BMO_ACTIONS`는 같은 GitHub 저장소의 별도 로컬 작업 폴더다. Git 권한 오류는 없었고 전역 Git 설정도 바꾸지 않았다.

`git status --short --branch --untracked-files=all`의 실제 결과:

```text
/home/bgh/BGH_BMO
## feature/bmo-local-piper-fallback

/home/bgh/BGH_BMO_ACTIONS
## feature/bmo-actions-mock-v0

/home/bgh/BMO_LUNA_AB
## feature/bmo-luna-low-voice-ab...origin/feature/bmo-luna-low-voice-ab
?? logs/voice_41.jsonl
?? logs/voice_luna.jsonl
```

`bmo-voice-test`의 Git 조회는 `not a git repository`를 반환했다. 이 경로를 새 저장소로 만들지 않았다. 미추적 로그는 경로만 기록하며 개인 대화·음성 로그 본문을 읽거나 업로드하지 않았다.

### 적용 작업 지침

| 범위 | 확인한 지침 |
|---|---|
| 공통 상위 | `/AGENTS.md`, `/home/AGENTS.md`, `/home/bgh/AGENTS.md` 없음 |
| 임시 공간 상위 | `/tmp/AGENTS.md`, `/tmp/bmo-preflight.3PqHT9/AGENTS.md` 없음 |
| BGH_BMO | `/home/bgh/BGH_BMO/AGENTS.md` 있음. 원본·미추적 파일·Piper·백업 보존, 장치/서비스 조작 금지, 팬 독립 유지, 팔 차단 유지, IMU 제어 금지, 보관된 테스트 결과와 이번 결과 구분 |
| 나머지 세 폴더 | 루트 AGENTS 없음. 조사한 프로젝트 하위 경로에서 추가 AGENTS 없음. `.git`, 가상환경, 패키지/캐시 내부는 지침 탐색에서 제외 |

사용자가 지정한 최신 preflight 작업서가 이번 범위이며, 광범위한 권한 허용도 구현·장치 실행 범위 확장으로 해석하지 않았다. 기존 `AGENTS.md` 자체도 수정하지 않았다.

### 실제 실행 상태·화면·오디오

- 호스트의 프로세스 이름과 제한된 프로젝트 식별 정보만 조회했다. `bmo-case-fan.sh`(PID 851), `labwc`(1532), Chromium(부모 102876), PipeWire(1525/1529), WirePlumber(1537), wayvnc 프로세스가 관찰됐다. 이 프로세스들을 시작·종료하지 않았다. 팬 프로세스 존재는 팬 성능 측정이 아니다.
- 허용 네 프로젝트의 경로/알려진 BMO Python 진입점으로 식별되는 대화·재생 프로세스는 관찰되지 않았다. 다른 Python 프로세스는 해당 앱으로 식별되지 않았다. 이것만으로 모든 사용자 앱의 부재나 기존 음성 설정 미사용을 단정하지 않는다. 전체 프로세스 인자는 출력·보관·업로드하지 않았다.
- 최초 sandbox의 프로세스 가시성과 `loginctl` 접근은 제한됐으므로 그것을 호스트 전체 상태로 사용하지 않았다. 호스트 읽기 전용 재조회에서 사용자 `bgh`의 `wayland`/`rpd-labwc` active 세션, active 사용자 manager, online tty 세션이 확인됐다. 실행 환경의 선별 항목은 `DISPLAY=:0`, `WAYLAND_DISPLAY=wayland-0`, `XDG_SESSION_TYPE=wayland`, `XDG_CURRENT_DESKTOP=labwc:wlroots`다. 실제 화면 해상도·표시 콘텐츠·얼굴 페이지 로드는 미확인이다.
- `/home/bgh/.asoundrc`, `/etc/asound.conf`는 없다. `/boot/firmware/config.txt:11`에 전역 `dtparam=audio=on`, `:57-58`의 `[speaker]` 구간에 `dtparam=audio=off`, `dtoverlay=max98357a`가 존재한다. 정적 파일 값이며 해당 구간의 현재 적용 여부·현재 출력 라우팅·음량은 검증하지 않았다.
- 음성 A/B 문서에는 입력 `plughw:CARD=Device,DEV=0`, 출력 `plughw:CARD=MAX98357A,DEV=0`가 명시된다([Luna 문서:87-88,117-118](https://github.com/GUNOBAE/BGH_BMO/blob/054ced8f894f25f4cd0a178c2b097b5afe2c8576/docs/luna-voice-ab.md#L87)). 이것은 실행 예시이지 현재 프로세스의 유효 설정·장치 사용 성공 증거가 아니다. 오디오 장치나 스트림은 열지 않았다.

### 원격 기준 스냅샷

아래 HEAD는 GitHub API에서 읽었다. 로컬 조회와 원격 조회는 구분한다. 처음 읽은 작업서 HEAD는 `1e8d4c5b486535c96d8b466fb07f713d8d58cb2b`였으며 사용자 안내 후 **최신 `3c889bda...`의 작업서를 다시 읽어 적용**했다. 최신판에는 `/tmp`와 실제 프로젝트의 구분 및 `git -C` 지침이 추가됐다.

| 원격 branch | 이번에 읽은 전체 HEAD | 대조 내용 |
|---|---|---|
| `docs/bmo-integration-baseline-20261022` | `3c889bda472866adca4a4de0d16ec7dcfad535de` | [최신 작업서](https://github.com/GUNOBAE/BGH_BMO/blob/3c889bda472866adca4a4de0d16ec7dcfad535de/docs/development/CODEX_PI_PREFLIGHT_KR.md), [통합 기준](https://github.com/GUNOBAE/BGH_BMO/blob/3c889bda472866adca4a4de0d16ec7dcfad535de/docs/development/BMO_INTEGRATION_2026-10-22_KR.md) |
| `docs/bmo-voice-policy-v1-20261022` | `707c66b8876130be75ad216fa10aaabf2bdcdb56` | [README](https://github.com/GUNOBAE/BGH_BMO/blob/707c66b8876130be75ad216fa10aaabf2bdcdb56/docs/voice-policy-v1/README_KR.md), [인수인계](https://github.com/GUNOBAE/BGH_BMO/blob/707c66b8876130be75ad216fa10aaabf2bdcdb56/docs/voice-policy-v1/INTEGRATION_HANDOFF_KR.md) |
| `feature/bmo-actions-mock-v0` | `b346cc3017ca0ee4cb2162384d1fa1499659739a` | 실제 `actions.py`. 로컬 HEAD와 일치 |
| `feature/bmo-local-piper-fallback` | `7bed6d02c4ac239671b56d601e7fd2845b5fe0c0` | 로컬 BGH_BMO HEAD와 일치 |
| `feature/bmo-luna-low-voice-ab` | `054ced8f894f25f4cd0a178c2b097b5afe2c8576` | 로컬 Luna HEAD와 일치. `core.py`, `app.py`, `config.py` 원격 파일도 SHA 기준 조회 |
| `reports/bmo-pi-preflight-20261022` | `161a2ebd99b21ddc31e4cbf6b1cbd4452967e264`（업로드 전 조회） | 미리 준비된 보고서 브랜치 존재. 지정 보고서 파일 GET은 404로 기존 본문 없음 |

실제 작업 트리의 읽기 전용 Git blob 계산도 원격과 일치했다:

| 로컬 실제 파일 | 원격에서 읽은 blob SHA |
|---|---|
| `BGH_BMO_ACTIONS/software/bmo_ai/actions.py` | `b7512b9976707d7b4b7be8ce7c21b7ed29cb4a2c` |
| `BMO_LUNA_AB/software/bmo_ai/core.py` | `176526f8d637557438a16a68edf88c9206fade25` |
| `BMO_LUNA_AB/software/bmo_ai/app.py` | `6e3d85b162cad35f163261802007384dc50db67c` |
| `BMO_LUNA_AB/software/bmo_ai/config.py` | `9462c319bd57470975b4f55f41e8e35bbe86cb7e` |

최신 작업서 blob은 `b3359ed8bb85fb050108433278a4ed12d0c6dc9f`다. 통합 기준 blob `c30973f9f6ab8d205e876b6eb4734d3866f46a6b`은 최초와 최신 HEAD 사이에서 동일했다. `feature/bmo-action-lab`를 Action 기준으로 대체하지 않았다.

## 3. 보존 설정과 승인 파일

### 사용자 선택 / 코드 기본 / 현재 적용값

아래 코드는 로컬 BGH_BMO=`7bed6d02...`, Luna=`054ced8f...` 기준이다. 실제 실행값을 확인하지 못한 칸은 문서 예시나 기본값으로 채우지 않았다.

| 항목 | 유지할 사용자 선택 | 소스 기본값·확인 내용 | 현재 실행 적용 |
|---|---|---|---|
| 두뇌 | `gpt-6-luna`, effort `low` | 두 루트 모두 `gpt-4.1-mini`. Luna에만 reasoning 선택 필드/CLI 존재 | 미확인 |
| STT | `gpt-4o-mini-transcribe` | 동일 | 미확인 |
| TTS | Fish Aaron | provider 기본 `openai`; Fish는 명시 선택 필요 | 미확인 |
| Fish engine | 시험 기준 `s2.1-pro-free` | 동일 문자열. 계정 가용성·과금 조건 재검증 안 함 | 미확인 |
| 목소리·연기·발음 | Aaron `106f41ea392c4cc48c6eee76f69ff17e` / `soft` / `beemo` | Fish 설정/프리셋은 동일 | 미확인 |
| PCM gain | **0.25** | 앱 config 기본 **0.30**. 시스템 음량 백분율과 별개 | 미확인 |
| Piper opt-in | 원본 영어 TTS 예비 보존 | `offline_voice=False`; 명시 옵션 필요. A/B 문서에는 `--offline-voice` 있음 | 현재 활성 여부 미확인 |
| 언어 | 새 정책은 기본 영어, 명시 한국어 요청에는 한국어 | 기존 `en`은 한국어 입력에도 한국어 응답. 새 정책 미적용 | 미확인 |
| 응답 토큰 | 시험값과 제품 설정 구분 | 기본 180, A/B 예시 320 | 미확인 |

근거: Luna [config.py:20-43,52-56](https://github.com/GUNOBAE/BGH_BMO/blob/054ced8f894f25f4cd0a178c2b097b5afe2c8576/software/bmo_ai/config.py#L20), BGH_BMO `software/bmo_ai/config.py:20-42`, Luna [CLI:20-25,68-74](https://github.com/GUNOBAE/BGH_BMO/blob/054ced8f894f25f4cd0a178c2b097b5afe2c8576/software/bmo_ai/__main__.py#L20), [사용자 음성 기준](https://github.com/GUNOBAE/BGH_BMO/blob/707c66b8876130be75ad216fa10aaabf2bdcdb56/docs/voice-policy-v1/README_KR.md#L84).

BGH_BMO와 Luna의 루트 및 `software/`에 표준 `.env` 파일은 없었다. 다른 셸·서비스 환경까지 설정이 없다는 뜻은 아니다. CLI는 `BMO_` 환경과 명시 옵션을 합치고 config는 dotenv를 로드하지 않는다(Luna `__main__.py:68-74`, `config.py:123-169`). `.env` 원문이나 인증 설정을 출력하지 않았다.

Fish Soft 지시는 `[gentle, warm, slightly lower pitch, still lightly playful]`이며 단어 BMO의 합성 발음만 Beemo로 정리한다(`fish_tts.py:21-24,191-193`, `pronunciation.py:8,12-24`). 앱은 Fish/Piper 모두 config gain을 전달한다(Luna `app.py:54-61,112-120`). 따라서 Piper 직접 함수 기본 gain 0.25를 앱의 현재 gain이라고 해석하면 안 된다.

GPT-4.1 mini 비교/복귀 경로는 남아 있다. Luna CLI에서 모델을 명시할 수 있으며 **4.1 mini로 복귀할 때 reasoning도 unset/None이어야 한다.** low가 남으면 validation이 거부한다(Luna `config.py:52-56`, `core.py:71-73,114-123`; `docs/luna-voice-ab.md:68-89`). 이번에 복귀 명령이나 API를 실행하지 않았다.

### 원본 Piper와 백업 상태

| 원본 경로 | 실제 파일 확인 |
|---|---|
| `/home/bgh/bmo-voice-test/bmo-piper/en_US-bmo_voice.onnx` | 일반 파일, 63,516,050 bytes. SHA-256 `2b323dfdd76efb36b096b93ef8a6f0a60a26886aaaeeba97e691c13641611fb8` |
| `/home/bgh/bmo-voice-test/bmo-piper/en_US-bmo_voice.onnx.json` | 일반 파일, 7,098 bytes. SHA-256 `0786c36ebf1ffc2b6979391941d64bd2ed0279bd0114654d980ad957ec914776` |
| `/home/bgh/bmo-voice-test/piper/piper` | 실행 권한이 있는 일반 파일, 5,283,040 bytes. 존재만 확인했으며 실행하지 않음 |

모델 JSON의 비밀 아닌 기본 추론 항목은 sample rate `22050`, language `en-US`, noise scale `0.667`, length scale `1`, noise w `0.8`, speaker 수 `1`이다(같은 JSON `:4,11,14-16,494`). 세 프로젝트의 기본 모델 경로가 위 원본을 가리킨다(BGH_BMO 및 Actions `config.py:31-32`, Luna `config.py:32-33`, 공통 `local_piper.py:19-20`). 소스는 추가 피치 보정 없이 binary에 모델·출력 경로를 전달한다(`local_piper.py:87-96`). 이번 모델 실행·튜닝·파일 변경은 없다.

별도의 현재 Piper 모델 백업 사본은 네 프로젝트 안에서 확인하지 못했다. `/home/bgh/BGH_BMO/PROJECT_STATUS.md:66-90`에는 10월 15일 stage1 백업·해시 검증의 과거 기록과 symlink를 역참조하지 않았다는 한계가 있다. `/home/bgh/BMO_backups` 내부 재검증은 이번 한정 탐색에서 하지 않았다. **현재 원본 파일 존재는 확인, 별도 백업의 현재 존재·동일성은 미확인**이다. 위 SHA를 과거 승인 해시와 동일하다고 주장하지 않는다.

### 승인 얼굴과 게임 원본

승인 기준은 `BMO_Screen_v13_2.html`, SHA-256 **`38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`**다. 네 프로젝트 및 `/home/bgh/Downloads`, `/home/bgh/Desktop`, `/home/bgh/Documents`에서 정확한 파일명을 탐색했으나 찾지 못했다. 따라서 **실제 로컬 경로·해시 일치·원본 JS API는 미확인**이다. 이는 Pi 전체에 없다는 뜻이 아니며 이전 얼굴 시안을 대체품으로 선택하지 않았다.

같은 한정 범위에서 `BMO_Screen_v13_2_Package.zip`, `BMO_Games_v3.zip`도 정확명으로 찾지 못했다. 홈 전체 수집·다른 프로젝트 탐색·패키지 설치·외부 콘텐츠 수집은 하지 않았다. 얼굴/게임/모델 원본은 공개 GitHub에 업로드하지 않았다.

## 4. 인터페이스와 충돌

아래에서 **소스 연결**은 읽은 코드의 호출 관계만, **미구현**은 조사한 코드 경로의 부재, **계약 충돌**은 두 기준의 불일치, **현장 미확인**은 실제 파일/실기 근거 부족을 뜻한다. 런타임 성공 판정이 아니다. 별도 표기가 없으면 음성 근거는 Luna `054ced8f...`의 `software/bmo_ai/`, Action/하드웨어 근거는 Actions `b346cc30...`의 같은 하위 경로다.

| 경계 | 판정 | 이번에 확인한 코드·필요한 차이 |
|---|---|---|
| 입력→LLM→음성 | 소스 연결 | `ConversationWorker`가 input→answer→playback 호출. `ConversationApp`/`BMOBrain` 보존 가능(`conversation.py:207-245`, `app.py:40-46`) |
| 중복 시작·turn 무효화 | 소스 연결 / 통합 ID 확장 필요 | worker당 하나의 thread, busy 중 start 거부. cancel 후 이벤트 폐기 구현. turn은 worker별 0으로 초기화하고 첫 start에서 1부터 증가하며 전역 session/utterance ID 없음(`conversation.py:24-31,71,90-121,133-169`) |
| FacePort→최종 HTML | 미구현 / 계약 충돌 | FacePort는 `set_state`/`close`뿐. enum은 idle/listening/thinking/speaking/error. Chromium `window.BMOFace` 브리지·감정·입 프레임 계약 없음(`hardware.py:18-29`). 기본 음성 CLI는 face 미주입, display는 별도 경로(`__main__.py:108-114`, `display_app.py:73-100`) |
| 재생 시작 | 소스 연결 / 현장 미확인 | aplay Popen 성공 뒤 on_start에서 speaking. 합성 완료 자체를 speaking으로 쓰지 않음. Popen 경계는 실제 가청 시작과 다름(`audio.py:80-97,323-324`, `app.py:88-105`) |
| 재생 종료·실패 | 소스 연결 / 이벤트 세분화 미구현 | finally에서 IDLE. 정상 종료·실패·취소를 utterance별 별도 이벤트로 구분하는 계약 부족(`app.py:135-137`) |
| 재생 중 취소 | cancel event에 따른 즉시 정지 미구현 | 재생 직전 cancel check는 있으나 `_run()`의 `communicate(timeout=...)` 중 cancel polling/즉시 stop 없음. worker 이벤트 무효화만으로 이미 재생 중인 aplay 정지를 보장하지 못함. timeout/예외의 kill 경로는 별도로 존재(`safe_playback.py:95-102`, `audio.py:88-95,360-364`) |
| 감정·입·재접속 | 미구현 | TurnEvent는 turn/kind/state/code/timings/synthetic만 보유. emotion/utterance/source/입 cue 및 브라우저 재연결 시 재동기화 계약 없음(`conversation.py:24-31`, `hardware.py:18-29`) |
| 새 음성 정책·프롬프트 | 미구현 / 계약 충돌 | 기존 SYSTEM_PROMPT+LANGUAGE_INSTRUCTIONS 하드코딩. 외부 정책 prompt 로더 없음. `en`은 한국어 입력에 한국어 응답하므로 새 `english_voice`와 충돌(`core.py:12-21,114-123`, `config.py:84-85`) |
| Fish→Piper | 소스 연결 / opt-in·실기 미확인 | Fish AudioSynthesisError + offline_voice일 때 같은 답변을 Piper로. 한국어 등은 짧은 영어 안내. STT/GPT 오프라인 두뇌가 생기는 것은 아님. OpenAI TTS 분기에는 같은 fallback catch 없음(`app.py:64-76,112-130`, `local_piper.py:25-29`) |
| Action 결과 표시 | 미구현 | ActionDispatcher/ActionResult 소비자가 대화·얼굴·TTS에 연결되지 않음. 5개 Mock 정의가 실제 실행 어댑터는 아님(`actions.py:1-5`, `conversation.py:207-245`, `app.py:40-46`) |
| RP2040 통신·팔 | 미구현 / 현장 미확인 | supplied bytes용 proposed-v1 JSONL parser와 관찰 monitor만 존재. serial transport/명령 TX/ACK 구현·실물 프로토콜 일치 증거 없음. DeniedArmController는 항상 move 차단(`hardware.py:108-123,172-275,286-364`) |
| 게임 종료→얼굴 복귀 | 조사 경로에 미구현 / 현장 미확인 | Action은 demo_game Mock만 처리. 조사한 대화/Action 경로에 launcher·return-face 소비자 없음. 실제 게임 패키지·소스를 찾지 못해 게임 서버 자체의 현재 동작은 검증 불가 |
| repeat | 정책과 기존 동작 차이 | 기존 `/repeat`은 last_answer 문자열을 app.playback에 다시 넘겨 재합성한다. WAV 캐시 재생이 아님(Luna `__main__.py:135-140`, BGH_BMO `:126-131`; Luna `app.py:112-134`, `fish_tts.py:197-208`, `audio.py:343-367`) |

음성 두 루트의 `conversation.py`, `hardware.py`, `audio.py`, `safe_playback.py`, `fish_tts.py`, `local_piper.py`, `pronunciation.py`, `face.py`, `display_app.py`는 각각 SHA-256이 동일했다. Luna의 app/config/core/CLI에는 reasoning·timing·source 추가가 있지만 위 누락 경계가 해결된 것은 아니다.

### 최종 얼굴 계약: 문서와 현장 확인 구분

최신 [통합 기준 §4](https://github.com/GUNOBAE/BGH_BMO/blob/3c889bda472866adca4a4de0d16ec7dcfad535de/docs/development/BMO_INTEGRATION_2026-10-22_KR.md#4-최종-얼굴-api를-실제-파일에서-확인한-내용)는 `window.BMOFace`의 setEmotion/setActivity/setSpeaking/setMouth/stop/getState 등을 기록한다. **이는 해당 문서의 이전 원본 검토 기록이며 이번 Pi 원본 확인은 아니다.** 현장 파일을 확보하기 전 임의 API를 구현하거나 버전 문자열만으로 파일을 대체하지 않는다.

문서 기준 activity는 idle/listening/thinking뿐이므로 기존 FaceState의 speaking/error를 그대로 넣을 수 없다. speaking은 setSpeaking 및 외부 mouth cue로 분리해야 한다. setActivity는 speech를 멈출 수 있어 입 프레임마다 호출하면 충돌한다. playAudio/playBlob의 await는 재생 종료 보장이 아니며 Python/브라우저 중 한 주체만 출력해야 한다. 이번에는 HTML을 실행하거나 네트워크 브리지를 추가하지 않았다.

### Action v0의 실제 계약

원격/로컬이 일치한 [actions.py](https://github.com/GUNOBAE/BGH_BMO/blob/b346cc3017ca0ee4cb2162384d1fa1499659739a/software/bmo_ai/actions.py)를 읽었다. 요청은 정확히 `action`, `arguments`, `request_id` 세 필드를 가진 dict 또는 검증된 ActionRequest다. 자연어·JSON 문자열·추가 필드는 실행 입력으로 받지 않는다(`:48-123`).

| action | 허용 arguments | 현재 결과 |
|---|---|---|
| `raise_left_arm` | `{}` | Mock |
| `raise_right_arm` | `{}` | Mock |
| `take_photo` | `{}` | Mock |
| `play_song` | `{"song_id":"demo_song"}` | Mock 자리표시자 |
| `launch_game` | `{"game_id":"demo_game"}` | Mock 자리표시자 |

결과 dict 필드는 `request_id`, `action`, `status`, `code`, `message`, `simulated`, `executed`다. 정상은 `status=simulated`, `code=mock_only`; `executed`는 모든 상태에서 False다(`:126-166`). duplicate/rejected/error도 실제 완료를 의미하지 않는다.

request_id는 1–64자 ASCII 식별자다. Dispatcher는 검증 후 ID를 실행 전에 예약하고 같은 요청은 duplicate, 다른 내용의 같은 ID는 conflict로 거부한다. 기본 1024개, 최대 10000개의 인스턴스 메모리 ledger이며 용량 초과는 거부하고 재시작을 넘는 중복 보장은 없다(`:19-26,186-242`). host가 논리 요청 ID를 안정적으로 생성·전달할 어댑터와 결과 표시가 필요하다. `utterance_id`와 혼동하지 않는다.

연결 검색은 Actions의 `software/bmo_ai/**/*.py` 내 `ActionDispatcher`, `ActionRequest`, `ActionResult`, actions import를 대상으로 했으며 자체 정의 외 소비자를 확인하지 못했다. 테스트 소스 정의는 읽었지만 실행하지 않았으므로 테스트 통과 수를 이번 결과로 보고하지 않는다.

### 하드웨어·게임의 확인 한계

`TelemetryDecoder.feed`는 이미 공급된 bytes를 해석할 뿐 포트를 열지 않는다. 제안 JSON v1과 실제 RP2040 펌웨어의 일치를 주장할 수 없다. `CameraPort`도 capture/close Protocol과 frame 자료형뿐이며 take_photo 저장 성공 연결은 없다(`hardware.py:47-61`). 펌웨어·CIRCUITPY·시리얼·카메라·모터에 접근하지 않았고 GP7 팔 토글/질문 버튼 충돌도 현장 미확인으로 남긴다. IMU는 제어 입력으로 사용하지 않는다.

통합 기준의 게임 `/api/return-to-face`가 `connected:false`라는 설명은 이전 패키지 검토 기록이다. 이번에는 해당 패키지를 찾지 못했으므로 실제 API 응답을 확인하지 않았다. 게임 실행·종료·USB 패드·얼굴 복귀는 검수되지 않았다. demo_game이 실제 Tetris 설치를 뜻하지 않는다.

핵심 소스 근거: [worker](https://github.com/GUNOBAE/BGH_BMO/blob/054ced8f894f25f4cd0a178c2b097b5afe2c8576/software/bmo_ai/conversation.py#L90), [app 재생](https://github.com/GUNOBAE/BGH_BMO/blob/054ced8f894f25f4cd0a178c2b097b5afe2c8576/software/bmo_ai/app.py#L78), [플레이어 대기](https://github.com/GUNOBAE/BGH_BMO/blob/054ced8f894f25f4cd0a178c2b097b5afe2c8576/software/bmo_ai/audio.py#L80), [정책 충돌](https://github.com/GUNOBAE/BGH_BMO/blob/054ced8f894f25f4cd0a178c2b097b5afe2c8576/software/bmo_ai/core.py#L12), [하드웨어 계약](https://github.com/GUNOBAE/BGH_BMO/blob/b346cc3017ca0ee4cb2162384d1fa1499659739a/software/bmo_ai/hardware.py#L108).

## 5. 첫 통합에 필요한 다음 작업 — 제안만

1. **승인 얼굴 원본 위치를 확정하고 승인 SHA와 대조한다.** 원본은 그대로 보존하고 외부 wrapper/adapter의 입력·순서·감정/입 전환 규칙을 실제 파일에서 인수한다. 현재 파일 미확인 상태로 다른 얼굴을 채택하지 않는다.
2. **격리된 후속 구현에서 Luna 계열을 기준으로 명시적 실행 프로필을 만든다.** Luna low/Fish Aaron Soft/beemo/PCM 0.25/Piper opt-in 및 실제 입력·출력 장치를 명시하고, 4.1 mini+reasoning unset 복귀 경로와 원본 Piper를 보존한다. 현재 브랜치들 전체 병합이나 원본 이동은 하지 않는다.
3. **단일 플레이어의 시작·진행·종료·취소를 얼굴 이벤트에 연결한다.** session/turn/utterance 식별자와 source/phase/emotion을 정의하고 오래된 이벤트를 거부한다. 소유한 aplay 자식만 중단·회수하도록 재생 중 취소를 구현한다. Python과 브라우저에서 같은 음성을 중복 출력하지 않는다.
4. **새 음성 정책을 실제 prompt/언어 분기에 적용한다.** english_voice 설계와 기존 en의 충돌을 제거한다. 합성 완료와 가청 시작, 정상 종료와 실패/취소, 재합성과 캐시 재생을 구분한다. 원본 Piper의 별도 백업 현재 상태도 후속 범위에서 확인한다.
5. **그다음 Action v0를 Mock 그대로 대화·결과 표시에 연결한다.** 실제 실행 성공 발화를 금지하고 GP7/실물 RP2040 통신을 별도로 검토한다. 게임은 실제 패키지 인수 후 진입·종료·얼굴 복귀부터, 팔·사진·음악은 각 실제 어댑터의 결과 계약과 실기 검수로 진행한다. IMU 자동 반응은 보류한다.

후속 검수는 장치 없는 모의 검증과 승인된 실음성/실물 검증을 분리해 기록한다. 이번 보고서의 소스 확인·파일 해시는 그 검수의 통과 결과를 대신하지 않는다.

원격 변경 대상은 **`reports/bmo-pi-preflight-20261022`의 `docs/reports/PI_PREFLIGHT_LATEST_KR.md` 한 파일**이다. main 병합·코드 PR·이슈·댓글은 만들지 않는다. 비밀값, 인증 설정, 로그 본문, 원본 얼굴/게임/음성/모델은 보고서에 포함하지 않는다.
