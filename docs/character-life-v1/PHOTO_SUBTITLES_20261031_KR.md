# BMO 통합 후보 — 팔·영상·눈 트래킹·기존 기능 인계 — 2026-10-31

## 최신 기준과 인수 신호

[BMO-COMPLETE-USER-FLOWS-20261031](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6532398427)이 아래 과거의 사진 한정/영상·tracking UI 유지 지시보다 우선한다. Cloud는 같은 PR #40의 코드 writer이며 Pi는 환경·후보 설치·제한된 실기 담당이다. 아래의 이전 사진 진단은 당시 기록으로 보존하며, 최신 기능과 현재 성공 기준은 이 절을 따른다.

사용자는 `3ebe0fb79b0515a303b6ea9474b36cfd3ded4c75` 후보의 **수동 사진 저장과 부드러운 preview 체감**, GP8·thinking 취소·감정·자막·승인 부팅음을 확인했다. 새 후보의 실제 fps·영상·사람 눈 따라오기·양팔 움직임은 별도다. 성공한 마이크·GPT·감정 코드를 예전 Fish401 때문에 다시 설계하지 않는다.

배포 가능한 전체 SHA와 **그 SHA의** Python3.11/3.13·제품 Chromium CI는 PR의 **`CLOUD-FULL-BATCH-READY-20261031`** 댓글이 인수 신호다. 작업 중인 HEAD나 아래 코드의 placeholder를 배포 대상으로 쓰지 않는다. 새 수정이 나오면 새 READY SHA가 이전 신호를 대체한다. 커밋 이력·미커밋 작업·정상본은 자동 stash/reset/clean/force로 바꾸지 않는다.

## 실제 제품 경로와 상태 구분

| 기능 | 구현 경로 | 자동 검사 근거 | 현장/자산/감독 상태 |
|---|---|---|---|
| 양팔 | 현재 턴의 단일 GPT 의미 해석/메뉴 → Host 허용 이름 → 기존 B1 adapter, HOLD/cancel·STOP·once/generation | semantic·B1/Host 회귀, 제품 메뉴 click/key | IMPLEMENTED / AUTOMATED_TESTED; 현재 remote 활성화·배선·즉시 전원 차단과 실물 움직임은 NEEDS_OPERATOR |
| 무음 영상 | 준비 대사 완료 → 실제 preview 표시 ACK →5초→REC/경과→GP8/Stop→압축 MP4 finalize/검증→저장 `<video>` 재생/일시정지/다운로드→Back | 실제 ffmpeg/ffprobe·range HTTP·브라우저 decode/상태 전환/취소·복구 | IMPLEMENTED / AUTOMATED_TESTED; Pi 실제 source FPS·길이·저장/재생은 PI_VERIFIED와 별개 |
| 눈 트래킹 | 영어/한국어 현재 요청·toggle → 같은 CameraFeature의4Hz TargetTracker → GazeArbiter → 기존 BMOFace | 다중 표적·상실·stale·사진/영상 일시 양보/복귀·제품 toggle | IMPLEMENTED / AUTOMATED_TESTED; HUMAN tracking 품질은 NEEDS_OPERATOR |
| 사진·대화·감정·자막·GP8 | 기존 승인 경로 보존, 사진5초/실제 PNG review/성공 후 대사/repeat 재촬영 없음 | 기존 제품 회귀와 신규 모드 왕복 | 기준3ebe0fb USER_ACCEPTED; 새 exact HEAD 비회귀는 CI/짧은 인수로 구분 |
| v8 음성팩 | private ZIP hash/경로/용량/PCM/provenance 검증→별도 staging→목록/선택/개별 SHA 승인→이벤트 | 주입 ZIP/PCM·선택·승인 gate·실제 playback 경계 | IMPLEMENTED / AUTOMATED_TESTED; 정리 ZIP 미전달 ASSET_PENDING, 일괄 청취 승인 없음 |
| master volume/mute | 기존 시작0.25, RAW1.0/preview baked0.25 분리, 기존 단일 출력 owner | PCM 진폭/0/mute·cache 불변·음악 현재 PCM 위치/일시정지 유지 | IMPLEMENTED / AUTOMATED_TESTED; 실제 ALSA 출력·청취는 USER_ACCEPTED 대기 |
| Hey BMO/ACK/idle | 기존 선택 wake, missing ACK는 visual listening, idle 기본OFF/승인 clip+opt-in만 | 무질문0회·취소·소유권·미승인/누락 자산 회귀 | IMPLEMENTED; 정확한 wake_ack_01은 ASSET_PENDING, 무간격 전체 질문 복원은 미검증/제한 |
| 자체 게임 | PR #38의 Stack Sprout/Lantern Labyrinth/Vine Run 자산·엔진만 이식, 같은 Host 입력/수명 | 실제 menu/key·engine gameplay·RAF/blur/restart·복귀, terminal event once | IMPLEMENTED / AUTOMATED_TESTED; 실제 USB pad/Pi 화면은 별도, ROM/공식 게임 아님 |
| 로컬 음악 | 기존 검증 WAV 목록·재생/일시정지/재개/다음/종료·얼굴복귀, master volume 공유 | 실제 menu click + 주입 출력, owned child·PCM/FD 정리 | IMPLEMENTED / AUTOMATED_TESTED; 사용자가 제공한 라이브러리 없으면 ASSET_PENDING |
| 후보 아이콘/종료/복귀 | BMO Candidate namespace, applications-only private0700 launcher, 같은 interpreter/옵션 | 합성 home의 실제 wrapper 실행·0775 기존 파일 보존·원자적 설치 | IMPLEMENTED / AUTOMATED_TESTED; Cloud는 Pi 아이콘/OS/서비스 설치·교체 안 함 |

`AUTOMATED_TESTED`는 소프트웨어 경계를 검사했다는 뜻이며 사용자 청취·실제 사진/영상·LCD 프레임·사람/USB/서보 PASS를 뜻하지 않는다. 최종 exact HEAD에서 필요한 검사가 실패하면 READY를 게시하지 않는다. 선택 게임팩/음성팩/codec 실패는 해당 capability에 안전한 이유를 남기며 정상 대화·사진까지 함께 막지 않는다.

### 최신 Pi 인계의 정확한 범위

[PI-FULL-BATCH-20261031](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6532528946)은 **2026-10-31 05:08–05:11 KST**의 준비 결과다.

- 후보 폴더는3ebe0fb/clean, 관측 때 실제 BMO 앱 PID는 식별되지 않았다. 폴더 SHA를 실행 SHA로 대신하지 않는다.
- 후보 resolver는 `saved_store`, env override 없음, **새 키 wallet 인증 HTTP200/AUTHENTICATION_PASS**다. 무료 TTS 한 번은 `diagnostic_interrupted`로 유효 WAV 미확보/미검증, 재생0이다. 새 키401·모델/음색·제품 마이크/감정 실패로 해석하지 않는다. TTS 한도는 소진됐으므로 추가 POST 없이 진행한다. 후처리의 `/tmp→home rename` EXDEV는 별도 진단 코드에서 재현된 후보 원인이며 당시 원인의 확정은 아니다.
- **FFmpeg7.1.5/ffprobe/libx264와 Chromium153**이 있고 합성 H264 MP4의 실제 decode10프레임은 PASS다. 이는 카메라 실측 FPS·실물 영상 PASS가 아니다. 현 준비에서 새 의존성 설치는 필요 없었다.
- photos/videos 기본 경로는 uid1000/0700/non-symlink다. private bytes/keys/개인 프레임은 업로드하지 않는다.
- `BMO_Voice_Organized_112_v8.zip`은 Pi 수신 경로에 **미전달**이다. 최신 자산 계약은112개 후보/224WAV, 초기30개는 선정안이며 모든 SHA의 청취 승인이 아니다. 정리 ZIP 예상 SHA는 `9f5ca192a86dedd87a227418802108ee7e13e713b0faf8361e484cd39134cfa0`다. wake_ack_01은 이112개에 없다.
- 보드 mounted 파일의 `REMOTE_ENABLED=False`는 정적 관측이다. code.py `4e30cb8dd29f3af03867828da2fee7e1ea1c1e5099be725e7daa11b98f67ebae`, bridge `14fc42886a39f5b74fb7bc28e7eecf0914801b05e1fe5bd74cfdb3df0f0fcbfe`, boot.py `f59e20f97feef6eab364bdb305cb3d7d0639c7f3aa88a0f5e3ff517912c122eb`다. RAM·배선·전원·reviewed-enabled SHA로 확대하지 않는다. 포트 open/close까지 무reset 근거가 없어 HELLO/출력0이며 새 개방을 요구하지 않는다.

같은 Pi 댓글의 **05:27 KST 마감 갱신**에서는 새 후보가 여전히 DEPLOY_PENDING이며 실제 설치/실물 검수0회다. 정상본은 `0f46e3203afcc0d2bb7455cf49b3ac7ba0d4e978`/clean으로 보존됐다. Pi task는 종료됐고 상주 watcher나 댓글 이후 자동 재개는 없다. 준비한 private 인수 도구는 READY/동일 SHA CI·clean branch를 확인한 뒤에만 fast-forward하며, 다음 명령은 사용자가 아침에 수동 실행한다.

```bash
python3 -B /home/bgh/BMO_private/tools/intake_full_batch_candidate.py --apply && python3 -B /home/bgh/BMO_private/tools/run_bmo_candidate.py
```

이 private 도구는 Pi 담당의 인계이며 Cloud가 접근·실행·코드 검수했다고 주장하지 않는다. 직접 Git 인수를 원하면 아래 exact-SHA 명령을 사용한다.

## 최소 Pi 갱신·한 앱 실행·후보 아이콘

현재 앱을 메뉴 **종료**로 닫고 mic/camera/player/encoder cleanup 완료를 확인한 뒤 실행한다. 아래 `FINAL_FULL_BATCH_SHA`에는 최신 READY 댓글의40자리 전체 SHA를 넣는다. 확인 실패 시 중단하며 기존 작업이나 정상본을 덮지 않는다.

```bash
(
  set -eu
  bmo_full_sha='FINAL_FULL_BATCH_SHA'
  [[ "$bmo_full_sha" =~ ^[0-9a-f]{40}$ ]]
  cd /home/bgh/BMO_CHARACTER_LIFE
  test "$(git branch --show-current)" = feature/bmo-character-life-v1
  test -z "$(git status --porcelain=v1 --untracked-files=normal)"
  git fetch origin refs/heads/feature/bmo-character-life-v1
  git cat-file -e "$bmo_full_sha^{commit}"
  git merge --ff-only "$bmo_full_sha"
  test "$(git rev-parse HEAD)" = "$bmo_full_sha"
)
cd /home/bgh/BMO_CHARACTER_LIFE && ./launch-character-life
```

한 앱이 대화·카메라·영상·눈 트래킹·자체 게임을 요청 시 사용한다. 기본 실행은 팔 remote/wake/idle 자동 활성화를 추가하지 않는다. 수면 중의 제한된 무재생 기동·카메라 검수에는 다음 명령을 사용한다. Cloud는 이 명령을 Pi에서 실행하지 않는다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE
./launch-character-life --silent-start --no-feedback-voice --presence-mode off
```

`--silent-start`는 chime과 cached greeting 모두 재생하지 않으며 승인 자산 bytes나 평소 기본값을 바꾸지 않는다. `--no-feedback-voice`는 사진/영상의 준비·결과를 시각 안내로만 처리한다. wake 옵션을 추가하지 않고, 새 음성 질문/PTT·자동 음성 재생·마이크 녹음은 하지 않는다. `--no-startup-chime`만으로 다른 승인 greeting까지 무음이라고 가정하지 않는다. 사용자 실제 출력·청취는 아침에 한다. 기존 키는 정상 resolver를 사용하고 키/prefix/hash/전체 환경변수를 출력하거나 바꾸지 않는다.

Pi에서 보고한0775 Desktop/source launcher는 chmod하거나 안전 검사를 완화하지 않는다. 다음 dry-run은 기존 Desktop을 건드리지 않고 **별도 BMO Candidate** application entry, candidate SVG와 private0700 wrapper를 준비한다. wrapper는 검증한 기존 launch-desktop bytes를 데이터로 hash-check하며0775 source shell을 실행/복사하지 않는다. `.venv-device/bin/python` 또는 명시적 BMO_PYTHON, 같은 module·프로필·카메라/voice 설정을 사용한다. 기존 운영 BMO.desktop은 교체하지 않는다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE
./launch-character-life --install-desktop --desktop-install-target applications-only --dry-run
# Pi 담당이 위 후보 target/옵션을 검토한 뒤에만 같은 명령에서 --dry-run을 뺀다.
```

이미 다른 bytes의 후보 entry가 있으면 installer는 conflict로 중단하고 기존 파일을 유지한다. 검수하지 않은 임의 launcher, 소유자/권한이 불안전한 application data 경로, source hash 불일치는 우회하지 않는다. 시스템 autostart/service/reboot는 이 작업에서 바꾸지 않는다.

정상 종료와 장치 해제 뒤 보존 정상본으로 복귀하려면 기존 검수 명령을 쓴다. 후보와 정상본을 동시에 실행하지 않는다.

```bash
env -u FISH_API_KEY XDG_CONFIG_HOME=/home/bgh/.config BMO_PYTHON=/home/bgh/BMO_REAL_ROBOT/.venv/bin/python /home/bgh/BMO_REAL_ROBOT/launch-desktop --live --frontend chromium --screen-profile r10 --ptt-microphone
```

### 모드별 조작·아침 짧은 확인

1. **영상:** 메뉴/영어·한국어 요청→준비 대사 완료→실제로 보이는 preview→5초→REC/경과→GP8 또는 Stop→저장 MP4 play/pause/replay→GP8/Back으로 대화. 녹화 중 PTT/wake/idle/TTS는 억제한다. 기본30초/상한60초, 요청 길이 clamp는 표시한다. preview 고유 decode/display와 영상 source timestamp/frame/drop·컨테이너 재생 길이를 따로 기록한다. 설정20/헤더FPS/복제frame/Cloud 속도를 실측15fps로 보고하지 않는다.
2. **눈 따라오기:** `Look at me`/`Follow my face` 또는 한국어 요청/메뉴 toggle→얼굴은 계속 BMO, 로컬 위치만 눈에 적용. 사람이 좌우·가림·복귀를 짧게 확인한다. 사진/영상은 잠시 양보하고 Back 때 이전에 켜 둔 tracking만 복구한다. Stop tracking은 다른 preview owner를 끄지 않는다. 신원/감정 식별·자동 촬영은 없다.
3. **팔:** 먼저 `cd software && ../.venv-device/bin/python -B -m bmo_ai.arm_prepare`의 기본 read-only dry-run으로 현재 static/admission/지원 이름/중단 의미를 확인한다. 실제 reviewed-enabled 전체 SHA·현재 사용자 입회·전체 전원 차단 수단이 없으면 NEEDS_OPERATOR에서 끝낸다. 조건이 모두 확인된 새 감독 session에서만 도구가 제시한 기존 실행 명령을 사용하고 메뉴의 허용 이름1개·취소/HOLD·STOP을 검수한다. 좌우는 BMO 자신의 좌우다. HOLD는 현재 명령 위치, STOP은 session latch이며 물리 전원 차단이 아니다. GP7/GP8·GP10/11·600–2400µs/50Hz·기존 반전/보정을 그대로 둔다.
4. **짧은 대화/자막·선택 기능:** GP8 질문→thinking 취소→다음 질문·en/ko/off/repeat 확인. master volume/0/mute를 실제 출력에서 짧게 검수한다. 게임 Enter는 START, Esc/Back은 복귀이며 PTT가 아니다. 음악은 이미 제공된 로컬 검증 WAV만 사용한다. 자산이 없으면 정확한 빈 목록/ASSET_PENDING을 확인하며 다운로드·생성하지 않는다.

이번 밤 Pi 제한은 **새 사진≤1장·무음 영상≤1개/5초·preview/tracking 관측 합계≤30초**다. 성공 사진을 불필요하게 다시 찍지 않으며 인간 tracking/청취/실제 팔동작을 밤 자동검사로 PASS 처리하지 않는다. 수면 중 remote enable/flash/reset/재보정/GP7 자동 입력·실물 구동은 금지다. wake/tracking을 밤새 켜 두지 않는다.

### v8 import·선택과 볼륨

private 정리 ZIP이 전달된 경우만 후보 staging에 import한다. 기본 staging은 `~/.local/share/bmo/voice-pack-v8`이며 기존 제작/원본/구 bank를 덮지 않는다. `python -m bmo_ai.voice_pack --help`의 import/list/approve/select 도구와 같은 앱의 선택/승인 UI를 사용한다. provenance가 없으면 실제 제작 모델을 확인해 명시하고 Drama3를 live free 모델로 위장하지 않는다. 자동 개별 청취 승인·유료 재생성은 없다. 영상/tracking/wake 추가 대사 WAV 미전달은 각 이벤트만 대기하며 시각 안내/기존 적절한 전후 응답으로 앱을 계속 쓴다.

master volume은 시작0.25에서 사용자가0..1/mute를 조절한다. RAW1.0과 preview baked0.25를 구분해 총0.0625로 중복 감쇠하지 않는다. runtime volume은 캐시/model fingerprint와 분리되며 변경 때문에 새 TTS를 생성하지 않는다. 현재 음악은 단일 owned player를 멈춘 뒤 같은 PCM 위치/일시정지 상태에서 새 음량 copy로 잇는다. live/cache/feedback 진행 발화는 실제 UI 정책에 따라 취소/다음 재생에 적용하며 OS mixer를 조절했다고 표시하지 않는다. 승인 부팅음 파일·R10·Luna low/Aaron Soft/Beemo는 바꾸지 않는다.

개인 후보의 기본 요청 budget은 비활성이다. 공용 검수가 필요할 때만 `--request-cooldown`, `--session-request-limit`, 사용자가 정한 `--estimated-request-cost`/`--estimated-session-cost-limit`를 명시한다. 같은 Host의 동시성1/취소/once gate를 유지하며 cooldown·횟수·추정 상한은 새 mic/provider 작업 전에 적용한다. 추정 계수/상한은 provider의 실제 과금·잔액·일예산이 아니며 임의 요율을 정하지 않는다. 사용자가 명시한 설치 옵션만 후보 아이콘에 보존한다.

---

## 이전 사진·자막 후속 기록 — 당시 근거 보존

PR #40의 [새 사용자 지시](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6527145621)를 적용한다. 기존 사진 촬영 직후 자동 복귀 지시는 이 문서의 명시적 review 복귀로 대체한다.

**밤 RC 갱신:** [NIGHT CLOSEOUT](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6529744247)에 따라 Cloud가 같은 PR의 유일한 코드 writer다. Pi의 이미 끝난 진단을 반복하지 않으며, 이 문서 하나에 아침 후보 인계를 모은다. 운영 앱·바탕화면·OS·서비스·펌웨어를 밤중에 설치하거나 교체하지 않는다.

**사진 후속:** [CAMERA-FOCUS-PREVIEW-20261031](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6530863546)은 `d6d6865bb53bdf42db044f0cadba5ce487ec622b`의 새 실기 실패와 preview 개선 지시다. 앞선 밤 RC를 사진 촬영·초점·부드러운 preview 완료로 취급하지 않는다. 아래의 최신 후속 정책과 최종 HEAD 검증을 함께 따른다.

## 사용자 실기와 Cloud 검증의 구분

- 사용자 확인: GP8 초기/메뉴 복귀/얼굴 클릭 후 대화, thinking 취소와 새 대화, 사실 질문의 기본 표정 복귀. 현재 부팅음은 승인되어 변경하지 않았다.
- 사용자가 한국어로 보고한 화남 요청도 실제 BMO에는 영어로 말한 것이었다. 이를 한국어 화남 실기 PASS로 취급하지 않는다. 정확한 영어 `Get angry`에서 neutral/약한 억양이 관찰됐으나 실제 STT·모델 메타데이터·런타임 설정은 Cloud에서 확인하지 못했다.
- 영어 사진 요청 실패와 한국어 요청의 촬영 관찰은 기존 후보의 보고다. 저장 파일·새 review UX까지 승인됐다는 뜻이 아니다.
- Alt+Tab/VNC 이탈, 실제 영어 의미 이해·분노 억양·번역 정확도·촬영 품질은 자동 검사 PASS로 대체하지 않는다.
- 밤 지시의 최신 사용자 성공 관찰은 **새 감정·자연어 대화·자막·GP8·사실 질문 neutral 복귀·승인 부팅음**이다. 위의 과거 영어 분노 실패 관찰은 당시 기록으로 남긴다. 성공한 prompt·감정·자막을 새 재현 오류 없이 다시 개작하지 않는다.

## 밤 사진 진단의 확인 범위

[PI-NIGHT-HANDOFF-20261031](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6530152438)의 판정은 **PHOTO_CAUSE_UNCONFIRMED**다. 확인한 후보 폴더는 `bdaffef759b79206d8b914166d1a9957d3b7bf88`이고 깨끗했지만, 실제 앱 PID·실행 HEAD·cwd·interpreter는 식별되지 않았다. 폴더 HEAD나 Chromium 존재로 실행 상태를 추정하지 않는다.

기존 journal에 `camera_timeout / executed=true / completed=false / capture_attempted`가 있으나 이번 실패의 request_id와 일치하지 못했다. AF와 still capture가 같은 timeout code를 썼으므로 이 기록만으로 둘을 구분하지 않는다. 폴더 owner `bgh:bgh`/0700, journal 0600, 여유 약 448GB는 권한·공간 부족을 뒷받침하지 않는다. 기존 PNG 하나는 다른 요청이며 이번 사진 저장 성공의 증거가 아니다. 기존 사진·journal을 삭제하거나 chmod·재설치·일괄 timeout 연장으로 덮지 않는다.

Cloud의 SDK·취소·review 경계 검사는 별도 소스 근거다. 최종 보고에서 **재현하여 고친 코드 문제**와 **이번 Pi 실패 원인의 미확정**을 구분한다. 사진의 새 후보 결과는 `PI_RETEST_REQUIRED`이며, 이미 끝난 진단을 사용자에게 다시 시키지 않는다. 현장 근거가 더 필요하면 아침 한 번의 검수에서 해당 요청의 whitelist 상태와 journal만 일치시킨다.

같은 Pi 댓글의 추가 정적 확인에서 Picamera2 **0.3.37**의 `wait(job, timeout)`은 `Job.get_result()` → `Future.result(timeout)`이고, AF 완료값은 bool, still 완료값은 metadata dict였다. still Job에 저장과 preview 복귀가 포함되는 계약은 현재 adapter와 맞으며, 이 계약 자체가 실패 원인이라는 근거는 없다.

이번 RC는 기존 `camera_timeout` code·시간 한도·AF/권한/취소 검사를 유지하면서 `PhotoResult.stage`를 journal·whitelist 상태에 남기고 AF 대기와 still 대기 실패를 구분한다. 사용자 UI에는 짧은 stage별 이유만 표시하며 임의 예외·개인 경로를 내보내지 않는다. 저장 뒤 성공 음성 실패는 이미 저장된 사진 결과를 촬영 실패로 뒤집지 않는다. 이 instrumentation과 독립 재현 코드 수정은 현장 실패 원인의 확정을 대신하지 않는다.

독립 SDK 재현에서 **현재 촬영 구간 안에 노출된 still**도 PNG 인코딩·preview 복귀에 0.8초가 걸리면 tracking용 0.5초 frame-age 검사에 걸려 `stale_photo`가 됐다. still은 완료 시점의 preview 표시 나이 대신 현재 capture 시작~완료 구간과 노출 시각을 비교하도록 수정했다. 이전 요청의 노출·미래 timestamp·SDK focus/still timeout·취소 검사는 유지한다. 이는 별도로 재현한 소스 결함이며, request_id가 일치하지 않는 Pi의 `camera_timeout`을 해결했다는 뜻은 아니다.

호출어에서는 수신 때 신선도를 검증한 질문 PCM을 소비자가 700ms 늦게 읽으면 버리던 문제와, detector 오류 뒤 이전 마이크 close가 끝나기 전에 새 reader를 시작할 수 있던 경합을 고쳤다. 기존 wake 질문 큐의 유한 12초 범위를 일관되게 적용하고 cleanup 완료 전 재시작을 보류한다. 원래 GP8 신선도·수신 검사와 무간격 질문 제한은 유지한다.

## 새 focus 실패와 preview 제품 정책

사용자는 `d6d6865…`에서도 countdown 뒤 **“The camera could not finish focusing.”**와 촬영 실패를 관찰했다. 이 문구만으로 확인되는 것은 `focus` stage다. 이후 [PI-CAMERA-FOCUS-20261031](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6531063407)에서 과거 journal에 없던 새 요청 `spoken_photo_98e5763f74f05362bdac4fc4811d2cf2`의 **`camera_timeout / focus / executed=true / completed=false / capture_attempted`**, 예상 PNG 없음이 확인됐다. 이 새 기록은 AF False의 `photo_focus_failed`나 SDK 예외의 `camera_focus_failed`가 아닌 **AF 대기 deadline 실패**로 좁혀진다. AF가 끝나지 않은 근본 이유는 계속 미확정이다.

폴더 HEAD는 `d6d6865bb53bdf42db044f0cadba5ce487ec622b`이고 깨끗했지만 live PID·실행 HEAD/interpreter·화면 request_id와 직접 대조하지 못했다. 따라서 사용자가 본 바로 그 요청과의 일치에는 이 한계가 있다. journal mtime **2026-10-31 03:38:52 KST**를 AF 시작/종료·elapsed로 쓰지 않는다. AfMode/AfState/LensPosition·실제 반환값/타입·동일 요청 preview fps는 미수집이다. 설정된 2.5초를 실측 AF 시간으로 보고하지 않으며 과거 stage 없는 timeout도 재사용하지 않는다.

반면 느린 preview에는 소스 근거가 있다. 이전 live feature의 고정 **4Hz gate**가 화면 preview와 tracking을 함께 제한했고, 매 프레임 PNG 생성/검증과 전체 JSON poll이 전달 경로에 있었다. 이는 확정된 소프트웨어 제한이며 센서·Pi 화면·VNC의 실측 fps가 아니다.

이번 후속의 제품 정책은 다음과 같다. 기존 한 CameraBroker/Picamera2 인스턴스·서버·UI를 유지하며 대화·감정·자막·GP8·승인 부팅음은 동결한다.

- AF를 preview/countdown 동안 진행·관측하고 현재 stream의 완료 상태를 사용한다. countdown 직후 매번 새 scan부터 시작하지 않으며 still 전환에서 초점/렌즈 위치 보존을 검사한다. 성공 bool이나 metadata가 없다고 임의로 `focus_ok=True`를 만들지 않는다.
- `AfMode`가 매 프레임 metadata에 없을 수 있으므로 유효한 `AfState=Focused`와 현재 렌즈 값으로 판정한다. 누락된 `af_mode`는 `None`으로 남기고 설정한 모드를 실측처럼 채우지 않는다. Refocus는 여전히 새 Scanning→Focused 관측을 요구한다.
- 살아 있는 stream에서 AF 작업이 종료됐지만 품질 확신이 낮으면 **Focus uncertain — Take anyway / Refocus / Back**을 제공한다. 사용자의 Take anyway는 현재 request/generation의 새 **1280×720 still**만 저장하고 초점 미확인으로 표시한다. preview 이미지를 사진 성공으로 저장하거나 흐릿한 결과를 초점 성공으로 선언하지 않는다.
- Refocus는 사용자 선택에 따른 한 번의 명시적 시도이며 자동 반복하지 않는다. 취소·Back·late result·repeat로 촬영 권한을 재생성하지 않는다. 미완료 AF job·timeout·cleanup unknown·장치 fault는 단순 품질 불확실과 구분하고, 정리 미확인 작업과 still capture를 병행하지 않는다.
- 화면 preview는 **640×360 JPEG, 목표 20fps**, tracking은 별도 **4Hz**다. 기존 SDK `capture_file(..., format='jpeg')`의 **소프트웨어 인코딩**을 비동기 producer에서 처리하며 MJPEG/hardware encoder로 바꿨다고 주장하지 않는다. buffer capacity는 1이고 느린 소비자에게 오래된 프레임을 쌓지 않고 drop한다. 실제 사진 저장 해상도·PNG review는 preview와 분리해 보존한다.
- frame 생산·인코딩·serve, browser 수신·decode·display의 서로 다른 프레임 수와 frame age를 각각 관측한다. 센서 설정값·HTTP 요청 수·requestAnimationFrame 호출 수만으로 화면 fps를 보고하지 않는다. **Pi 화면의 서로 다른 유효 프레임 15fps 이상은 인수 목표이며 현재 실측은 NOT_MEASURED**다. Cloud/headless 합성 수치를 Pi/VNC 성능으로 바꾸지 않는다.
- Back·review·취소·종료에서는 producer·encoder·HTTP 소비 루프와 기존 카메라 소유권을 정리한다. latest-frame endpoint는 기존 loopback 접근 제어와 캐시된 bytes만 사용하며 HTTP 요청이 두 번째 카메라 owner를 만들지 않는다. review는 live stream이 아닌 저장한 정지 사진을 유지한다.

자동 검사는 주입한 SDK/시계/프레임/브라우저 경계의 AF 상태·취소·중복 방지·버퍼/소유권 정리를 확인한다. 실물 AF·촬영 품질·Pi 화면 15fps·VNC 지연은 사용자의 다음 한 번의 현장 검수 항목이다. 정확한 SDK 실패 원인이 아직 없으면 제품 정책 개선과 현장 root-cause 확정을 분리한다.

### 현재 진단 필드와 해석

`camera.focus`의 whitelist는 `status/code/stream_generation/observed_at/sensor_timestamp/af_mode/af_state/lens_position/af_started_at/af_finished_at/elapsed/can_take_anyway`다. AF mode/state는 고정 enum이고 현재 stream의 신선한 관측만 사용한다. preview에서는 Continuous를 사용하고 명시적 Refocus는 Auto+Start 한 번 뒤 **새 Auto/Scanning 관측 → Focused/Failed**를 확인하므로 이전 focused frame을 새 시도의 완료로 쓰지 않는다. shutter에서는 새 autofocus_cycle을 시작하지 않고 현재 초점을 Manual과 관측 lens position으로 고정해 still configuration에도 넘긴다. 지원하지 않는 AF·불확실·장치 fault는 별도 상태다.

사진 stream을 닫은 뒤에도 Host의 `photo_focus`와 준비 상태 메뉴에는 **선택/촬영 직전의 whitelist 관측**을 남긴다. `observation=before_capture_or_choice`는 그 시점의 관측이며 실제 실패 순간의 AF trace나 사후 물리 초점 증거가 아니다. 실패 원인은 해당 요청의 `photo_diagnostic.stage/code`와 journal로 비교한다.

`camera.preview_pipeline`에는 `sensor_frames_observed/encoded/published/served/dropped_replaced/capacity/target_hz/worker_alive/physical_fps`가 있다. sensor counter는 이 producer가 metadata와 함께 받은 서로 다른 preview frame 관측이며 센서의 전체 노출 수나 하드웨어 FrameRate 실측이 아니다. `served`는 서버가 제공한 프레임이며 브라우저 decode/display 수와 다르다. browser의 고유 frame ID별 수신·decode·display/age를 따로 비교하고 `physical_fps=NOT_MEASURED`를 유지한다. frame HTTP는 `/api/preview/frame?after=<epoch:seq>`에서 캐시된 최신 bytes만 읽고 새 프레임이 없으면 204다. 처리·취소·Back/종료를 포함한 단일 producer/worker 회귀는 Pi에서의 실제 속도 측정을 대신하지 않는다.

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

## 밤 RC의 실행·설치 계획 확인

`launch-character-life`는 후보 `.venv-device/bin/python`으로 한 Desktop을 실행하고 R10·기존 대화/자막·GP8/WebRTC·요청 시 사진/영상을 선택한다. 설치 계획이 쓰는 기존 `launch-desktop`도 `--character-life`가 있으면 같은 `.venv-device`를 고르게 수정했다. 이전에는 바탕화면 실행만 `.venv`로 달라질 수 있었다. 명시적 `BMO_PYTHON`은 계속 우선하고 일반 실행의 `.venv`는 보존한다. 후보 interpreter가 없다고 다른 환경으로 조용히 fallback하지 않는다.

기존 installer의 **읽기 전용 계획**은 다음과 같다. 실제 Pi의 기존 아이콘·XDG 상태는 Pi 인계로 확인해야 하며, 계획 출력만으로 클릭 성공을 승인하지 않는다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE && ./launch-character-life --install-desktop --dry-run
```

`files_written=0`, `activation=NOT_RUN`, autostart/service 변경 없음과 후보 checkout·camera/wake 옵션을 검토한다. 이 밤 작업에서 `--dry-run`을 빼거나 운영 아이콘을 덮어쓰지 않는다. 기존 아이콘과 충돌하면 그대로 보존한다. R10 원본은 기존 screen-profile registry 경로를 읽고 private 자산·키는 설치 계획에 넣지 않는다. arm 옵션은 runtime 감독 조건이므로 installer에서 계속 명시적으로 거부한다. 후보 아이콘에 물리 확인을 영구 저장하지 않는다.

Pi의 최종 읽기 전용 인계는 다음과 같다. 자산의 존재·내부 완전성·사람 승인은 각각 구분하며, 새 음성 생성·재생·복사·승인·제작 lock 변경을 하지 않는다.

- 설치된 승인 부팅음 `~/.config/bmo/startup_chime.wav`의 SHA256은 `ba72dce47853da5a3da5df9e0e65e2b7f9c147720b6653c2ac24845ea1f26082`, PCM16/mono/48kHz/1.28초다. **현재 승인본을 보존하며 다른 세션의 대안 `461967…` WAV로 교체하지 않는다.**
- 기본 ACK `~/.cache/bmo/wake-ack-v1/greeting.cache`는 없다. 따라서 기본 경로는 `VISUAL_LISTENING` fallback이며 다른 세션의 제작 실패/종료나 다른 경로의 승인 상태를 추정하지 않는다.
- 기존 `~/.cache/bmo/character-voice-bank-v1/s2.1-pro-free/`의 6개 번들은 모두 `approved=false`, `simulated=false`다. WAV/cue SHA·spec fingerprint 내부 일치만 확인됐으며 현재 config 호환성·청취 승인은 아니다. 남아 있는 generation/bank/ledger lock 파일을 획득·삭제·해제하지 않는다.
- 기존 wake manifest와 `.venv-device`의 `openwakeword 0.6.0`·`onnxruntime 1.31.1` metadata가 있다. 저장 WAV 추론 기록은 `OPERATIONAL_INFERENCE_PASS`지만 무라벨 PCM·활성화 0회이므로 사람 정확도 PASS가 아니다. manifest 지원 문구는 **Hey Beemo**, 사람 검수 `NOT_RUN`, 한국식 발음 `UNVERIFIED`다.
- `.venv-device`는 Python 3.13.5/system-site이고 정적 launcher의 interpreter·cwd·R10·사진/영상 경로와 일치한다. 실행 PID·override·실제 config와 모든 장치 owner의 해제 여부까지 확인된 것은 아니다. 모듈 metadata 확인을 runtime import/실기 성공으로 바꾸지 않는다.

## Pi 후보 갱신·실행과 정상 작동본 복귀

아침 사용자가 기존 앱의 메뉴 **종료**로 정상 종료하고 자원 정리를 확인한 뒤 실행한다. `/home/bgh/BMO_REAL_ROBOT`은 보존하고 후보만 갱신한다. 미커밋 변경·다른 브랜치·분기한 커밋이 있으면 중단하고 보존·통합한다. 정상 환경의 의존성·음성팩·부팅음·펌웨어를 다시 설치하지 않는다. 아래 `FINAL_RC_SHA`는 **이번 focus/preview 후속 완료 보고**의 검토된 전체 SHA로 치환하며 과거 `d6d6865…`나 폴더 HEAD를 그대로 쓰지 않는다.

```bash
(
  set -eu
  bmo_rc_sha='FINAL_RC_SHA'
  [[ "$bmo_rc_sha" =~ ^[0-9a-f]{40}$ ]]
  cd /home/bgh/BMO_CHARACTER_LIFE
  test "$(git branch --show-current)" = feature/bmo-character-life-v1
  test -z "$(git status --porcelain=v1 --untracked-files=normal)"
  git fetch origin refs/heads/feature/bmo-character-life-v1
  git cat-file -e "$bmo_rc_sha^{commit}"
  git merge --ff-only "$bmo_rc_sha"
  test "$(git rev-parse HEAD)" = "$bmo_rc_sha"
  test -x .venv-device/bin/python
)
```

기본 PTT 후보 실행은 한 줄이다. 실행 자체가 실제 장치 사용·기존 유료 대화 경로의 사용자 감독 시작이며 밤에는 자동 실행하지 않았다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE && ./launch-character-life
```

선택 wake는 같은 앱에 더한다. 아래 manifest 존재·runtime metadata는 Pi 인계에서 확인됐지만 실제 사람 인식률은 `NOT_RUN`이며 ACK 기본 자산은 없다. 사용자가 이 후보의 **Hey Beemo**를 검수하고, 누락·오류이면 PTT로 계속한다. 다른 모델을 자동 다운로드하지 않는다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE && ./launch-character-life --wake-word --wake-question-wait 5 --wake-model-manifest /home/bgh/.local/state/bmo/wake-model-candidates/amrmantawi-bmo-openwakeword-58eb9404df18/manifest.json
```

후보를 닫을 때 메뉴 **종료**를 사용하고 cleanup 완료를 확인한다. cleanup이 미확인되면 두 번째 앱이나 장치 owner를 열지 않는다. 정상 종료 뒤 기존 정상 작동본으로 돌아갈 때는 [기존 검수에 기록된 명령](DEVICE_ACCEPTANCE_20261029_KR.md)을 그대로 사용한다. 이번 밤에 정상 작동본을 수정·재시작하거나 실행 interpreter를 관측한 것은 아니다.

```bash
cd /home/bgh/BMO_REAL_ROBOT && BMO_PYTHON="$PWD/.venv-camera/bin/python" ./launch-desktop --live --windowed --ptt-microphone --ptt-trace
```

이 복귀에는 Git reset·펌웨어 복원·서비스 재시작·private 자산 삭제가 필요 없다. `.venv-camera`나 기록된 런처가 없으면 추정 경로로 대체하지 않고 기존 실제 실행 경로를 확인한다.

## 아침 최소 네 시나리오

1. **사진 한 장과 preview:** 실제 preview에서 같은 10초 구간의 서로 다른 생산/encoded/served 프레임과 browser decoded/displayed 프레임·age를 구분한다. Pi 자체 화면의 ≥15fps 목표와 VNC 전달 지연을 따로 기록하고 계측이 없으면 NOT_MEASURED다. 자연스러운 영어 사진 요청 → 표시 ACK → 5초 중 AF 진행 → 필요하면 현재 요청의 Take anyway 또는 명시적 Refocus 한 번 → 새 1280×720 still → 저장 PNG review 유지 → 저장 성공 뒤에만 짧은 대사 → repeat 재촬영 없음 → GP8/Back 복귀를 확인한다. 실패하면 같은 요청의 stage/code·AF 상태/elapsed·journal·파일 존재 여부만 일치시켜 기록하고 자동 재촬영하지 않는다. 취소·Back 뒤 AF/stream 정리와 late result 차단도 관련 경로만 확인한다.
2. **성공 기능 비회귀:** 기존 자연어 대화·감정 뒤 `two plus two` neutral, en/ko/off 자막·repeat·thinking 취소 후 다음 GP8 질문을 짧게 비교한다. 실제 억양·번역·하단 가림은 사람이 판단한다. 승인 부팅음·고정 하늘색 배경/원본 등장·R10/v13.2 원본은 변경하지 않는다. 이미 성공한 하드웨어 전체 검사는 반복하지 않는다.
3. **선택 wake:** 기존 모델/자산이 확인된 경우 호출 → ACK 또는 listening → 새 질문 → 질문 종료 후 thinking, 무질문 timeout·취소를 확인한다. 모호한 앞부분을 버린 뒤 뒷부분만 STT로 보내지 않으며, 무간격 호출+질문의 전체 보존은 계속 미검증/제한이다. 누락·오류·OFF 뒤 GP8는 유지한다.
4. **팔은 별도 감독 조건만:** 현재 Pi의 board SHA/REMOTE_ENABLED 정적 관찰과 실제 B1 adapter 상태를 source/preflight/transport로 나누고, 과거 disabled/커넥터 분리를 현재 상태로 복사하지 않는다. 실제 연결·기구 간섭·즉시 전원 차단 수단·현재 전체 SHA의 검토가 준비되지 않으면 `physical_verified=false / NEEDS_OPERATOR`에서 멈춘다. 조건 충족 후에만 기존 B1 명명 gesture 하나와 기존 취소/STOP 흐름을 감독 검수하고, 문제 시 전체 전원을 즉시 차단한다. STOP ACK는 전원 차단·위치 확인이 아니며 GP7 수동 기능과 기존 보정을 보존한다.

구형 static `arm_readiness()`의 BLOCKED는 기존 소스 gate이며 현재 B1 adapter runtime과 같지 않다. adapter의 software preflight, HELLO 전 UNVERIFIED, 검증된 ACK의 transport usable, STOP/close의 BLOCKED를 따로 표시하고 어느 상태에서도 `physical_verified`를 true로 추정하지 않는다.

Pi가 재사용한 보드 파일 관측은 **2026-10-30의 정적 기록**이다. `code.py` SHA `4e30cb8dd29f3af03867828da2fee7e1ea1c1e5099be725e7daa11b98f67ebae`/`REMOTE_ENABLED=False`, bridge SHA `14fc42886a39f5b74fb7bc28e7eecf0914801b05e1fe5bd74cfdb3df0f0fcbfe`였으며 10/31 현재 runtime·배선의 증거가 아니다. 마지막 실제 B1은 10/29 HELLO 1회 → DISABLED/POSITION_UNVERIFIED, motion 0이다. **현재 사용할 수 있는 reviewed-enabled 전체 SHA는 미확인**이므로 과거 값을 감독 인자에 채우지 않는다. reset/재부팅도 원격 disabled만으로 물리 안전을 보장하지 않는다.

팔 감독 실행의 기존 인자는 `--arms-supervised --arm-legacy-bridge --arm-reviewed-code-sha <현재_검토한_전체_code.py_SHA256> --arm-power-off-ready`다. 실제 승인 SHA를 채우거나 원격 펌웨어를 켜지 않는다. `--emotion-arms`는 같은 감독 실행에서만 선택한다. 무인 모션·펌웨어 write/reset·재보정 없이 준비만 하며 B1 ACK를 실물 움직임 PASS로 바꾸지 않는다.

위 조건이 준비된 단일 감독 시험은 `--windowed`와 기존 팔 인자로 같은 앱을 실행하고, 기존 앱의 `BMODesktop.intent('arm_gesture','wave')` 한 번만 요청하는 절차다. 자동 감정 모션을 늘리지 않도록 `--emotion-arms`는 이 한 번의 명명 시험에 필요하지 않다. 기존 `BMODesktop.control('cancel')`은 HOLD, 메뉴 **정지** 또는 `BMODesktop.control('stop')`은 현재 세션 STOP latch다. DISABLED/STOPPED/불명 ACK·timeout·boot 변경·cleanup 불명·예상치 못한 움직임에서 재요청·자동 재시도하지 않고 감독자가 중단한다. 이 명령 형식은 준비 자료이며 이번 밤에 실행하지 않았다.

Luna low/history/output, Aaron Soft, Beemo, gain 0.25, GP7/GP8와 GP10/GP11·600–2400µs·50Hz·좌우 반전·서보 보정, 별도 음성팩은 보존했다. 새로운 게임·카메라 엔진·이미지 클라우드 업로드·팔 활성화는 없다. 유한 제품 회귀의 주입 IO 결과와 실제 장치 검수는 분리한다. 공개 전시 비용 상한·rate limit은 별도 승인/미검수 사항이며 공개 운영 준비 완료로 보고하지 않는다. 최종 SHA와 해당 HEAD의 Python 3.11/3.13·제품 브라우저 CI는 PR #40의 최신 focus/preview 후속 완료 보고를 기준으로 한다.
