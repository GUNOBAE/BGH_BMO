# v10 음악 후보 인수 절차

현재 개발 브랜치 `feature/bmo-character-life-v1`, 기존 PR #40을 이어간다.
배포 SHA와 해당 SHA의 CI는 PR의 최신 구현 인계 댓글이 최종 기준이다.
문서 브랜치로 checkout하지 않는다. Cloud는 코드 writer, Pi는 private 자산·
설정·설치·실기 담당이다. 정상본 `/home/bgh/BMO_REAL_ROBOT`은 보존한다.

근거: [공통 작업서](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6534211753),
[v10 문서](https://github.com/GUNOBAE/BGH_BMO/blob/2023f3a9c12ce4c0628afdfc8deda4a8831ec241/docs/voice-pack-v10/README_KR.md),
[Pi 실제 인계](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6534420182).

## 2026-11-05 집중 검토 — Pi 후보와 Cloud 후속 구분

[구현 인계](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6609896494)와
[최신 Pi 인계](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6613424628)의
설치 기준은 `43262705b9cb520056694d34521cde74bec01220`이다. 이 SHA의 CI는
6/6 성공이며, Pi는 등록142/우선60/음악1곡·2모드 보존을 확인했다. 새 음악
UI·GP8 hold·묶음 활성화는 당시 NOT_RUN이다. 후속 Cloud 수정은 별도 후보이며
Pi를 자동 갱신하지 않는다. 실제 영상·음악 재생의 이전 사용자 성공과 새 UX의
검수 대기를 구분한다. 승인된 새 노래 표정 결과물은 아직 전달되지 않았다.

집중 검토에서 재현한 수정은 현재 미리듣기 요청의 실제 시작·성공/취소/실패
receipt, pause 뒤 늦은 video play 실패의 폐기, 중복 Stop의 종료 음성 방지,
현재 캐시 이벤트 OFF/묶음 해제의 취소, wake ACK OFF 상태 표시다.
과거 played 기록을 이번 대표 미리듣기 완료로 사용하지 않는다. 자동 검사와
exact HEAD CI 결과는 최신 PR 인계에 기록하며 실제 청취 승인으로 대체하지 않는다.

### 공개 시연 준비 — 코드에서 확인한 범위

| 항목 | 확인됨 | 미검증 / 한계 |
|---|---|---|
| API 키 | private store 0700/0600·symlink 거부, redacted repr, 고정 오류 코드, Chromium 환경 allowlist | 저장소는 평문이다. 실제 Pi 로그·권한·전체 Git 역사 비노출은 이번 Cloud 검증 범위 밖이다. 비밀 값은 읽어 출력하지 않았다. |
| 사용자 데이터 | STT 임시 WAV는 성공/취소/실패 후 정리. 대화 history는 bounded 메모리, API store=False. repeat WAV는 교체/종료 시 정리 | 강제 종료의 잔여 temp·provider 보관 정책은 보장하지 않는다. 명시적 one-turn 진단은 원문을 담는 private 파일이므로 별도 보관 관리가 필요하다. |
| 사진/영상 | 아래 launcher의 private photos/videos에 보관. 사진 8MiB/장·총256MiB, 영상 60초·96MiB/건·총512MiB, 여유32MiB 검사 | 자동 기간 삭제 없음. 실제 Pi 사용량·권한은 미검증이다. |
| 로컬 서버 | 127.0.0.1 고정, Host/Origin 검사, POST token·body 상한·nonce 중복 방지 | 같은 컴퓨터의 로컬 프로세스까지 인증하는 구조가 아니다. VNC/포워딩/방화벽의 실제 외부 노출은 미검증이다. |
| 재시도/비용 | OpenAI SDK 기본 retry1(허용0~5), Fish 자동 재시도 없음. 인증 오류는 명시적 복구 필요. 선택적 요청수·cooldown·추정비용 gate 존재 | 비용 gate 기본 OFF, 프로세스 메모리 기준이다. 실제 결제 상한이 아니며 메뉴/후속 TTS를 각각 계량하지 않는다. 임의 금액/설정 변경 없음. |
| 종료/복구 | owner 취소·종료·오류 후 정리와 정상본 실행 경로 존재. 아래 구버전 복귀 명령에서 지원하지 않는 frontend 옵션 제거 | 실물 release/다음 대화는 Pi 검수. runtime_directory_permissions의 0700 guard는 유지하며 세션 권한 변경 주체는 미확정이다. |

정상 telemetry는 원문 대신 이벤트/시간을 기록하며, 선택 로그는 1MB 및 백업3개로
회전한다. 이번 점검은 관련 코드·기존 검사에 한정하며 공개 시연의 전체 승인이나
실제 Pi 설정 검수를 뜻하지 않는다.

## 코드에서 연결한 경로와 남은 증거

- 기존 한 번의 GPT 응답의 v5 `plan`에 최대 3개의 allowlisted 동작을 담는다.
  이전 v1–v4 단일 intent도 읽는다. Host가 turn/step ID·세대·기한을 부여하며
  완료 receipt 뒤에만 다음 단계를 실행한다. 실패·불명·취소·새 질문·repeat는
  tail을 실행하지 않는다. stop tracking → video 전에 명시 OFF를 적용하므로
  Back으로 OFF를 되살리지 않는다. 절별 부정·인용·과거·미래를 검사한다.
  사진/영상 review나 게임은 사용자의 복귀를 요구한다. 계획이 해당 화면을
  임의로 닫지 않으며, 그 상태에서 허용되지 않는 후속 단계는 거부한다.
- 실제 Pi에서 빈 게임 목록은 같은 후보의 번들 세 파일 mode0664 때문에
  `unsafe_game_bundle`로 재현됐다. SHA·파일 존재는 정상이었고 private 실행
  도구도 기존 launcher에 위임했다. 과거 실제 실행 PID/argv는 미관측이다.
  새 코드는 permission/missing/hash/disabled 이유를 구분하고, 실행 profile을
  통일한다. `game_pack_prepare`는 정확한 세 파일만 사전 검증·권한 수정한다.
  Pi 후속 인계에서는 0644 정리 후 catalog3 READY, 제품 Chromium 게임
  10그룹 PASS까지 확인했다. 실제 pad/native 사용자 승인은 별도다.
- v10 `entries/spoken_text/file/requested_model`를 기존 importer로 정규화한다.
  142종을 보관하고 60종을 우선 표시한다. imported/selected/listened/approved/
  played는 별도이며 자동 승인하지 않는다. RX01의 빈 speech는 웃음이고
  자막·합성문을 만들지 않는다. 제작 출처와 countdown 편집 기록을 보존한다.
- 네트워크 E07/서비스 E08/카메라 E14/촬영 E15/사진 저장 E16/음악 E13,
  game menu G01/start G02/launch fail G04/win G05/loss G06으로 교정했다.
  승인된 해당 캐시는 같은 이벤트의 live TTS를 대신한다. 오류 원인은 화면에
  구분하며 깨진 출력 장치에 음성 오류를 재시도하지 않는다.
- 사진 준비 캐시는 실제 preview 표시 후 재생한다. 승인된 5초 카운트의
  실제 player-start가 UI/timer 공통 t0다. 오직 timer가 한 셔터를 소유한다.
  WAV ended·repeat·late callback은 셔터를 호출하지 않는다. 미승인·mute·
  시작 실패는 전체 시각 5초다. 사진 저장·review 확인 뒤 성공 대사를 낸다.
- 승인된 `wake_ack_01`을 기존 wake owner/플레이어에 실제 drama-3 provenance와
  source gain으로 전달한다. live 모델을 바꾸지 않는다. 질문 시작이 관측되면
  ACK를 생략한다. 기존 ACK로 닫힌 마이크 경계의 무간격 질문 전체 복원은
  새로 증명하지 않았다. wake/idle은 명시 옵션이고 기본 OFF다.
- `space_potatoes`는 한 항목의 face/video 변형이다. video의 내장 AAC를
  검증 PCM으로 디코드해 기존 AudioFocus를 가진 Python ALSA 출력에서 낸다.
  browser video는 항상 muted다. 별도 MP3를 겹치지 않는다. ALSA submitted
  frames minus delay를 기준으로 영상/입을 갱신한다. 이 clock은 출력 스트림
  진행 증거이며 실제 스피커 청취를 증명하지 않는다. Face whole-mix RMS는
  반주에도 반응하는 근사치이고 정확한 가사/음소 lip sync가 아니다.
- media GP8는 짧게 눌렀다 놓으면 pause/resume, 1.5초 hold는 종료 후 얼굴
  복귀이며 해당 keyup을 소비한다. Stop/Back/Esc도 종료한다. seek/replay,
  volume0/mute, 늦은 decoder 실패와 held keyup 후 다음 대화를 검사한다.
  기존 촬영 GP8 stop-save/review-back은 유지한다.
- 팔은 기존 B1·보정 그대로다. `arm_candidate`는 기본 read-only, prepare는
  정확한 단일 flag 후보만 만들고 apply/rollback은 현재 감독 조건·SHA·백업을
  요구한다. 보드 쓰기는 auto-reload의 set_arms(0)을 일으킬 수 있다.
  [감독 절차](../../firmware/staging/bmo_arm_legacy_bridge_v1/README_KR.md)를 따른다.
- Tracking은 기본 OFF의 선택 기능이다. 기존 rotation→mirror→normalized
  좌표와 R10 look API를 보존했다. 사람/BMO 관점 라벨만으로 반전을 바꾸지
  않았다. 실제 방향·작동 범위는 Pi 선택 검수다.

아래는 10/31 최초 통합 시점의 기록이며 최신 상태는 위 인계를 우선한다.
Cloud는 합성 짧은 미디어·주입 IO·실제 제품 Chromium을 검사한다. 사용자
대화·자막·음량·취소·영상 PASS는 최신 사용자 관찰로 보존한다. Pi는 두 ZIP
전체 해시·PCM·decode를 검증했지만 당시 runtime 등록0/청취 승인0/재생0이다.
Pi는 실제 제공 MP4의 무음 Chromium decode/pause/seek도 별도 확인했다.
이 결과는 새 제품 음악 두 모드·스피커 청취 승인이 아니다. Cloud가 private
ZIP을 받거나 실제 Pi에 접속·재생·촬영·구동한 것은 아니다.

## 후보 갱신과 private import (Pi 담당)

앱의 Exit로 정상 종료한다. dirty/분기면 아래 검사가 중단하며 stash/reset/
clean을 자동 실행하지 않는다. READY의 exact SHA를 `BMO_RC_SHA`에 입력한다.

```bash
(
 set -eu
 cd /home/bgh/BMO_CHARACTER_LIFE
 test "$(git branch --show-current)" = feature/bmo-character-life-v1
 test -z "$(git status --porcelain=v1 --untracked-files=normal)"
 git fetch origin refs/heads/feature/bmo-character-life-v1
 BMO_RC_SHA='<최신 Cloud 인계에서 검토한 exact SHA>'
 git merge-base --is-ancestor HEAD "$BMO_RC_SHA"
 git merge --ff-only "$BMO_RC_SHA"
 test "$(git rev-parse HEAD)" = "$BMO_RC_SHA"
)
```

먼저 게임 번들을 읽기 전용으로 확인한다. permissions인 경우에만 검토 뒤
`--apply`한다. 이는 후보의 pinned 세 파일 group/other write만 제거한다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.game_pack_prepare --dry-run
# 위 결과가 permissions이면 Pi가 기존 메타데이터 백업 후:
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.game_pack_prepare --apply
```

확인된 두 원본 ZIP은 그대로 보존하고 이전 팩·라이브러리와 다른 디렉터리에
명시 import한다. 아래 명령은 재생·승인·유료 API를 호출하지 않는다.
같은 import가 이미 완료됐으면 덮어쓰지 말고 list/검증 결과를 재사용한다.

```bash
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.voice_pack \
 --root /home/bgh/BMO_private/runtime/voice-v10 import \
 /home/bgh/Downloads/BMO_Voice_Release_v10_142.zip \
 --sha256 6bcbf926b2d5519e63eb502c141fab4571e06eafc72315597cb147121c3a189e
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.voice_pack \
 --root /home/bgh/BMO_private/runtime/voice-v10 list

PYTHONPATH=software .venv-device/bin/python -m bmo_ai.media_assets \
 --media-seed /home/bgh/Downloads/BMO_Media_Seed_SpacePotatoes_v1.zip \
 --sha256 5de21e50e275e37207f185247f7062f14bd58d46686eb92c8b7fd0f78ff2cde2 --dry-run
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.media_assets \
 --media-seed /home/bgh/Downloads/BMO_Media_Seed_SpacePotatoes_v1.zip \
 --sha256 5de21e50e275e37207f185247f7062f14bd58d46686eb92c8b7fd0f78ff2cde2 \
 --content-root /home/bgh/BMO_private/runtime/media-space-potatoes-v1 \
 --private-use-confirmed --install
```

`MEDIA_SEED.json`을 runtime manifest로 읽지 않는다. 정확한 ZIP/component
SHA를 확인한 adapter가 기존 library v2 레코드를 만든다. 원본 MP3·handoff·
energy 후보는 private 보관하며 video용 오디오는 MP4 내장음원에서만 만든다.
ffmpeg/ffprobe와 시스템 libasound를 재사용한다. 새 Python 의존성은 없다.
음악 권리는 user_provided/공개 권한 미확정이고 소유/라이선스를 날조하지 않는다.

## 한 앱 실행·선택 승인·아이콘·복귀

```bash
env -u FISH_API_KEY XDG_CONFIG_HOME=/home/bgh/.config \
 BMO_PYTHON=/home/bgh/BMO_CHARACTER_LIFE/.venv-device/bin/python \
 /home/bgh/BMO_CHARACTER_LIFE/launch-character-life \
 --voice-pack-directory /home/bgh/BMO_private/runtime/voice-v10 \
 --music-directory /home/bgh/BMO_private/runtime/media-space-potatoes-v1 --music-supervised
```

음성 메뉴에서 대표 클립을 연속 미리 듣고 사용자가 명시적으로 동의하면
초기60+자연종료1개 묶음의 재생권한을 활성화한다. 묶음 해제/이벤트별 OFF·ON이
가능하며, 개별 SHA 미리듣기·청취 승인은 별도로 유지한다. 묶음 수락을 개별
청취61회로 기록하지 않는다. ACK·5초 카운트도 해당 권한이 있어야 한다.
`--wake-word`는 custom detector 경로가 준비되고
사람이 원하는 세션에만 덧붙인다. 자동 idle 주기/새 음성 생성은 하지 않는다.
음악/기능 캐시가 마지막 대화 repeat WAV를 바꾸지 않는다.

기존 installer를 같은 인자로 dry-run한다. 사용자 기존 Desktop/정상본
아이콘을 덮지 않는다. 설치 실행은 Pi 설정 담당자가 검토한다.

```bash
./launch-character-life --voice-pack-directory /home/bgh/BMO_private/runtime/voice-v10 \
 --music-directory /home/bgh/BMO_private/runtime/media-space-potatoes-v1 --music-supervised \
 --install-desktop --desktop-install-target applications-only --dry-run
```

정상 종료는 앱 Exit다. 자산 복귀는 이전 팩/음악 디렉터리 옵션으로 실행하거나
새 옵션을 빼는 방식이며 원본·승인 기록은 삭제하지 않는다. 정상본 복귀:

```bash
env -u FISH_API_KEY XDG_CONFIG_HOME=/home/bgh/.config \
 BMO_PYTHON=/home/bgh/BMO_REAL_ROBOT/.venv/bin/python \
 /home/bgh/BMO_REAL_ROBOT/launch-desktop --live \
 --screen-profile r10 --ptt-microphone
```

## 이번에 확인할 짧은 네 묶음

1. 음악 Video 화면/overlay, GP8 short pause/resume, hold 종료 후 keyup이
   대화를 시작하지 않는지 확인하고 새 GP8로 대화한다.
2. 대표 미리듣기 중 취소 시 다음 클립/묶음 완료로 넘어가지 않는지 확인한다.
   실제 청취 뒤 사용자가 묶음을 수락하고 OFF/ON·전체 해제와 개별 기록 보존을 본다.
3. M04→음악, 수동 종료→M10 1회, 별도 자연 종료→종료 음성1회. 출력 중첩 없이
   pause/seek/replay/Back 후 다음 대화가 가능한지 실제 청취한다.
4. 이전 runtime_directory_permissions가 재발하면 현재 소유자/권한만 확인하고
   세션 변경 주체를 좁게 진단한다. guard 우회·전체 chmod·정상본 변경은 하지 않는다.
