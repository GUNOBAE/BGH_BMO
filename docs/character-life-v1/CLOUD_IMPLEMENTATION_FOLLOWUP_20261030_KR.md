# 일반/Cloud Codex 구현 후속과 Pi 후보 검수 — 2026-10-30

구현 담당은 Raspberry Pi Codex에서 현재 일반/Cloud Codex로 옮겼다.
현재 저장소에서 실제 코드 수정·검증·커밋을 진행하고 기존 PR #40에 이어 간다.
Pi 연결 증거가 없으므로 이 작업에서 Pi 접속·화면·청취·촬영·서보 실기를
수행했다고 보고하지 않는다. `/home/bgh/BMO_REAL_ROBOT`은 기존 정상 작동본으로
보존하고, `/home/bgh/BMO_CHARACTER_LIFE`만 사용자가 갱신·검수한다.
앞선 네 인계 문서의 Pi Codex 구현 지시는 이 담당 갱신으로 대체한다.

## 실제 코드 변경과 유지한 동작

- 영어 분노 matcher, 새 말투 prompt/pin, 명시적 음성 사진 요청,
  `launch-character-life`, desktop 설치 시 팔 옵션 오류 처리는 이미 있었다.
  중복 구현하거나 prompt를 계속 개작하지 않았다. 확장한 기존 브라우저 검사에서
  `Get angry`/`Get mad`/`Become angry`와 한국어 분노가 CharacterPlan →
  theatrical_angry TTS 지시 → player-start → adapter → BMOFace API로 이어진다.
  한국어·영어 슬픔 뒤 사실 질문의 neutral 복귀, repeat·취소도 유지한다.
  요청의 pinned prompt, Luna low/output 320과 Aaron Soft/Beemo/gain 0.25를 검증한다.
  얼굴 enum 성공과 실제 화난 억양·자연스러운 BMO 답변은 별도 실기 항목이다.
- 호출 직후의 짧은 쉼을 발화 경계로 오인해 질문 앞부분을 버릴 수 있던 경로를
  보수적으로 수정했다. 호출 검출 뒤 모호한 voiced prefix가 있으면 0.12초의
  쉼으로 자르지 않고 기존 전체 발화 종료 0.9초를 기다린다. 문장 뒷부분만
  STT로 넘기는 오류를 차단하고 화면에 쉬었다 다시 질문하거나 버튼을 쓰도록
  안내한다. 버린 앞부분을 복원한 것은 아니며 전체 질문 보존으로 보고하지 않는다.
  Boolean 호출 검출기에는 어휘별
  종료 위치가 없으므로, **호출어부터 질문 끝까지 무음이 전혀 없는 한 호흡의
  전체 질문 분리는 계속 미지원**이다. 호출 후 질문하지 않으면 STT/GPT/TTS
  없이 종료하며 GP8 PTT·빠른 발화 종료·thinking 취소는 유지한다.
- 승인 wake ACK 재생은 기존 한 player의 start/ended 이벤트와 WAV mouth cues를
  사용해 listening 얼굴에 연결한다. 대화의 기존 표정·history·repeat cache를
  ACK로 교체하지 않으며, 입력 시작·취소·종료와 player 경합을 검사한다.
  이는 주입한 자산의 코드 검증이며 실제 승인 음성팩 재생 성공이 아니다.
- 음성 사진 촬영 성공·실패 뒤 같은 앱의 대화 상태로 복귀하고, 사진이 잠시
  점유했던 기존 tracking 경로를 복구한다. 현재 명시적 요청 → 준비 답변 완료 →
  취소 가능한 countdown → 기존 CameraBroker 저장을 유지한다. repeat·과거 턴·
  취소로 재촬영하지 않으며 영상·tracking의 기존 UI와 단일 카메라 엔진을 사용한다.
- 기존 B1 named gesture·취소·중복 방지·연결 상태를 코드로 검사한다.
  GP7 수동 팔 토글, GP8 PTT, GP10 오른팔/GP11 왼팔, 600–2400µs, 50Hz,
  기존 좌우 반전과 보정은 변경하지 않는다. 펌웨어 원격 활성화·재보정은 하지 않는다.
- 하늘색 고정 배경, 아래에서 올라오는 원본 표정 요소, 영어 loading,
  승인 R10/v13.2 원본은 보존한다.

## 검증 상태와 남은 실기

감정 관련 단위 검사 63개와 확장한 field-expression Chromium 검사는 통과했다.
브라우저 검사는 공개 합성 face 계약/주입한 provider·PCM 경계를 사용한다.
실제 승인 R10 그림 렌더링이나 자연 발화·청취를 PASS로 바꾸지 않는다.
최종 커밋 SHA와 전체 테스트·해당 SHA CI 결과는 PR #40 및 최종 보고에
정확하게 기록하며, 커밋 전 결과를 최종 HEAD의 CI로 대신하지 않는다.

실제 Pi의 새 후보에서 얼굴·억양·사진 품질·영상·추적·서보 움직임은 검수 필요다.
마지막 알려진 팔 기록은 `REMOTE_ENABLED=False`, 서보 커넥터 분리이며
현재 보드 SHA·runtime·배선·전원은 미확인이다. 이전 실기 전체를 반복하지 않고
변경 경로의 최소 검수만 한다.

## 기존 변경을 보존하는 최소 후보 갱신

아래 명령은 **Pi에서 사용자가 실행**한다. 먼저 기존 앱을 정상 종료한다.
미커밋 파일이나 다른 브랜치·분기한 커밋이 있으면 명령은 중단하며, 별도 음성팩과
로컬 변경을 확인한 뒤 보존·통합한다. 자동 stash/reset/force-push를 사용하지 않는다.

```bash
(
  set -eu
  cd /home/bgh/BMO_CHARACTER_LIFE
  test "$(git branch --show-current)" = feature/bmo-character-life-v1
  test -z "$(git status --porcelain=v1 --untracked-files=normal)"
  git fetch origin refs/heads/feature/bmo-character-life-v1
  git merge --ff-only FETCH_HEAD
  git rev-parse HEAD
  test -x .venv-device/bin/python
)
```

출력 HEAD가 검수할 PR #40 HEAD인지 비교한다. 이 명령은 `.venv-device`,
기존 개인 설정·사진·영상·음성팩이나 정상 작동본을 덮어쓰지 않는다.
의존성 변경이 필요하다고 확인되지 않으면 기존 Pi 환경을 다시 설치하지 않는다.

## 한 앱 실행과 최소 실기 순서

```bash
cd /home/bgh/BMO_CHARACTER_LIFE && ./launch-character-life
```

기존 wake 모델이 준비된 Pi에서는 같은 명령에 다음 옵션만 추가한다.
모델을 새로 생성하거나 자동 다운로드하지 않는다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE && ./launch-character-life \
  --wake-word --wake-question-wait 5 \
  --wake-model-manifest /home/bgh/.local/state/bmo/wake-model-candidates/amrmantawi-bmo-openwakeword-58eb9404df18/manifest.json
```

1. 부팅의 고정 하늘색 배경·원본 등장·영어 loading을 확인하고 GP8로 `Get angry`
   뒤 평범한 사실 질문을 한다. angry 얼굴과 실제 목소리 연출을 각각 기록한다.
   필요하면 한국어 분노·양언어 슬픔 뒤 사실 질문을 한 번씩 비교한다.
2. 같은 앱에서 `사진 찍어줘` 또는 `Take my picture please.`로 준비 답변이 끝난
   뒤 countdown·사진 한 장 저장·대화 복귀를 확인한다. countdown 취소와 repeat가
   추가 촬영하지 않는지 확인한다. 기존 UI로 짧은 영상·tracking 복귀를 확인한다.
3. wake를 켠 경우 호출 → listening → 새 질문 → thinking을 확인한다. 무질문
   timeout, 질문 중간의 짧은 쉼, thinking 중 취소를 비교한다. STT가 다르면 해당
   한 발화의 로컬 기록만 requested/model/resolved/emitted trace와 비교한다.
   첫 단어가 빠진 결과를 전체 질문 보존으로 승인하지 않는다.
4. 팔은 아래 현재 상태를 먼저 확인한 뒤 기존 감독 조건이 준비된 실행에서만
   검수한다. 현재 연결을 확인할 수 없으면 카메라·대화 검수만 계속한다.

## 팔의 현재 상태 확인과 기존 감독 실행

다음은 알려진 mount가 실제 존재할 때 **파일만 읽는** Pi 측 예다.
경로가 다르면 실제 mount를 먼저 확인한다. serial 개방·reset·펌웨어 쓰기·
`REMOTE_ENABLED` 변경을 하지 않으며, 파일 SHA만으로 실행 중 보드 상태를 추정하지 않는다.

```bash
sha256sum /media/bgh/CIRCUITPY/code.py /media/bgh/CIRCUITPY/bmo_arm_bridge.py
rg -n '^REMOTE_ENABLED\s*=' /media/bgh/CIRCUITPY/code.py
```

사용자가 현재 서보 커넥터·별도 전원 차단 수단·기구 간섭·기존 serial 소유자를
확인한다. 과거 disabled SHA를 enabled 보드의 승인 SHA로 사용하지 않는다.
DISABLED/STOPPED는 그대로 보존하며, 이 작업에서 원격 동작을 켜지 않는다.
별도 검토된 현재 보드가 기존 감독 조건을 모두 충족할 때의 실행 형식만 남긴다.

```text
./launch-character-life --arms-supervised --arm-legacy-bridge \
  --arm-reviewed-code-sha <현재_검토한_code.py_전체_SHA256> --arm-power-off-ready
```

`--emotion-arms`는 같은 감독 실행에서만 추가한다. 연결 상태 표시·명명 gesture
전달·취소·중복 방지와 실제 팔 움직임을 구분한다. protocol ACK의
`POSITION_UNVERIFIED`는 위치 도달이나 motion 완료가 아니다.

## 미전달 자산

`bmo_boot_clean.wav`는 현재 Cloud 환경에 없다. 재생성·유료 TTS·공개 업로드를
하지 않았다. 사용자 다운로드 파일의 예상 SHA256은
`461967e899791f05bc97bd7f2e6f3c9fede68b91d6801f9f4a507c43d84eb6e8`이며,
기존 override `~/.config/bmo/startup_chime.wav`를 사용한다. Pi에 파일을 사적으로
전달한 뒤 사용자가 다음을 실행한다. 기존 override는 별도 private backup에 보존한다.

```bash
(
  set -eu
  bmo_sound_source="$HOME/Downloads/bmo_boot_clean.wav"
  test -f "$bmo_sound_source" && test ! -L "$bmo_sound_source"
  printf '%s  %s\n' 461967e899791f05bc97bd7f2e6f3c9fede68b91d6801f9f4a507c43d84eb6e8 "$bmo_sound_source" | sha256sum -c -
  install -d -m 700 "$HOME/.config/bmo"
  bmo_sound_target="$HOME/.config/bmo/startup_chime.wav"
  test ! -L "$bmo_sound_target"
  if test -e "$bmo_sound_target"; then
    bmo_sound_backup=$(mktemp "$HOME/.config/bmo/startup_chime.backup.XXXXXX.wav")
    cp -p "$bmo_sound_target" "$bmo_sound_backup"
    chmod 600 "$bmo_sound_backup"
  fi
  install -m 600 "$bmo_sound_source" "$bmo_sound_target"
  sha256sum "$bmo_sound_target"
)
```

승인 wake ACK 자산은 이 Cloud 검증에 전달되지 않았다. 실제 승인/청취 상태를
추정하지 않고 [공유 자산 계약](WAKE_ACK_ASSET_CONTRACT_KR.md)의 정확한 fingerprint·
SHA·승인을 검증한 번들만 읽는다. 누락·미승인·부정합이면 `VISUAL_LISTENING`이며
새 유료 생성·다른 인사음 fallback·음성팩 덮어쓰기·자동 승인은 하지 않는다.
부팅 WAV·원작 영상·음성팩·개인 음성·사진을 공개 GitHub에 넣지 않는다.
