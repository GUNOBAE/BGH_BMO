# v10 음악 후보 인수 절차

현재 개발 브랜치 `feature/bmo-character-life-v1`, 기존 PR #40을 이어간다.
배포 SHA와 해당 SHA의 CI는 PR의 최신 구현 인계 댓글이 최종 기준이다.
문서 브랜치로 checkout하지 않는다. Cloud는 코드 writer, Pi는 private 자산·
설정·설치·실기 담당이다. 정상본 `/home/bgh/BMO_REAL_ROBOT`은 보존한다.

근거: [공통 작업서](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6534211753),
[v10 문서](https://github.com/GUNOBAE/BGH_BMO/blob/2023f3a9c12ce4c0628afdfc8deda4a8831ec241/docs/voice-pack-v10/README_KR.md),
[Pi 실제 인계](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6534420182).

## 2026-11-05 후속 후보 — 저장 음성·자막·혼잣말·얼굴 조작

시작 기준 원격은 `d833470d73d6a1b7eed01a0e437236efc6bb47af`, clean이며
push/PR 필수 검사 6개가 모두 PASS였다. [후속 Pi 인계](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6652308031)는
기존 private 아이콘/GUI 세션 복구를 확인했다. 사용자는 아이콘부터 음악 두 모드,
pause/seek/resume·volume/mute·GP8 short/hold·대화 복귀·자연 종료·아이콘 재실행까지
14개를 실제 PASS했다. [최신 Pi 인계6653292587](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6653292587)는
이 PASS를 설치본 `43262705b9cb520056694d34521cde74bec01220`에 연결했다. 원격 d833 또는
이번 새 후보의 실기 PASS로 옮기지 않는다. 아래 과거
NOT_RUN/차단 기록은 역사이며 현재 미해결 상태로 승계하지 않는다.

이번 후보의 변경:

- 실제 선택된 클립 ID·원문과 SHA 검증 PCM을 묶어 로컬 EN/KO 자막을 전달한다.
  prepared에서는 숨기고 player-start에 표시한다. 종료/취소/오류/새 질문/OFF에서
  정리한다. boot/idle overlay도 현재 자막 설정을 따른다. 재생마다 번역/TTS 없음.
- 전체142 사용 허용은 `listened`/`approved`와 별도다. 대표10 재청취를 요구하지
  않는다. M04–06은 선택곡 준비, M01–03은 BMO의 곡 선택에 쓴다. 작은 후보군은
  한 바퀴씩 섞고 직전 반복을 피한다. 조회는 선택 이력/파일을 변경하지 않는다.
  선택은 실제 이벤트 load에서 한 번만 확정한다. 명시 수동 pin/OFF/해제/SHA를
  유지한다. 과거 batch와 같은 값의 자동 pin은 origins 기록으로 이관하며 원본
  selection.json을 보존한다. 구버전에서 같은 값으로 수동 저장한 경우 출처를
  구분할 기록이 없다는 한계가 있다. 새 수동 pin은 항상 구분해 보존한다.
- low intensity에서도 맥락에 맞는 짧은 v10 반응을 기존 단일 WAV/플레이어에
  합성한다. 발화3회 간격·같은 반응 반복 억제·진지한 고민·이미 포함된 웃음
  중복 억제를 유지한다. Ta-da 성공 문구는 실제 저장 성공 뒤에만 허용한다.
  raw 자산은 바꾸지 않는다. 실제 음량/길이/억양 비교와 필요한 상대 trim은 청취 대기다.
- 기존 PresenceScheduler normal은 3–7분 기회 판단, 실제 시작 기준 15분에
  최대2회, 같은 idle 클립 30분 제외다. 대화/음악/게임/녹화/mute/최근 고민/설정
  조작 중 억제하고 GP8이 우선한다. history/repeat를 바꾸거나 GPT/Fish를 부르지
  않는다. 기존 한 번의 의미 응답으로 혼잣말 off/quiet/normal과 메뉴 open/close를
  연결한다. 재시작 기본값은 기존 off를 유지하며 Pi가 normal을 명시 선택한다.
- 얼굴 오른쪽 아래 터치 → −/현재%/+/Mute, 5%p 단위, 약3초 무조작 후 숨김.
  기존 MasterVolume의 실제 값과 음악 overlay를 공유한다. 음악은 즉시 적용,
  대화 WAV는 다음 재생부터 적용, mute/0은 현재 발화를 취소한다. 시작25%,
  GP7/GP8, 승인 얼굴과 기존 음악 입 움직임은 보존한다.

검증: 로컬 Python3.13 전체 **1,946개 PASS**, 기존 음악/v10·character-life 및
새 얼굴 조작/저장 음성 자막의 실제 synthetic Chromium 회귀 PASS. 새 브라우저 검사를
public-browser CI에도 포함한다. 최종 exact HEAD의 push/PR 결과는 최신 PR 인계 기준이다.

### Pi 최소 인수

1. Exit로 종료하고 아래 기존 dirty/ff-only 후보 갱신 절차에 최신 PR 인계의
   **exact SHA**를 넣는다. `BMO_REAL_ROBOT`과 private runner/GUI 세션 복구는 유지한다.
2. 이미 등록한142개를 다시 import하지 않는다. 사용자가 요청한 전체 사용 허용을
   적용하거나, 음성 메뉴의 **Use all saved voices**에서 한 번 적용한다. 개별 청취
   기록을 만들지 않으며 재생·유료 API도 호출하지 않는다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.voice_pack \
 --root /home/bgh/BMO_private/runtime/voice-v10 accept-full-use --user-accepted
python3 -B /home/bgh/BMO_private/tools/run_bmo_candidate.py
```

3. 복구된 같은 아이콘/위 private 실행 도구를 사용한다. 혼잣말은 메뉴에서 normal
   (드물게 말하기), 또는 “다시 가끔 말해”로 현재 세션에 켠다. 다음 실행에도 켤
   경우 Pi 담당이 기존 private launcher 인자에 `--presence-mode normal`을 추가한다.
   GUI 세션 wrapper를 우회하거나 OS/서비스/원래 runtime directory 권한을 바꾸지 않는다.
4. 전체 사용 해제는 기존 메뉴 버튼 또는 아래 명령이며 개별 승인과 원본 파일은
   유지한다. 정상본 복귀 명령은 이 문서 아래 기존 절차와 같다.

```bash
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.voice_pack \
 --root /home/bgh/BMO_private/runtime/voice-v10 disable-release
```

추가 실기는 네 묶음만 확인한다: (1) KO 캐시 자막/음성 일치와 취소/OFF 정리,
(2) 선택곡 준비·BMO 곡 선택의 다양한 대사와 자연스러운 짧은 반응,
(3) normal 혼잣말의 간격·GP8 우선·끄기/다시 켜기,
(4) 얼굴 볼륨창·음악 즉시 적용·mute 취소 후 다음 대화.
Cloud는 주입 IO와 합성 PCM/제품 Chromium을 검사하며 실제 청취/실기 승인을
대신하지 않는다. 새 노래 얼굴은 다른 WORK의 승인 결과가 올 때까지 보존한다.

### 142개 연결 상태

아래 **연결75 / 조건부66 / 제외1**은 코드 이벤트 생산자 기준이다. 등록·전체
사용 허용·개별 청취 승인·실제 재생은 다른 상태다. 조건부는 해당 맥락 또는
추가 실제 이벤트 생산자가 필요하며 임의로 다른 이벤트의 대사로 섞지 않는다.
정확한 원문/번역/후보군/감정 조건은
[로컬 메타데이터](../../software/bmo_ai/policies/voice-pack-v10-context.json)에 있다.

<details>
<summary>전체142개 ID별 이벤트와 이유</summary>

| ID | 상태 | 이벤트 | 이유 |
|---|---|---|---|
| B01 | 조건부 | boot | Requires explicit boot-voice selection; the currently approved boot greeting and sound are preserved. |
| B02 | 조건부 | boot | Requires explicit boot-voice selection; the currently approved boot greeting and sound are preserved. |
| B03 | 조건부 | boot | Requires explicit boot-voice selection; the currently approved boot greeting and sound are preserved. |
| B04 | 조건부 | boot | Requires explicit boot-voice selection; the currently approved boot greeting and sound are preserved. |
| B05 | 조건부 | boot | Requires explicit boot-voice selection; the currently approved boot greeting and sound are preserved. |
| B06 | 조건부 | boot | Requires explicit boot-voice selection; the currently approved boot greeting and sound are preserved. |
| G01 | 연결 | game_menu | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| G02 | 연결 | game_start | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| G03 | 연결 | game_exit | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| G04 | 연결 | game_launch_failed | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| G05 | 연결 | game_win | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| G06 | 연결 | game_loss | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| G07 | 조건부 | game_controller_lost | Requires an observed controller disconnect; never infer from game loss. |
| M01 | 연결 | music_random | BMO chooses a ready song through the explicit random-song action; not used for a user-selected song. |
| M02 | 연결 | music_random | BMO chooses a ready song through the explicit random-song action; not used for a user-selected song. |
| M03 | 연결 | music_random | BMO chooses a ready song through the explicit random-song action; not used for a user-selected song. |
| M04 | 연결 | music_selected | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| M05 | 연결 | music_selected | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| M06 | 연결 | music_selected | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| M07 | 조건부 | music_paused | Requires confirmed pause and a free audio owner; never overlap music. |
| M08 | 조건부 | music_resumed | Requires an owned resume announcement before music resumes. |
| M09 | 조건부 | music_next | Requires explicit next-song selection; not natural playlist completion. |
| M10 | 연결 | music_exit | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_READY_01 | 연결 | photo_ready | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_READY_02 | 연결 | photo_ready | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_READY_03 | 연결 | photo_ready | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_READY_04 | 조건부 | photo_group_ready | Requires an explicit group-photo context; do not infer multiple people. |
| PH_READY_05 | 연결 | photo_ready | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_READY_06 | 연결 | photo_ready | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_COUNTDOWN_05S | 연결 | photo_countdown | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_REVIEW_01 | 연결 | photo_saved | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_REVIEW_02 | 연결 | photo_saved | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_REVIEW_03 | 연결 | photo_saved | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_REVIEW_04 | 연결 | photo_saved | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_REVIEW_05 | 연결 | photo_saved | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_REVIEW_06 | 연결 | photo_saved | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_RETAKE_01 | 조건부 | photo_retake | Requires a new explicit retake request; repeat cannot take a photo. |
| PH_RETAKE_02 | 조건부 | photo_retake | Requires a new explicit retake request; repeat cannot take a photo. |
| PH_RETURN_GUIDE_01 | 조건부 | photo_return_guide | Requires a saved-photo review and a one-time return hint. |
| PH_EXIT_01 | 연결 | photo_exit | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_EXIT_02 | 연결 | photo_exit | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_EXIT_03 | 연결 | photo_exit | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_CANCEL_01 | 연결 | photo_cancelled | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| E01 | 조건부 | error_unrecognized | Retained for an explicit unrecognized-speech event; no automatic STT-error cache producer is connected. |
| E02 | 조건부 | error_no_speech | Retained for a no-speech event; current empty-input handling remains silent. |
| E03 | 조건부 | error_microphone | Retained for a confirmed microphone failure; no automatic microphone-error cache producer is connected. |
| E04 | 조건부 | clarification | Requires a current clarification question; not a device failure. |
| E05 | 조건부 | waiting_reply | Requires a measured pending reply; never loop a waiting announcement. |
| E06 | 조건부 | error_timeout | Requires an actual response timeout; not an arbitrary service failure. |
| E07 | 조건부 | error_network | Retained for a confirmed network failure; no automatic network-error cache producer is connected. |
| E08 | 조건부 | error_service, error_authentication, error_rate_limit | Retained for a confirmed response-service failure; no automatic provider-error cache producer is connected. |
| E09 | 조건부 | unsupported_action | Requires an unsupported requested action. |
| E10 | 조건부 | error_action | Requires a confirmed generic action failure. |
| E11 | 조건부 | error_music_missing | Retained for an unknown-song result; current Host reports this through its generic music-error path. |
| E12 | 조건부 | error_music_empty | Retained for an empty music library; no separate empty-library voice producer is connected. |
| E13 | 연결 | error_music | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| E14 | 연결 | error_camera | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| E15 | 연결 | error_capture | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| E16 | 연결 | error_storage | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| RX01 | 연결 | reaction_chuckle | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| RX02 | 연결 | reaction_gasp | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| RX03 | 연결 | reaction_playful_puff | Explicit acting only; never a spontaneous reaction to a serious concern. |
| RX04 | 연결 | reaction_sob | Explicit acting only; never a spontaneous reaction to a serious concern. |
| RX05 | 조건부 | reaction_cheer | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| RX06 | 연결 | reaction_hmm | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| RX07 | 연결 | reaction_aha | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| RX08 | 조건부 | reaction_whoa | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| C01 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C02 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C03 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C04 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C05 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C06 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C07 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C08 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C09 | 조건부 | idle | 13.189s exceeds the initial 10s automatic-idle length limit; retained for explicit review. |
| C10 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C11 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C12 | 조건부 | idle | 11.099s exceeds the initial 10s automatic-idle length limit; retained for explicit review. |
| C13 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C14 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C15 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C16 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| F01 | 조건부 | goodbye | Requires an explicit farewell; not app termination by inference. |
| F02 | 조건부 | goodbye | Requires an explicit farewell; not app termination by inference. |
| F03 | 조건부 | goodbye | Requires an explicit farewell; not app termination by inference. |
| F04 | 조건부 | sleep | Requires an explicit sleep transition. |
| F05 | 조건부 | goodnight | Requires an explicit goodnight context. |
| F06 | 조건부 | goodnight | Requires an explicit goodnight context. |
| C17 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C18 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| A01 | 조건부 | utility | Requires an explicit talk-button help context. |
| A02 | 조건부 | quiet | Retained for an explicit quiet acknowledgement; current presence changes do not start a second cached reply. |
| A03 | 조건부 | recovered | Requires an observed recovery; never claim success after an error. |
| wake_ack_01 | 연결 | wake_ack | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| REACTION_LINE_CHUCKLE_SILLY | 조건부 | reaction_silly | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_OOH_INTERESTING | 연결 | reaction_interesting | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_AHA_IDEA | 조건부 | reaction_idea | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_OH_SURPRISE | 조건부 | reaction_surprise | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_HMM_MYSTERY | 조건부 | reaction_mystery | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_POUT_NO_FAIR | 조건부 | reaction_unfair | Explicit acting only; never a spontaneous reaction to a serious concern. |
| REACTION_LINE_WHEW_RELIEF | 조건부 | reaction_relief | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_TADA_SUCCESS | 연결 | reaction_tada, photo_saved, video_saved | Only after a verified successful photo or video save; generic dialogue cannot claim a successful action. |
| REACTION_LINE_GIGGLE_FUNNY | 조건부 | reaction_funny | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_AWW_KIND | 조건부 | reaction_kind | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_OOPS_MY_MISTAKE | 연결 | reaction_oops | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_GASP_AMAZING | 조건부 | reaction_amazing | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_PROUD_HELP | 조건부 | reaction_proud | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_SOFT_SOB_ACTING | 조건부 | reaction_sob_acting | Explicit acting only; never a spontaneous reaction to a serious concern. |
| VIDEO_PREP_MOVIE | 연결 | video_ready | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| VIDEO_PREP_READY | 연결 | video_ready | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| VIDEO_PREP_ACTING | 연결 | video_ready | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| VIDEO_RECORDING_STARTED | 제외 | video_recording_started | Recording suppresses speech; retain this asset for manual review but do not auto-play it during capture. |
| VIDEO_TAKE_FINISHED | 조건부 | video_stopped | Retained for an observed recording-stop event; current flow waits for save feedback and does not announce cut separately. |
| VIDEO_SAVED_PRODUCTION | 연결 | video_saved | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| VIDEO_REVIEW_WATCH | 조건부 | video_review | Requires a saved playable video and an explicit review hint. |
| VIDEO_RETAKE | 조건부 | video_retake | Requires a new explicit video retake request. |
| VIDEO_ERROR_START | 연결 | video_start_failed | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| VIDEO_ERROR_SAVE | 연결 | video_save_failed | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| VIDEO_LIMIT_REACHED | 조건부 | video_limit | Retained for an observed recording time limit; no separate time-limit voice producer is connected. |
| VIDEO_CAMERA_BUSY | 조건부 | video_camera_busy | Retained for a camera-busy result; no separate busy-camera voice producer is connected. |
| VIDEO_NOT_RECORDING | 조건부 | video_not_recording | Retained for a stop request with no recording; no separate not-recording voice producer is connected. |
| VIDEO_REVIEW_EXIT | 조건부 | video_exit | Retained for a completed video-review exit; current Back returns silently. |
| TRACK_START | 연결 | tracking_started | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| TRACK_TARGET_FOUND | 조건부 | tracking_found | Requires a real tracker found event with deduplication. |
| TRACK_TARGET_LOST | 조건부 | tracking_lost | Requires a real tracker lost event with deduplication. |
| TRACK_TARGET_RETURNED | 조건부 | tracking_returned | Requires a real tracker return event with deduplication. |
| TRACK_STOP | 연결 | tracking_stopped | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| TRACK_UNAVAILABLE | 연결 | tracking_failed | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| TRACK_NO_TARGET_YET | 조건부 | tracking_no_target | Requires a qualified no-target state; no repeated search chatter. |
| MUSIC_CHOOSE_MODE | 조건부 | music_choose_mode | Requires an explicit choice prompt before song selection. |
| MUSIC_REPEAT_SELECTED | 조건부 | music_repeat | Requires explicit song replay; not dialogue repeat. |
| MUSIC_PLAYLIST_FINISHED | 연결 | music_finished | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| SYSTEM_VOLUME_UP | 연결 | volume_up | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| SYSTEM_VOLUME_DOWN | 연결 | volume_down | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PHOTO_FOCUS_UNCERTAIN | 연결 | focus_uncertain | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PHOTO_REFOCUS_ONCE | 조건부 | focus_retry | Retained for a new explicit refocus attempt; current refocus does not announce this cue. |
| SYSTEM_STORAGE_FULL | 연결 | storage_full | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| SYSTEM_SAVED_FILE_OPEN_FAILED | 연결 | error_file | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| SYSTEM_CAMERA_OFF | 조건부 | camera_off | Retained for a completed camera-off operation; current off control returns silently. |
| SYSTEM_VOLUME_MAX | 연결 | volume_max | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| SYSTEM_NOTHING_TO_REPEAT | 조건부 | nothing_to_repeat | Retained for an empty repeat cache; current empty-repeat handling does not play a separate cue. |

</details>

<details>
<summary>이전 집중 검토 기록 — 아래 상태는 당시 기준</summary>

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

</details>

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

기존 초기60+자연종료1개 묶음은 호환 유지한다. 현재 전체142 사용 허용은
위 인수 절차를 따르며 대표 클립 재청취를 요구하지 않는다. 묶음 해제/이벤트별 OFF·ON이
가능하며, 개별 SHA 미리듣기·청취 승인은 별도로 유지한다. 묶음 수락을 개별
청취61회로 기록하지 않는다. ACK·5초 카운트도 해당 권한이 있어야 한다.
`--wake-word`는 custom detector 경로가 준비되고
사람이 원하는 세션에만 덧붙인다. idle는 위 normal 명시 선택 절차를 따르며 새 음성 생성은 하지 않는다.
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

## 이전 후보 검수 목록 (역사 기록; 현재 추가 검수는 위 네 묶음)

1. 음악 Video 화면/overlay, GP8 short pause/resume, hold 종료 후 keyup이
   대화를 시작하지 않는지 확인하고 새 GP8로 대화한다.
2. 대표 미리듣기 중 취소 시 다음 클립/묶음 완료로 넘어가지 않는지 확인한다.
   실제 청취 뒤 사용자가 묶음을 수락하고 OFF/ON·전체 해제와 개별 기록 보존을 본다.
3. M04→음악, 수동 종료→M10 1회, 별도 자연 종료→종료 음성1회. 출력 중첩 없이
   pause/seek/replay/Back 후 다음 대화가 가능한지 실제 청취한다.
4. 이전 runtime_directory_permissions가 재발하면 현재 소유자/권한만 확인하고
   세션 변경 주체를 좁게 진단한다. guard 우회·전체 chmod·정상본 변경은 하지 않는다.
