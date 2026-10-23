# Codex 묶음 작업서 — BMO 기능 통합 v1

2026-10-23 KST. **상태: 사용자 요청에 따른 개발 계약과 작업 배정. 이 문서 업로드가 기능 구현·Pi 실행·실물 합격을 뜻하지 않는다.**

## 0. 목표와 이번 승인 범위

사용자는 감정 연속성 수정을 진행하면서, 답변에 따른 팔 제스처·IMU 반응·게임 목록/실행/종료/복귀·카메라 촬영/화면 시선 추적/작은 미리보기·노래 재생을 한 번에 맡기길 요청했다. 노래 영상 자료는 사용자가 별도 채팅에서 나중에 제공한다. **모듈 개발과 모의 통합은 병렬로, 코드 합치기는 조정자 한 명이, 실물 활성화는 검증된 기능별로** 진행한다. 계획만 다시 작성하지 말고 아래 산출물까지 구현한다.

허용: 분리된 개발 폴더의 소스·테스트·모의 UI·명시적으로 실행해야 동작하는 장치 어댑터 구현, 해당 개발환경 의존성 설치, 로컬 fake/fixture/headless 검사, 커밋·push·draft PR·GitHub 보고. 누락된 노래/게임/센서 자료 때문에 독립적인 작업까지 중단하지 않는다.

이번 자동 개발에서 하지 않을 것: 실제 녹음/스피커 재생/카메라 켜기/직렬 포트 열기/서보 이동/IMU 폴링/펌웨어 복사·재시작, 유료 GPT/Fish/STT/vision 호출, 전체 OS 업그레이드·부팅·팬·SSH·Tailscale·기존 서비스 설정 변경, main 병합·force-push·기존 브랜치 삭제. 이는 기능 포기가 아니라 사람 없이 하는 개발과 실물 검수의 구분이다. 구현 뒤 기능별 정확한 실기 명령과 남은 입력을 한 보고서에 남긴다.

## 1. 현재 진행 작업과 충돌을 피하는 방법

### 이미 진행 중인 작업

- 감정 연속성: `feature/bmo-auto-emotion-v1`, `/home/bgh/BMO_EMOTION`, PR #13.
- 우선 계약: 그 브랜치의 `docs/development/CODEX_EMOTION_CONTINUITY_V2_KR.md`.
- 조회 시 PR #13 HEAD: `dd12c4a2235d97da7efbf3f460c39a84c7ed609a`. 문서 추가까지 확인된 스냅샷이며 후속 구현 완료를 의미하지 않는다. 수행 직전 최신 HEAD/보고서/차이를 읽는다.
- 유지할 흐름: **이전 감정으로 대기→듣기→생각→다음 실제 발화 시작에 새 감정**. 새 질문 및 2초 뒤 강제 neutral 초기화는 복원하지 않는다.
- 이 묶음의 worker는 진행 중인 PR #13의 session/adapter/voice를 동시에 수정하지 않는다. 현재 Codex가 v2를 진행 중이면 그 작업은 마무리하고 별도 기능 모듈의 준비를 병렬 위임한다. 최종 통합은 v2 코드 검증 커밋을 먼저 인수한 뒤 한다.

### 이번 묶음의 작업 공간

새 통합 작업은 **하나의 독립 clone `/home/bgh/BMO_FEATURES`**, 브랜치 **`feature/bmo-feature-bundle-v1`**로 모은다. 같은 목적의 폴더/브랜치가 있으면 현재 remote/HEAD/status와 AGENTS.md를 확인하고 이어간다. 사용자 변경은 덮어쓰지 않는다. 기존 BMO_INTEGRATION/BMO_EMOTION/BGH_BMO/BMO_LUNA_AB/BGH_BMO_ACTIONS는 그대로 보존한다. 기능마다 사용자용 clone을 계속 늘리지 않는다.

v2가 아직 진행 중이면 검증된 현 HEAD에서 독립 모듈을 만들되, 공통 코드 연결 전 v2 결과만 검토해 가져온다. 기준 SHA와 포함한 변경을 보고한다. 과거 모든 브랜치를 통째로 merge하지 않는다. 작업서가 있는 docs 브랜치는 읽기용 기준이며 런타임 기준으로 clone하지 않는다.

조정자가 공통 계약과 Git 작업을 소유한다. subagent가 가능하면 독립 모듈 단위로 위임하되 같은 파일을 둘이 수정하거나 같은 checkout에서 checkout/reset/commit/push를 병렬 수행하지 않는다. Pi 자원을 고려해 무거운 브라우저·전체 회귀 검사는 순서대로 돌린다. subagent가 불가능하면 사용자에게 다시 묻지 말고 동일 범위를 순차 구현한다.

## 2. 보존할 기준과 확인된 제약

- 승인 얼굴: `/home/bgh/Downloads/BMO_Screen_v13_2.html`, SHA-256 `38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`. 원본 HTML/SVG/입 프레임/시선 범위/표정/색/비율 수정 또는 공개 업로드 금지. 새로운 메뉴·카메라 위젯은 외부 shell에서만 만든다.
- Luna low + 기존 STT + Fish Aaron Soft / beemo / PCM gain 0.25 / 기존 출력 상한 320, 원본 Piper opt-in, 기존 4.1 mini 복귀와 고정 음성 정책을 보존한다. 이 값의 변경이나 새 웃음/울음 TTS 프리셋은 별도 청취 검수 전 하지 않는다.
- 메인 Pi 5 8GB, Debian 13/aarch64/Python 3.13 기록, USB 마이크·MAX98357A I2S 출력. 이 사양은 과거 현장 기록이며 설치된 패키지·현재 장치 건강 상태는 별도로 읽기 전용 확인한다.
- 제어보드는 RP2040-Zero. MPU6050은 Pi GPIO I2C가 아닌 RP2040 GP0/GP1, INT GP9 연결. 서보 GP10/GP11 기록은 실제 좌우 방향·각도 한계·전류 안전 검증을 대신하지 않는다.
- 기존 펌웨어의 GP7 양팔 토글과 TALK/취소 버튼 구상에 충돌이 있다. 버튼 번호·키 이벤트를 임의로 새 명령에 연결하지 않는다.
- `docs/hardware.md`에는 2026-10-13 IMU 정지 편향/포화/self-test 실패 기록과 당시 RP2040 재시작 시 팔이 움직일 수 있다는 경고가 있다. 현재 고장 확정으로 확대하지 않되 **건강 확인 전 신뢰하지 않는다**. 정상적인 팔 통합과 IMU 정상화를 하나의 선행조건으로 묶지 않는다.
- `actions.py`의 Action v0는 5종 Mock 전용이다. `executed=False`, `demo_song`/`demo_game` 자리표시자 및 기존 검사 계약을 변경하지 않는다. 실제 기능은 이름/버전을 구분한 host executor로 만든다. Mock에 옵션 하나를 붙여 실제 실행기로 바꾸지 않는다.
- 기존 게임 준비 기록은 `BMO_Games_v3.zip`의 목록/런처/패드 설정/연동용 Tetris와 8877 서버 설계다. 23개 목록은 23개 설치된 게임이 아니다. 이 작업서 작성 시 원본 ZIP 바이트를 재확보하지 못했다. Pi의 명시 자산 경로/Downloads/BMO 관련 알려진 폴더에서 제한적으로 확인하고 없으면 해당 재사용 검증만 ASSET_PENDING으로 기록한다. 과거 채팅의 sandbox 경로는 Pi 파일 경로가 아니다.
- 사용자 노래 자료는 추후 제공한다. 지금 새 다운로드·크롤링·곡 선정·가창 합성을 시작하지 않는다.

## 3. 사용자에게 보일 흐름

아래는 **이번에 구현할 목표**이지 이미 동작하는 기능 목록이 아니다.

### 게임

1. 대화에서 “게임 하고 싶어/게임 목록 보여줘” → BMO 게임 목록. 실제 설치·런타임 준비가 확인된 항목만 실행 버튼을 활성화한다. 미확보/미지원 항목은 이유와 함께 준비 중 표시.
2. USB 패드 방향키로 선택, 확인(A/녹색에 대응하는 검증된 논리 입력)으로 실행. 메뉴 뒤로(B)는 얼굴 복귀. 본체 키와 USB 패드의 물리 번호를 같다고 가정하지 않는다.
3. 게임 중 B는 게임 조작이다. 뒤로가기 전역 단축키로 가로채지 않는다. 기존 웹게임 설계의 **Start+Select 1.2초 또는 Esc → 현재 게임 종료→게임 목록**을 기본 복귀로 보존한다. 목록에서 B/“BMO로 돌아가기” → 얼굴. 목록에서 다른 게임 선택 → 새 게임.
4. 계속하기/다른 게임/얼굴 메뉴는 pause/resume이 실제 지원되는 backend에서만 추가한다. 웹게임마다 pause 구현이 다르며, native/RetroArch가 동일 wrapper 안에 있다고 가정하지 않는다. 지원하지 않으면 종료 시 진행 상황 유실을 명시한다. 보편적인 세이브·일시정지 지원을 꾸미지 않는다.
5. RetroArch/native는 각각 전용 실행 어댑터와 앱 전용 키 설정을 사용한다. 공식 RetroArch Menu Toggle/Close Content와 BMO로 돌아오는 경계를 구분한다. 1.2초 hold가 모든 backend에 기본 제공된다고 쓰지 않는다. 외부 backend에서 hotkey를 검증하지 못하면 UNVERIFIED로 표시한다.
6. 게임 종료/크래시/패드 분리 후 빈 화면·겹친 소리·좀비 프로세스 없이 목록 또는 얼굴로 복구한다. 외부 앱은 소유한 PID/프로세스 그룹만 정리한다. graceful quit 후 유한 timeout, 필요한 경우 해당 앱만 종료. 전역 killall 금지.
7. 실제 음성 명령은 명시적 push-to-talk가 연결된 뒤 사용한다. **현재 Enter 수동 녹음만 있는 상태에서 게임 중 항상 “그만”을 듣는다고 광고하지 않는다.** pad/메뉴 종료 경로를 반드시 독립 제공한다. 게임 중 음성 입력이 구현되면 게임 소리를 안전하게 정지/일시정지하고 한 번만 녹음한다. 호출어/VAD/echo cancellation은 별도 검수다.

### 카메라 촬영·시선

- “사진 찍어줘” → 카메라 사용 표시+프리뷰 → 짧은 카운트다운 → 저장 확인 → 사진 결과(다시 찍기/얼굴 복귀). 촬영 취소·초점 실패·디스크 부족·저장 실패 처리. 저장 완료 전 “사진 찍었어”라고 말하지 않는다. 기본은 로컬 저장이며 공유/업로드/삭제 자동 수행 없음.
- “나 봐/나 따라 봐” → 명시적으로 추적 모드 켜기 → **검출된 얼굴 위치에 맞춰 화면의 눈/얼굴 시선 움직임**. 작은 카메라 미리보기는 외부 shell 하단/모서리에서 켜고 끌 수 있게 한다. 승인 얼굴에 UI나 새로운 표정을 직접 삽입하지 않는다.
- 이는 사람의 시선 방향이나 신원 인식이 아니라 얼굴 위치 추적이다. 알려진 두 서보는 팔용이고 목/카메라 pan-tilt 장치는 확인되지 않았으므로 카메라나 본체가 물리적으로 따라 돈다고 표현하지 않는다. 시야 밖 사용자를 계속 추적한다고 하지 않는다.
- 저해상도 로컬 face detector를 주입 가능한 backend로 구현한다. 초기 tuning 후보는 320×240/640×480 및 5~10Hz 검출이며 실제 성능 보장값이 아니다. 영상 매 프레임을 클라우드에 보내지 않는다. 추적용 영상/얼굴 embedding/신원 DB를 기본 저장하지 않는다.
- 최초 target 선택·여러 얼굴에서 target 유지·갑작스런 target 전환 억제·대상 상실 후 짧은 grace와 원래 시선 복귀·시간표시/freshness·좌우 mirror/회전/축 변환·dead zone·속도 제한·smoothing을 명시한다. 카메라 영상 정규화와 눈 좌표를 분리한다. 멈춘 프레임에 계속 눈이 붙지 않게 한다.
- 카메라 장치 소유자는 한 개. 추적·미리보기·사진은 같은 broker가 공유/중재하며 사진 때 tracker를 일시 정지한 후 원래 모드로 복귀한다. 라이브러리 두 개가 같은 센서를 동시에 열지 않는다.
- 기본 카메라 off, on 표시와 즉시 off/돌아가기 제공. 끌 때 capture·detector·preview stream·buffer·예약된 작업을 정리한다. 모드 종료 후 자동 재활성화 금지.
- 사진을 보고 “이 물건 뭐야” 같은 장면 설명, 타이머 단체사진, 사용자 요청 타임랩스는 후속 확장 후보로만 문서화한다. 이미지 외부 전송이 필요한 설명 기능은 명시 요청과 비용/전송 범위 없이는 실행하지 않는다.

### 답변에 맞춘 팔과 화면 리액션

- 발화와 별개인 제한된 gesture 선택: 인사→짧은 손인사, 축하→검증된 양팔 반응, 가벼운 의아함→검증된 작은 제스처, 보통 정보/위로→none 기본. 매 답변마다 팔을 움직이거나 happy이면 무조건 자동 팔 명령으로 바꾸지 않는다.
- 모델은 허용 gesture ID만 제안한다. 각도·PWM·속도·직렬 명령을 생성/지정하지 못한다. 호스트가 사용자가 실기로 검증한 좌우별 제한값/방향/순서/쿨다운으로 변환한다. 검증 프로필이 없으면 real motion은 disabled다.
- 실제 발화 시작에 짧은 제스처를 허용하되 오래된 turn/취소 결과는 실행하지 않는다. 사용자가 명시한 팔 명령은 별도 intent로 처리한다. explicit stop이 자동 장식을 앞선다. 팔 queue는 제한하고 대화가 밀렸다고 이전 제스처를 나중에 몰아 실행하지 않는다.
- 기존 RP2040 코드/통신이 불명확하면 wire protocol을 새로 추정해 실행하지 않는다. 전용 CDC data transport의 버전/handshake/request id/ACK timeout/controller-side dedup 계약과 firmware 변경안을 repository 내 staging 파일로만 준비한다. console/REPL을 데이터 포트로 쓰거나 자동 reset하지 않는다. 현 펌웨어에 필요한 최소 변경과 HID 보존/GP7 충돌은 실물 검수 항목으로 남긴다.
- 팔 위치 피드백 센서가 확인되지 않았으므로 ACK는 명령 수신/처리 근거이지 실제 기계 각도 도달 증거가 아니다. “명령 전달됨/피드백 없음/위치 미확인”을 구분한다. 추정 상태로 닫힌 루프 제어가 있다고 말하지 않는다.
- stop/watchdog/disconnect의 안전 정지는 기구별 검증이 필요하다. PWM 무조건 off나 90도 복귀가 안전하다고 가정하지 않는다. 미검증이면 아예 시작을 금지한다.
- 화면 리액션은 승인 look/lookEyes/blink를 쓰되 원본 listening/thinking 시선·끄덕임·crying 눈물·입을 덮지 않는 arbiter를 둔다. 카메라 추적 중 독립 랜덤 시선 scheduler를 동시에 켜지 않는다. 대각선 포함 좌표 제한·원래 방향 복귀·취소를 테스트한다. 새로운 얼굴 아트·과장된 랜덤 움직임·웃음소리는 추가하지 않는다.

### IMU

- host telemetry/schema/freshness를 재사용해 synthetic trace로 motion/tilt/still 후보를 분리하고 hysteresis·지속 시간·쿨다운·상실/복구를 구현한다.
- 센서 건강 상태는 unknown/healthy/degraded/fault로 별도 관리. 포화·고정값·정지 편향·잘못된 timestamp·패킷 누락/역순/재시작을 검사한다. 0x68 주소 응답/heartbeat만으로 healthy로 승인하지 않는다.
- 실기에서 정상 정지값·여러 자세·움직임·드리프트를 확인하기 전 실제 IMU는 제품 반응이나 팔 안전장치 입력에 쓰지 않는다. 고장값을 임의 offset으로 숨기지 않는다.
- 가능한 후속 표현: 몸체를 기울이면 눈/표정 반응, 움직여 들 때 짧은 반응, 심한 흔들림 때 반복되지 않는 반응. “들어올림”은 센서 변화에서 추정하는 후보이지 항상 식별되는 사실이 아니다. 샘플마다 GPT를 호출하지 않는다.
- 검증된 팔 기능을 IMU가 없다는 이유만으로 영구 차단하지 않는다. 단, IMU 기반 자동 제스처는 센서 건강·동작 검수가 모두 된 경우만.

### 노래·미디어

- 사용자 자료 전에는 빈 catalog+합성 fixture만 사용한다. 로컬 재생/정지/일시정지/재개/다음곡/목록과 얼굴 복귀를 만든다. backend가 못 하는 pause/seek는 unsupported로 명시한다.
- import manifest에 안정적 ID, 표시 제목, 원본 출처, 로컬 상대경로, 파일 hash, media type/duration(실제 추출 시), 선택 cue 파일을 둔다. 승인된 콘텐츠 root 안만 접근하고 절대경로/../symlink 탈출을 거부한다. 임의 인터넷 URL이나 셸 명령은 catalog로 받지 않는다.
- 노래 틀기는 제공된 파일의 재생이지 새 가창 생성/성우 변환이 아니다. 등록되지 않은 제목은 목록을 제시하고 파일이 없다고 말한다. 사용자 노래 문서/파일을 받으면 catalog importer로 추가하는 절차를 남긴다.
- 노래 입 동작: 노래 영상/사람 목소리와 반주가 섞인 RMS를 완벽한 가창 립싱크로 설명하지 않는다. 초기에는 명시 승인 cue가 없으면 단순 재생을 우선하고, 별도 cue가 제공되면 같은 playback clock으로 사용한다. Fish gain 0.25를 음량이 다른 모든 미디어에 무조건 동일 적용하지 말고 대화 프로필은 그대로 두고 미디어 출력은 별도 검수한다.

## 4. 먼저 구현할 공통 뼈대

### A. mode와 자원 소유권

전경 UI mode는 최소 conversation/game_menu/game/photo/music, tracking은 camera 옵션으로 별도 관리한다. emotion과 phase를 mode 하나의 enum에 합치지 않는다. 대화 brain/history와 감정 상태는 앱 전환 중에도 보존하고 /reset에서만 명시 초기화한다.

ModeManager가 전환 transaction을 소유한다. 이전 작업 정리→필요 자원 release→새 모드 준비→성공 확인→화면/입력 전달. 실패하면 마지막 사용 가능한 화면으로 돌아온다. 화면이 이동했는데 소유 앱·playback이 남는 상황을 검사한다. mode generation으로 종료된 게임/카메라 callback이 얼굴을 덮지 못하게 한다.

AudioFocus는 대화·음악·게임의 **한 번에 한 소리 소유자**를 정한다. 대화는 계속 Python/ALSA 단일 경로다. 게임 자체 오디오는 HTML/RetroArch가 낼 수 있으므로 전체 제품의 모든 소리를 Python WAV로 이중 재생하지 않는다. TTS가 필요하면 이전 소유자를 일시정지/정지·mute할 수 있는지 확인한 후 넘긴다. unsupported인데 겹쳐 틀지 않는다. microphone은 초기 PTT만, 자동 재청취/게임 소리 STT 루프 금지.

InputRouter는 mode별로 키/USB pad를 하나의 소비자에게 전달한다. talk/stop/menu와 게임 입력의 소유권을 분리한다. 물리 버튼은 검증된 맵 없이는 활성화하지 않는다.

GazeArbiter는 화면 감정/입을 건드리지 않고 look/lookEyes만 중재한다. 우선순위 초안: 수동 검수 override > 승인 listening/thinking 활동 시선 > 명시 camera target(일반 idle/speaking) > 짧은 발화 연출 > 원본 idle. crying 눈물 등 감정별 시각 표현은 어떤 시선 mode에서도 보존한다. target 상실·mode 종료 시 현재 phase의 기준 방향으로 복귀한다. 듣는 동안 camera tracking을 우선할지의 대안은 별도 시각 검수 전 기본을 바꾸지 않는다.

### B. 기존 face heartbeat와 전경 전환 충돌

현재 session은 face 상태 조회가 3초 이상 끊긴 활성 턴을 취소한다. 게임으로 정상 전환해 얼굴 iframe이 숨겨진 것을 고장으로 취급하지 않도록 **전경 shell liveness와 face 가시성/대화 active 상태를 분리**한다. 반대로 실제 shell 연결 상실을 무조건 무시하지 않는다. visibility 복귀 시 최신 감정/상태를 적용하고 지난 audio/cue/제스처를 재실행하지 않는다.

얼굴 서버 8765와 게임 서버 8877을 무작정 같은 출처로 가정하지 않는다. 다른 port는 다른 origin이다. 가능하면 신뢰 shell 한 곳으로 조정하되 외부/팬 HTML 게임은 별도 origin 또는 적절한 sandbox로 격리하고 host controller/API에 접근시키지 않는다. postMessage는 origin/source/schema 검증, 필요한 최소 기능만 허용한다. broad CORS 및 원본 프로젝트 폴더 전체 HTTP 노출 금지.

카메라 preview/미디어 UI 때문에 기존 GET-only 서버를 확장할 때는 상태 읽기와 side effect command를 구분한다. side effect는 검증된 POST와 origin/host/token 검사를 둔다. loopback 바인딩만으로 CSRF나 같은 머신의 비신뢰 페이지를 모두 막는다고 가정하지 않는다. raw path·API 키·임의 command endpoint를 노출하지 않는다. 개인정보/콘텐츠를 공개 Git에 업로드하지 않는다.

### C. 문장·표정·의도·결과의 버전 있는 계약

현재 ExpressionClient는 speech/emotion 두 필드만 받는 strict 계약이다. 여기에 임의 action 필드를 덧붙여 기존 검사를 깨뜨리지 않는다. 새 기능모드용 **버전이 구분되는 FeatureReply/HostIntent 계약**을 만들고 기존 manual/auto 두 필드 경로는 복귀 가능하게 유지한다.

모델 출력 후보: clean speech + 여섯 emotion + 허용 gesture ID + 최대 하나의 intent. 모델은 경로/URL/PWM/셸 문자열을 실행 대상으로 만들지 못한다. request id/session/turn/mode generation은 호스트가 발급한다. 같은 논리 요청의 재시도에는 같은 ID를 유지한다.

기능 intent 예: open_game_menu, launch_game(catalog_id), exit_game, return_to_face, play_song(catalog_id), stop_media, take_photo, start_face_tracking, stop_face_tracking, arm_gesture(allowlisted_id). 최초 구현에서 미지원 intent는 unsupported로 명시하고 수행을 꾸미지 않는다. 정지·취소는 호스트 즉시 제어이며 LLM 응답을 기다리지 않는다.

대화와 의도 추출은 같은 첫 LLM 요청에 넣는 구조를 우선한다. 매 프레임/센서/제스처용 추가 호출 금지. 기능 완료 알림은 고정된 짧은 host template/표시로 시작해 불필요한 후속 GPT를 줄인다. 실제 모델의 schema 지원/지연/의미 적합성은 live 전 미확인이다.

Host executor 상태는 accepted/running/succeeded/failed/cancelled/rejected/unknown 등을 명시하고 executed/completion을 구분한다. JSON의 success나 Mock 결과로 실제 완료를 주장하지 않는다. 사진은 파일 저장·검증, 게임은 앱 준비/출력 연결, 노래는 재생 시작, 팔은 ACK와 위치 피드백 유무에 맞는 결과를 근거로 한다. 이미 저장된 사진은 취소했다고 없었던 것으로 처리하지 않는다. stop 성공과 과거 작업 완료도 별개다.

실제 side effect에는 로컬 private journal/idempotency를 설계한다. 재시작 뒤 팔/촬영 명령 자동 재생 금지. MCU timeout은 미실행이라고 단정하지 말고 unknown으로 남겨 무분별 재전송을 막는다. controller dedup/상태 피드백 없이 exactly-once 기계 동작을 보장하지 않는다. journal에 키/전체 대화/원본 이미지를 넣지 않는다.

## 5. 조정자와 병렬 작업 배정

기존 동등 모듈이 있으면 중복 앱을 만들지 말고 재사용한다. 아래 디렉터리는 제안되는 소유 경계이며 실제 저장소와 대조 후 이름을 고정한다.

| 담당 | 범위 | 독립 산출물 | 완료 조건 |
|---|---|---|---|
| 조정자 | contracts/mode/audio/input/공통 shell, launcher·session 연결 | feature contracts, capability registry, mode transaction, 종료/복귀 | 전경 하나·소리 하나·중복 명령 차단·v2 감정 유지 |
| Games | `software/bmo_ai/features/games/` 및 전용 tests/UI | catalog, list UI, backend별 runner, pad·exit/return | fake game→목록→다른 게임→얼굴, crash/패드 loss/미설치 처리 |
| Camera | `features/camera/` 및 전용 tests/UI | lazy camera broker, photo store, detector interface, target tracker, preview | synthetic frame/target로 추적·촬영·취소·닫기, raw camera 0 호출 |
| Motion | `features/motion/` 및 전용 tests | gesture allowlist/scheduler, calibrated profile loader, dedicated transport/ACK 설계, IMU health/events | fake transport에서 timeout·중복·stale 차단, 건강 미확인 센서/팔 real 미활성 |
| Media | `features/media/` 및 전용 tests | song manifest/import validation, player interface, pause/stop/return | fixture 재생/종료/error 복구, 실제 사용자 노래 없이 검증 |

화면 장식/gaze와 natural-language intent의 최종 연결은 조정자가 맡아 각 worker가 integration_session.py, expression_contract.py, integration_web/adapter.js를 동시에 수정하지 않게 한다. 인터페이스를 먼저 동결하고 독립 모듈을 진행한다. stub만 만들고 완료했다고 하지 말고 적어도 fake를 통한 실제 end-to-end 실행 경로를 남긴다. 실물 backend 작성과 실물 backend 실행은 구분한다.

## 6. checkpoint와 검사

### 공통 검사

- 상태 전환 중복/실패/취소/복귀, mode generation, 오래된 callback·브라우저 응답 차단, 자원 release 순서.
- 여러 launcher/재연결로 camera/player/worker가 중복 생성되지 않음. 앱 종료는 앱 소유 자원만 정리.
- crying 대화→crying listening/thinking→새 답변 감정, game/photo/music 종료 후 감정 복귀. 기존 v2 회귀 포함.
- schema malformed/추가 필드/알 수 없는 ID·gesture·경로 거부, payload와 비밀이 로그/예외에 새지 않음.
- Mock Action v0는 정확히 5개/모든 executed=False. 새로운 실제 executor 상태와 섞이지 않음.
- 승인 HTML SHA 전후 동일. 원본 감정·입·깜빡임·듣기/생각 동작을 그대로 검증.

### 기능별 모의 E2E

- 게임: 메뉴/설치 안 된 항목/1개 fake runner/종료 조합 debounce/목록·다른게임·얼굴 복귀/crash cleanup. 실제 게임팩이 있으면 별도로 시험용 Tetris 코드 재사용을 검토; 없다면 ASSET_PENDING을 숨기지 않는다.
- 카메라: 좌우/상하/대각선·mirror·여러 얼굴·target loss·stale frame·추적 off, 촬영 성공/디스크 부족/취소·retry 요청 중복·사진 후 tracking 복원. preview on/off가 승인 얼굴/입을 훼손하지 않음.
- 팔: gesture none/한 턴 1개/쿨다운·busy·취소·ACK timeout·재연결·reboot unknown·미검증 한계값 차단. 실제 mechanical position 미확인을 명시.
- IMU: healthy trace/degraded/포화/고정값/heartbeat-only/seq 역순·누락, 오래된 샘플은 반응 생성 안 함, 반복 반응 제한.
- 노래: 빈 목록/등록·잘못된 path/hash/미지원 codec·pause/stop·끝/실패 후 얼굴 복귀. 원작 음원 없이 generated fixture 사용.
- 리소스: camera와 tracking/photo 공유, game/music와 conversation audio 독점, game 중 자동 녹음 없음, 정상 화면 전환과 실제 liveness loss 구별.

단계별 코드 커밋과 보고 checkpoint를 남긴다. 한 기능이 환경/자산 문제로 막혀도 나머지 검증과 제출을 끝낸다. 모의 연출이 진짜 팔/촬영/노래 성공처럼 보이지 않도록 UI에 simulation을 표시한다. 테스트 수는 실제 실행 결과만 기록한다. 승인 파일이 없는 CI의 browser 검사를 Pi 로컬 결과와 혼동하지 않는다.

## 7. 이번 제출물과 이후 실물 검수

코드/검사/수동 실행 안내/manifest 예제/기능별 enable 조건/복구 절차/미실행 항목을 `feature/bmo-feature-bundle-v1`에 정상 push한다. v2 검증 SHA를 포함한 PR base는 `feature/bmo-auto-emotion-v1`이며 별도 draft PR 하나로 제출한다. 같은 head PR이 이미 있으면 갱신한다. PR #13에는 연결 설명만 남기고 기능 묶음 diff를 섞지 않는다.

최종 보고서는 **`docs/reports/FEATURE_BUNDLE_V1_LATEST_KR.md`**. 첫 문장에 전체 PASS/PARTIAL/BLOCKED와 각 모듈의 code/mock/local/live 상태를 구분한다. live 미검수라면 제품 전체 완료로 쓰지 않는다. 보존한 기준/실제 경로·SHA/변경 파일/검사 명령·결과/의존성·자산 누락/실물 차단 사유를 포함한다.

사용자에게는 결론·GitHub 보고서/PR 링크·현재 다음 행동 하나만 전달한다. 보고서를 ChatGPT로 복사하라고 하지 않는다. 소프트웨어 누락을 사용자의 반복 재설정 과제로 떠넘기지 않는다.

실물 검수 권장 순서: 감정 연속성 → 시험용 게임 왕복 → 카메라 사진/추적 → 검증된 팔 제스처 → IMU 건강 진단 후 반응 → 사용자가 제공한 노래 등록. 독립 기능 코드는 이 순서를 기다리지 않고 개발한다. 실제 API/장치 실행은 사람이 보는 별도 명시 단계다. 노래 자료 미제공은 전체 작업의 blocker가 아니다.

## 8. 외부 문서와 확인 경계

2026-10-23에 확인한 1차 문서:

- Raspberry Pi camera software: https://www.raspberrypi.com/documentation/computers/camera_software.html — libcamera/rpicam/Picamera2 방향. 실제 Debian 13의 설치/동작은 확인하지 않았으므로 기존 환경 조사 없이 시스템 패키지 교체 금지.
- CircuitPython USB CDC: https://docs.circuitpython.org/en/latest/shared-bindings/usb_cdc/ — console과 data 분리, data 기본 비활성, DTR·endpoint 제약. 현재 펌웨어가 data를 제공한다고 추정하지 않는다.
- Libretro controls: https://docs.libretro.com/guides/input-and-controls/ 및 https://docs.libretro.com/guides/quick-menu/ — menu/hotkey/close content 경계. 실제 USB 패드 mapping과 backend 복귀는 별도 검증.

기존 프로젝트 근거: docs/hardware.md, docs/action-system-v0.md, docs/voice-policy-v1/, docs/reports/EMOTION_STAGE1_LATEST_KR.md, 진행 중인 EMOTION_CONTINUITY_V2 작업서/보고서, docs 브랜치의 BMO_INTEGRATION_2026-10-22_KR.md(게임팩 확보 상태). 오래된 문서의 '얼굴 미구현' 등은 최신 승인/실기 기록으로 읽고 원본을 되돌리지 않는다.
