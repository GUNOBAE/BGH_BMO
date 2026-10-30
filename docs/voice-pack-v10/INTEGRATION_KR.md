# v10 실제 음성팩 → 현재 BMO 통합 인계

이 문서는 구현 담당에게 넘기는 설계·차이 분석이다. 이 문서 작성 작업은 runtime 코드를 수정하지 않았다.

## 1. 확인한 기준과 범위

확인한 PR #40 HEAD: `91665e4dd19229d6144ff04d4a398b0181a98270`.

근거:
- [당시 완료 보고](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6533380761)
- [voice_pack.py](https://github.com/GUNOBAE/BGH_BMO/blob/91665e4dd19229d6144ff04d4a398b0181a98270/software/bmo_ai/voice_pack.py)
- [master_volume.py](https://github.com/GUNOBAE/BGH_BMO/blob/91665e4dd19229d6144ff04d4a398b0181a98270/software/bmo_ai/master_volume.py)
- [wake_ack.py](https://github.com/GUNOBAE/BGH_BMO/blob/91665e4dd19229d6144ff04d4a398b0181a98270/software/bmo_ai/wake_ack.py)

기존 OrganizedVoicePack, 개별 해시 승인, 단일 playback/AudioFocus, MasterVolume, 사진·영상·tracking 흐름을 재사용한다. 이 SHA는 관측 기준이지 현재 개발을 되돌릴 checkout 지시가 아니다. 이후 변경이 있으면 현재 코드와 다시 대조한다.

## 2. 실제 v10은 현 v8 importer와 그대로 호환되지 않는다

최종 ZIP의 `catalogue.json`은 `schema=bmo.voice_asset_handoff.v10` 및 `not_runtime_compatible_manifest=true`를 명시한다. 현재 importer는 v8용 `clips/candidates/items`를 찾는다. SHA만 교체하거나 검사만 끄는 것으로 해결하지 않는다.

| v10 필드/속성 | 현재 importer 입력/처리 | 필요한 적응 |
|---|---|---|
| 최상위 entries | clips / candidates / items | v10 버전을 명시 판별해 entries를 정규화 |
| clip_id | clip_id; candidate_id/id | 선택된 한 후보의 안정적 ID 유지. A/B를 재확장하지 않음 |
| spoken_text | text / sentence / script | 실제 발화문으로 매핑 |
| file + sha256 + baked_gain | files[{path,sha256,baked_gain}] 또는 raw_path | ZIP의 catalogue 위치 기준으로 경로 해석, gain 명시 |
| requested_model=drama-3 | source_model / model / provenance.model | 요청 출처를 명시적으로 기록; live 모델에서 추정하지 않음 |
| voice_id | 기존 Aaron ID | 동일성 검증 유지 |
| approvals.listening_approved=false | 별도 approvals.json | 자동 승인으로 변환하지 않음 |
| local_edit/local_edit_record | 현재 일반 PCM | 카운트 편집 이력·변경 SHA 보존 |
| source_pack/source_job_id/source_raw_sha256 | job/provenance | 출처 및 감사 기록 보존 |
| face_direction_draft / phrase_timecodes_status | cue_review=UNRESOLVED | 구절 anchor를 참고 자료로 보존, 임의 초 단위 cue 생성 금지 |

142 WAV 모두 원본 레벨 1.0이며 RAW/LEGACY_PREVIEW_GAIN025 폴더 구조가 아니다. 따라서 경로 문자열로 gain을 추측하면 안 된다. 전체 ZIP SHA와 각 WAV SHA를 모두 검증한다. ZIP의 HTML, Python, synthesis_text, 연출 지시를 importer가 실행하거나 유료 합성에 넘기지 않는다.

### 비언어 RX01

RX01의 spoken_text는 빈 문자열이고 실제 내용은 짧은 웃음이다. 현재 importer의 `0 < len(text)` 검사를 그대로 적용하면 실패한다. 명시적인 비언어 kind/allowlist 또는 별도의 UI 표시문을 두되, 표시문을 실제 발화/자막/재합성 대상으로 바꾸지 않는다. 빈 text를 이유로 웃음을 재생성하지 않는다.

### 긴 혼잣말

현재 새 `_pcm`은 30초까지, 구 VoiceBank는 10초까지다. C09=13.189초, C12=11.099초는 새 경로의 길이 범위 안에 있다. 다만 idle 선택·등록은 별도이며 기본 OFF다. 구 bank로 우회 등록하거나 무조건 속도 조절/재생성을 하지 않는다.

## 3. 먼저 고칠 event 의미 연결

아래는 확인한 소스의 EVENT_CANDIDATES와 실제 v10 대사를 대조해 발견한 불일치다. 현재 이 잘못된 매핑이 사용자 기기에서 실제 발화됐다고 관찰한 것은 아니다. **파일 승인 전에 수정·회귀검사가 필요하다.**

| 현재 event → ID | 실제 그 파일의 의미 | 권장 연결/처리 |
|---|---|---|
| error_network → E01 | 말을 알아듣지 못함 | 실제 인터넷 단절은 E07 |
| error_service → E03 | 마이크 실패 | 온라인 답변 서비스 불가는 E08 |
| error_camera → E07 | 인터넷 단절 | 카메라 준비 실패 E14, 촬영 실패 E15를 별도 구분 |
| error_storage → E08 | 온라인 답변 불가 | 사진 저장 실패 E16; 공간 부족 SYSTEM_STORAGE_FULL 별도 |
| error_authentication → E10 | 일반 기능 실패 | 인증 원인은 화면/로그에 명확히 표시. 음성 필요시 해당 서비스에 맞는 일반 실패 대사만 |
| error_rate_limit → E14 | 카메라 준비 실패 | 온라인 응답 서비스 상황이면 E08 같은 일반 안내, rate limit 원인은 화면/로그 |
| error_playback → E15 | 사진 촬영 실패 | 사진 실패 음성 금지. 음악은 E13; 출력 장치 실패면 음성 재시도 대신 화면 |
| error_file → E16 | 사진 저장 실패 | 저장 파일 열기 실패는 SYSTEM_SAVED_FILE_OPEN_FAILED |
| game_start → G01/G02 | 메뉴 선택 / 실제 시작 | menu G01, actual launch G02 분리 |
| game_result → G04 | 게임 실행 불가 | launch failure G04; 실제 승리 G05/패배 G06 별도 이벤트일 때만 |
| goodbye → F04/F05 | 기기 잠듦 / 밤 인사 | 일반 대화 작별·앱 종료·밤 인사를 구분 |

`_matches`는 row.events OR 기본 EVENT_CANDIDATES를 사용한다. 올바른 events를 메타데이터에 추가하는 것만으로 잘못된 기본 매핑이 사라지지 않는다. 기존 잘못된 별칭을 제거/교정하고, 유효한 이벤트에 정해진 의미의 대사만 선택되는지 검사한다.

## 4. v10 첫 연결 목표

[초기 60개](INITIAL_SELECTION_60.json)는 개발 우선순위이지 승인 배치가 아니다. 기존 142개 모두 보관하되 상태를 imported/unapproved/approved/selected/played로 나눠 보고한다.

| 실제 사건 | 기본 후보 | 재생 조건 |
|---|---|---|
| 사진 preview 준비 완료 | PH_READY_03 | 화면 preview 준비와 현재 요청 유효성 확인 후 |
| 사진 카운트 | PH_COUNTDOWN_05S | 편집본 청취와 5초 UI 동기화 승인 후 |
| 새 사진 저장·review 표시 | PH_REVIEW_02 | 현재 사진 저장+표시 확인 후 |
| 명시 재촬영 | PH_RETAKE_01 | 새 요청에서만; repeat는 재촬영 안 함 |
| 사진→얼굴 복귀 | PH_EXIT_02 | 실제 얼굴 복귀 후 |
| 음악 랜덤 / 지정곡 | M01 / M04 | 허용 곡 선택 후; 안내 완료→재생 |
| 음악 완전 정지 | M10 | 실제 중지 후; 매 자동 곡 전환에 발화 금지 |
| 영상 시작 준비 | VIDEO_PREP_ACTING | recording 시작 전에 종료 |
| 영상 정지 완료 | VIDEO_TAKE_FINISHED | 실제 recording 종료 뒤; 아직 저장 성공을 뜻하지 않음 |
| 영상 finalize·저장 성공 | VIDEO_SAVED_PRODUCTION | 유효 파일 확인 후 |
| 영상 시작/저장 실패 | VIDEO_ERROR_START / VIDEO_ERROR_SAVE | 실패 단계에 맞춰 선택 |
| 추적 시작/중지/실패 | TRACK_START / TRACK_STOP / TRACK_UNAVAILABLE | 실제 상태 변경 확인 후 |
| 호출어 확인 | wake_ack_01 | 별도 wake 계약/승인/echo handoff 검증 후 |
| 볼륨 증가/감소 | SYSTEM_VOLUME_UP / SYSTEM_VOLUME_DOWN | 사용자 음성 명령 성공 뒤. slider마다 말하지 않음 |
| 초점 품질 불확실 | PHOTO_FOCUS_UNCERTAIN | 실제 Take anyway 선택을 허용할 때만 |
| 명시 초점 재시도 | PHOTO_REFOCUS_ONCE | 허용된 한 번의 refocus 요청에 한정 |
| 웃음/놀람/삐침/울음 | RX01 / RX02 / RX03 / RX04 | 기존 director 맥락·연기 권한 유지 |

소스에서 영상/tracking/volume/focus는 비어 있는 후보 tuple로 남아 있다. 새 파일은 이제 존재한다. 원하는 event 이름을 현재 allowlist와 실제 callback에 맞게 확장하고, 빈 항목을 랜덤 문장으로 채우지 않는다. 카테고리명만으로 success·failure를 혼합하지 않는다.

## 5. Wake ACK

정확한 대본 `Yeah? What's up?`, clip `wake_ack_01`, 1.764717초, SHA와 경로는 PACKAGE_RECORD.json에 있다. v8 시점의 '음성 없음'을 그대로 유지하지 말고 **파일 있음 / 등록·개별 청취·실물 연결 미완료**로 구분한다.

현재 OrganizedVoicePack은 source_model을 별도로 보관하지만 snapshot의 wake_ack_available은 false이고, 별도 WakeAckCache는 여전히 config에서 만든 spec으로 검증한다. 캐시 모델을 무료 모델로 위장하거나 일반 대화 모델을 Drama3로 바꾸지 않는다. 실제 제작 provenance와 승인 SHA를 검증한 pack asset을 기존 wake 오디오 소유자에 명시적으로 전달하는 확장을 검토한다. 미연결·미승인 시 기존 visual listening 유지, 자동 합성 금지. 이미 사용자가 후속 질문을 시작했으면 ACK를 생략하고 첫 음절을 보존한다.

## 6. 볼륨·얼굴·상태

MasterVolume은 이미 구현됐다. source baked_gain과 사용자 출력 수준을 분리하고 기존 안전한 시작값을 유지한다. RAW1.0에는 원하는 출력비율을 한 번 적용; 기존 .25 preview에는 중복 .25 금지. 캐시·실시간 대화·미디어 모두 기존 단일 출력 경로를 따른다. 현재 구현은 다음 owned playback부터 level 변경 적용이며 mute에는 진행 재생 취소가 필요하다. 재생 중 모든 slider 변경이 샘플 단위 실시간 반영된다고 주장하지 않는다.

R10 원본/배경/입모양을 바꾸지 않는다. 실제 player 시작 기준 speaking+감정, 실제 출력 위치에 맞춰 mouth cue. phrase별 시선은 최종 WAV anchor 시간 미검수 상태다. tracking이 시선을 소유하면 idle 시선 연출은 양보하고 표정·입만 사용한다. PTT/취소/종료 시 현재 세대의 남은 cue를 폐기한다. idle/기능 안내가 대화 history 또는 마지막 답변 repeat를 덮지 않게 한다.

## 7. 사진·영상의 중복 동작 방지

사진은 현재 요청의 preview→준비음성→5초→한 장 촬영→저장 review→성공 대사→Back/GP8 복귀를 보존한다. 편집본 5.000초와 시각 timer는 같은 기준 시계를 사용하되, WAV ended와 timer expiry가 각각 셔터를 실행하지 않는다. 취소 뒤 늦은 이벤트·repeat로 새 사진을 찍지 않는다. 아직 카운트 음성을 승인하지 않았다면 기존 시각 5초를 사용하며 새로 합성하지 않는다.

현재 영상 구현 보고는 무음 MP4다. 음성 녹음이 된다고 안내하지 않는다. 녹화 중 idle/TTS/wake/PTT 억제 정책, one-camera 소유권, interrupted 부분 파일의 비성공 처리를 보존한다. 컷·저장·review·복귀는 별도 사건이며 파일 생성만으로 저장 완료를 주장하지 않는다.

## 8. 순서와 완료 기준

1. 현재 작업 HEAD/변경 상태를 확인하고 기존 Cloud/Pi 역할을 유지한다. 이 문서 브랜치로 실행환경을 바꾸지 않는다.
2. 사용자 비공개 ZIP 확보, SHA256 확인, 경로·파일 크기·PCM·각 SHA 검증. 새 v10 schema를 테스트 fixture로 지원하되 앱/장치 실행 없이 import-only 검증부터 한다.
3. 142개가 누락/중복 없이 인벤토리에 보이고 승인 수는 자동으로 증가하지 않는지 확인한다. 빈 RX01, 5초 카운트, 10초 넘는 C09/C12, 파일명 변경, B 미포함을 검사한다.
4. 위 event 매핑을 수정·검증하고 초기60 중 실제로 들은 항목의 정확한 SHA만 승인한다. 필요한 파일부터 단계적으로 진행한다.
5. 시뮬레이션에서 missing/corrupt/unapproved→무음/시각 fallback, 재합성0, replay/late/cancel, 한 audio owner, 그림/영상 실제 결과와 대사 일치를 검증한다.
6. 기존 담당 분담에 따라 Pi에서 낮은 시작 음량, volume0/mute/복귀, 호출 확인과 질문 첫 음절, 사진 review/Back, 영상 저장, 추적 눈동자를 확인한다. 팔/펌웨어는 이 음성 작업 때문에 변경하거나 무인 시험하지 않는다.
7. 완료보고는 IMPORTED / LISTENING_APPROVED / EVENT_BOUND / PI_PLAYBACK_TESTED / CUE_TIMING_REVIEWED를 구분한다. 새 TTS 생성 성공이나 코드 테스트 수를 실제 청취 합격으로 쓰지 않는다.

롤백은 이전 자산 디렉터리/선택 기록/승인 부팅음을 보존한 채 새 팩 선택만 해제하는 방향이다. 기존 팩/승인 기록을 삭제하거나 force push, 운영 앱 자동 교체, main 자동 병합을 하지 않는다. v8 import가 이미 있다면 그 위를 강제로 덮지 말고 별도 versioned 디렉터리와 명시적 전환을 사용한다.
