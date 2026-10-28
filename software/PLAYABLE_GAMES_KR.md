# BMO 실제 오프라인 게임팩 v1

PR #36의 동일 Desktop에서 실행하는 자체 제작 게임 3개다. ROM·외부 게임 ZIP·CDN·외부 스크립트·브라우저 오디오를 사용하지 않는다. 게임 코드와 Canvas 그래픽은 저장소에 포함되어 있으며 MIT 라이선스다.

| 게임 | 실제 플레이 | 키보드 / 표준 패드 |
|---|---|---|
| Stack Sprout | 블록 이동·회전·쌓기·줄 제거·점수·레벨·쌓임 gameover | ←→ 이동, ↓ 빠른 하강, ↑ 또는 Z/A 회전, B/Space 하드 드롭 |
| Lantern Labyrinth | 미로의 빛 수집·큰 빛 효과·추격자 회피·목숨·완주 | 방향키 / D-pad·왼쪽 스틱 이동 |
| Vine Run | 먹이·성장·점수·속도 변화·벽/자기 몸 충돌 | 방향키 / D-pad·왼쪽 스틱 조향, Z/A 시작 |

공통: Enter/Start는 일시 정지·계속, Esc는 게임 목록, Start+Select를 1.2초 유지하면 목록으로 돌아간다. 화면의 **다시 시작**은 새 실행 ID와 입력 세대를 생성하므로 이전 대기 키가 새 보드에 적용되지 않는다. 게임 종료 화면에서 A/B/Start로도 다시 시작할 수 있다. 목록의 **얼굴로 돌아가기** 또는 B/Esc로 R10에 복귀한다.

## 실행

새 checkout의 `.venv`에 기존 개발 의존성이 설치된 상태에서, Pi의 그래픽 세션에서 실행한다.

```bash
cd /home/bgh/BMO_PLAYABLE_GAMES
./launch-desktop --simulate --playable-games --usb-gamepad
```

여기서 `--simulate`는 기존 대화·장치 경계를 가상 모드로 유지한다. **`--playable-games`의 세 게임은 실제 JavaScript 엔진을 실행한다.** 가짜 점수/가짜 게임 runner가 아니다. 이 실행은 API 키나 유료 요청을 필요로 하지 않는다. Esc → 게임 목록 → 게임을 선택한다. 원본 R10이 계속 보존되며 플레이가 끝나면 같은 얼굴·세션으로 돌아간다.

기존 검수된 실제 대화 실행에 게임만 추가하려면 해당 명령에 `--playable-games`를 추가한다. 카메라·팔·마이크 권한을 이 옵션이 추가하지 않는다. 게임패드도 `--usb-gamepad`를 지정했을 때 게임/목록에서만 읽는다. 기본 실행은 기존과 같으며 `--demo-assets`와 실제 게임팩은 함께 선택하지 않는다.

선택적 native Qt와 Chromium은 기존 Desktop frontend를 그대로 사용한다. Pi 실제 Qt/USB 매핑은 별도 사용자 검수이며, 문제가 있으면 기존 `--frontend chromium`을 명시적으로 사용할 수 있다. 새 프로그램·OS 패키지·서비스·펌웨어 설치는 필요하지 않다. 기존 아이콘을 덮어쓰지 않고 설치 미리보기에 `--playable-games`를 보존하는 API를 제공한다. 이번 개발 작업은 바탕화면 설치를 실행하지 않았다.

## 입력·정리

- 표준 패드의 A=0, B=1, Select=8, Start=9, D-pad=12~15와 왼쪽 스틱을 사용한다. 메뉴/게임 활성화 시 이미 눌린 버튼은 놓았다 다시 눌러야 한다.
- 키보드와 패드는 기존 Host·ModeManager·AudioFocus·InputRouter를 거친다. 긴 세션은 bounded 입력 스트림을 사용해 일반 요청 장부를 소진하지 않는다.
- blur/숨김/연결 지연은 입력을 해제하고 플레이를 멈춘다. 패드 연결 해제/서버 watchdog은 게임을 정리하고 목록으로 복귀한다. 포커스를 되찾은 뒤 Start나 계속 버튼으로 재개한다.
- 게임에서 Enter는 GP8 PTT가 아니다. 게임 입력으로 대화·카메라·팔·GPIO·유료 API를 요청할 수 없다. 음악/대화와 오디오 소유권을 겹치지 않는다. v1 게임은 무음이다.
- 엔진에는 타이머가 없고 Desktop이 단일 animation frame을 소유한다. pause/gameover/복귀/페이지 종료에서 루프를 정리한다. 재로드하면 현재 보드가 초기화될 수 있으며 영구 저장은 제공하지 않는다.

## 검증·자산

`features/games/pack/manifest.json`의 고정 SHA와 `engines.js`·`LICENSE.txt` SHA가 일치해야 READY가 된다. 검증한 bytes만 고정 `/games/` 경로로 제공하며 임의 파일·외부 프로그램·LLM 명령 실행을 허용하지 않는다. 파일을 수정했다면 코드 리뷰와 manifest/pin 갱신이 필요하다. 기존 외부 게임 실행 제한을 해제하지 않는다.

```bash
.venv/bin/python -B software/checks/check_playable_games_browser.py --chromium /usr/bin/chromium --artifacts artifacts/playable-games
```

검사는 실제 Desktop에서 키보드·표준 Gamepad API 입력으로 세 게임의 점수·플레이·재시작·충돌·R10 복귀를 확인한다. Gamepad API의 장치 경계만 대체하며 실제 USB 하드웨어 검수로 보고하지 않는다. 원본 R10 파일은 검사 전후 같은 SHA인지 검증한다. 실제 화면 캡처와 결과 JSON은 위 artifacts 경로에 남고 GitHub CI에도 공개 합성 얼굴 프로필의 artifact를 남긴다.

이미 검증된 R10/GPT/Fish/카메라/RP2040 코드는 이 게임팩에서 변경하지 않았다. 실제 Pi 게임패드·native Qt 화면만 사용자가 짧게 확인하면 된다. 이번 작업에서 USB/GPIO/서보/펌웨어/카메라/유료 API는 실행하지 않았다.
