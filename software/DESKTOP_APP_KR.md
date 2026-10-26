# BMO Desktop App v1

[Issue #17](https://github.com/GUNOBAE/BGH_BMO/issues/17)의 바탕화면 앱 구현이다.
[Draft PR #18](https://github.com/GUNOBAE/BGH_BMO/pull/18)은 PR #16의
`067d0a4ef7cdb89e54c60b90d796e2ba9163bf58`에서 분리했다. 기존 AI 엔진을
재작성하지 않고 `FeatureHost`·`IntegrationSession`·`FeatureServer`와 외부 얼굴
어댑터를 사용한다. [최신 검증 보고서](../docs/reports/BMO_DESKTOP_APP_V1_LATEST_KR.md)가
정확한 SHA·검사 수·CI와 실제 미검수 항목의 기준이다.

## 기본 화면과 실행 수명주기

최종 흐름은 바탕화면 BMO 아이콘 → 외부 `BMO Loading...` → 얼굴 준비 확인 →
승인된 새 고정 환영 음성으로 `Oh! You're back! BMO missed you!` 한 번 → R10 얼굴 전용 화면이다.
Loading에는 가짜 퍼센트를 표시하지 않는다. 로컬 서버/엔진/브라우저/iframe 준비를
구분하며, 서버가 떴다고 유료 API가 준비됐다고 표시하지 않는다.

제품 화면에는 개발 버튼·가상 패드·모의 상태·브라우저 주소를 표시하지 않는다.
주소창·탭과 OS 패널 제거를 위한 Chromium app/kiosk 플래그를 구성했고 실제
Wayland/VNC/LCD의 모양은 사용자 입회 검수가 남아 있다. headless 800×480 검사와
실제 OS 화면 검수는 구분한다. 바탕화면 entry는 `Terminal=false`라 별도 터미널도
자동으로 열지 않는다. 프로그램은 stdin에 의존하지 않는다.

- Esc 또는 얼굴 왼쪽 위 60px 영역을 1.2초 길게 누르면 사용자 메뉴를 연다.
- 메뉴에서 한 번 듣기·다시 듣기·정지·게임 목록·얼굴 복귀·종료를 선택한다.
- 게임 실행 중 Esc와 Start+Select 1.2초는 기존 InputRouter/Games 종료 계약을 사용한다.
  물리 pad/GP7 매핑은 새로 연결하지 않는다.
- `--developer`를 명시한 실행에서만 Ctrl+Alt+D로 기존 개발 화면 `/developer`에
  들어간다. 개발 화면의 제품 화면 링크로 돌아온다. 기본 실행에서는 그 경로가 404다.
- `--windowed`는 개발용 창이다. VNC/실 LCD 모두 같은 활성 사용자 Wayland 세션을
  사용하며, 세션 설정·VNC·OS 패널 설정을 변경하지 않는다.

원본 R10 HTML/SVG/표정/입/배율은 변경하지 않는다. 원본 내부 scale controller가
배율을 한 번만 소유한다. 외부 어댑터는 iframe 단독 reload 때도 재생성하고,
새로고침·재연결·게임/사진/음악 복귀에서 시작 인사를 다시 실행하지 않는다.
대화 history·감정 연속성·마지막 정상 답변의 `/repeat` cache는 보존한다.

## 이번 자동 작업에서 사용할 수 있는 오프라인 검사

다음 상태·계획 명령은 비밀 저장소·장치·포트를 열지 않는다.

```bash
cd /home/bgh/BMO_FEATURES
./launch-desktop --help
./launch-desktop --status
./launch-desktop --dry-run
./launch-desktop --install-desktop --dry-run
```

다음 모의 실행은 실제 개발용 Chromium 창을 수동으로 열 때의 명령이다. 이번
자동 작업에서는 fake Chromium child와 별도의 headless만 사용했고 사용자 세션의
interactive Chromium은 열거나 닫지 않았다.

```bash
./launch-desktop --simulate --windowed
./launch-desktop --simulate --windowed --developer --demo-assets
```

`--simulate`는 브라우저의 전용 임시 HOME과 임시 인사 캐시·합성 WAV, 기존 모의 엔진을 사용한다.
실제 greeting/credentials.json/.env를 읽지 않는다. 유료 Fish·GPT/STT·실제 녹음·
스피커·카메라·서보를 호출하지 않는다. 브라우저 음성은 사용하지 않고 Python 소유
플레이어만 사용한다. 기존 live 기능 capability deny는 그대로다.

## 입회 단계: 키와 한국어 시작 인사 준비

아래는 코드로 준비한 **추후 사용자 검수 절차**이며 이번 자동 작업에서 실행하지 않았다.
현재 저장된 키가 있으면 다시 입력하지 않는다. 키가 없을 때만 기존
`./launch-bmo --setup-credentials`의 숨김 입력을 사용한다. 별도 키 저장소·키 복사·
명령행 키 옵션은 없다. 기존 `~/.config/bmo/credentials.json` 또는 XDG 경로의
0700/0600 저장소를 사용하며 live도 공유 launcher lock 뒤에만 resolve한다.
바탕화면 실행은 비대화형이므로 키가 없으면 얼굴과 설정 필요 상태를 표시하며
키를 자동으로 에코 입력받지 않는다.

일반 앱 실행은 승인된 캐시를 읽기만 한다. 인사 캐시가 없거나 손상·미승인·다른
음색이면 얼굴과 진단 상태로 넘어가며, API를 자동 호출하지 않는다. 메뉴의 인사
재시도도 캐시 재생만 시도한다. 새 인사는 앱을 종료한 뒤 별도 명령으로 준비한다.

```bash
# 다음 명령부터는 별도 비용/음성/스피커 검수 승인 이후에만 실행한다.
cd /home/bgh/BMO_FEATURES
./launch-desktop --live --prepare-greeting
# 위 명령은 Fish 합성 한 번만 요청하고 pending cache를 저장한다. 자동 재생하지 않는다.
./launch-desktop --live --audition-greeting
# 실제 한국어 발음·밝기·Aaron 음색·Soft·음량을 사람이 확인한다. 추가 Fish 호출 없음.
# 출력된 WAV SHA-256을 사람이 확인한 뒤 승인한다.
./launch-desktop --live --approve-greeting WAV_SHA256_FROM_PREPARATION
./launch-desktop --live
```

새 고정 문장은 `Oh! You're back! BMO missed you!`이며 Fish Aaron Soft / beemo / gain 0.25를 유지한다.
이 성우의 한국어 지원·발음·음질은 실제 청취 **NOT_RUN**이다. Piper로 무단 대체하지
않는다. `--offline-voice`는 기존 대화용 원본 Piper opt-in이며 시작 인사의 음색을
자동 변경하지 않는다. USB mic와 MAX98357A ALSA 기기 기본값도 기존 통합 런처와
같고 `--mic-device`/`--speaker-device`의 명시 변경만 지원한다.

캐시는 기본 `$XDG_CACHE_HOME/bmo/greeting-welcome-en-v1/greeting.cache` 또는
`~/.cache/bmo/greeting-welcome-en-v1/greeting.cache`다. private 디렉터리/0600 단일 bundle에
텍스트·voice/model/delivery/name/gain/version fingerprint, gained WAV와 SHA,
cue와 SHA, 승인·synthetic 구분을 함께 저장한다. 검증 후 별도 소유 WAV snapshot으로
재생하여 경합을 막는다. gain은 저장 시 한 번만 적용한다. synthetic cache는 승인해도
live로 재생할 수 없다. 준비 실패 시 이전 캐시를 보존하고 매 실행마다 유료 합성을
강요하지 않는다. 인사 재생은 AudioFocus 하나를 잡으며 대화와 겹치지 않는다.

이전 `난 비모야~!` 음성 캐시(legacy `bmo/greeting/greeting.cache`)는 자동 삭제·덮어쓰기하지 않는다. 새 환영 문구의 SHA 청취 승인을 별도로 진행한다.

## 입회 단계: 사용자 홈에 수동 아이콘 설치

실제 설치는 이번에 실행하지 않았다. 먼저 dry-run의 target 경로와 실행 명령을
확인한다. 기본은 `~/Desktop/BMO.desktop`, 사용자 applications entry와 generic BMO
문자 SVG 세 파일이다. 원본 R10 그림을 아이콘으로 복사하지 않는다. 한국어 바탕화면
등 다른 위치라면 실제 경로를 확인하고 `XDG_DESKTOP_DIR`에 절대 경로로 명시한다.
셸 실행/eval이나 시스템 경로 설치는 지원하지 않는다.

```bash
cd /home/bgh/BMO_FEATURES
./launch-desktop --install-desktop --dry-run
# 사용자 설치 승인 후에만 실행; 자동 앱 실행이나 부팅 등록은 하지 않는다.
./launch-desktop --install-desktop
# 모의 실행 전용 아이콘이 필요하면 --simulate를 명시한다.
# ./launch-desktop --install-desktop --simulate --dry-run
```

새 entry의 기본 Exec는 `launch-desktop --live`다. 기존 동일 파일이면 그대로 두고,
내용이 다르거나 symlink·hardlink·권한이 안전하지 않으면 덮어쓰지 않고 중단한다.
기존 파일의 chmod·desktop trust를 자동 변경하지 않는다. 파일 관리자가 요구하는
새 아이콘의 "실행 허용"은 사용자가 GUI에서 검토한다. `desktop_trust=NOT_RUN`을
실제 아이콘 작동 합격으로 바꾸지 않는다. 설치는 stage/no-overwrite publish와 inode
기반 rollback을 사용하며 기존 백업·다른 프로그램·autostart/systemd를 건드리지 않는다.

## 실패·종료·복구

공유 사용자 lock으로 기존 launch-features/launch-bmo와 중복 실행을 막는다. 중복
더블클릭은 새 엔진·인사·브라우저를 만들지 않는다. 전용 Chromium profile을 사용자
runtime directory에 새로 만들고 기존 Chromium HOME/북마크/로그인과 분리한다.
브라우저 환경은 세션에 필요한 항목만 허용하고 API 키를 넘기지 않는다. remote debug나
sandbox 해제 옵션은 없다. 자동 재실행 루프도 없다.

브라우저 시작 실패·얼굴 timeout·엔진 오류는 고정 진단 코드로 구분한다. 시작 인사
실패 뒤에도 얼굴과 재시도/종료 메뉴를 제공한다. 정지는 ready/retry보다 우선하고,
시간 초과 뒤 늦은 완료는 실패 원인을 덮지 않는다. 재생이 실제로 시작된 사실과
완료를 분리한다. 앱 종료는 앱 소유 player·browser group·server만 정리한다.
`killall`/`pkill chromium`이나 기존 세션 종료 명령을 사용하지 않는다.

복구할 때 메뉴의 종료 또는 개발 창 닫기로 새 앱을 종료한다. 원래 수동 통합
`/home/bgh/BMO_FEATURES/launch-features`나 보존된 BMO_EMOTION/BMO_INTEGRATION을
입회 승인 범위에서 다시 선택한다. clean 상태를 확인한 뒤 기존
`feature/bmo-offline-integration-v2`로 switch할 수도 있다. 설치를 되돌리려면
설치 계획에 나온 **새 BMO entry/icon 파일의 소유권·내용을 확인한 뒤** 사용자 검수로
처리하며, 이 작업은 실제 사용자 파일을 삭제하지 않았다. 인사 캐시·키·기존 백업을
자동 삭제하는 uninstall은 제공하지 않는다. main 병합·force-push·hard reset은 필요 없다.

hard SIGKILL/전원 상실, 실제 labwc/VNC/LCD fullscreen·OS 패널, 파일 관리자 trust와
한국어 음질·실시간 부하 검수는 남아 있다. 이슈에 있는 기존 실기 성공·전원/온도
관측은 사용자 보고로 보존하며 이번 실행에서 다시 측정하거나 전원·팬을 바꾸지 않았다.
