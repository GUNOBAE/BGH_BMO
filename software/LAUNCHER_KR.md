# 비공개 키 설정과 수동 Launcher

`/home/bgh/BMO_EMOTION/launch-bmo`는 수동 실행 진입점이다. 자동 부팅·서비스·OS 설정을 설치하지 않는다.
모드를 생략하면 live를 시작하지 않으며, `--simulate`와 `--live`를 명시적으로 구분한다.

## 한 번 입력하고 재사용

실제 API를 호출하지 않고 키만 준비하려면 사용자 터미널에서 한 번 실행한다.
이 자동 개발에서는 실행하지 않았고 실제 키를 요청·읽기·저장하지 않았다.

```bash
cd /home/bgh/BMO_EMOTION
./launch-bmo --setup-credentials
```

화면에 보이지 않는 getpass 입력으로 OpenAI와 Fish 키를 받는다. 비공개 저장 위치는
`${XDG_CONFIG_HOME:-$HOME/.config}/bmo/credentials.json`이며 이 계정 소유 디렉터리 700,
파일 600으로 보호한다. 이 파일은 **로컬 평문**이며 암호화/keyring이라고 표현하지 않는다.
저장 성공은 실제 계정 인증이나 모델 지원 확인을 뜻하지 않는다.

설정 명령 없이 첫 live 실행에서 누락된 키만 입력해도 저장되고 다음 실행에 재사용된다.
우선순위는 현재 환경 → 저장한 키 → 누락분 입력이다. 기존 환경 키를 자동으로 파일에
복사하지 않고 새로 입력한 값만 저장한다. 명시적 setup은 두 키를 모두 입력한 뒤 교체한다.
비TTY에서 누락되면 대기하지 않고 고정 오류로 종료하며, 인증 실패 시 자동 재입력 loop나
파일 덮어쓰기를 하지 않는다. 키는 명령행 인자·Git·로그·HTTP 상태에 넣지 않는다.

저장은 소유권/권한/정규파일/단일 링크/symlink를 확인하고 같은 private 디렉터리의
600 임시 파일에 기록·fsync 후 원자적으로 교체한다. 교체 전 취소/실패는 기존 파일을
보존한다. 교체 후 디렉터리 fsync 실패라면 완전한 새 파일이 이미 존재할 수 있다.
권한이 잘못된 기존 파일을 자동 chmod하거나 본문을 진단 출력하지 않는다.

## 모의 실행 — 키 파일을 읽거나 만들지 않음

```bash
cd /home/bgh/BMO_EMOTION
./launch-bmo --simulate --emotion-mode auto --screen-profile v13.2 --port 0
```

`--help`, `--effective-config`, simulate는 키 resolver를 호출하지 않는다.
일반 launcher의 기존 사용자별 lock을 먼저 확보하므로 기존 앱이 있으면 새 입력/실행을 거부한다.
기존 앱·lock을 강제로 종료하거나 지우지 않는다.

## 화면 버전 분리

- `--screen-profile v13.2`: 기존 승인 원본과 SHA 검증을 그대로 사용하며 기본 복구 경로다.
- `--screen-profile r10`: 명시적으로 선택하는 별도 외부 파일과 별도 SHA 핀이다.
- `--face-file PATH`: 선택한 profile의 **같은 SHA**를 가진 로컬 파일 위치만 바꾼다.
- `/api/screen`: 비밀 없는 profile/hash/승인 상태를 읽는다. 경로나 키를 노출하지 않는다.

현재 R10 로컬 후보는 `Downloads/BMO_Screen_v13_2_7inch_preview_v10.html`, SHA-256
`8c3ef98ba0e0645213c95295a24d7353fc2865b5ad9bc85390360c4ec9b78c82`다.
HTML 내부가 검토본으로 표시되어 있고 지정 ZIP/master order가 미확보라 최종 승인본 일치
확인을 요청했다. 확인 전 기본 v13.2를 바꾸지 않는다. 별도 profile로 후보를 연결·검사한
결과와 사용자 최종 승인은 구분한다. R10 내부 표시층에 기존 v13.2 확대 스크립트를
중복 주입하지 않으며 두 원본을 복사·편집·Git 업로드하지 않는다.

## 사용자 입회 live 실행 — 자동 개발에서는 NOT_RUN

```bash
cd /home/bgh/BMO_EMOTION
./launch-bmo --live --emotion-mode auto --screen-profile v13.2
```

설정한 키는 메모리에서 SDK/Fish에 직접 주입하며 player subprocess 환경에 새로 넣지 않는다.
음성 정책은 Luna low / Aaron Soft / beemo / gain 0.25 / 출력 320을 유지한다.
`/quit`로 정상 종료한다. 감정 연속성과 `/cancel`, `/repeat`, `/reset`, `/emotion manual|auto|NAME`은
[EMOTION_KR.md](EMOTION_KR.md)를 따른다. 실제 키 없이 단순 실행 검증은 위 simulate로 한다.

R10 최종본을 확인한 사용자는 같은 명령의 `--screen-profile r10`을 명시할 수 있다.
후보 검토에서 surprised 종료의 작은 O는 그 원본의 idle 표현이며, 외부 silence cue의 closed와 구분한다.
원본 표정을 맞추려고 강제로 바꾸지 않는다.

`tools/launcher/BMO.desktop.example`은 검토 가능한 수동 desktop entry 예제다.
이 작업은 Desktop/autostart/systemd에 설치하지 않았다. 개발/CI에서 Python 경로를 바꿀 때만
`BMO_PYTHON`을 지정하며 키를 넣는 변수가 아니다.

복구: `/quit` 후 `--screen-profile v13.2`와 `--emotion-mode manual`을 선택하거나
보존된 `/home/bgh/BMO_INTEGRATION`을 사용한다. 새 private 설정은 Git rollback에 포함되지 않으며
기존 백업·소스·서비스를 되돌리거나 삭제하지 않는다.

## 오프라인 재현

```bash
cd /home/bgh/BMO_EMOTION/software
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B -m unittest discover -s tests -q
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B checks/check_screen_profiles_browser.py --chromium /usr/bin/chromium
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B checks/check_manual_launcher_browser.py --screen-profile v13.2
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B checks/check_manual_launcher_browser.py --screen-profile r10
```

검사는 합성 credential/temp 파일만 사용한다. 프로덕션 비공개 키 파일과 실제 API를 사용하지 않는다.
