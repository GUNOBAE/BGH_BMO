# Codex 작업서 — BMO 1차 얼굴·음성 통합

기준일: **2026-10-22 KST**

## 목표

기존 BGH_BMO의 대화·Fish 재생에 **사용자 승인 `BMO_Screen_v13_2.html`을 연결**한다. 첫 결과는 수동으로 실행 가능한 하나의 앱이다.

**대기 → 듣기(질문 녹음 중) → 생각 → 실제 음성 재생과 입 동작 → 대기**, 그리고 취소·오류·앱 종료 시 올바른 정리를 구현한다. 작업의 최신 범위는 사용자의 현재 통합 지시다. 과거 독립 세션의 읽기 전용 조사 지시를 이유로 이번에 허용된 격리 개발을 막지 않는다.

이 문서는 실행 명령 자체가 아니며, 읽었다는 이유로 코드가 바뀌지는 않는다. Pi Codex에게 이 문서 전체 또는 아래의 짧은 지시를 전달해 작업한다.

## 0. 먼저 읽을 것

- [통합 기준·계보·합격 기준](BMO_INTEGRATION_2026-10-22_KR.md)
- 저장소의 실제 `AGENTS.md` 및 해당 경로의 작업 지침
- 현재 runtime의 `config.py`, `core.py`, `app.py`, `conversation.py`, `audio.py`, `safe_playback.py`, `__main__.py`, 기존 face/UI 연결
- [음성 정책 원본](https://github.com/GUNOBAE/BGH_BMO/blob/c28ab396df6eff77693f8b7d7bd6e63bc21c633e/docs/voice-policy-v1/README_KR.md)과 별도 프롬프트. 다른 세션에서 갱신 중일 수 있으므로 적용 직전 HEAD/내용 대조

## 1. 현장 조사 후 격리 개발을 계속한다

알려진 Pi 경로는 `/home/bgh/BGH_BMO`, `/home/bgh/BGH_BMO_ACTIONS`, `/home/bgh/BMO_LUNA_AB`다. 실제 존재 여부와 각각의 remote/branch/HEAD/dirty 상태를 확인한다. `BGH_BMO_ACTIONS`는 같은 GitHub 저장소의 작업 폴더다. `.env` 값이나 API 키를 출력하지 않는다.

현재 실행 중인 BMO 프로세스, ALSA 입력/출력 이름, 화면 세션을 읽기 전용으로 파악한다. 기존 서비스/펌웨어를 시작하거나 재시작하지 않는다. RP2040의 `code.py` 재시작은 팔 동작을 일으킬 수 있어 이 단계에서 하지 않는다.

격리된 `feature/bmo-integration-v1` 브랜치/작업 디렉터리를 만들거나 기존 동일 목적 작업을 이어간다. 기본 구현 출처 후보는 `feature/bmo-luna-low-voice-ab`의 확인된 `054ced8f894f25f4cd0a178c2b097b5afe2c8576`이며 Fish/Piper/기존 worker를 포함한다. 새 HEAD가 있으면 차이를 검토한다. main에만 있는 최신 문서 변경을 보존하고, 사용 중인 체크아웃을 전환하거나 미커밋 파일을 덮어쓰지 않는다.

`feature/bmo-action-lab`나 모델 benchmark를 상시 앱에 병합하지 않는다. Action v0는 다음 단계에서 연결한다. 기존 독립 브랜치를 삭제·rebase·force-push하지 않는다.

원본 얼굴을 기존 BMO 폴더/다운로드/사용자 제공 위치에서 찾고 다음 해시와 대조한다.

```text
BMO_Screen_v13_2.html
SHA-256 38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932
```

원본이 없으면 이전 버전을 대신 채택하거나 비슷한 얼굴을 새로 만들지 않는다. 원본 없이 가능한 코드 조사·adapter 경계·모의 검사를 먼저 끝내고, 필요한 것은 **이 파일 하나의 위치/전달**이라고 짧게 보고한다. 원본은 공개 Git에 넣지 않고 실행 시 지정하는 로컬 파일로 사용한다.

## 2. 이번에 구현할 것

### A. 원본을 보존한 화면 adapter

같은 출처의 wrapper/iframe에서 원본 `?kiosk=1`을 열고 `window.BMOFace`를 호출한다. 원본 HTML/SVG/animation data를 수정하지 않는다. 외부 bridge는 localhost에만 바인딩하고 API 키나 임의 파일 읽기/실행 기능을 브라우저에 노출하지 않는다. 얼굴 원본은 명시한 한 파일만 제공하며 BMO 폴더 전체를 웹 root로 열지 않는다.

필요한 이벤트는 session/turn/utterance 식별자, phase, emotion, playback start/progress/end/cancel/error와 입 시간표다. 구체 필드는 기존 worker와 맞추되 상태·감정·입 크기를 하나의 enum에 섞지 않는다. 중단된 turn의 이벤트는 브라우저에서도 폐기한다.

실제 원본 계약:

- 감정: `neutral/happy/angry/sad/crying/surprised`.
- 활동: `idle/listening/thinking`. `speaking` 또는 `error`를 `setActivity()`에 보내지 않는다.
- 듣기·생각에서 감정과 활동을 함께 적용할 때는 `setEmotion()` → 필요한 `setActivity()` 순서. `setEmotion()`은 기존 non-idle activity를 idle로 돌린다. 이미 말하는 중 감정만 변경할 때는 `setEmotion()`만 호출하고 뒤에 `setActivity()`를 호출하지 않는다.
- `setActivity()`는 말하기를 멈춘다. 상태/감정이 바뀔 때만 호출하고 입 프레임마다 반복하지 않는다.
- 실제 재생 시작 시 `setSpeaking(true)`, 이후 `setMouth()`를 공급한다. `setSpeaking(true)`만으로 입이 움직이지 않는다.
- 종료·취소·실패 시 현재 입 시간표를 폐기하고 `stop()` 및 올바른 활동 복귀를 적용한다.
- 원본의 `BMOFace.version='13.0'`은 잔존 문자열이고 `getState().version='13.2'`다. 원본을 수정해서 맞추지 않는다.

### B. 기존 음성 출력 한 경로

**Python/ALSA가 유일한 대화 재생자다.** 현재 검증된 MAX98357A 경로와 Fish/Piper 합성을 재사용한다. Chromium은 얼굴과 입 이벤트만 처리한다. `playAudio/playBlob`을 동시에 호출하지 않는다.

실제 출력 WAV의 타임라인을 승인된 입 키로 매핑한다. 미리 정해진 무음 데모를 실제 음성과 무관하게 재생하거나 무작위 입 움직임으로 대체하지 않는다. 사용 가능한 입 키와 매핑은 원본의 데이터/음량 함수에 맞춰 결정한다. 원본 아트와 프레임 스타일을 유지한다.

외부 `setMouth()`는 원본의 `timelineMouth()` → `bridge()` 전환 경로를 우회한다. 원본에서 확인한 큰 개구 변화의 중간 프레임과 `round` 앞의 `tight` 전환 등을 cue 확장에 반영해 승인된 움직임을 유지한다. 원본 `_test.envelope/bridge`를 재사용할 수 있는지 실제 코드로 검토하고, 구현 위치는 adapter 내부로 제한한다. 탭 일시 중지 뒤 원본 화면 시간의 경과와 실제 재생 시간이 다를 수 있으므로 `setMouth(seconds)` 만료만으로 종료를 보장하지 않는다. visibility 복귀/재연결 때 최신 turn과 재생 상태로 재동기화하고 지난 음성을 다시 시작하지 않는다.

현재 `on_start()`는 `aplay` 프로세스 시작 직후이며 실제 가청음 시작 보장이 아니다. Python과 JS의 monotonic 시간 원점을 같다고 가정하지 않는다. 상대 시간/동기화·지연 보정 방식을 정하고 Pi에서 확인한다. 합성이 완료됐다는 이유만으로 speaking을 켜지 않는다.

### C. 실제 재생 중 취소와 종료

기존 `ConversationWorker`의 turn ID·busy guard·bounded queue·늦은 이벤트 차단을 재사용한다. 단, 현재 `safe_playback`의 시작 전 취소 검사와 `audio._run().communicate()`만으로 재생 중 즉시 중단되지는 않는다.

소유한 재생 프로세스만 실행 중 취소를 감지하고 종료·회수한다. 동일 turn의 입 타임라인을 함께 무효화한다. 앱/화면 종료, 브라우저 연결 끊김·재연결, 재생 실패 때의 정리도 정의한다. `killall`, 시스템 전체 오디오 종료, 팬/SSH/다른 서버 중지는 사용하지 않는다.

현재 `/repeat`는 새 Fish 합성을 요청할 수 있다. 이번 작업이 WAV 캐시까지 구현하지 않으면 이를 캐시 재생이라고 설명하지 않는다. Fish/Piper의 정상·실패·취소가 같은 플레이어 이벤트 계약을 거치게 한다.

### D. 통합 실행 프로필과 대화 규칙

사용자 선택을 명시하는 통합 프로필을 둔다. 기존 실행 설정/`.env` 원본을 덮어쓰지 않는다.

| 항목 | 값 |
|---|---|
| GPT | `gpt-6-luna`, `low` |
| 수동 비교/복귀 | 기존 `gpt-4.1-mini` |
| STT | 기존 `gpt-4o-mini-transcribe` |
| TTS | `fish`, 기존 Aaron reference |
| Fish | 시험한 `s2.1-pro-free`, `soft`, `beemo` |
| 게인 | PCM `0.25`, 한 번만 적용 |
| 백업 | 기존 Piper 원본, opt-in 상태와 실행기/모델 존재 확인 |

`tts_provider=openai` 및 `fish_gain=0.30` 코드 기본값을 그대로 실행해 선택이 바뀌지 않도록 프로필에서 명시한다. `0.25`를 WAV와 브라우저 양쪽에 곱하지 않는다. 무료/유료 가용성을 추측하여 엔진을 자동 변경하지 않는다.

말투 규칙은 별도 정책의 원본을 사용하고 기존 `SYSTEM_PROMPT`/언어 지침과 충돌을 제거한다. `english_voice`는 현재 CLI 옵션이 아니므로 필요하면 명시적으로 구현하고 문서화한다. 자동 API 호출로 프롬프트 시험을 대량 실행하지 않는다. 감정 생성이 아직 없으면 중립 및 수동 감정 주입으로 adapter를 검증하고, LLM 감정 자동 선택까지 완료했다고 말하지 않는다.

기존 고정 녹음/수동 시작을 먼저 유지한다. 이 단계에서 물리 GP7, 호출어, VAD, 게임, 카메라, 서보 실행을 동시에 추가하지 않는다. 이는 이후 단계의 범위로 유지한다.

## 3. 필요한 검증

먼저 오프라인 fixtures/mock로 검증한다. 테스트는 실제 위험인 상태 순서·취소·오래된 이벤트·종료·단일 출력에 집중한다. 기존 회귀는 변경 영역과 관련된 범위를 실행한다. 테스트를 위해 유료 STT/GPT/Fish를 호출하지 않는다.

| 검사 | 합격 상태 |
|---|---|
| 원본 보존 | 작업 전후 v13.2 SHA-256 동일 |
| 대화 한 턴 | idle → listening → thinking → playback 중 speaking → idle |
| 감정 유지 | crying/happy 상태의 듣기·생각에서 감정이 초기화되지 않음 |
| 입 시작·정지 | player 시작 전 닫힘, 무음/종료/취소 시 닫힘 |
| 실제 재생 취소 | 소유한 플레이어가 종료·회수되고 입 이벤트도 폐기 |
| 중복·지연 결과 | 중복 시작 거부, 취소 후 늦은 TTS/입 프레임 무시 |
| 연결·앱 종료 | browser reconnect와 종료 후 이전 turn 재생 없음, 소유 자원 정리 |
| 오류·Piper | 오류 뒤 다음 턴 가능, fallback opt-in과 영어 제약 유지, 같은 재생 이벤트 경로 |
| 설정 | Luna low/Fish/soft/beemo/.25 프로필이 실제 전달되며 기존 4.1 mini 선택 가능 |

브라우저 검사는 승인된 얼굴 자체와 adapter 전환을 보며, 기존 아트를 수정하지 않는다. Pi 실기 검사는 사용자가 통합 앱을 수동으로 실행해 마이크·스피커·화면을 확인할 때 기록한다. 테스트 로그가 없으면 실제 소리/버튼/립싱크가 검증됐다고 표시하지 않는다.

## 4. 종료 시 남길 산출물

1. 변경 파일과 실행 방법: 수동 시작, 취소, 종료, 복구를 각각 한 번에 따라 할 수 있게.
2. 현재 소스 commit, 가져온 main 문서/정책 commit, 최종 얼굴 체크섬.
3. 자동 검사 결과와 Pi 실기 미검증 항목을 구분한 짧은 보고.
4. 사용자 브랜치/원본을 보존한 reviewable diff. 관련 코드만 커밋/검토용 PR로 제시하고 main 병합·서비스 배포를 완료했다고 표시하지 않음.
5. 남은 차단 요인이 있으면 원본 위치/장치명 등 필요한 한 가지를 정확히 적기. 조사만 끝내고 구현 가능한 부분을 남겨두지 않기.

## Codex에게 보낼 짧은 지시

```text
BMO 1차 통합을 시작해.
이 작업서와 통합 기준 문서를 읽고, 현재 Pi 체크아웃·미커밋 변경·실행 프로세스를 먼저 확인해.
격리된 feature/bmo-integration-v1에서 기존 대화/Fish/Piper 코드와 승인 얼굴 v13.2를 연결해.
Python/ALSA 단일 재생, 원본 얼굴 보존, 실제 재생 중 취소, 상태/감정/입 이벤트 분리를 구현해.
Luna low·Aaron Soft·Beemo·PCM gain 0.25를 통합 프로필로 명시하고 기존 4.1 mini 실행을 보존해.
오프라인 검증까지 진행하고, 변경 파일·수동 실행 방법·실물 미검증 항목을 보고해.
이번 첫 단계의 상세 경계와 합격 기준은 CODEX_INTEGRATION_STAGE1_KR.md를 따라.
```
