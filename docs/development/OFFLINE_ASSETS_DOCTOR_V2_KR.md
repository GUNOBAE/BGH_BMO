# 오프라인 자산 검토와 수동 실행 준비

Issue #15에 맞춘 준비 도구다. 자동 다운로드, 실제 게임 실행, 영상 디코딩, 로봇 장치 접근을 수행하지 않는다. 기존 대화 런처와 원본 화면, 기존 백업을 수정하지 않는다.

## 현재 상태

| 항목 | 구현·검증 범위 | 남은 검증 |
| --- | --- | --- |
| 게임팩 | 알려진 로컬 후보의 메타데이터 목록, ZIP 정적 검토, 명시적 비실행 설치 준비 | 실제 게임팩은 `ASSET_PENDING`; 사람의 라이선스·활성 웹 코드 검토와 런타임별 검증 필요 |
| doctor | 의존성 설치 메타데이터, 고정 화면 SHA-256, 로컬 Git 상태, 알려진 자산 존재 여부 | 실제 장치·유료 API·LCD·원격 CI는 `NOT_RUN` |
| desktop | 통합 진입점의 수동 실행 템플릿 출력 | 바탕화면 설치·클릭 실행·자동 시작은 `NOT_RUN` |
| 합성 영상 | 직접 생성한 작은 무음 AVI의 구조·해시·시간 검증과 주입한 가짜 타임라인 | 실제 브라우저 코덱·영상 출력·음성 출력은 미지원/`NOT_RUN` |

`READY_FOR_HUMAN_TEST`는 doctor의 메타데이터 선행조건이 충족되었다는 뜻이다. 모든 기능이 실행 가능하거나 사람의 실물 검증을 통과했다는 뜻으로 사용하지 않는다. 선택적 카메라 패키지 누락과 사용자 게임·노래 부재는 각각 별도로 표시한다.

## 읽기 전용 확인

프로젝트 `software` 폴더에서 가상환경 Python으로 실행한다.

```sh
../.venv/bin/python -B -m bmo_ai.offline_doctor --offline
../.venv/bin/python -B -m bmo_ai.game_assets --dry-run
../.venv/bin/python -B -m bmo_ai.desktop_plan --dry-run
../.venv/bin/python -B -m bmo_ai.desktop_plan --dry-run --print-template
```

doctor는 지정된 HTML을 읽어 고정 해시와 비교하고, 패키지 메타데이터와 로컬 Git 상태만 조회한다. 설치된 장치 드라이버를 import하거나 외부 도구를 실행하여 장치를 탐지하지 않는다. Git 조회는 네트워크 없이 optional lock·fsmonitor·untracked 탐색을 끈다. 키 파일, 환경 키 값, 개인 로그, 포트, 장치 파일을 확인하지 않는다. JSON에는 원본 HTML 본문, 절대 콘텐츠 경로, Git 변경 파일명이나 원시 오류를 넣지 않는다.

게임 기본 목록은 알려진 후보 경로 몇 개에 대한 `lstat`만 수행한다. 디렉터리 재귀 탐색과 파일 본문 열기는 없다. 후보가 존재하더라도 `AVAILABLE_FOR_REVIEW`이며 게임 설치·검증 완료로 표시하지 않는다.

desktop 도구는 `launch-features --simulate --screen-profile r10`을 기본으로 출력한다. `--mode live`는 명시적으로 요청한 대화 실행 계획만 출력한다. 어느 모드에서도 프로세스를 실행하거나 `.desktop`, autostart, systemd 파일을 설치하지 않는다. 템플릿의 `Terminal=true`는 추후 사용자가 수동 실행할 때 비밀 입력과 안전 오류를 볼 수 있게 하는 설정이다.

## 이미 소유한 ZIP의 검토와 설치 준비

`bmo_ai.game_assets --archive /absolute/owned-game.zip --dry-run`은 ZIP을 실행하지 않고 검토 결과를 출력한다. `--sha256`을 주면 지정한 고정 해시와 일치해야 한다. 이름과 상대 경로·파일 해시만 검토 결과에 포함하며 라이선스 본문은 출력하지 않는다.

다음 조건을 충족해야 명시적인 `--install` 준비 작업을 수행할 수 있다.

- 절대 경로의 이미 소유한 ZIP, 그 SHA-256, private content root, 고정 pack ID를 제공한다.
- 사람이 라이선스를 검토한 뒤 `--license-id`와 `--license-reviewed`를 명시한다. 이 기록은 자동 법적 판단이 아니다.
- HTML·JS·SVG 같은 활성 웹 코드가 있으면 사람이 내용을 검토한 뒤 `--web-content-reviewed`도 명시한다. 이 플래그가 웹 코드의 네트워크 접근 안전성을 기술적으로 입증하지 않는다.
- 기존 같은 pack을 교체하려면 별도로 `--replace-existing`을 명시한다. 자동 교체하지 않는다.

검토기는 절대·상위·Windows·인코딩 경로, 중복·충돌 경로, `.git`·`.env`·credential 파일명, symlink·장치·FIFO, 암호화 항목, 실행 비트와 네이티브 실행 파일 헤더를 거부한다. 지원 목록 밖의 확장자는 거부한다. 외부 게임 코드의 import, 쉘 실행, ROM·에뮬레이터 실행, 브라우저 열기는 없다. ZIP 전체·항목 수·항목 크기·압축 비율·총 해제 크기를 제한하고, 재확인한 ZIP·항목 해시만 추출한다.

`ZipFile`이 항목 객체를 만들기 전에 EOCD와 중앙 디렉터리를 작은 고정 버퍼로 사전 검사한다. 최대 2,048항목, 중앙 디렉터리 4 MiB만 허용하며 EOCD의 개수와 실제 레코드 수·길이, 로컬 헤더·데이터 범위, 오프셋을 대조한다. ZIP64·다중 디스크·겹치는 데이터 영역을 거부하고 압축 방식은 stored/deflate로 제한한다. 허위 항목 수와 범위 초과는 `ZipFile` 생성 전에 거부한다.

설치는 사용자 소유 700 디렉터리와 600 일반 파일만 사용한다. `O_NOFOLLOW`·고정 dirfd·파일 정체성 검사·private flock·새 staging 디렉터리·파일 fsync 후 디렉터리 rename을 사용한다. 설치 manifest도 `runtime_verified=false`, `runtime=NOT_RUN`이다. 설치 준비가 성공해도 HTTP 등록, 게임 catalog READY 전환, 실행 허용을 자동으로 수행하지 않는다.

## 교체와 복구

기존 pack은 새 무작위 `.rollback-*` 디렉터리에 보존한다. 기존 rollback과 이름이 충돌하면 새 이름을 시도하며 덮어쓰거나 삭제하지 않는다. 자기 작업에서 만든 staging만 실패 정리 대상으로 삼는다.

실행 중 예외나 rename 직후 취소는 실제 디렉터리 inode를 확인하여 기존 버전을 복원한다. 교체 후 fsync 실패도 가능한 범위에서 복원한다. 복원 자체가 실패하면 `game_rollback_pending`을 반환하고 이전 rollback과 새 staging을 모두 남긴다. 사람이 경로·해시를 확인한 후 복구해야 한다. 저장장치 고장이나 전원 차단까지 항상 원래 경로가 유지된다고 보장하지 않는다. 기존 경로를 rollback으로 이동하는 rename과 새 디렉터리 게시 rename은 두 단계이며, 영구 장애 시 보존된 rollback을 찾아 수동 복구할 수 있어야 한다.

## 합성 영상의 제한

`features.media.video_fixture`는 외부 콘텐츠 없이 최대 64×64, 120프레임의 24-bit 무압축 AVI만 생성·검사한다. 파일은 최대 8 MiB이며 RIFF 크기, 한 개의 비디오 스트림, 코덱, 프레임·인덱스·길이와 manifest 해시를 검사한다. 오디오 스트림은 없다.

`FakeVideoTimelineBackend`를 테스트에서 직접 주입한 경우에만 `MediaFeature`의 play/pause/resume/next/end/stop 상태를 가짜 시계로 진행한다. `executed=false`, `simulated=true`다. 기본 `FakeMediaBackend`와 `AlsaWavBackend`는 계속 WAV만 지원한다. 이 준비 도구는 통합 Host의 기본 목록에 영상을 추가하지 않는다. 실제 사용자 MP4·WebM·AVI 코덱 디코딩, 브라우저에서 보이는 프레임, 스피커 출력 성공을 주장하지 않는다.

## 검증

합성 임시 파일만 사용한 새 테스트는 `test_game_assets.py` 22개, `test_offline_doctor.py` 8개, `test_desktop_plan.py` 5개, `test_media_video_fixture.py` 7개다. 게임 테스트는 ZIP 공격 경로·해시·라이선스 gate·사본 보존·경합·추출/게시 실패·rename 직후 취소·복구 실패와 중앙 디렉터리 사전 제한을 확인한다. 범위 초과 파일은 `ZipFile` 생성·`ZipInfo` 할당이 각각 0회임을 확인한다. 영상 테스트는 실제 player/network/credential 호출을 금지한 상태에서 기본 backend 거부와 명시적 fake 타임라인만 확인한다. 기존 `test_feature_media.py` 회귀 14개도 확인했다. 실물 장치와 실제 콘텐츠 실행 검증은 별도 `NOT_RUN`이다.
