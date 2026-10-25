# BMO 카메라 영상·게임·음악 준비 가이드

Issue #22의 소프트웨어 및 모의 검수 경로다. 카메라 영상은 GP8의 **음성 녹음**과 별개다.
GP8 초록 버튼은 대화 입력에만 사용하고 GP7 빨간 버튼·현재 RP2040 펌웨어를 재매핑하지 않는다.
R10 HTML/SVG, v13.2, 감정 연속성, 기존 Python 음성 정책과 Piper 원본은 보존한다.

## 현재 동작 범위

영상 계약은 `bmo_camera_video_v1`로 기존 사진·HostIntent v1과 분리했다.
`features/camera/video.py`의 `VideoRecorder`와 `VideoStore`가 기존
`CameraFeature.broker` 한 개를 사용한다. 앱 시작·idle·대화·얼굴 추적을 이유로
영상 녹화를 자동 시작하지 않는다. 영상에는 **마이크·오디오 스트림이 없다**.

제품 live의 `camera_video`는 항상 **denied**다. 키, 모델 응답, 환경 변수,
저장 위치 선택이나 동의 버튼만으로 실제 카메라를 활성화할 수 없다.
이 버전에 실제 영상 encoder를 켜는 옵션은 없다. Picamera2/libcamera·Camera Module 3 Wide의
실제 녹화·autofocus·codec·장치 전환·LCD 미리보기는 **NOT_RUN**이다.

`--simulate`에서 명시한 영상 시작은 DemoBackend의 합성 PNG 프레임을 기존 broker로
읽고 유효성을 검사한다. 저장 영상은 그 프레임 수에 대응해 새로 만든 **16×16 DIB/24-bit
무압축 AVI 합성 fixture**다. 실제 카메라 프레임을 변환하는 PNG encoder나 네이티브
영상 decoder를 검증한 것이 아니다. 파일의 `duration`은 `frames/fps`의 합성 timeline이다.
출력은 AVI 구조·프레임 크기·index·무음 스트림 수·SHA-256을 검사한다.

## 저장 위치를 선택한 뒤 명시적으로 시작·종료

코드를 검토한 사용자가 나중에 실행할 모의 명령의 예다. 이번 야간 작업은
일반 Chromium 창이나 실제 장치를 이 명령으로 실행하지 않았다. 새 worktree에는
기존 검증된 Python 환경을 명시한다.

```sh
BMO_PYTHON=/home/bgh/BMO_FEATURES/.venv/bin/python ./launch-desktop --status
BMO_PYTHON=/home/bgh/BMO_FEATURES/.venv/bin/python ./launch-desktop \
  --simulate --demo-assets --windowed --video-directory "$HOME/BMO_Video_Simulation"
```

`--video-directory`는 사용자가 선택한 로컬 영상 디렉터리다. **부모 디렉터리는 미리
존재해야** 하며 symlink·`..` 경로를 거부한다. 선택한 마지막 디렉터리만 첫 명시 시작 때
0700으로 만들 수 있다. 기존 디렉터리의 소유자나 권한이 다르면 자동 chmod·수리하지 않고
거부한다. 영상·journal·lock·임시 파일은 0600이다. 위치를 선택하지 않으면
`video_storage_not_selected`이며 녹화를 시작할 수 없다.

외부 Desktop 메뉴의 별도 **모의 영상 시작** 동작으로 준비·동의를 확인하고 시작한다.
녹화 중 외부 shell에 표시를 유지하며 **영상 저장/종료**로 종료한다.
기본 전체 화면은 승인 R10이며 카메라 UI를 원본 HTML/SVG에 추가하지 않는다.
음성 버튼·Enter·대화 모델의 일반 intent를 영상 시작으로 바꾸지 않는다.

Host 제어 계약은 다음과 같다. HTTP의 기존 exact Host/Origin/token·nonce 검증을 거친다.
API 키나 파일 경로를 이 payload에 넣지 않는다.

```json
{"action":"video_start","value":{"consent":true}}
{"action":"video_stop","value":null}
```

내부 호출은 `start_video_recording(request_id, generation=mode_generation, consent=True)`,
`poll(generation=mode_generation)`,
`stop_video_recording(원래_시작_request_id, generation=mode_generation)`다.
`VideoResult.public()`와 `camera_video` snapshot은 경로·이미지·대화 내용을 공개하지 않는다.
모의 성공은 `status=simulated`, `executed=false`, `completed=true`다.
`actual_camera_verified=false`와 `audio_streams=0`을 유지한다.

## 제한·중재·실패 처리

기본 제한은 최대 **12초**, AVI 최대 **8 MiB**, 게시 후 여유 공간 **16 MiB**,
합성 timeline **2 fps**다. `VideoConfig`는 최대 30초/120프레임 범위까지 검사한다.
시작 때 최대 파일 예산과 최소 여유 공간을 함께 확보할 수 있는지 확인하고,
게시 직전에도 실제 파일 크기와 여유 공간을 다시 검사한다.

- 시작·종료 요청을 중복해도 재촬영·재게시하지 않는다. 요청 ID가 같고 설정 fingerprint가
  다르면 충돌로 거부한다. 다른 녹화가 있으면 `video_busy`다.
- 같은 broker의 preview·tracking·사진이 이미 열렸다면 영상이 자원을 빼앗지 않는다.
  영상 소유권을 가진 동안 다른 사진·preview 호출과 unowned `camera.off()`는
  해당 영상 스트림을 닫을 수 없다. Host가 명시적으로 먼저 종료·취소한 후 전환한다.
- 시간·바이트 한도는 마지막으로 검증된 완전한 합성 프레임까지만 게시한다.
  모드 generation 변경, 취소, 앱 종료는 미완성 영상을 버리고 사유를 journal에 남긴다.
- 저장은 0600 임시 파일 → 내용·AVI 검증 → SHA-256 → 같은 디렉터리의
  **덮어쓰기 없는 원자 게시** → 디렉터리 fsync 순서다. 기존 사용자 파일이나
  symlink를 덮어쓰지 않는다. 게시 전에 취소되면 자기 임시 파일만 정리한다.
- backend close 실패는 `UNKNOWN/video_close_failed`다. 불확실한 broker 소유권을
  유지하고 새 녹화를 차단한다. 네이티브 장치의 정지를 강제로 보장한다고 말하지 않는다.
- 게시 뒤 journal 저장에 실패하면 파일을 보존하고 `video_saved_journal_pending`을
  표시한다. 성공 파일을 삭제하거나 같은 요청을 자동 반복하지 않는다.

완성한 사용자 영상·사진은 앱 종료나 일반 취소로 자동 삭제하지 않는다.
기존 사진은 기존 PhotoStore 계약을 따른다. 합성 fixture도 사용자가 선택한 영구 영상
디렉터리에 게시됐다면 자동 삭제하지 않는다. 테스트에서 직접 만든 임시 디렉터리만
테스트가 정리한다. 클라우드 전송·자동 업로드·얼굴 embedding·인물 신원 DB는 없다.

## 중단·전원 장애 뒤 확인

`.video-journal.json`에는 원본 픽셀·음성·대화 대신 요청 ID, 설정 fingerprint,
결과 ID·해시·크기·사유만 남긴다. 다른 프로세스는 같은 private lock을 점유할 수 없다.
전원 장애 등으로 `running` 기록이 남았으면 같은 요청을 재시도할 때 **UNKNOWN**을
돌려주고 자동 녹화하지 않는다. 완료된 AVI가 있어도 journal 복구를 추정하지 않는다.

사용자가 앱을 종료한 뒤 선택한 영상 디렉터리의 `.avi`와 metadata를 별도로 보존하고
검토한다. 잘못된 journal·권한·symlink·디스크 부족을 코드가 자동 수리하거나 지우지 않는다.
private 자료를 Git/이슈/공개 로그에 복사하지 않는다. `video_close_failed`나
`video_saved_state_unknown`이면 재시작·중복 촬영 전에 사용자 검수를 거친다.

## 게임·음악 자산과 복귀

기존 GamesFeature, OwnedProcessRunner, ZIP importer·rollback, MediaFeature와
AudioFocus를 재사용한다. `--demo-assets`의 기본 자산은 합성 게임 2개와 무음 audio fixture다.
실제 `BMO_Games_v3.zip`·Tetris·사용자 음원은 출처·라이선스·checksum을 검토하기 전까지
**ASSET_PENDING**이다. 빈 catalog를 설치 성공으로 표시하지 않는다. ROM·원작 음악을
외부에서 자동 다운로드하거나 공개 저장소에 추가하지 않는다.

보유 게임팩 검토는 기존 도구를 사용한다. dry-run은 자산을 실행·설치하지 않는다.

```sh
BMO_PYTHON=/home/bgh/BMO_FEATURES/.venv/bin/python
PYTHONPATH=software "$BMO_PYTHON" -B -m bmo_ai.game_assets --dry-run
```

위 모듈 명령은 프로젝트 루트에서 실행한다.
실제 보유 ZIP·신뢰한 SHA·라이선스 자료가 준비되면 기존 importer의 검증 절차로 인수한다.
웹/native/RetroArch의 실물 종료 hotkey·USB gamepad·출력은 별도 검수다.
게임 메뉴/게임의 GP8 Enter는 선택·게임 입력으로 남고 PTT를 시작하지 않는다.

음악은 stop/pause/resume/next와 AudioFocus 한 명의 소유·얼굴 복귀를 검사한다.
cue가 있으면 같은 playback clock을 쓰며, cue가 없으면 RMS로 노래 립싱크를 꾸미지 않는다.
미디어 중 마이크 입력을 막아 BMO 자신의 출력을 새 질문으로 만들지 않는다.
게임·사진·영상·음악 뒤 기존 history와 마지막 감정을 보존하고 `/repeat`가 저장·촬영·
게임 실행 intent를 다시 실행하지 않도록 회귀 검사한다.

## 실기 인계와 안전한 복구

실제 카메라·사진·영상·마이크·스피커·USB 버튼·서보·IMU·firmware·유료 API 검수는
자동 모의 PASS와 별개인 **NOT_RUN/HUMAN_TEST_PENDING**이다. native codec 설치나
Debian OS 패키지 변경, fan·SSH·VNC·Wayland·서비스 변경은 이 단계에 포함하지 않는다.
Camera Module 3 Wide에서 저해상도 추적/preview/고해상도 사진/무음 영상의 전환,
autofocus·timestamp·codec·hotplug·장기 발열·전원 검증 후에만 새 승인 범위를 검토한다.

되돌릴 때 새 앱을 정상 종료하고, 보존한 기존 `/home/bgh/BMO_FEATURES` 실행 경로를
사용한다. 새 브랜치나 사용자의 영구 영상 디렉터리를 삭제할 필요가 없다.
`reset --hard`, `clean`, force-push, main 병합, 기존 백업 삭제는 하지 않는다.
모듈/Host/브라우저의 실제 테스트 수·커밋·PR·CI는 기능 보고서와 상위 인계서를 따른다.
