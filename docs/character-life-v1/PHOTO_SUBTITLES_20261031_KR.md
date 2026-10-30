# 현재 요청 해석·사진 review·응답 자막 — 2026-10-31

PR #40의 [새 사용자 지시](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6527145621)를 적용한다. 기존 사진 촬영 직후 자동 복귀 지시는 이 문서의 명시적 review 복귀로 대체한다.

## 사용자 실기와 Cloud 검증의 구분

- 사용자 확인: GP8 초기/메뉴 복귀/얼굴 클릭 후 대화, thinking 취소와 새 대화, 사실 질문의 기본 표정 복귀. 현재 부팅음은 승인되어 변경하지 않았다.
- 사용자가 한국어로 보고한 화남 요청도 실제 BMO에는 영어로 말한 것이었다. 이를 한국어 화남 실기 PASS로 취급하지 않는다. 정확한 영어 `Get angry`에서 neutral/약한 억양이 관찰됐으나 실제 STT·모델 메타데이터·런타임 설정은 Cloud에서 확인하지 못했다.
- 영어 사진 요청 실패와 한국어 요청의 촬영 관찰은 기존 후보의 보고다. 저장 파일·새 review UX까지 승인됐다는 뜻이 아니다.
- Alt+Tab/VNC 이탈, 실제 영어 의미 이해·분노 억양·번역 정확도·촬영 품질은 자동 검사 PASS로 대체하지 않는다.

## 한 응답의 의미 해석

Character Life는 기존 한 번의 Luna low 응답을 V4 구조로 받는다. 현재 요청의 photo/acting/subtitles 의미, 대사, CharacterPlan, 필요한 번역, 성공 후 대사를 함께 준비한다. 문장 allowlist miss만으로 의미를 지우지 않으며 추가 분류/번역 GPT 호출은 없다. 기존 output 320·history 설정도 그대로다.

응답마다 새 turn token을 검증한다. 메타데이터는 해석일 뿐 장치 권한이 아니다. 현재 턴/generation, 카메라 opt-in, 명시적 부정·인용·기능 질문·과거·미래의 보수적 차단, 취소, once-only journal을 유지한다. 모델만의 camera intent는 허가되지 않는다. 확인이 필요하면 짧은 질문 한 번과 45초짜리 호스트 확인 상태를 만들며, 바로 다음 턴에서만 확인 토큰을 소비한다. 만료되거나 취소된 요청을 Yes로 부활시키지 않는다. 팔의 감독/admission 조건은 바꾸지 않는다.

caption 누락/부정합은 해당 번역만 숨긴다. 전체 응답이 토큰 한도로 incomplete이거나 구조 자체가 잘못되면 실행 권한을 만들지 않고 기존 실패 경로를 따른다. 한도를 몰래 높이거나 재분류 요청으로 보정하지 않는다.

## 사진의 실제 실행 경로

`요청 → 안전한 준비 대사 완료 → 기존 CameraBroker live preview → 표시 ACK → 5·4·3·2·1 → 한 장 저장 → 저장한 PNG review → 성공 대사 → Back to BMO/GP8`

- 준비 시간은 5초에 포함되지 않는다. 현재 요청/generation의 유효한 preview가 실제 image로 decode되고 표시된 뒤에만 부모 UI가 표시 ACK를 보낸다.
- 저장 결과와 읽을 수 있는 실제 PNG가 확인된 경우에만 성공 대사를 재생한다. 별도 성공 대사는 동일 응답의 `photo_success`에서 준비하며, 기존 한 worker/player로 재생한다. 성공마다 Fish TTS가 한 번 추가될 수 있고 GPT 왕복은 추가하지 않는다. 별도 음성팩은 건드리지 않는다.
- review는 live preview가 아니다. 센서를 종료하고 저장 이미지를 유지한다. 타이머로 복귀하지 않는다. Back/GP8가 성공 음성을 취소·정리하고 얼굴로 돌아간 뒤 기존 tracking을 복구한다. 같은 Enter의 repeat/keyup은 새 PTT를 시작하지 않는다.
- 취소·실패·저장 이미지 읽기 실패·늦은 결과에는 성공 음성이나 자동 재시도가 없다. repeat는 마지막 성공 음성과 대응 자막만 반복하며 촬영·팔·GPT·TTS를 다시 요청하지 않는다.
- 독립 카메라 검수 도구는 `photo_feedback=False`로 기존 음성/API/모션 금지를 유지한다. 도구의 preview decode 검증은 실제 화면 표시와 구분해 기록한다. 이 작업에서 기존 실물 전체 검사를 다시 실행하지 않았다.

## 하단 자막

기본 `off`, 메뉴에서 `off/en/ko`를 고른다. 자연어 설정도 같은 응답의 현재 요청 해석을 사용한다. 언어 없는 ON은 이전 선택을 기억하며 첫 선택은 en이다. 음성 언어·성우·모델은 변경하지 않는다.

en 원문 또는 ko 번역은 실제 player-start부터 답변 단위로만 표시한다. 생성만 된 대사/취소된 대사는 표시하지 않는다. 번역은 같은 GPT 응답의 caption 필드로 받고 별도 API를 쓰지 않는다. 번역이 없으면 빈 자막으로 음성을 계속한다. 메뉴에서 나중에 고른 설정이 진행 중인 이전 요청에 덮이지 않도록 generation을 검증한다.

부모 wrapper의 하단 text overlay를 사용하며 R10/v13.2 원본 bytes·도형·좌표는 수정하지 않았다. cancel/reset/다음 질문/off/오류에서 오래된 자막을 제거한다. repeat는 성공 WAV와 함께 보관된 대응 자막만 사용한다. 자막 텍스트는 일반 진단 events나 대화 history에 별도로 추가하지 않는다. 단어 timestamp/karaoke 동기화는 구현하지 않는다.

## 한 턴만 좁게 확인하는 Get angry 진단

평소 실행은 기존 한 명령이다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE && ./launch-character-life
```

실제 실패 한 턴의 증거가 필요할 때만 사용자가 기존 앱을 정상 종료하고 **새 비공개 파일명**으로 실행한다. 자동 질문이나 추가 모델 호출은 없다. 실행 후 GP8로 한 번 질문하면 첫 턴의 STT 텍스트·구조화 모델 응답과 안전한 stage trace를 기록한다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE
./launch-character-life --one-turn-diagnostic \
  /home/bgh/.local/state/bmo/private-diagnostics/angry-one.json
```

파일은 Git checkout 밖에 0600으로 새로 생성하며 부모는 0700이다. 기존 파일·symlink·공개 디렉터리는 거부하고 다음 턴은 기록하지 않는다. 원문/메타데이터를 GitHub나 일반 로그에 올리지 않는다. `--status`/dry-run은 파일을 만들지 않으며 바탕화면 설치에도 이 옵션을 저장하지 않는다.

requested/model/resolved/emitted, validation/degradation 이유, 실제 TTS backend/mechanism/Soft override, character_life와 emotion mode를 비교한다. typed literal 시험과 실제 STT 입력을 `input_kind`로 구분한다. manual 표정 선택 또는 plain voice 옵션도 유효 설정에서 확인한다. 현재 실기 자료 없이 특정 단계가 Pi 실패 원인이라고 단정하지 않는다.

## Pi 후보 갱신과 최소 세 시나리오

기존 앱을 정상 종료한다. `/home/bgh/BMO_REAL_ROBOT`은 보존하고 후보만 갱신한다. 미커밋 변경·다른 브랜치·분기한 커밋이 있으면 중단하고 보존·통합한다. 정상 환경의 의존성·음성팩·부팅음·펌웨어를 다시 설치하지 않는다.

```bash
(
  set -eu
  cd /home/bgh/BMO_CHARACTER_LIFE
  test "$(git branch --show-current)" = feature/bmo-character-life-v1
  test -z "$(git status --porcelain)"
  git fetch origin feature/bmo-character-life-v1
  git merge --ff-only FETCH_HEAD
  git rev-parse HEAD  # 최종 보고의 검토된 HEAD와 비교한 뒤 실행
  ./launch-character-life
)
```

1. `Get angry.`와 자연스러운 영어 변형 → 실제 angry 얼굴과 억양을 각각 확인 → `two plus two`에서 기본 표정 복귀. 필요할 때만 실패 한 턴의 로컬 진단을 사용한다.
2. 자연스러운 영어 사진 요청 → 실제 live preview → 5초 → 저장 사진 유지 → 짧은 성공 대사 → repeat 재촬영 없음 → GP8/Back 복귀. 별도로 countdown 취소/실패 시 성공 대사 없음도 확인한다.
3. `Turn on Korean subtitles`로 영어 음성과 한국어 번역을 함께 확인 → 메뉴 en/ko/off, 취소·repeat·다음 질문을 비교한다. 실제 번역 품질·하단 가림 정도는 Pi 화면에서 검수한다.

Luna low, Aaron Soft, Beemo, gain 0.25, GP7/GP8와 서보 보정, 별도 음성팩은 보존했다. 새로운 게임·카메라 엔진·이미지 클라우드 업로드·팔 활성화는 없다. 최종 SHA와 exact-HEAD CI는 PR #40의 완료 보고를 기준으로 한다.
