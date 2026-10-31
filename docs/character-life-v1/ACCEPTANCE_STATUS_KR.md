# Character Life v1 — 24개 인수 시나리오 검증 대응표

**2026-10-30 현장 후속:** [현장 표정·wake·등장 회귀 보고](FIELD_REGRESSIONS_20261030_KR.md)에 새 실제 제품 경로 검사를 연결했다. R10 headless 원본은 검사했으나 LCD/사람 발화 인수는 별개다. 완전 무간격 호출+질문은 word alignment 없이 보수적으로 자동 질문 처리하지 않는다.

**최신 핵심 후속:** [저장 WAV VAD·실제 무료 WAV 6개·실제 ONNX 후보와 수동 검수](REMAINING_CORE_20261029_KR.md)를 추가했다. 아래 표의 실제 Fish 미생성/호출어 MODEL_MISSING은 초기 검사 시점의 기록이며, 현재 자산 준비와 사람의 청취·발화 정확도 미승인을 구분한다.

**2026-10-29 후속 실제 검수:** [실제 장치 보고서](DEVICE_ACCEPTANCE_20261029_KR.md)에 마이크 PCM·취소/재열기, 사진 3장·영상 1개, 로컬 시험음/루프백, DISABLED B1 HELLO 결과를 추가했다. 아래 표는 최초 장치 없는 인수 검사의 대응표로 보존한다. 새 실기가 사람 발화·Aaron 청취·실제 얼굴 추적·팔 이동 승인을 뜻하지 않는다.

기준은 [VOICE_BANK_AND_ACCEPTANCE_KR.md](VOICE_BANK_AND_ACCEPTANCE_KR.md)의 **최소 인수 시나리오 24개**다. 아래 상태는 실제 작성된 assertion과 실행된 장치 없는 검사 범위에 한정한다. 이 문서는 최종 HEAD의 전체 회귀·CI 성공을 대신하지 않는다. 최종 커밋 SHA, 최종 회귀 수와 CI 링크는 작업 최종 보고서에서 확인한다.

- `OFFLINE_PASS`: 해당 소프트웨어 조건을 직접 검사하는 목표 검사가 통과했다. 입력·provider·오디오·카메라·모델 경계는 합성 또는 주입이다.
- `PARTIAL`: 일부 구성 요소의 assertion만 있으며 시나리오 전체가 연결된 검사는 없거나, 필요한 모델·실기 검증이 남아 있다.
- `NOT_RUN`: 해당 실제 검증을 실행하지 않았다. 누락된 측정값을 fixture 시간이나 추정으로 채우지 않는다.

**모든 행의 실제 LLM 감정 선택, 실제 Fish 생성·Aaron 청취 승인, 실제 GP8/마이크/스피커/카메라/팔, 호출어 음향 정확도는 `NOT_RUN`이다.** `OFFLINE_PASS`는 이러한 실기 승인이 아니다. 실행 중인 `BMO_REAL_ROBOT`의 성공 기록과 이번 후보 코드의 검증을 합산하지 않는다.

## 파일·클래스 참조

표의 `별칭.메서드`는 다음 파일의 실제 클래스·메서드를 가리킨다. `Expr.test_twenty_four_fixed_contexts_use_one_reply_and_bounded_performance`는 자체 24개 대화 fixture를 가진 **하나의 테스트 메서드**다. 그 fixture 24개가 작업서의 서로 다른 인수 시나리오 24개를 모두 수행했다는 뜻은 아니다.

| 별칭 | 파일 | 실제 클래스/함수 |
|---|---|---|
| Expr | [test_character_expression.py](../../software/tests/test_character_expression.py) | `CharacterExpressionTests` |
| Session | [test_character_expression.py](../../software/tests/test_character_expression.py) | `CharacterSessionContinuityTests` |
| Host | [test_character_host.py](../../software/tests/test_character_host.py) | `CharacterHostTests` |
| Desktop | [test_character_desktop.py](../../software/tests/test_character_desktop.py) | `CharacterDesktopTests` |
| PTT | [test_character_ptt_timing.py](../../software/tests/test_character_ptt_timing.py) | `CharacterPttTimingTests` |
| Bank | [test_character_voice_bank.py](../../software/tests/test_character_voice_bank.py) | `CharacterVoiceBankTests` |
| Reaction | [test_character_cached_reaction.py](../../software/tests/test_character_cached_reaction.py) | `CharacterCachedReactionTests` |
| Schedule | [test_character_presence.py](../../software/tests/test_character_presence.py) | `PresenceSchedulerTests` |
| Presence | [test_character_presence.py](../../software/tests/test_character_presence.py) | `CachedPresenceTests` |
| WakeModel | [test_character_wake_word.py](../../software/tests/test_character_wake_word.py) | `WakeManifestTests` |
| MicOwner | [test_character_wake_word.py](../../software/tests/test_character_wake_word.py) | `MicrophoneOwnerTests` |
| WakeHost | [test_character_wake_host.py](../../software/tests/test_character_wake_host.py) | `HostWakeTests` |
| Continuity | [test_emotion_continuity_v2.py](../../software/tests/test_emotion_continuity_v2.py) | `EmotionContinuityV2Tests` |
| LegacyHost | [test_feature_host.py](../../software/tests/test_feature_host.py) | `FeatureHostTests` |
| Camera | [test_feature_camera.py](../../software/tests/test_feature_camera.py) | `CameraTests` |
| Tracker | [test_feature_camera.py](../../software/tests/test_feature_camera.py) | `TargetTrackerTests` |
| Close | [test_feature_host_close.py](../../software/tests/test_feature_host_close.py) | `FeatureHostCloseTests` |
| Video | [test_video_host_integration.py](../../software/tests/test_video_host_integration.py) | `VideoHostIntegrationTests` |
| Browser | [check_character_life_browser.py](../../software/checks/check_character_life_browser.py) | `run(profile, chromium)`의 직접 assertion |

## 24개 시나리오

| # | 작업서 시나리오 | 현재 상태 | 실제 검사와 확인 범위 / 남은 조건 |
|---|---|---|---|
| 01 | 명시적으로 “기뻐해 봐” | `OFFLINE_PASS` | `Expr.test_explicit_joy_has_a_happy_face_without_forced_body_motion`에서 한국어·영어 명시 요청을 neutral provider fixture로 받아도 happy/excited로 연결하고 팔을 강제하지 않는다. 최신 관련 31개 검사에서 통과했다. 실제 모델이 자연스럽게 happy를 선택하는지는 `NOT_RUN`. |
| 02 | “장난스럽게 화내 봐” | `OFFLINE_PASS` | `Expr.test_twenty_four_fixed_contexts_use_one_reply_and_bounded_performance`, `Host.test_acting_face_changes_at_player_start_and_repeat_has_no_new_provider_or_motion`에서 sad fixture도 angry/theatrical_angry로 연결한다. Browser `run`은 동일 요청이 실제 Host/session/외부 얼굴 API까지 도달하는 것을 검사한다. 실제 Aaron 분노 연기는 `NOT_RUN`. |
| 03 | “우는 연기를 해봐” | `OFFLINE_PASS` | `Expr.test_twenty_four_fixed_contexts_use_one_reply_and_bounded_performance`, `Expr.test_voice_uses_congruent_direction_with_unchanged_identity_and_one_synthesis`, `Expr.test_explicit_acting_is_not_suppressed_by_automatic_reaction_quota`와 Browser `run`이 crying/theatrical_crying·sobbing 지시·얼굴을 확인한다. 주입한 합성기 1회이며 실제 울음 음질은 `NOT_RUN`. |
| 04 | 평범한 사실 질문 | `OFFLINE_PASS` | `Expr.test_twenty_four_fixed_contexts_use_one_reply_and_bounded_performance`의 fact/calm fixture는 neutral/calm·reaction none이다. `Expr.test_prior_acting_request_cannot_authorize_a_later_fact`는 이전 연기 요청이 다음 사실 질문의 연기 권한이 되지 않음을 확인한다. 실제 답변 품질은 `NOT_RUN`. |
| 05 | 실제 고민/상실 이야기 | `OFFLINE_PASS` | `Host.test_real_distress_suppresses_comic_reaction_and_body_flourish` 및 Expr의 고정 맥락 검사는 happy/crying/angry fixture를 차분한 empathetic 계획으로 제한하고 comic reaction·팔 flourish를 없앤다. 실제 사용자의 상황 이해는 `NOT_RUN`. |
| 06 | 좋은 소식/성공 | `OFFLINE_PASS` | Expr의 고정 맥락 검사에서 small success → happy/proud와 축하 계획을 확인한다. `LegacyHost.test_opt_in_emotion_gesture_happens_once_at_real_player_start`는 주입한 player-start에서 제스처 1회 및 repeat 미실행을 확인한다. Reaction의 `test_existing_quota_cooldown_and_explicit_acting_authorization_remain_active`는 자동 반응 쿼터와 캐시 cooldown을 확인한다. 실제 축하의 자연스러움은 `NOT_RUN`. |
| 07 | 웃긴 상황 | `OFFLINE_PASS` | Expr의 funny fixture와 `Expr.test_explicit_acting_is_not_suppressed_by_automatic_reaction_quota`, `Reaction.test_existing_quota_cooldown_and_explicit_acting_authorization_remain_active`가 짧은 chuckle 선택·자동 반복 억제를 검사한다. 실제 웃음 소리는 `NOT_RUN`. |
| 08 | 놀라운 이야기 | `OFFLINE_PASS` | Expr의 surprise fixture는 surprised/curious/gasp를 유지하면서 reaction과 gesture 동시 선택을 없앤다. `Reaction.test_all_four_reaction_mappings_use_one_final_wav_and_single_start`는 gasp를 포함한 반응의 단일 WAV/재생 시작을 검사한다. 실제 놀람 소리는 `NOT_RUN`. |
| 09 | crying 뒤 listen → think | `OFFLINE_PASS` | `Continuity.test_all_six_emotions_survive_long_idle_listening_thinking_and_prepared`는 crying을 포함한 6개 감정을 idle/listening/thinking/prepared에서 보존한다. `Session.test_actual_player_start_commits_plan_and_repeat_preserves_it_without_new_request`는 이전 crying에서 다음 angry가 주입한 실제 player-start 콜백 때만 바뀌는 것을 확인한다. 물리 재생 시각은 `NOT_RUN`. |
| 10 | `/repeat` | `OFFLINE_PASS` | `Reaction.test_repeat_reuses_combined_wav_without_new_selection_synthesis_or_history_charge`는 결합 WAV bytes/path와 1회 선택·생성·기록을 보존한다. `Host.test_acting_face_changes_at_player_start_and_repeat_has_no_new_provider_or_motion`, Browser `run`은 얼굴 유지·provider 재호출 없음·motion 재실행 없음을 검사한다. |
| 11 | idle clip 중 GP8 | `OFFLINE_PASS` | `Desktop.test_gp8_interrupts_idle_clip_and_starts_exactly_one_existing_microphone_turn`과 Browser `run`은 캐시 취소 후 기존 source/STT/brain 경로 1회, 최대 player/mic owner 1개, heard quota 미소비를 검사한다. 실제 HID·마이크 연동은 `NOT_RUN`. |
| 12 | boot 인사 중 사용자 중단 | `OFFLINE_PASS` | `Desktop.test_question_interrupts_boot_and_never_automatically_restarts_greeting`, `Desktop.test_explicit_boot_cancel_leaves_no_dialogue_cache_or_history_and_no_auto_restart`, `Desktop.test_gp8_before_face_presentation_suppresses_pending_boot_and_preserves_the_question`이 인사 취소·자동 재인사 금지·질문 보존을 검사한다. |
| 13 | 빠른 GP8 연타 | `OFFLINE_PASS` | `Desktop.test_cached_boot_gp8_double_edge_preempts_once_without_player_or_microphone_overlap` 및 Browser `run`이 중복 Enter/GP8 경계 후 source·STT·brain 1회와 플레이어 중첩 없음을 확인한다. 실제 버튼 bounce 파형·USB 이벤트는 `NOT_RUN`. |
| 14 | 말하기 첫 음절/짧은 머뭇거림 | `OFFLINE_PASS` | `PTT.test_known_threshold_starts_without_unused_calibration_wait_and_keeps_first_syllable`, `PTT.test_advancing_monotonic_clock_accepts_actual_first_frame_without_retimestamp`, `PTT.test_short_hesitation_and_final_syllable_stay_in_one_capture`, `PTT.test_short_initial_word_before_qualifying_phrase_is_never_trimmed_away`가 합성 PCM의 첫 샘플·짧은 pause·마지막 음절·원래 VAD tail을 보존한다. 실제 사람 음성에서의 잘림과 지연은 `NOT_RUN`. |
| 15 | wake + GP8 동시 발생 | `OFFLINE_PASS` | `WakeHost.test_simultaneous_manual_ptt_retires_pending_wake_without_cancelling_capture`, `WakeHost.test_ready_boundary_wake_queues_existing_ptt_and_uses_one_shared_reader`, `MicOwner.test_repeated_detector_hits_make_one_pending_host_talk`가 주입한 hit 경계에서 한 turn·한 reader와 stale wake 폐기를 검사한다. 실제 호출어 탐지는 `NOT_RUN`. |
| 16 | BMO 스스로 “BMO”를 말함 | `PARTIAL` | `MicOwner.test_playback_suppresses_detector_and_closes_owned_mic_then_cools_down`, `WakeHost.test_boot_audio_and_foreground_modes_prevent_persistent_mic_open`은 재생 소유권·cooldown에 따른 억제 구조를 검사한다. 실제 Aaron의 “BMO/Beemo” 녹음을 모델에 넣은 false activation 검사와 스피커 누설 검사는 `NOT_RUN`; 자가 호출 성능 PASS로 표시하지 않는다. |
| 17 | wake model missing/error | `OFFLINE_PASS` | `WakeModel.test_constructor_snapshot_missing_model_do_not_import_or_load`, `WakeModel.test_optional_runtime_missing_does_not_become_model_ready`, `WakeModel.test_detector_error_disables_without_leaking_exception`, `WakeHost.test_construction_and_boot_do_not_load_or_open_and_missing_model_preserves_ptt`가 정확한 실패 상태·자동 마이크 미개방·기존 PTT 사용을 확인한다. 실제 custom 모델은 `MODEL_MISSING`. |
| 18 | wake OFF | `OFFLINE_PASS` | `MicOwner.test_wake_off_stops_persistent_source_and_detector_ptt_still_works`, `WakeHost.test_wake_off_is_explicit_and_keeps_finite_gp8_available`, `MicOwner.test_finite_cleanup_failure_blocks_every_new_owner`는 reader/source/detector 종료·유한 PTT 복귀·미확인 정리 시 새 owner 차단을 확인한다. 실제 ALSA 종료는 `NOT_RUN`. |
| 19 | 캐시 없음/미승인/손상 | `OFFLINE_PASS` | `Bank.test_missing_clips_never_synthesize_or_claim_approval`, `Bank.test_gain_once_sha_approval_and_simulated_provenance`, `Bank.test_corrupt_bundle_refuses_runtime_and_stays_invalid`, `Presence.test_missing_or_corrupt_boot_falls_back_only_before_actual_start`가 SHA/승인/합성 구분과 안전한 fallback을 확인한다. `Reaction.test_missing_unapproved_or_corrupt_cache_keeps_original_live_reaction_direction`의 fallback은 기존 한 번의 대화 TTS 방향을 유지하는 검사이며 별도 캐시 생성 API가 아니다. 실제 음성팩은 미생성이다. |
| 20 | 영어 boot + reload | `OFFLINE_PASS` | Browser `run`은 `Starting BMO…`/`Getting ready…`, 실제 checkpoint 100%, 인사 전 550 ms wrapper 등장 1회, reload 후 등장 0회·추가 인사 없음·원본 얼굴 SHA 보존을 검사한다. 합성·R10·v13.2 profile을 분리해 실행했다. 실제 바탕화면·스피커 검수는 `NOT_RUN`. |
| 21 | 강한 sad/crying 뒤 idle | `OFFLINE_PASS` | `Schedule.test_modes_strong_emotions_and_all_foreground_owners_suppress_voice`, `Schedule.test_recent_distress_and_user_input_reset_idle_without_counting_a_clip`, `Presence.test_strong_or_sensitive_idle_never_selects_or_falls_back`, `Bank.test_idle_emotion_context_cooldown_and_distress_silence`는 crying/angry·최근 distress·quiet에서 침묵하고 sad에는 허용 mood만 고르는 것을 검사한다. 실제 대화에서 sensitive 분류의 정확도는 `NOT_RUN`. |
| 22 | camera 가림/target 상실 | `PARTIAL` | `Camera.test_stale_or_frozen_frames_do_not_refresh_preview_or_gaze`, `Tracker.test_loss_grace_expiration_returns_none_not_frozen_gaze`는 loss를 좌표 없음으로 처리한다. `WakeHost.test_idle_gaze_is_small_short_lived_and_yields_to_camera_manual_and_native`, `Host.test_dialogue_gaze_precedes_camera_only_while_speaking_and_native_input_wins`는 bounded gaze와 우선권을 따로 검사한다. 카메라 가림 → 대화 지속 → 관측했다고 허위 발화하지 않음의 전체 연결 시나리오는 `NOT_RUN`. |
| 23 | arms unavailable/disabled | `PARTIAL` | `LegacyHost.test_emotion_arms_disabled_keeps_existing_no_gesture_behavior`는 disabled 상태에서 팔 submit 0회를 확인한다. `Host.test_acting_face_changes_at_player_start_and_repeat_has_no_new_provider_or_motion`은 팔 없이 얼굴·대화를 유지한다. 실제 B1 unavailable 오류와 새 Character Life 전체 경로를 한 fixture로 묶은 검사 및 실물 팔 검수는 `NOT_RUN`; 움직였다는 성공 주장은 하지 않는다. |
| 24 | 게임/미디어/영상/종료 경합 | `OFFLINE_PASS` | `Schedule.test_modes_strong_emotions_and_all_foreground_owners_suppress_voice`, `WakeHost.test_boot_audio_and_foreground_modes_prevent_persistent_mic_open`, `LegacyHost.test_crying_conversation_survives_game_music_and_photo_returns_with_same_brain`, `Video.test_recording_blocks_question_ptt_repeat_without_audio_or_stt`, `Video.test_game_transition_cancels_video_before_broker_off`, `Close.test_close_during_owned_mock_player_releases_focus_and_rejects_late_input`가 독립 소유권·전환·정리 조건을 검사한다. 실제 게임 자산·실제 음악·카메라/오디오 장치 경합은 `NOT_RUN`. |

## 지연·음성·호출어 보고 한계

`PTT.test_trace_retains_host_receipt_and_distinct_source_frame_vad_close_observations`, `PTT.test_marker_clock_validation_and_stale_turn_cannot_fabricate_latency`와 `WakeHost.test_queue_receipt_is_host_clock_and_retained_across_turn_admission`는 **주입한 host clock**으로 구간 계산과 누락 표시를 검사한다. Fish transport의 fake HTTP 검사는 first body byte/download/validation 표식을 구분한다. 이 숫자는 Pi의 실제 STT/GPT/Fish 속도가 아니다. 실제 host의 버튼 수신·첫 PCM·VAD·STT·GPT·Fish·player-start 계측은 다음 감독 실행에서 수집해야 한다. 물리 GP8 접점 시각, 실제 발화 종료와 실제 스피커 첫 소리는 `UNMEASURED`다.

32개 대본은 후보이며 새 실제 Aaron WAV를 생성하지 않았다. 테스트의 `approved=true`는 임시 합성 PCM 경로를 검사하기 위한 값이다. 실제 사용자 청취 승인을 대신하지 않는다. `test_character_cached_reaction.py`의 주입한 합성기가 `source="fish"`를 반환하는 것도 기존 성공·쿼터 분기를 검사하는 장치일 뿐 실제 Fish 응답이 아니다. 최종 상태는 `AUDIO_NOT_GENERATED` / `LISTENING_NOT_APPROVED`로 보고한다.

실제 custom 호출어 classifier와 정확한 frontend가 없으므로 `MODEL_MISSING`이다. fake detector 및 모델 shape fixture의 PASS로 `Hey BMO / Hey Beemo / 헤이 비모` 인식 완료를 주장하지 않는다. 모델 확보·ARM64 optional runtime·정확도/오인식률·자가 호출·OFF 후 실제 마이크 종료는 [WAKE_WORD_SETUP_KR.md](WAKE_WORD_SETUP_KR.md)의 별도 감독 검수 항목이다.
