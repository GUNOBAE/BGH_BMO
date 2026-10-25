# 게임·음악 자산 준비와 읽기 전용 진단

Issue [#22](https://github.com/GUNOBAE/BGH_BMO/issues/22)의 독립 모듈이다.
실물 카메라·마이크·스피커·버튼·팔·IMU, 유료 API, 펌웨어, OS/팬/서비스를
실행하거나 변경하지 않는다. 아래 설치·재생 안내는 사용자가 자산과 권한을
검토한 뒤 수동으로 수행할 절차이며 야간 개발에서 실행한 기록이 아니다.

## 기존 코드 재사용 범위

게임은 기존 `game_assets.py`/`features/games/assets.py`의 ZIP 검토, SHA 검증,
라이선스 확인, private staging, 실패 rollback을 유지한다. 실제 게임팩/HTML이
없으면 `ASSET_PENDING`이다. 두 synthetic game은 실제 게임 설치 증거가 아니다.
실행 명령·웹/native/RetroArch 런타임·패드 전달은 서로 별도 검수가 필요하다.
일반 ZIP을 검토·import했다고 즉시 게임 실행 권한을 부여하지 않는다.

음악은 기존 `MediaAsset`, `AssetHandle`, manifest 검증, `MediaFeature`의
stop/pause/resume/next/cue 계약을 재사용한다. 지원 여부는 **파일 형식 검증**과
**실제 backend codec 지원/volume 승인**을 나눈다. 현재 importer는 PCM16 WAV,
1~2채널, 8~96kHz, 최대 64MiB/7200초까지만 받는다. MP3/OGG/FLAC/MP4를
WAV로 간주하지 않으며 변환 프로그램을 실행하지 않는다. 없는 노래를 생성하거나
인터넷에서 내려받지 않는다. cue가 없는 곡은 `cue_kind=none`이며 가짜 노래
립싱크를 만들지 않는다. 실물 `music` capability는 계속 denied다.

## 명시적인 로컬 WAV 검토·import

저장 경로를 선택하지 않은 기본 명령은 파일 생성·장치 open·네트워크 없이
빈 준비 상태만 출력한다. 실행 위치는 repository의 `software/`, Python은
사용자가 검증한 가상환경을 사용한다.

```sh
python -m bmo_ai.media_assets
python -m bmo_ai.media_assets --audio /absolute/path/owned-song.wav --dry-run
```

dry-run은 SHA/바이트 수/시간/PCM 속성만 출력하며 경로·음성 내용·출처 문자열을
출력하지 않는다. 음악을 소유하거나 사용할 수 있는 근거를 사용자가 확인한 뒤,
출력된 실제 SHA를 아래 `REVIEWED_SHA256` 자리에 넣는다. 이 명령은 **파일
준비만** 하며 스피커·STT·모델·Fish를 실행하지 않는다.

```sh
python -m bmo_ai.media_assets --audio /absolute/path/owned-song.wav \
  --sha256 REVIEWED_SHA256 --content-root /absolute/private/bmo-media \
  --media-id my_song --title 'My song' --license-id user_reviewed \
  --license-reviewed --install
python -m bmo_ai.media_assets --content-root /absolute/private/bmo-media --dry-run
```

`license-reviewed`는 사용자의 명시적 확인 기록이다. 도구가 저작권/라이선스를
판단하거나 승인한 것이 아니다. 경로는 절대 경로를 쓰며 `.config`/`.ssh`/`.git`,
symlink/하드링크/장치/FIFO, `..`, 기존 파일 교체는 거부한다. 기존 루트의 권한이
private 0700이 아니면 자동 chmod하지 않는다. 새 파일은 0600이고 장치 실행은 0이다.

API는 `review_wav(path, expected_sha256=...)`,
`install_wav(review, root, id, title=..., license_id=..., license_reviewed=True)`,
`load_library(root) -> tuple[MediaAsset]`다. 마지막 튜플은 기존 `MediaFeature`에
연결한다. 제품의 `--music-directory /absolute/private/bmo-media`는 사용자가 명시한
검토된 목록을 읽기 전용으로 인수한다. `--simulate`는 무음 fake backend로만
선택/정지/일시정지/이어서/다음을 검증하며 실제 스피커를 열지 않는다.
`--demo-assets`와 동시에 고를 수 없다. live의 runtime/music deny는 해제하지 않는다.
라이브러리를 읽는 동작은 경로를 새로 만들지 않는다.

검토 후 import된 노래를 목록에서 빼려면 같은 SHA를 지정해 metadata만 보존
이름으로 전환한다. 오디오와 metadata 바이트를 모두 남긴다.

```sh
python -m bmo_ai.media_assets --content-root /absolute/private/bmo-media \
  --media-id my_song --sha256 REVIEWED_SHA256 --retire
```

기존 자산을 덮어쓰는 갱신은 v1에서 제공하지 않는다. 새 버전은 새 ID로 준비한다.
원자 publish는 WAV→manifest 순서의 no-overwrite link다. 프로세스 강제 종료나
전원 상실이 WAV publish 뒤 manifest publish 전에 발생하면 **미참조 WAV**가
남을 수 있다. 도구는 이를 자동 재생·자동 삭제하지 않는다. `.media-stage-*`와
미참조 WAV는 사용자가 별도 백업 후 검토한다. manifest가 없으면 해당 노래는
라이브러리 목록에 들어오지 않는다. native 장치 hang의 강제 회수를 보장하지 않는다.

## 소유 프로세스 정리

기존 `OwnedProcessRunner`의 `Popen.poll()/wait()` 뒤 numeric PGID에 다시 signal을
보내던 순서를 수정했다. 이제 direct child를 `waitid(WNOWAIT)`로 관찰하고,
기존 Chromium에서 검증한 bounded `/proc/*/stat` 메타데이터 probe로 **동일
session/group**의 활성 멤버가 끝난 뒤 direct child를 reap한다. 이후 signal은 0이다.
부모가 먼저 종료되고 자식이 SIGTERM을 무시하는 경우도 harmless Python 자식으로
검증했다. 전역 subreaper는 앱에 설정하지 않고 격리된 테스트 자식에서만 사용한다.
정리가 불명확하면 `cleanup_failed`로 소유권을 유지한다. 외부 ready/quit callback도
child를 `poll/wait`로 먼저 reap하면 안 된다. 실제 emulator/ALSA 실행은 NOT_RUN이다.

## 전원·버튼 진단 계약

`bmo_ai.system_readiness.decode_throttled('0x50000')`는 과거 undervoltage와
throttled 비트를 `historical`에 표시하고 `current_fault=False`로 구분한다.
알 수 없는 비트는 숨기지 않고 `interpretation_complete=False`로 표시한다.
단일 읽기 결과는 전원 건강/장기 안정성 합격 증거가 아니다.

`telemetry_snapshot()` 기본값은 `NOT_RUN`, IO 0이다.
`telemetry_snapshot(runner=..., enabled=True)`는 명시적으로 주입한 runner에만
고정 `('vcgencmd', 'measure_temp')`와 `('vcgencmd', 'get_throttled')`, 1초·64바이트
예산을 전달한다. shell/subprocess fallback, 자동 폴링, 팬 제어는 없다. 이번 개발에서는
runner에 합성 문자열만 주입했고 Pi 명령을 실제 실행하지 않았다.

`button_inventory()`는 사용자 문서의 GP2 UP/3 LEFT/4 RIGHT/5 DOWN/6 triangle/7 red/
8 green Enter를 읽기 전용 자료로 제공한다. 현재 펌웨어 검증이 아니며 GP7은
`PRESERVED_ARM_TOGGLE`, GP8도 `HUMAN_TEST_PENDING`이다. 키보드 Enter와 실제
GP8 HID Enter의 출처를 브라우저 키 이벤트만으로 구분할 수 없다. 펌웨어/serial/HID
장치를 읽거나 재시작하지 않는다. 팔·IMU는 DISABLED를 유지한다.

## 오프라인 검증과 남은 실기

신규 28개 테스트: readiness 9, WAV library 14, owned-process lifecycle 5.
합성 PCM WAV·임시 private 폴더·고정 telemetry 문자열·harmless Python 자식만
사용한다. 기존 game/media/ZIP/doctor 검사는 삭제하거나 skip하지 않는다.
최종 전체 회귀/브라우저/CI 수치는 상위 야간 인계서와 기능 보고서를 따른다.

실제 자산 license, GP8/키보드 포커스와 게임 Enter, 음량·오디오 품질·PTT echo,
실제 power/hotplug/장기 RAM·CPU, VNC/LCD, native/RetroArch·음악 재생은
`ASSET_PENDING` 또는 `HUMAN_TEST_PENDING/NOT_RUN`이다.
