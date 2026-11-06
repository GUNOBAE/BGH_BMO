# PR #40 현장 회귀 수정·인계 — 2026-10-30

기준은 [사용자 현장 피드백과 상세 수정 지시](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6522786371)다. `/home/bgh/BMO_CHARACTER_LIFE`, `feature/bmo-character-life-v1`, 같은 Draft PR #40에서 이어갔다. 초기 local HEAD `cbcba1f`에 미커밋 변경이 없음을 확인하고 `git fetch` 후 **fast-forward만** 수행해 ChatGPT의 `d6e3a6f` → `ec293db` → **`1c726fe1aa27cb4280ad57e2b4e4a09b2673353c`**를 인수했다. reset·force push·새 PR·main 병합·운영본 업데이트는 하지 않았다. 정확한 제출 SHA와 최종 HEAD CI는 PR 완료 댓글에 남긴다.

## 1. 현장 PASS를 보존한 범위

사용자가 확인한 GP8 대화 종료 감지, 발화 종료 뒤 thinking, thinking 중 GP8 취소, 실제 고민에 대한 sad 위로, 영어 loading은 사용자 관찰 PASS로 재사용했다. 이전 마이크·카메라·스피커·B1 조회·팔 보정을 다시 검사하지 않았다. 새 실제 녹음·촬영·스피커 재생·serial/GPIO/서보 조작·펌웨어 변경·OS/서비스 변경·유료 API 호출은 모두 0회다. 기존 `BMO_REAL_ROBOT` 코드·가상환경·프로세스를 변경하거나 종료하지 않았다.

별도 채팅의 음성팩·voice cache·승인 상태에 쓰지 않았다. 실제 생성·덮어쓰기·일괄 승인·모델 변경·provider 자동 전환도 없다. 회귀에서 생성/승인한 PCM과 번들은 매번 사라지는 합성 테스트 폴더만 사용했다. 검수 PNG는 Git 외부 private 폴더에만 보관한다.

## 2. 실제 표정 전달 경로

인수한 matcher를 재작성하지 않았다. `acting_request.py`의 현장 문구·부정·인용·일반 설명 구분과 sad/crying 분리를 유지했다. 추가로 모델이 정상 happy/sad/surprised를 잘못 `source=acting`으로 표시했을 때에도 절제된 자연 감정은 살리고, 연기 권한·강도·부적절한 reaction만 제거한다. 명시적 요청 없는 theatrical angry/crying 권한을 모델 metadata만으로 부여하지 않는다. 진지한 위로의 comic sob/laugh/팔 억제도 유지한다.

`FeatureResponses.last_resolution`과 `IntegrationSession.expression_trace`는 **requested/model/resolved/emitted emotion**, resolved source·검증 상태·turn·실제 player-start 여부·repeat 여부만 노출한다. 요청 원문·STT 원문·speech·키·provider 응답을 저장하지 않는다. 준비 상태에서 emitted emotion을 미리 성공 처리하지 않고, 기존 owned Python player의 실제 `on_start` 경계에서 갱신한다. Host의 `character_life.expression_trace`, 실제 session `/api/state`, 외부 adapter와 R10 상태를 비교한다.

새 `check_field_expression_browser.py`는 production FeatureClient → CharacterPlan → IntegrationSession → 기존 player-start → 외부 face adapter → 승인 R10의 `BMOFace.getState().selectedEmotion` **및 실제 렌더 `emotion`**을 검사한다. provider·PCM·player IO만 합성 경계이며 paid API/실제 스피커 성공으로 표시하지 않는다.

| 입력 유형 | requested | fixture model | resolved / emitted / R10 |
|---|---|---|---|
| `express a mad or angry?` | angry | neutral/acting | angry |
| 다음 일반 사실 질문 | 없음 | angry/권한 없음 | neutral |
| `슬픈척 해봐` | sad | neutral/acting | sad, soft_sad |
| 진지한 상실·위로 | 없음 | happy/chuckle | sad, comic reaction/gesture 없음 |
| 자연스러운 좋은 소식 | 없음 | happy/잘못된 acting label | restrained happy/dialogue |
| 우는 연기 | crying | neutral | crying |
| 취소 후 다음 일반 질문 | 없음 | neutral | neutral |

crying은 listening/thinking에도 유지한다. cancel 후 늦은 결과는 버리고, repeat는 마지막 실제 성공 음성의 표정과 trace를 재사용하며 새 provider/synthesis/제스처를 실행하지 않는다. 취소된 후보 trace가 이전 repeat용 trace를 덮는 오류도 검사했다. `/emotion` 같은 직접 표정 조작을 음성 반응 실행 성공으로 해석하지 않는다.

## 3. Wake 확인과 새 질문 청취 분리

이전 broker의 `_acquire()`는 trigger 전 ring을 일반 GP8 source에 재생했다. 호출어 자체가 min speech를 채우면 새 질문 없이 STT→thinking으로 갈 수 있었다. trigger frame만 자르는 초안도 검토 과정에서 거부했다. **trigger 뒤에 남은 voiced keyword tail도 질문으로 오인**할 수 있기 때문이다.

원본 commit `1c726fe`의 broker 소스를 `git show`로 읽어 별도 namespace에서 실행한 오프라인 비교도 남겼다. 같은 주입 PCM/trigger에서 기존 recorder 결과는 `ready/silence`, keyword samples 포함, 질문 PCM 42240 bytes였다. 후보의 wake-specific 경로는 `cancelled/no_speech`, keyword samples 없음, 질문 PCM 0 bytes였다. 양쪽 최대 reader는 1개였고 장치/API를 사용하지 않았다. 이 비교는 실제 사람의 Hey BMO 음향 인식률 검사가 아니라 기존 ring 재생 오류를 production broker/recorder 코드로 재현한 증거다. 로컬 집계는 `/tmp/bmo-field-wake-legacy-reproduction.json`이다.

수정은 기존 단일 `MicrophoneBroker`/PttRecorder/session 경로 안에 있다.

- 수동 GP8는 기존 ring/preroll, threshold, calibration, silence, no-speech, 종료·취소·debounce를 유지한다. wake는 버튼 press로 계산하지 않아 바로 뒤 GP8가 가짜 중복 입력으로 소거되지 않는다.
- wake-specific cursor는 trigger 이전 ring을 재생하지 않는다. 이후 PCM은 별도 bounded buffer로 보관한다. 동등한 timestamp도 순서가 유효하면 보존한다.
- 승인 ACK가 없을 때는 `I heard you…`와 기존 face listening을 사용한다. post-trigger keyword tail은 **로컬 WebRTC의 0.12초 연속 quiet evidence**가 관측될 때까지 질문 밖에 둔다. 이는 매번 실행하는 sleep이 아니라 실제 PCM 경계 검사다. 정상적으로 관측된 경계 뒤 첫 PCM은 새 질문으로 보존한다.
- 새 질문 자체도 wake-specific WebRTC VAD를 사용한다. 수동 GP8의 설정을 바꾸거나 fan/OS gain/threshold를 올리지 않는다. wake의 speech-start 대기는 기본 **5초**, `--wake-question-wait 1..10`으로만 조절한다. 일반 GP8의 no-speech 3초와 silence 0.9초는 그대로다.
- 질문 speech-start 전에 일반 silence-end가 실행돼 질문을 만들어내지 않는다. 1/3초에는 listening, 5초 무질문에서는 정상 `wake_no_question`/idle이며 STT/GPT/TTS 0회다. 새 질문이 시작되면 기존 VAD와 한 번의 STT/brain/voice를 사용한다.
- 유효한 새 질문 buffer에 GP8의 오래된 preroll 폐기 규칙을 적용하지 않는다. worker가 0.7초 늦어져도 전체 bounded 새 질문과 첫 단어 bytes를 보존한다. 한도 초과는 부분 첫 단어 폐기가 아닌 whole handoff 오류로 처리한다.
- 이미 후속 발화가 도착했으면 ACK를 생략한다. ACK 직전에도 같은 lock에서 재검사한다. repeat·취소·동시 GP8·wake OFF는 단일 mic owner를 유지하며 OFF 후 finite GP8가 계속 가능하다.

### 명확한 한계: 무음 경계 없는 완전 연속 음성

현 ONNX는 keyword 확률을 제공하며 phoneme/word 종료 시각을 제공하지 않는다. 0.12초는 WebRTC의 unvoiced evidence 누적값이며, detector hangover 때문에 필요한 실제 쉼은 더 길 수 있다. **분리 가능한 quiet edge도 없고 승인 ACK 완료 경계도 없는 zero-gap `Hey BMO+질문`을 완전히 분리해 지원한다고 주장하지 않는다.** 무간격 또는 너무 짧은 쉼의 후속 질문은 보존을 보장하지 않는다. 이 모호한 구간은 keyword tail인지 새 질문인지 추정해 cloud에 보내지 않고 quarantine/무질문 종료한다. 이런 입력은 시각적 `I’m listening. What’s up?` 뒤 질문하거나 GP8를 사용해야 한다. 이 제약은 실제 사람 발화 정확도 미측정과 별개인 현재 보수적 동작이다.

공개 C WebRTC/public PCM 브라우저 검사는 한 호흡의 **관측 가능한 짧은 comma/quiet 경계** 뒤 질문과 첫 reference 전체 bytes 보존을 검사한다. 이를 zero-gap phoneme alignment 또는 사람의 첫 음절 인수 PASS로 바꾸지 않는다. 새 caller/model을 도입하거나 유료 STT로 keyword-only 입력을 검사해 이 한계를 숨기지 않았다.

### 승인 ACK의 공유 계약

별도 제작자와 공유할 계약은 [WAKE_ACK_ASSET_CONTRACT_KR.md](WAKE_ACK_ASSET_CONTRACT_KR.md)다. `wake_ack_01`, 정확한 대본 `Yeah? What's up?`, private `~/.cache/bmo/wake-ack-v1`의 approved GreetingCache 번들이다. 기존 Aaron·Beemo·gain·config model fingerprint를 검증한다. 이번 작업은 read-only consumer이며 파일 생성·덮어쓰기·승인·TTS 경로가 없다. 실제 ACK는 아직 미준비/미승인일 수 있으며 자동 생성하지 않는다.

승인 ACK가 들어오면 **기존 `session.voice._play` 하나**와 AudioFocus를 사용한다. 자체 mic reader를 닫은 뒤 재생하고, 실제 재생 종료 후 새 reader/질문 경계를 시작한다. echo 억제를 끄지 않는다. 종료가 확인된 선택적 ACK의 장치 오류는 visual fallback으로 진행하고 재시도하지 않는다. cleanup failure는 fail closed한다. ACK는 dialogue history/repeat cache/표정/질문 첫 재생 시각을 덮지 않는다. resume 오류도 voice lock을 반드시 해제한다. 미래 ACK 경로는 마지막 발화 판정과 실제 reader 종료 사이에 새 발화가 들어오는 좁은 경합 구간이 있으며 그 첫 음절 보존을 보장하지 않는다. 현재 ACK 미제공의 visual 경로에는 ACK를 위한 reader 종료 구간이 없다. 이 ACK 실물 음향 품질과 barge-in은 이번에 재생하지 않았다.

## 4. 배경 고정과 표정 레이어만 등장

원본 R10 HTML/SVG bytes와 SHA `8c3ef98ba0e0645213c95295a24d7353fc2865b5ad9bc85390360c4ec9b78c82`를 보존했다. 승인 원본의 배경 fill **`#CBFAD3`를 그대로** 유지했다. 사용자의 색 표현을 근거로 재색칠하지 않았다.

외부 adapter가 실제 R10 `#lookRoot` 주위에 일시적인 SVG group만 넣어 눈·입·눈썹·눈물을 아래→위 550ms로 이동시킨다. `main`/iframe/body/SVG/viewBox/배경은 이동하지 않는다. native `lookRoot`/`eyeOffset` gaze transform과 좌표·path는 덮지 않는다. 완료·취소·visibility·reduced-motion·exit에서 원래 부모와 순서를 복원한다. reload 때 entry/인사를 반복하지 않는다. SVG setup/getBBox 실패도 DOM을 복구하고, iframe reload 뒤 이전 promise의 늦은 `face_presented` ACK는 document/adapter/handshake/generation으로 차단한다.

새 실제 R10 headless 검사는 start/middle/end PNG와 geometry를 검사했다. 표정 Y는 `586.807 → 140.609 → 106.807`, 배경/main/iframe/SVG/viewBox는 동일하고 gaze 속성도 일치했다. 실제 원본 렌더 PNG를 직접 확인했다. 공개 CI fixture에는 실제 작화가 없으므로 `artwork_unavailable`/움직임 없음 계약만 검사하며 실제 R10 PASS로 꾸미지 않는다.

실제 SVG 증거는 Git 밖 private 폴더 `/home/bgh/.local/state/bmo/field-entrance/` 아래 날짜별 폴더의 `report.json`, `start.png`, `middle.png`, `end.png`에만 남긴다. 실제 LCD의 체감 속도 판단은 사용자가 한다.

## 5. 부팅 원음: 설치만 보류

관련 전달 경로에서 `BMO_Boot_Reference_20261030.zip`, `bmo_boot_reference.wav`, 기존 custom override를 찾지 못했다. **ASSET_NOT_TRANSFERRED**로 구분하며 영상·WAV를 공개 Git에서 구하려고 하지 않았다. 기대 원음은 mono PCM16/48kHz/1.28초, SHA `ba72dce47853da5a3da5df9e0e65e2b7f9c147720b6653c2ac24845ea1f26082`다. 실제 파일이 없으므로 해시 대조·`~/.config/bmo/startup_chime.wav` 설치·청취는 미실행이다.

기존 startup sound 경로를 재사용한다. Character Life에서 custom 파일이 없으면 **기존 합성 3음을 다시 쓰거나 생성하지 않고 조용히 생략**, 영어 인사와 loading은 유지한다. `startup_chime_status=ASSET_NOT_TRANSFERRED`를 노출한다. legacy 호출자의 기본 동작과 기존 캐시는 보존했다. 사용자 원음이 도착하면 같은 기존 override를 읽으며 pitch/길이/정규화/gain을 추가하지 않는다. 테스트는 가짜 48kHz/1.28초 WAV의 bytes 보존과 기존 한 player/once-only 경로만 검사했다.

## 6. 대화 품질 비교와 변경하지 않은 정책

[비교 보고서](FIELD_PROMPT_COMPARISON_20261030_KR.md)에 성공본/후보의 소스와 순수 두 턴 요청 캡처를 남겼다. Luna low, output budget 320, 8턴 history, 입력 4000/history 16000자, 승인 prompt·Fish/Aaron·gain은 동일하다. v3 instructions/schema overhead와 잘못된 metadata fallback은 실제 차이다. 이름만 같다고 모든 prompt가 같다고 주장하지 않았다. 실제 paid usage·incomplete 발생률·발화 품질을 측정하지 않았으므로 “모델이 구려짐” 원인을 단정하거나 모델/예산을 올리지 않았다. 별도 감정 API·두 번째 대화 엔진도 없다.

## 7. 검증 기록과 수정 파일

최종 전체 Python·공개 브라우저·R10 회귀 수, 해당 HEAD의 Python 3.11/3.13 및 CI 링크는 제출 전 아래에 기록하고 PR 완료 댓글에 exact SHA로 연결한다. 이전 `1c726fe` 또는 `cbcba1f`의 녹색 CI를 새 HEAD의 결과로 재사용하지 않는다.

로컬 최종 Python 3.13.5 전체 **1556개 PASS / 88.998초**다. 최초 전체 1555개 PASS 뒤, simulation이 별도 사용자 음성팩 ACK 경로를 읽지 않고 Host 전용 임시 경로만 사용하도록 격리 회귀 1개를 추가해 전체를 재검증했다. 기존 GP8/마이크 목표 회귀 61개와 기존 wake 27개도 통과했다. 새 wake handoff 목표는 13개로 실제 C WebRTC/public reference tail 배제, 1/3/5초 무질문, queue 지연·첫 단어, ACK/취소/OFF/launcher·격리를 포함한다.

Playwright 전체 묶음은 **공개 fixture 9회 + 승인 R10 5회 = 14회·122개 검사 그룹 PASS**다. 기존 shell/Desktop/RMS/WebRTC/Character/media/release를 보존하고 field expression/wake를 추가했다. R10은 Character, WebRTC PTT, field emotion, field wake, expression-layer entrance의 실제 원본 DOM을 검사했다. R10 그림 없는 CI에는 공개 계약만 사용한다. 마지막 ACK 경로 격리 변경 후 **R10 wake 7개 그룹도 별도로 재확인 PASS**했다. 기본 격리환경 `pip check`, AST 271개 Python 파일, `git diff --check`도 통과했다. 실물 장치·LLM 음질/인식 성능·LCD 체감 검사를 포함한 수치가 아니다.

초기 제출 `e2d786b`의 PR CI는 세 job 모두 성공했지만 push CI의 기존 Desktop repeat 검사가 실패했다. audio-ended 공개 시점과 worker-finished/AudioFocus 해제 사이에 반복 명령을 보낼 수 있는 검사 경합을 확인했다. 기존 repeat는 busy 상태를 거부하므로, 캐시가 보인다는 이유만으로 worker 완료를 가정하면 안 된다. busy 거부·캐시·재생 정책을 바꾸지 않고, 검사에서 실제 worker 완료와 cache 보존을 확인한 뒤 repeat를 요청하도록 수정했다. 수정한 Desktop 검사는 **공개 fixture 7그룹·실제 R10 7그룹 PASS**다. 최종 추가 커밋의 CI를 다시 확인하며 초기 push 실패를 숨기지 않는다.

- 표정: `character_director.py`, `features/reply.py`, `integration_session.py`, Host trace 노출, 신규 field flow/실제 제품 브라우저 검사.
- wake: `features/host.py`, `input/microphone_owner.py`, `input/microphone.py`, `wake_ack.py`, CLI/launcher wait 옵션, 신규 handoff/cache 검사와 제품 브라우저 검사. 기존 wake 양성 fixture의 DC 상수는 실제 C VAD가 사용할 수 있는 공개 reference로 교체하고 같은 one-reader/one-turn assertion을 유지했다.
- 등장: `desktop_web/desktop.js`, `integration_web/adapter.js`, 기존 character browser assertion과 신규 실제 R10 geometry/reload/error 검사.
- 부팅: `startup_sound.py`, `greeting.py`, Desktop/Runtime 정책 연결과 새 asset-preservation 검사.
- CI: 기존 모든 검사를 유지하고 public field-expression, field-wake 브라우저 검사를 추가했다. 실제 R10 파일은 CI에 복사/게시하지 않는다.

## 최종 후보 실행과 최소 재검수

아래는 **사용자가 직접 실행할 명령**이며 이번 자동 작업에서는 후보를 실물 장치로 실행하지 않았다. 기존 앱이 장치를 쓰면 그 앱의 정상 종료 메뉴로 먼저 종료한다. 다른 프로세스/서비스를 종료하거나 장치를 강탈하지 않는다. 팔·카메라 활성화 옵션은 없다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE/software && ../.venv-device/bin/python -B -m bmo_ai.desktop_app --live --frontend chromium --screen-profile r10 --character-life --ptt-microphone --ptt-vad-mode webrtc --ptt-trace --wake-word --wake-question-wait 5 --wake-model-manifest /home/bgh/.local/state/bmo/wake-model-candidates/amrmantawi-bmo-openwakeword-58eb9404df18/manifest.json
```

긴 장치 검사표를 다시 요구하지 않는다. 실패했던 세 동작만 확인한다.

1. `express a mad or angry?`, `슬픈척 해봐`: 음성 시작과 실제 angry/sad 얼굴 일치, 다음 일반 사실 질문 neutral. 필요하면 원문 없는 expression trace 다섯 단계를 비교한다.
2. Hey BMO 뒤 잠시 기다리기: 질문 없는 thinking 없이 listening/idle. 시각적 새 질문 안내 뒤 말해 첫 음절·GP8 종료·thinking 취소를 확인한다. zero-gap 분리는 위 한계대로 인수하지 않았다.
3. 부팅 등장: 기존 배경은 고정, 눈·입 등만 아래→위로 등장, 영어 loading 유지. 원음 미전달 상태에서는 합성 3음 대신 조용히 생략한다.

복귀는 후보를 정상 종료한 뒤 기존 `/home/bgh/BMO_REAL_ROBOT`의 승인 런처를 사용한다. 원본을 교체하지 않았으므로 배포 rollback이나 firmware 복원은 필요 없다. 신규 private 자산/음성팩/기존 백업을 삭제하지 않는다.
