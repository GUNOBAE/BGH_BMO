# GitHub 동기화 검토안

2026-10-15 기준. 이 문서의 명령은 제안이며 아직 실행하지 않았다. commit·push·stage·이력 재작성·파일 삭제를 수행하지 않는다.

## 저장소와 인증

- 원격 `origin`: [GUNOBAE/BGH_BMO](https://github.com/GUNOBAE/BGH_BMO), HTTPS. 원격 주소에 자격증명 없음.
- 권장 브랜치: **`feature/bmo-robust-conversation`**. 로컬 HEAD와 읽기 전용 원격 조회의 해당 브랜치 HEAD는 모두 `3ee112fc78a87dce46a062991a80b981d8156c2a`다. `main`은 대상이 아니다.
- 로컬 브랜치의 upstream은 미설정이다. 원격 브랜치는 이미 존재하므로 새 원격이나 새 브랜치가 필요하지 않다.
- GitHub 읽기 연결은 확인했으나 **쓰기 인증·권한은 미검증**이다. `gh`, Git Credential Manager, credential helper, GitHub 토큰 환경변수, HTTP 인증 헤더는 확인되지 않았다. SSH agent 소켓 존재는 현재 HTTPS 원격의 인증 근거가 아니다.
- Git `user.name`·`user.email`은 미설정이다. 향후 공개 이름과 GitHub noreply 주소를 이 저장소에만 설정한다. 기존 Git 이력에 일반 이메일 1종이 남아 있다. 값은 출력하지 않았으며 이력은 수정하지 않았다.

HTTPS push에는 자격증명 관리자 또는 Git의 숨김 password 프롬프트를 사용한다. 계정 비밀번호 대신 해당 저장소에 필요한 권한의 PAT를 사용할 수 있다. 토큰을 명령어·원격 URL·프로젝트 파일·채팅에 넣지 않는다. 현재 새 도구 설치나 로그인은 하지 않았다. [GitHub 공식 인증 안내](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-authentication-to-github), [commit 이메일 안내](https://docs.github.com/en/account-and-profile/how-tos/email-preferences/setting-your-commit-email-address).

## 검사 결과와 업로드 범위

추적 51파일, HEAD의 26커밋·72개 고유 blob과 파일 유형을 검사했다. 실제 API 키·토큰, 실제 `.env`, 백업, 음성 모델, 가상환경, 녹음·개인 대화 파일은 발견하지 못했다. 테스트의 합성 credential와 `.env.example` placeholder는 실제 자격증명과 구분했다. gitleaks/trufflehog는 설치되어 있지 않아 제한된 패턴·파일 유형·아카이브/문서 메타데이터 검사다. 탐지되지 않았다는 결과가 비밀정보 부재의 보증은 아니다.

기존 추적 문서의 로컬 경로와 Git 이력의 일반 이메일은 이미 원격과 같은 이력에 포함되어 있다. `.gitignore` 추가로 기존 이력이 제거되지는 않는다. 이번에는 이력을 다시 쓰거나 파일을 삭제하지 않는다.

| 분류 | 파일 |
|---|---|
| 이번 코드 동기화 후보 | `.gitignore`, 이 문서, `software/FACE_DISPLAY.md`, 신규 Python 6파일, upstream 코드 고지, stage2/3 테스트 결과 TXT 6파일, 자산 없는 공개 후보 회귀 TXT, stage3 보존 검증 JSON |
| 로컬 운영 문서로 보존·공개 전 익명화 | `AGENTS.md`, `PROJECT_STATUS.md`, `NEXT_TASKS.md`, `docs/face-renderer-stage2.md`, `docs/face-display-stage3.md`, stage2 보존 검증 JSON |
| 현장 환경정보로 공개 보류 | `docs/reports/20261015-stage3-environment.json` |
| 경로 익명화·권리 확인 전 보류 | 얼굴 PNG 13개, `software/assets/faces/PROVENANCE.json` |

보류 파일은 삭제하지 않고 `.gitignore`로 우발적 추가를 막았다. upstream MIT 고지는 공개 후보로 유지했다. 세 관리 문서의 현재 원본은 로컬 운영 지침으로 유지하며, 공개용 상태·실행 설명은 이 문서와 `software/FACE_DISPLAY.md`로 분리했다.

PNG 없이 동기화하면 원래 얼굴 화면 재현과 선택적 자산 검사는 불가능하다. 추적된 software 파일과 공개 후보 Python만 별도 검토 복사본에 복사해, PNG/provenance·모델·자격증명·venv 없는 상태에서도 기존 인터프리터로 기본 모의 회귀 **130/130 통과**를 확인했다. 실패·오류·skip은 없다. 원본을 이동·삭제하지 않았다. PNG의 라이선스 범위를 확인한 뒤 출처를 익명화해 같은 브랜치에 추가하는 후속 단계를 둔다. 실제 화면 앱은 아직 실행하지 않았고 실제 화면 검증을 완료했다고 표기하지 않는다.

## 승인 후에만 실행할 절차

저장소 루트에서 공개할 Git 이름·GitHub noreply 주소와 HTTPS 쓰기 인증을 먼저 준비한다. 아래 placeholder는 실제 공개 설정으로 바꾸되 토큰을 명령에 넣지 않는다.

```bash
git switch feature/bmo-robust-conversation
git config --local user.name "YOUR_PUBLIC_NAME"
git config --local user.email "YOUR_GITHUB_NOREPLY_EMAIL"
git ls-remote --heads origin refs/heads/feature/bmo-robust-conversation
```

원격 HEAD가 위 기준에서 변경되었으면 먼저 동기화 방법을 다시 검토하고 중단한다. 원격 조회는 fetch가 아니며 로컬 원격 ref를 갱신하지 않는다. 강제 push는 사용하지 않는다.

원격 기준·이메일 공개 범위·아래 파일 목록을 승인한 뒤에만 명시적 목록으로 stage한다. `git add .`, `git add -A`, `git add -f`는 사용하지 않는다.

```bash
git add -- \
  .gitignore \
  docs/github-sync-plan.md \
  software/FACE_DISPLAY.md \
  software/bmo_ai/face.py \
  software/bmo_ai/display_app.py \
  software/tests/test_face.py \
  software/tests/test_display_app.py \
  software/checks/check_face_offscreen.py \
  software/checks/check_display_offscreen.py \
  software/assets/faces/UPSTREAM_LICENSE \
  docs/reports/20261015-stage2-baseline-tests.txt \
  docs/reports/20261015-stage2-final-tests.txt \
  docs/reports/20261015-stage2-offscreen-tests.txt \
  docs/reports/20261015-stage3-final-tests.txt \
  docs/reports/20261015-stage3-face-offscreen-tests.txt \
  docs/reports/20261015-stage3-display-offscreen-tests.txt \
  docs/reports/20261015-github-code-only-tests.txt \
  docs/reports/20261015-stage3-verification.json
git diff --cached --check
git diff --cached --name-only
git diff --cached --stat
```

선정한 diff를 로컬에서 검토하고, 승인된 공개 후보 외의 파일이 없는지와 기본 회귀 130개 통과를 확인한 뒤에만 실행한다:

```bash
git commit -m "Add manual face display app and GitHub sync preparation"
git push --set-upstream origin feature/bmo-robust-conversation
```

위 commit/push는 이번에 실행하지 않았다. push 후 같은 브랜치의 PR로 검토하고, `main` 병합은 별도로 판단한다.
