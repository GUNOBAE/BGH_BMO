# 기능 묶음 v1 — 독립 모의 통합

작업 경로는 `/home/bgh/BMO_FEATURES`, 브랜치는 `feature/bmo-feature-bundle-v1`이다.
검증된 감정 연속성 v2와 수동 Launcher 기준 `174dd974fdf119fc3d24a5cda0870ae70c007cf6`에서
분리했다. 기존 BMO_EMOTION/BMO_INTEGRATION을 변경하지 않는다.

**이 실행기는 simulation 전용이다.** 실제 API·서보·직렬·IMU·카메라·게임·스피커를
활성화하는 CLI 옵션은 없다. 선택적 real backend 코드는 명시적으로 차단되어 있고
감독하 검수·검증된 자산/프로필을 갖춘 별도 단계가 필요하다. Mock PASS는 실물 합격이 아니다.

## 실행과 종료

```bash
cd /home/bgh/BMO_FEATURES
./launch-features --simulate --demo-assets --screen-profile v13.2 --port 0
```

출력된 loopback 주소를 브라우저에서 연다. `--demo-assets`일 때만 합성 게임 2개와
합성 PCM 2개를 앱 소유 임시 디렉터리에 생성한다. 실제 게임팩/사용자 노래가 아니다.
이 옵션 없이 실행하면 catalog는 빈 준비 중 상태다. 앱 종료 시 합성 임시 자료만 정리한다.
실제 사진/사용자 미디어를 만든 뒤 지우는 동작이 아니다.

`/quit`, Ctrl-C, EOF로 정상 종료한다. 기존 앱이 실행 중이면 공통 사용자별 lock이
새 실행을 거부한다. 다른 앱을 kill하거나 lock을 삭제하지 않는다. 자동 검사는
별도 임시 lock/port를 쓴다. autostart/systemd/desktop/OS 설정을 설치하지 않는다.

`--screen-profile r10`은 별도 핀의 로컬 후보를 명시 선택한다. R10 최종본 일치 확인은
아직 INPUT_PENDING이다. 기본 v13.2와 R10 원본은 편집·복사·공개 업로드하지 않는다.
화면·키 수동 Launcher의 기준과 복귀는 [LAUNCHER_KR.md](LAUNCHER_KR.md)를 따른다.
이 모의 실행기는 private key resolver를 호출하지 않는다.

## 사용 가능한 모의 흐름

- 대화: 같은 session/brain/history를 유지한다. 감정은 idle/listening/thinking/prepared를
  이어가고 실제 소유 player started에서만 새 답변 감정으로 바뀐다. 모의 음성 출력은
  합성 PCM과 무해 Python sleep child다. 실제 스피커로 소리를 내지 않는다.
- 게임: 목록 → 합성 게임 A → B는 게임 입력 → Esc 또는 검증된 논리 Start+Select
  1.2초 hold → 목록 → 다른 게임 → 얼굴. 실제 USB 패드 번호/GP7/HID mapping은
  미검수이며 추정해 연결하지 않았다. native/RetroArch의 pause/hotkey는 보편 지원으로
  표시하지 않는다. 종료 시 진행 상황 유실을 표시한다.
- 카메라: 기본 off. 합성 얼굴 위치 추적/작은 PNG 프리뷰 → 3초 countdown → 검증된
  합성 PNG 저장 → 다시 찍기/얼굴. 사진·tracker·preview는 같은 broker를 공유한다.
  사진 후 이전 옵션을 복원하지만 game/music로 벗어나 명시 off된 카메라는 복귀 때
  자동으로 켜지지 않는다. 실제 촬영이나 사람 식별·물리 pan/tilt가 아니다.
- 미디어: 합성 catalog에서 play/pause/resume/next/stop/end → 얼굴. backend가 pause를
  지원하지 않으면 unsupported다. cue가 없으면 단순 재생이며 RMS 가창 입을 만들지
  않는다. 명시 cue가 있으면 같은 backend position으로 샘플링한다. pause는 발화를 멈추고 원본 대기 입으로,
  resume은 현재 위치로, 종료는 기존 대화 감정으로 복귀한다. 완벽한 가창 립싱크로
  설명하지 않는다. media gain과 대화 gain 0.25를 혼용하지 않는다.
- 팔: none/wave/celebrate/shrug의 모의 요청·ACK를 검증한다. 한 턴 한 자동 제스처,
  실제 started timestamp/TTL, generation, bounded queue, cooldown을 확인한다. ACK도
  `position_unverified`이며 실제 각도 도달 합격이 아니다. happy 표정만으로 팔을
  움직이지 않는다. 검증 프로필이 있어도 real motion은 disabled다.
- IMU: 합성 trace로 health/freshness/포화/편향/고정값/누락/역순/재부팅과 제한된 반응
  후보를 검사한다. 실제 관측은 physical_unverified이며 제품/팔 안전 제어에 사용하지 않는다.
  주소 응답/heartbeat만으로 healthy로 인정하지 않는다.

터미널 명령:

```text
/games
/game fixture_game_a
/key B down
/key B up
/key ESC down
/face
/track
/photo
/camera-off
/song fixture_song_a
/media pause
/media resume
/media next
/media stop
/gesture wave
/emotion crying
/emotion auto
/repeat
/cancel
/reset
/stop
/quit
```

자산 ID는 `--demo-assets`일 때만 준비된다. 일반 Enter는 모의 질문이다.
게임 중 항상 “그만”을 듣는 호출어/VAD/상시 녹음 기능은 없다. 정지·취소와 메뉴
복귀는 모델 응답을 기다리지 않는 호스트 입력이다. `/repeat`는 음성만 재사용하며
과거 intent/gesture를 다시 실행하지 않는다. `/reset`만 대화 history/cache/감정을
명시 초기화한다. 모드 왕복은 brain/history와 감정을 보존한다.

## 공통 소유권과 계약

`features/resources.py`의 ModeManager가 이전 정리 → 새 준비 → 성공 확인을 소유한다.
정리 실패면 이전 소유자를 유지하고 새 소리를 시작하지 않는다. 새 준비 후 정리도
실패하면 복구 실패를 명시하며 다른 owner를 몰래 켜지 않는다. AudioFocus는 한 소리
소유자만 허용하고 stale lease가 새 owner를 해제하지 못한다. InputRouter는 mode별
하나의 소비자에 논리 입력을 주며 검증되지 않은 물리 mapping을 거부한다. STOP은 큐에서 우선 처리하고 이전 세대의
대기 launch를 무효화해 정지 직후 오래된 명령이 자원을 다시 켜지 못하게 한다.

GazeArbiter 우선순위는 수동 검수 > 원본 듣기/생각 > 명시 camera target > 제한된
reaction offer > 원본 idle이다. 감정·입을 바꾸지 않는다. 원본 look/lookEyes만
호출하고 원본 listening/thinking 시선·끄덕임·눈물·깜빡임을 덮지 않는다. 카메라
좌표의 stale/loss/변환/dead zone/smoothing/speed limit은 독립 모듈이 처리한다.

`features/reply.py`의 버전 1 FeatureReply는 speech/emotion/gesture/최대 한 intent다.
기존 `expression_contract.py`의 두 필드 manual/auto 복귀 계약은 그대로 보존했다.
같은 첫 Responses 요청에 strict schema와 신뢰된 catalog ID/준비 상태만 추가하며
두 번째 분류 호출이나 frame/sensor별 GPT 호출은 없다. history/TTS에는 clean speech만
전달한다. mode/turn/utterance/started/완료 검사 뒤 owner thread에서만 intent를 실행한다.
실제 계정의 schema/의미 품질·추가 토큰/지연/비용은 검수하지 않았다.

호스트 발급 request ID와 별도 idempotency journal을 사용한다. restart의 미완료 요청은
unknown이며 자동 재실행하지 않는다. 시작 전 journal 장애는 실행을 거부하고, 기능 시작/저장
뒤 journal 실패는 unknown으로 남긴다. 정지·취소는 journal과 LLM을 기다리지 않는다.
Mock Action v0의 5종/executed=False 계약과 새 HostResult를 섞지 않았다.

## shell과 보안

하나의 loopback origin에 선택 얼굴·정적 shell·상태만 허용한다. 정상 게임 전경과
face iframe 가시성을 shell liveness와 분리하며, 실제 shell 단절은 owned 작업을 정지한다.
게임 iframe은 `allow-scripts`만 갖는 sandbox이고 parent DOM/controller에 접근하지 않는다.
광범위 CORS/프로젝트 폴더 노출/임의 파일·URL·shell·키 endpoint는 없다.

side effect는 별도 POST다. exact Host/Origin, per-server token, 중복 헤더/JSON field,
8KiB 상한/Content-Length/type/transfer encoding을 검사한다. token은 API 키가 아니며
보고서·로그에 출력하지 않는다. HTTP thread는 bounded command queue에 넣기만 하고
실제 자원 변경은 owner poll에서 실행한다. private 사진/preview 응답은 CORP same-origin이다.

## 사용자 미디어를 나중에 제공할 때

기본 manifest는 `software/examples/media_manifest.example.json`이다. 임의 인터넷 URL,
절대경로·../·symlink/hardlink 탈출·임의 command를 거부한다. 명시한 content root의
상대 파일과 SHA-256을 import 및 재생 직전에 검증하고 재생 중 원본 FD를 유지한다.
현재 backend가 지원하지 않는 codec은 unsupported로 표시한다. 실제 사용자 노래 파일을
받기 전 다운로드·가창 생성·성우 변환은 하지 않았다.

등록 API는 `MediaFeature.import_manifest(payload)` 또는
`features.media.import_manifest(payload, content_root=...)`다. 모델/HTTP에서 content root를
받지 않는다. 소유자 검토하에 준비된 root/manifest를 주입하는 단계와 실제 음량·출력 검수는
별도다. 실제 노래/게임팩의 자산 승인과 실제 장치 enable을 이 모의 CLI에서 자동 처리하지 않는다.

## 검사와 롤백

개발 의존성은 새 clone의 `.venv`에만 설치했다. 전체 unittest와 승인 HTML 검사는
[FEATURE_BUNDLE_V1_LATEST_KR.md](../docs/reports/FEATURE_BUNDLE_V1_LATEST_KR.md)의 정확한 결과를 따른다.

```bash
cd /home/bgh/BMO_FEATURES/software
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B -m unittest discover -s tests -q
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B checks/check_feature_bundle_browser.py \
  --face /home/bgh/Downloads/BMO_Screen_v13_2.html --chromium /usr/bin/chromium
```

`/quit` 후 보존된 `/home/bgh/BMO_EMOTION/launch-bmo`로 돌아가면 된다. 유료 API나 실물
검수는 입회·별도 승인 전 실행하지 않았다. C에서 기존 폴더/서비스/펌웨어/팬/SSH/백업을
수정하거나 main에 병합하지 않았다. 정확한 SHA/PR/자산 누락은 최신 보고서에 남긴다.
