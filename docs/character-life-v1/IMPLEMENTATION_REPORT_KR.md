# BMO Character Life v1 구현·검증 기록

**후속 갱신(2026-10-29):** 사용자 권한 갱신으로 실제 마이크·덮인 카메라·짧은 로컬 시험음·비구동 B1 HELLO 검수를 수행했다. 아래 초기 단계의 장치 `NOT_RUN`은 당시 기록이다. 최신 실제 결과·발견한 오류 수정·현장 명령은 [실제 장치 검수 보고서](DEVICE_ACCEPTANCE_20261029_KR.md)를 따른다. 기존 실행본·서비스·펌웨어 및 모터 구동 금지 조건은 유지한다.

## 작업 기준과 보존

Issue #39, PR #40의 최신 준비 커밋 `83b1c1479e0cc01fa678c5bea031ec7caba31246`에서 `/home/bgh/BMO_CHARACTER_LIFE` 별도 worktree로 이어간다. Pi Codex 단독 구현 인수를 두 스레드에 기록했다. Cloud 재설정이나 중복 구현 요청은 하지 않았다.

기존 진단 댓글: https://github.com/GUNOBAE/BGH_BMO/issues/39#issuecomment-6469479607

진단 당시 실제 실행본 소스는 `/home/bgh/BMO_REAL_ROBOT`, HEAD `0f46e3203afcc0d2bb7455cf49b3ac7ba0d4e978`이었다. 후보 브랜치는 더 새로운 통합 기반 `0933d9f75b533f3c0c4d285de7b942c201ea3cba` 위에 있으므로, 아직 배포하지 않은 후보 코드가 기존 증상을 일으켰다고 판단하지 않는다. 마지막 저장된 실행 옵션은 `.venv-camera/bin/python`, `--live --windowed --ptt-microphone --ptt-trace`이며 현재 프로세스 실행 증거를 뜻하지 않는다.

실행본·가상환경·대화 프로세스·원본 R10·펌웨어·보정값·백업·서비스·OS는 변경하지 않는다. 후보 코드 자동 배포, 실물 장치/유료 API 호출, main 병합은 수행하지 않는다.

## 1단계: 대화 대기와 감정 전달

- `--character-life` opt-in으로 기존 한 개의 GPT 응답에 allowlist CharacterPlan을 포함한다. 별도 감정 분류 API나 두 번째 brain은 없다.
- 명시적인 화내기/울기 연기와 실제 상실·고민의 표현을 구별한다. 잘못 축소된 angry/crying을 명시적 요청에서 복구하고 실제 고민에서는 과한 웃음/축하/몸짓을 억제한다.
- 얼굴·음성 delivery·시선·짧은 반응·기존 팔 제스처를 한 계획으로 조정한다. 반응음과 몸짓을 무조건 겹치지 않는다. 원본 얼굴은 외부 API로만 제어하며 listening/thinking 동안 이전 감정, 실제 player 시작 시 전환, repeat/cancel/수동 override를 보존한다.
- GPT-6 Luna low, Aaron ID, 기본 S2.1 Pro Free, Beemo 발음, gain 0.25, 기존 Piper와 팔 통신/보정은 유지한다. Drama3는 명시적인 별도 샘플 선택만 지원한다. 강한 연기에서는 검증된 host delivery가 상충하는 고정 Soft 접두사를 대체하며 `--plain-voice`를 존중한다. 실제 음질·연기 강도는 청취 미승인이다.
- 기본 speech-safe PTT의 고정 threshold를 쓰는 경로는 사용하지 않던 calibration 대기를 생략한다. 기본 silence 0.9초/threshold 0.008은 유지한다. 첫 음절 보존을 위해 최대 0.4초 preroll을 남긴다. `baseline(.9)/balanced(.6)/quick(.45)` 비교는 명시적인 옵션이며 성능 개선 실측값은 없다.
- host 입력 수신, arecord pipe 준비, 첫 PCM, 마지막 voiced frame, VAD 닫힘, capture 정리, STT 실제 요청, GPT, Fish 첫 HTTP byte/다운로드/오디오 준비, Python player 시작을 분리한다. 물리 버튼 시점·실제 마이크 준비·스피커 첫 소리는 근거가 없으면 `UNMEASURED`이다. 합성/Piper 경로에 Fish 완료를 만들어 넣지 않는다.

진단에서 재사용한 baseline은 calibration 0.4s, silence 0.9s, min speech 0.24s, no speech 3s, max 12s, 16kHz mono S16/40ms이다. 이전 GPT-only 벤치마크의 지연은 현재 PTT 전체 지연으로 재사용하지 않는다. 새 계측은 다음 감독 실행의 근거를 남기기 위한 코드이며 실제 성능 발표가 아니다.

1단계 검증: Python 3.13.5에서 기존 전체 회귀와 1단계 신규 검사를 합쳐 **1357개 PASS (80.264초)**. 아직 별도 개발 중인 bank/presence/wake 모듈 검사는 이 커밋의 수에 넣지 않았다. `checks/check_character_life_browser.py`의 production Desktop/Host/session/director/voice/face API 경로 **5개 검사 PASS**. 입력 PCM·STT·GPT·player 경계만 합성으로 대체했고 실제 키·유료 API·장치는 차단했다. `git diff --check` PASS. 브라우저는 공개 합성 얼굴 계약 fixture로 먼저 검증했고, 원본 R10 headless는 후속 단계에 기록한다. 최종 HEAD와 Python 3.11/3.13 CI는 단계별 PR #40 댓글 및 최종 인계에 기록한다. 장치 없는 Python/브라우저 PASS와 실제 Pi 동작 PASS를 구별한다.

## 상태 구분

- 대화/감정 코드: IMPLEMENTED, 오프라인 회귀·브라우저 검증 후 제출.
- 실제 대화 단계별 지연: UNMEASURED.
- 신규 캐시 음성 생성: NOT_RUN / AUDIO_NOT_GENERATED.
- Aaron 강한 감정 연기 청취: NOT_RUN / LISTENING_NOT_APPROVED.
- 호출어 실제 모델: MODEL_MISSING, 실제 마이크 수신·정확도 NOT_RUN.
- 팔/카메라/USB/스피커 실기: NOT_RUN. 기존 성공 결과를 재시험하거나 변경하지 않는다.

## 2단계: 실제 Desktop 연결

- 영어 `Starting BMO… / Getting ready… / Ready!`를 기존 실제 checkpoint 진행률에 연결했다. wrapper만 550ms 아래→위로 등장시키고, 표시 확인 후 Python 캐시 인사를 시작한다. reduced-motion을 존중하고 같은 실행의 reload에서는 slide/인사를 반복하지 않는다. 원본 HTML/SVG 바이트를 수정하지 않았다.
- 32개 후보의 `VoiceBank`/`BankPlayer`는 기존 private GreetingCache 번들·SHA·길이·lip cue·모델별 청취 승인을 재사용한다. 최근 인사 3개, clip cooldown, 누락/미승인/손상 fallback을 구현했다. 실제 음성 자산은 새로 만들지 않았다. 기존 승인 greeting이 있으면 그대로 쓰며 없으면 API 없이 얼굴과 대화를 계속 사용할 수 있다.
- 짧은 반응 캐시는 검증된 CharacterPlan과 기존 반응 쿼터에서 허용됐을 때만 선택한다. 승인된 동일 PCM 형식의 gained 반응+답변을 하나의 WAV/player로 연결하며 gain을 다시 적용하지 않는다. PCM이 다르면 반응을 생략하고 두 번째 Fish 요청을 하지 않는다. repeat는 같은 결과 WAV를 재사용한다.
- idle 발화는 기본 OFF다. opt-in 시 3–7분 후보·15분당 최대 2회·clip별 30분 cooldown을 적용하며, 질문·게임·영상·다른 재생·최근 민감한 대화·angry/crying·quiet/sleep에서 억제한다. 작은 idle 시선은 기존 깜빡임과 GazeArbiter를 재사용하고 카메라/수동/native/대화 시선에 양보한다. sleep은 캐릭터 정책으로 OS 절전 명령이 아니다.
- boot/idle 중 GP8과 질문을 우선한다. cached worker의 취소·join·AudioFocus 반환을 확인한 뒤 기존 한 개의 PTT worker/brain을 실행한다. 첫 face-presented 전의 입력과 연타를 처리하고, timeout 후 안전하게 정리된 idle은 ready로 복귀한다. idle은 대화 history/repeat WAV를 쓰지 않는다.
- 선택적 로컬 detector와 단일 MicrophoneBroker를 기존 finite PTT에 연결했다. 기본 OFF이고 명시적인 `--wake-word`에서만 준비 후 활성화를 시도한다. 승인 custom model이 없으면 `MODEL_MISSING`이고 실제 source를 생성하지 않는다. READY 자산이 있을 경우에도 단일 reader/preroll·입력 중복 방지·자기 음성 억제·오래된 wake TTL·OFF/종료 정리를 적용한다. worker 지연 중 preroll을 잃던 경합도 장치 없는 지연 fixture로 검증했다.
- 설치 계획은 Character Life 옵션을 보존하지만 실제 바탕화면 설치·autostart 변경은 하지 않았다. 기존 카메라·B1 통신·수동 GP7·게임·음악 구현을 재사용하며 새 게임이나 보정·펌웨어 변경을 하지 않았다.

도구와 미설치 조건: [음성팩 도구](VOICE_BANK_TOOL_KR.md), [호출어 모델 계약](WAKE_WORD_SETUP_KR.md), [24개 인수 시나리오 상태](ACCEPTANCE_STATUS_KR.md).

## 검증 근거와 한계

1단계 코드 커밋 `6dabda2c34bb441f06e148f0ea10dfe8b4dd06c8`의 Python 3.11/3.13 및 public browser CI는 [40696333241](https://github.com/GUNOBAE/BGH_BMO/actions/runs/40696333241)에서 성공했다. 이 결과를 후속 HEAD 결과로 대신 쓰지 않는다. 최종 통과 수·최종 SHA·해당 CI는 아래 및 PR #40 댓글에서 확정한다.

최종 후보의 Python 3.13.5 전체 회귀 **1442개 PASS (86.615초)**, `pip check`와 `compileall` 및 `git diff --check` PASS다. 기존 shell/Desktop/PTT/media/release browser 5종의 **42개 검사 그룹**, 새 Character Life 공개 fixture/R10/v13.2 **각 9개**를 순차 실행하여 총 **8회 실행·69개 검사 그룹 PASS**였다. 이는 고유 시나리오 69개라는 뜻이 아니며, 같은 Character Life 시나리오를 3개 얼굴 profile에서 반복 검증한 수다.

원본 R10 SHA는 `8c3ef98ba0e0645213c95295a24d7353fc2865b5ad9bc85390360c4ec9b78c82`, v13.2는 `38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`이며 검사 전후 일치했다. 실제 product Desktop·Host·session·외부 얼굴 어댑터를 실행하되 PCM/STT/GPT/player/브라우저 child 경계는 합성이며 실제 Fish/마이크/스피커 증거가 아니다.

공식 공개 Fish 문서의 모델/감정 기능/가격 확인은 2026-10-28에 읽기 전용으로 수행했다. 생성 도구 기본 8개 A/B 후보의 792 UTF-8 bytes, USD 0.019800은 공개 정가 상한 미리보기이며 청구 실측이 아니다. 생성 직전 공식 문서를 다시 검증하고 별도 `--generate`와 양수 지출 상한이 있어야 키 접근이 가능하다. 실제 유료 요청/신규 WAV 생성/청취는 모두 NOT_RUN이다.

승인 custom Hey BMO 모델과 `openwakeword`/`onnxruntime`/`numpy`는 이 격리 환경에 설치하지 않았다. 모델 SHA·라이선스·frontend·shape 계약 및 합성 경합 PASS는 세 구문의 실제 인식 정확도 PASS가 아니다. 실제 마이크를 상시 켜지 않았다.

## 사용자가 감독할 후보 실행과 복귀

자동 실행하지 않았다. 기존 BMO와 동시에 실행하지 말고, 사용자가 기존 앱을 정상 종료한 후 아래 후보 **한 명령**으로 먼저 검수한다. 팔·카메라·호출어 상시 듣기는 이 명령에 넣지 않았다. 새 음성팩이 없으면 기존 승인 인사/침묵이며, 대화 호출은 기존 유료 대화 경로를 사용하므로 사용자 감독 실행에 해당한다.

```sh
cd /home/bgh/BMO_CHARACTER_LIFE && BMO_PYTHON="$PWD/.venv/bin/python" ./launch-desktop --live --character-life --ptt-microphone --ptt-trace --idle-voice
```

최소 검수는 (1) 부팅 진행률/인사와 GP8 중단, (2) 첫 음절·짧은 머뭇거림·문장 끝·연타/취소, (3) 장난스러운 화내기/울기와 실제 고민에 대한 얼굴·Aaron 음질, (4) repeat·listen/think 동안 감정 유지이다. 준비 상태 메뉴의 bounded trace에서 실제 단계별 값을 수집하되 마이크 준비·스피커 첫 소리는 별도 관측 없이는 계속 미측정이다. silence 비교는 baseline 안정성을 확인한 뒤에만 명시적인 balanced/quick 선택으로 진행한다. 음성팩 A/B 청취와 호출어 실기에는 각각 별도 생성/지출 승인, 실제 모델과 명시적 마이크 opt-in이 필요하다.

문제가 있으면 후보 앱의 숨김 메뉴에서 **종료**하고 `/home/bgh/BMO_REAL_ROBOT`의 기존 launcher와 `.venv-camera` 실행 옵션으로 돌아간다. 해당 checkout·가상환경·기존 캐시를 바꾸지 않았으므로 Git reset/원본 복원/서비스 재시작이 필요하지 않다. 후보 worktree/브랜치만 유지하여 검토할 수 있고 main에는 병합하지 않았다.
