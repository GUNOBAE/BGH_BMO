# BMO Character Life v1 — 음성 후보와 인수 시나리오

연결 작업서: Issue #39. 개발 기준: PR #36 `0933d9f75b533f3c0c4d285de7b942c201ea3cba`.

**현재 상태: 대본/연출 후보만 작성. 실제 WAV 생성·Fish 호출·청취 승인·새 기능 구현·Pi 배포는 아직 하지 않았다.** 기존 Aaron Soft/Beemo/gain0.25, 승인 R10, RP2040 보정과 동작 성공 기준을 바꾸지 않는다. 원작 녹음이나 대사를 가져온 것이 아니라 이 프로젝트를 위해 새로 작성한 짧은 문구다.

## 음성팩 계약

먼저 6–8개만 S2.1와 Drama3로 A/B 비교하고, 승인된 음색/연기만 전체 팩에 적용한다. 아래 모든 항목의 초기 `approved=false`, `audio_path=null`, `audio_sha256=null`, `status=AUDIO_NOT_GENERATED`다. `duration`은 실제 WAV 생성 후 측정해야 한다. 감정 태그는 사용자에게 읽어줄 문구가 아니라 host가 관리하는 연출 metadata다.

권장 manifest: `clip_id, category, text, delivery_id, allowed_emotions, allowed_contexts, target_max_seconds, cooldown_seconds, weight, voice_id, model, direction, audio_sha256, approved, lip_cues`.

모델 공식 문서(2026-10-28): S2.1 free/pro는 같은 모델 품질이다. Drama3는 연기 제작 비교 후보이며 더 비싸다는 이유만으로 교체하지 않는다. 모델/가격/계정 이용 가능성을 생성 직전에 확인한다. 생성기는 dry-run을 기본으로 하고 예상 UTF-8 bytes/비용/생성수/지출 상한을 먼저 출력한다. 단순 부팅/idle 재생 중에는 모델 API 호출이 없다.

## Boot — 8개

| ID | Spoken English | Direction candidate | Face |
|---|---|---|---|
| boot_01 | Hey, you're back! How have you been? | Warm, small smile; conversational, not shouting | happy |
| boot_02 | Oh! Hello, friend. What's our adventure today? | Gentle surprise turning into curiosity | happy |
| boot_03 | There you are. Ready for a little adventure? | Bright but soft, a playful invitation | happy |
| boot_04 | Hello again! What shall we do first? | Friendly, unhurried, clear question | happy |
| boot_05 | BMO is ready. How are you doing? | Quiet confidence, then attentive | neutral |
| boot_06 | Well, hello! Got any good ideas today? | Playfully curious, lightly smiling | happy |
| boot_07 | Welcome back. Let's make something nice. | Warm and relaxed; no exaggerated cheer | happy |
| boot_08 | Hi, friend. We can take it easy today. | Especially gentle; suitable for quiet mode | neutral |

Bootは `Starting BMO…` → `Getting ready…` → `Ready!`의 실제 로딩 단계, 승인 얼굴 wrapper의 아래→위 이동(후보 0.45–0.65초), 실제 greeting playback clock으로 연결한다. 원본 얼굴 HTML/SVG 자체는 수정하지 않는다. 최근 3개 greeting 재선택 회피, reload에 재인사 금지. 후보 중 하나가 없으면 기존 승인 greeting 또는 침묵으로 정상 시작한다.

## Idle — 12개

각 문구는 '가끔 발화할 수 있는 후보'이지 타이머마다 반드시 말하는 문구가 아니다. 기본 제안은 idle 3–7분 후보, 15분당 최대2회, 동일 clip 30분 내 반복 금지다. 질문/게임/영상/다른 재생/quiet mode/최근 민감한 대화에는 억제한다. 자동 strong angry/crying을 음성으로 재점화하지 않는다.

| ID | Spoken English | Allowed mood/context |
|---|---|---|
| idle_01 | Hmm. What should we make next? | neutral, curious |
| idle_02 | I have room for one more good idea. | neutral, curious |
| idle_03 | Maybe a very small adventure. | neutral, playful |
| idle_04 | This is a good little day. | happy, settled |
| idle_05 | I like when a plan comes together. | happy, recent non-sensitive success |
| idle_06 | A tiny victory is still a victory. | happy, pleased; not right after distress |
| idle_07 | Hmm. Just thinking. | neutral, calm |
| idle_08 | No rush. We have a little time. | neutral, relaxed; no factual deadline assumption |
| idle_09 | A little quiet can be nice. | neutral, quiet; not silent-mode |
| idle_10 | One little step at a time. | sad, gentle; silence remains preferred |
| idle_11 | We can have a quiet moment. | sad, calm; avoid unsolicited counselling |
| idle_12 | Hmph. I will give that another little think. | only recent playful annoyance, never serious conflict |

`idle_12`는 자동 일반 angry 상태의 기본 clip이 아니다. 장난스러운 맥락이 명확하지 않으면 선택하지 않는다. crying 상태에서 어울리는 승인된 idle clip이 없다면 기존 표정/조용한 움직임만 유지한다. idle clip은 사용자의 대화 history와 `/repeat`용 답변 WAV를 덮어쓰지 않는다.

## Vocal reaction — 12개

0.3–2초 정도의 짧은 후보를 우선하지만 실제 길이는 생성 후 측정한다. 상황에 맞지 않으면 none이 정답이다. 같은 턴에 모든 반응을 붙이지 않는다. 명시 연기 요청은 일반 자동 반응 쿼터와 구별한다.

| ID | Spoken seed | Performance candidate | Allowed mood/context |
|---|---|---|---|
| reaction_01 | Woo-hoo! | Brief delighted cheer, soft character, no scream | happy celebration |
| reaction_02 | Yay! | Small genuine celebration | happy |
| reaction_03 | Hehe. | Short natural chuckle, not reading syllables flatly | happy playful |
| reaction_04 | Ha! That's funny. | One light laugh; restrained | happy humour |
| reaction_05 | Oh! | Small gasp, then relaxed | surprised |
| reaction_06 | Whoa… | Curious wonder rather than fear | surprised/wonder |
| reaction_07 | Hmph! | Playful pout, not threatening | requested/lighthearted angry |
| reaction_08 | Hey! | Mild cartoon protest, not hostile shouting | requested/lighthearted angry |
| reaction_09 | Oh… | Voice catching softly; touched, not miserable | crying/tearful acting or fictional scene |
| reaction_10 | Hmm… | Gentle sniffle/close-to-tears delivery | crying acting |
| reaction_11 | Oh, dear… | One short theatrical sob, no prolonged wailing | explicit crying performance |
| reaction_12 | Hmm? | Curious upward intonation | neutral/curious |

가능하면 비언어 laugh/sniffle/gasp만의 샘플도 청취 비교하되, 모델이 tag를 발음하면 채택하지 않는다. 초고음 pitch shift를 사용하지 않는다. 강한 연기를 위해 다른 voice ID로 전환하지 않는다. BMO가 실제 사용자 고통에서 자기 울음으로 주인공을 차지하지 않게 한다.

## 구현 순서

1. 실행 checkout/metadata 경로 확인 + PTT timing 계측/병목 수정.
2. 기존 emotion/reaction 규칙 수정 + 자연스러운 director/우선권.
3. 위 voice bank manifest/생성 dry-run·승인 캐시·영어 boot wrapper.
4. idle gaze/발화 스케줄러와 quiet/interrupt.
5. optional local Hey BMO detector + 하나의 mic broker. 실제 custom model이 없으면 모델 미설치를 명시하고 PTT 유지.
6. 기존 camera target·팔 gesture와 통합. live 팔·원본 firmware 무인 변경 금지.

## 최소 인수 시나리오 24개

자동 fixture가 통과했다고 실제 모델의 감정 선택이나 사용자 청취 만족도를 통과로 표시하지 않는다. 실제 LLM/TTS 평가에는 별도 허용된 실행과 명확한 비용 상한이 필요하다.

| # | Scenario | Expected |
|---|---|---|
| 01 | 명시적으로 '기뻐해 봐' | happy, optional short joy; no forced body motion |
| 02 | '장난스럽게 화내 봐' | angry + playful delivery, not worried/sad collapse |
| 03 | '우는 연기를 해봐' | crying + appropriate delivery, not only sad cue |
| 04 | 평범한 사실 질문 | clear speech, neutral/low expression, no forced laugh |
| 05 | 실제 고민/상실 이야기 | empathetic calm/sad, no celebratory cache |
| 06 | 좋은 소식/성공 | proportional happy/celebrate; anti-repeat |
| 07 | 웃긴 상황 | occasional genuine chuckle, not every sentence |
| 08 | 놀라운 이야기 | surprise/gasp optional, no simultaneous all-channel animation |
| 09 | crying 뒤 listen→think | 기존 emotion continuity 유지 |
| 10 | `/repeat` | 이전 answer WAV 그대로, 새 LLM/TTS/gesture 없음 |
| 11 | idle clip 중 GP8 | clip 중단, 동일 mic 경로로 사용자 turn 1회 |
| 12 | boot 인사 중 사용자 중단 | 인사 취소, 재시작/중복 인사 없음 |
| 13 | 빠른 GP8 연타 | 중복 녹음/STT/LLM 요청 없음 |
| 14 | 말하기 첫 음절/짧은 머뭇거림 | preroll 유지, 성급한 잘림 없음 |
| 15 | wake + GP8 동시 발생 | 한 turn만 시작 |
| 16 | BMO 스스로 'BMO'를 말함 | self-wake 없음 |
| 17 | wake model missing/error | 정확한 상태 + PTT 정상 |
| 18 | wake OFF | persistent mic/detector 해제, PTT-only |
| 19 | 캐시 없음/미승인/손상 | 자동 API 생성 없이 침묵/기존 greeting fallback |
| 20 | 영어 boot + reload | 실제 progress, 한번의 slide, once-only greeting |
| 21 | 강한 sad/crying 뒤 idle | cheerful random clip 없음, silence 허용 |
| 22 | camera 가림/target 상실 | bounded idle gaze, 봤다는 허위 발화 없음 |
| 23 | arms unavailable/disabled | 얼굴·대화 계속, 실제 움직였다는 주장 없음 |
| 24 | 게임/미디어/영상/종료 경합 | 단일 audio/mic owner, idle/wake 재진입 정리 |

## 완료 보고 구분

`IMPLEMENTED` / `OFFLINE_TESTED` / `AUDIO_NOT_GENERATED` / `LISTENING_NOT_APPROVED` / `WAKE_MODEL_MISSING` / `PI_NOT_RUN`을 분리한다. 전체 CI SHA, 단일 후보 실행 경로, 최소 다음 실기 검수만 인계한다. 원본 보정·백업·이미 성공한 B1/HID 검사를 처음부터 반복하지 않는다.
