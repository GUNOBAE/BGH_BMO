# Codex 야간 작업서 — BMO 얼굴·음성 통합 구현

기준일: **2026-10-22 KST**

## 결론과 실행 권한

**이번에는 조사 보고서에서 끝내지 말고, 격리된 통합 폴더에서 실행 가능한 코드·모의 실행기·검증·GitHub 업로드까지 완료한다.** 핵심 결과를 먼저 저장한 다음 반복 재생 캐시와 5개 Mock Action 연결을 진행한다. 실제 소리와 Pi 화면을 함께 보는 최종 검수는 사용자가 돌아온 뒤 수행한다.

사용자는 승인 얼굴 파일을 Pi에 넣었다고 알렸으며, 자러 가는 동안 가능한 작업을 최대한 진행하고 Codex 권한을 허용한다고 지시했다. 이 지시는 이전의 읽기 전용 preflight 단계에서 **격리된 코드 개발 단계로 진행하라는 후속 승인**이다. 코드 작성, 해당 폴더의 개발 의존성 설치, 모의·headless 검증, 커밋·push·draft PR·보고서 업로드를 다시 확인받지 않는다. 작업 분할이 유리하면 독립 파일의 구현/검토를 subagent에 위임한다.

이 작업에서 실제 마이크 녹음·스피커 재생·Piper 합성·STT/GPT/Fish 호출·카메라·직렬 포트·서보·RP2040 펌웨어는 실행하지 않는다. 기존 팬/SSH/서버/서비스, OS 오디오 설정, 자동 시작 설정도 바꾸지 않는다. 기존 사용자 파일과 원본은 보존하고 main 병합·force-push·브랜치 삭제를 하지 않는다. 관리형 정책이나 자동 승인 검토가 거절하면 설정 파일/AGENTS/승인 규칙을 바꿔 우회하지 말고, 가능한 대체 경로와 나머지 작업을 진행한 뒤 정확한 차단 사유를 보고한다.

## 1. 확정된 출발점과 먼저 읽을 자료

저장소는 `GUNOBAE/BGH_BMO` 하나다. 이 문서와 다음 두 문서는 **`docs/bmo-integration-baseline-20261022`의 최신 HEAD**에서 읽는다. 통합 코드 브랜치나 main에 같은 파일이 이미 있다고 가정하지 않는다.

- [1차 통합 기술 계약](CODEX_INTEGRATION_STAGE1_KR.md)
- [전체 통합 기준·계보·파일 해시](BMO_INTEGRATION_2026-10-22_KR.md)
- [완료한 Pi preflight 보고서](https://github.com/GUNOBAE/BGH_BMO/blob/0bfb8bd4b4d99bffb9ba479eab6b8b7f62ac25b4/docs/reports/PI_PREFLIGHT_LATEST_KR.md)
- `docs/bmo-voice-policy-v1-20261022`의 `docs/voice-policy-v1/README_KR.md`, `INTEGRATION_HANDOFF_KR.md`, 연결된 프롬프트와 acceptance cases. 확인된 기준은 `707c66b8876130be75ad216fa10aaabf2bdcdb56`; HEAD가 달라졌으면 관련 차이만 확인한다.
- 적용 경로의 `AGENTS.md`. 기존 `/home/bgh/BGH_BMO/AGENTS.md`의 원본·장치 보존 지침도 확인한다. 파일을 만들어 지침을 대체하지 않는다.

### 작업 경로와 Git

| 용도 | 실제 경로 / 기준 |
|---|---|
| 새 통합 작업 | `/home/bgh/BMO_INTEGRATION`, `feature/bmo-integration-v1` |
| 코드 출발점 | Luna `054ced8f894f25f4cd0a178c2b097b5afe2c8576`에서 위 원격 브랜치를 준비함 |
| 보존할 기존 음성 | `/home/bgh/BGH_BMO`, Piper `7bed6d02c4ac239671b56d601e7fd2845b5fe0c0` |
| 보존할 Luna 비교 | `/home/bgh/BMO_LUNA_AB`, Luna `054ced8f`; 미추적 `logs/voice_41.jsonl`, `logs/voice_luna.jsonl` 보존 |
| Action 계약 출처 | `/home/bgh/BGH_BMO_ACTIONS`, `feature/bmo-actions-mock-v0`, `b346cc3017ca0ee4cb2162384d1fa1499659739a` |
| 비 Git 음성 자산 | `/home/bgh/bmo-voice-test` |

`/tmp`나 Codex 시작 폴더가 저장소일 필요는 없다. Git 조회에는 `GIT_OPTIONAL_LOCKS=0 git -C <실제 프로젝트 경로>`를 사용한다. 세 기존 체크아웃의 remote/HEAD/status와 적용 지침의 **변동만** 확인하고 전체 preflight를 반복하지 않는다. GitHub 결과의 404, 일시적 네트워크 실패, `/tmp`의 not-a-repository를 프로젝트 고장으로 확대하지 않는다.

새 폴더는 원격에서 독립 clone한다. 기존 clone을 전환하거나 linked worktree/`--shared`/`--reference`를 만들지 않는다. 새 경로가 이미 있으면 remote·branch·미커밋 변경을 확인하고 동일 작업이면 이어간다. 다른 파일이 있으면 보존하고 별도 이름의 새 폴더를 만들어 보고한다. `reset --hard`, `clean`, 덮어쓰기로 정리하지 않는다. 원격 통합 브랜치에 이미 후속 커밋이 있으면 이를 확인해 이어간다.

Luna가 물려받은 과거 문서로 최신 main 문서와 별도 통합/음성 정책을 덮어쓰지 않는다. 필요한 정책 파일은 확인한 commit 출처와 함께 가져오고, 전체 브랜치를 한꺼번에 merge하지 않는다. 구현과 관련 없는 README 전체 개편이나 저장소 재구성은 하지 않는다.

### 승인 얼굴과 Piper 인수

먼저 `/home/bgh/Downloads/BMO_Screen_v13_2.html` 또는 사용자가 명시한 경로를 확인한다. 없으면 Downloads 안의 같은 이름 변형과 이미 알려진 전달 폴더만 제한적으로 확인한다. 전체 디스크 검색을 반복하거나 이전 시안으로 대체하지 않는다.

```text
BMO_Screen_v13_2.html
159892 bytes
SHA-256 38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932
```

원본은 수정·이동하지 않고 로컬 파일 경로로 참조한다. Git/PR에 원본 HTML·패키지를 추가하지 않는다. 해시 불일치/미발견이면 해당 파일을 승인본으로 표시하지 말고 독립적으로 가능한 구현·모의 검증을 계속한다. 실제 승인 HTML 검증만 `BLOCKED`로 남긴다. 게임 ZIP은 이 작업의 필수 입력이 아니다.

Piper ONNX·JSON·실행 파일은 Stage 1 문서/현장 보고서의 경로와 해시를 확인하며 변경하지 않는다. `/home/bgh/BMO_backups`에 대응 백업이 이미 있으면 알려진 원본과 동일한지 제한적으로 확인한다. 대응 백업을 확인할 수 없고 공간이 충분하면 이번 작업에서 사용하는 승인 얼굴과 Piper 모델/JSON을 새 비공개 timestamp 폴더에 복사·해시 대조할 수 있다. 기존 백업 덮어쓰기/삭제는 하지 않는다. 백업 확인 문제로 독립적인 코드 개발까지 중단하지 않는다.

## 2. 우선 완성할 핵심 결과

### A. 수동 실행기와 선택된 프로필

실제 실행용 수동 launcher와 **같은 통합 경로를 사용하는 완전 모의 `--simulate` 모드**를 만든다. 실제 실행 모드의 마이크·클라우드·재생 backend를 주입 가능한 경계로 분리하고, 모의 모드는 어떤 실제 장치/API도 열지 않도록 한다. 문서에 정확한 실행·취소·종료 명령을 남긴다. launcher 작성과 실제 음성 실행은 구분한다. 바탕화면 아이콘 파일을 만든다면 설치/자동 실행 없이 템플릿까지만 제공한다.

프로필은 `gpt-6-luna`/reasoning `low`, `gpt-4o-mini-transcribe`, Fish Aaron reference `106f41ea392c4cc48c6eee76f69ff17e`, 시험한 engine `s2.1-pro-free`, delivery `soft`, pronunciation `beemo`, PCM gain `0.25`를 실제 설정 경로에 전달한다. `soft`의 기존 연기 지시 `[gentle, warm, slightly lower pitch, still lightly playful]`를 보존한다. gain은 정확히 한 번 적용한다. launcher가 읽지 않는 `.env`만 만들어 적용했다고 하지 않는다. 모델 가용성이나 가격을 추측해 엔진을 바꾸지 않는다.

기존 GPT-4.1 mini 비교/복귀 경로는 유지하고 이때 reasoning을 unset/None으로 정리한다. 최근 실음성 시험의 출력 토큰 상한 320을 통합 출발값으로 명시하되 benchmark 600을 자동 기본값으로 올리지 않는다. 비밀값 없는 effective config 확인 기능을 제공한다.

음성 정책 원본의 `english_voice`를 적용한다. 한국어 입력을 이해하되 명시적 한국어 답변 요청이 없으면 영어 음성이 기본이다. 기존 언어 지침과 충돌을 해소하고 간결하고 따뜻한 BMO 말투, 기억/행동 완료에 대한 정직성을 유지한다. 20개 acceptance case가 작성됐다는 사실을 실제 모델 시험 통과라고 쓰지 않는다. 모델 호출 없이 정책 로딩/우선순위만 검사한다.

Piper는 기존 원본과 opt-in fallback을 보존한다. Fish 실패 시 영어 답변은 이미 생성된 텍스트를 재사용하고 추가 GPT 호출을 만들지 않는다. 한국어/클라우드 실패의 기존 고정 안내와 영어 모델 제약을 유지한다. 이를 오프라인 두뇌·STT라고 표현하지 않는다.

### B. 단일 플레이어, 취소, 이벤트 계약

Python/ALSA를 유일한 음성 출력 경로로 유지한다. 기존 worker의 busy guard·bounded queue·늦은 결과 차단을 재사용하고, session/turn/utterance 식별자로 새 세션과 이전 결과를 구별한다. phase·emotion·playback·mouth timeline은 별도 필드로 관리한다.

시작 전 취소 검사만으로 완료하지 말고 **실행 중 소유한 player 프로세스의 취소·종료·회수**를 구현한다. 합성 중 취소, 합성 완료 뒤 늦은 결과, playback 오류, UI 연결 단절/복귀, 앱 종료도 같은 계약으로 정리한다. 타임아웃과 취소 뒤 다음 대화가 가능해야 한다. 전역 프로세스 kill이나 다른 앱의 오디오를 중단하지 않는다.

`on_start`는 player 프로세스 시작 근사값이며 실제 가청음 시작과 같다고 보장하지 않는다. Python/JavaScript monotonic 원점을 직접 비교하지 않고 상대 타임라인과 현재 상태 스냅샷으로 복귀한다. 재연결 때 지난 음성을 처음부터 재생하거나 오래된 cue를 몰아서 실행하지 않는다.

### C. 승인 얼굴 wrapper와 입 타임라인

same-origin wrapper/iframe에 승인 원본을 `?kiosk=1`로 띄우고 `window.BMOFace`로 제어한다. 원본 HTML/SVG/animation data는 바꾸지 않는다. 서버는 loopback에만 바인딩하고 승인한 파일과 앱 정적 자원만 제공한다. 폴더 전체·키·임의 명령 실행을 노출하지 않는다.

감정 유지, `setEmotion`/`setActivity` 호출 순서, 외부 speaking, `stop`, stale cue 처리의 상세 계약은 Stage 1 문서를 따른다. 브라우저 `playAudio`/`playBlob`/`HTMLMediaElement.play()`는 이 대화 출력 경로에서 호출하지 않는다.

실제 출력 WAV의 envelope를 승인 mouth key에 매핑하고 외부 `setMouth()`가 원본 bridge를 우회하는 점을 보완한다. 원본에서 확인한 30fps 전환과 중간 프레임을 adapter에서 유지한다. 고정 무음 데모나 무작위 입 움직임을 실음성 동기화로 설명하지 않는다. 무음·종료·취소 시 입을 닫고 감정을 보존한다. 원본의 thinking `hmm`는 정상 생각 표정이며 합성 대기 중 speaking과 구분한다.

## 3. 필요한 검증과 첫 checkpoint

테스트 환경에는 실제 클라우드 키를 전달하지 않는다. 비밀값을 읽어 출력하지 않고 테스트 child 환경에서 관련 키를 제외한다. mic/STT/GPT/Fish/Piper/player/serial/camera를 fake 또는 stub으로 대체한다. 실제 `aplay` 대신 소유한 무해한 child process로 실행 중 취소·회수를 검사한다.

| 검사 | 구체적인 합격 기준 |
|---|---|
| 모의 한 턴 | 하나의 launcher에서 idle → listening → thinking → player start/speaking → idle까지 완료. 실제 장치/API 호출 0 |
| 대화 문맥 | 같은 launcher에서 연속 두 모의 턴의 기존 history 유지, `/reset` 뒤 초기화. 매 턴 brain을 새로 만들어 문맥을 잃지 않음 |
| 설정 전달 | Luna low/Fish/soft/beemo/.25 실효값, gain 단일 적용, 4.1 mini reasoning 해제, 정책 충돌 해소 확인 |
| 실제 HTML | 지정 해시의 원본 iframe을 headless로 로드. `getState().version === '13.2'` 및 원본 해시 전후 동일. 잔존 `BMOFace.version === '13.0'`은 수정하지 않음 |
| 감정/활동 | crying·happy에서 listening→thinking, surprised→thinking에도 감정·SVG 참조 유지. 발화 중 감정만 바꿀 때 말하기가 멈추지 않음 |
| 입 전환 | 105ms 이상 목표 구간 fixture에서 closed→wide는 30fps의 low→body→wide, closed→round는 tight→round. 실제 원본과 대조 |
| 시작/정지 | TTS 완료만으로 speaking 시작 안 함. player start 뒤 cue 진행. 재생 중 무음에서는 입만 closed로 하고 현재 utterance·speech external 유지. ended/cancelled/error 때 시간표 폐기 및 idle·speech off·closed |
| 취소 | 합성 중 취소·재생 중 취소·늦은 결과 거부. 무해한 실제 child process가 종료·회수되고 새 턴이 정상 진행 |
| 중복/복귀 | 중복 시작/질문이 worker/player를 늘리지 않음. reconnect/탭 일시 중지 뒤 현재 위치 또는 idle로 복귀하며 이전 utterance cue 거부 |
| 단일 출력/종료 | browser 재생 호출 0. iframe 제거·앱 종료 뒤 callback/연결/프로세스 정리. 앱 소유 자원만 종료 |
| fallback/error | opt-in·영어 제약·추가 GPT 호출 없음과 동일한 플레이어 이벤트 경로를 mock으로 확인. 실패 뒤 다음 턴 가능 |

기존 Python/Chromium과 사용 가능한 headless 도구를 우선 사용한다. 필요한 테스트 의존성은 새 프로젝트의 venv/개발 범위에 설치할 수 있다. 실제 승인 HTML을 읽는 검사가 중요하며, 가짜 BMOFace만 통과하고 브라우저 검증을 완료했다고 하지 않는다. 설치/환경 차단은 정확히 기록하고 나머지 검증을 계속한다. 실제 음질·볼륨·가청 지연·Pi LCD 시각·물리 버튼은 `NOT_RUN`이다.

코드에 관련된 기존 회귀 검사만 실행하고 실패 원인을 해결한다. 충분한 근거를 얻으면 같은 테스트를 무한 반복하거나 모델 비교/전체 재감사를 하지 않는다. 핵심 구현과 검증 결과가 정리되면 **추가 작업 전에 첫 코드 커밋·push·보고서 checkpoint를 남긴다.**

## 4. 핵심이 통과한 뒤 이어갈 추가 작업

추가 작업은 순서대로 수행하고 각각 별도 커밋과 회귀 검증을 남긴다. 핵심 검사에 미해결 실패/차단이 있으면 추가 기능보다 이를 해결하거나 정확한 부분 완료 결과를 먼저 남긴다.

1. **최근 성공 WAV의 세션 캐시:** 세션 안에서 마지막 성공 음성 하나를 보존하고 `/repeat`가 GPT/Fish/Piper 재합성 없이 동일 WAV·gain·타임라인으로 재생하도록 한다. 반복 중 취소와 단일 출력도 유지한다. reset/exit 때 정리하며 녹음 장기 보관이나 전체 대화 음성 DB는 만들지 않는다. 캐시가 없을 때의 동작을 명확히 한다.
2. **Action v0 Mock host 연결:** `feature/bmo-actions-mock-v0`의 기준 `b346cc3017ca0ee4cb2162384d1fa1499659739a`에서 정확한 계약/validator/테스트를 확인하고 필요한 파일만 통합한다. live LLM 도구 호출이나 실제 장치 adapter는 만들지 않는다. 검증된 구조화 요청을 통합 앱의 모의 경로로 전달하고 결과를 표시한다.

| Action | arguments |
|---|---|
| `raise_left_arm` | `{}` |
| `raise_right_arm` | `{}` |
| `take_photo` | `{}` |
| `play_song` | `{"song_id":"demo_song"}` |
| `launch_game` | `{"game_id":"demo_game"}` |

요청은 `action`, `arguments`, `request_id`의 정확한 계약을 따른다. 결과의 `request_id/action/status/code/message/simulated/executed`를 보존하고, 정상은 `status=simulated`, `code=mock_only`, 모든 Mock 결과는 `executed=False`다. 중복·충돌·거부와 성공한 모의 실행을 구분한다. 논리 요청의 안정적인 ID를 사용하고 기존 in-memory ledger가 프로세스 재시작까지 영속한다고 쓰지 않는다. 사진·노래·게임·팔이 실제 실행됐다는 답변을 만들지 않는다.

물리 버튼·시리얼 ACK·게임 원본/패드·서보·IMU·호출어/VAD·스트리밍은 이후 검수/개발로 남긴다. GP7 기존 팔 토글 충돌을 firmware 수정으로 밤새 해결하지 않는다. 위 추가 작업까지 끝나면 결과를 정리하고 종료한다.

## 5. GitHub 제출과 완료 조건

- **코드/보고 브랜치:** `feature/bmo-integration-v1`
- **보고서 경로:** `docs/reports/INTEGRATION_STAGE1_LATEST_KR.md`
- **draft PR:** head `feature/bmo-integration-v1`, base `feature/bmo-luna-low-voice-ab`. Luna에서 물려받은 기존 기능과 이번 변경을 구분하며 main 최종 통합은 후속 검토로 남긴다. 같은 head의 기존 PR이 있으면 중복 생성하지 않는다.

보고서는 첫 문장에 `PASS / PARTIAL / BLOCKED`와 이유를 적고, 실제 작업 경로·branch·base/결과 SHA·상태, 원본 해시, 구현 범위, 실행한 검사/결과, 미실행 항목, 정확한 수동 실행·취소·복귀 방법을 기록한다. 추가 기능의 완료 여부, 실패·차단 원인과 다음 한 단계도 기록한다. 가상의 성공 로그나 실행하지 않은 소리/실물 결과를 넣지 않는다. 보고서 자체의 커밋 SHA를 미리 단정하지 말고 코드 검증 SHA와 업로드 후 확인한 최종 SHA를 구분한다.

stage할 파일은 명시적으로 선택한다. `.env`, 키, 토큰, 인증 정보, 원본 얼굴/모델/게임, 개인 대화 로그·녹음·생성 WAV·venv를 공개 저장소에 올리지 않는다. 소스/검사/문서만 diff 확인 후 커밋한다. 원격의 새 커밋을 확인하고 정상 push한다. 충돌은 사용자 작업을 보존해 해결하고 force-push하지 않는다.

업로드 후 GitHub branch HEAD·파일·PR 링크를 직접 확인한다. 일반 Git push가 막히면 이미 준비된 동일 브랜치에 GitHub connector/Contents API로 **보고서만** 제출하는 대안을 시도할 수 있다. 코드가 업로드되지 않았다면 보고서에 분명히 적고 로컬 코드/커밋을 보존한다. 원격 보고서 커밋이 추가되면 다시 push하기 전에 이를 확인하고 조정한다. 네트워크 전체 차단이면 로컬 보고서 경로·완료 커밋·정확한 오류를 남긴다. 업로드 실패를 업로드 완료로 표시하지 않는다.

사용자 최종 응답은 **결론, GitHub 보고서/PR 링크, 아침에 할 한 가지**만 짧게 전달한다. 보고서를 ChatGPT에 붙여 넣으라고 요구하지 않는다. 현재 작업과 다른 새 조사/모델 선택 질문으로 사용자를 깨우지 않는다. 독립적으로 진행 가능한 작업을 끝내고 검토 가능한 결과를 남긴다.
