# Codex 작업서 — BMO 통합 전 Pi 현황 점검

기준일: **2026-10-22 KST**

## 이번 작업의 결론

**기존 Pi 작업 폴더와 장치를 읽기 전용으로 점검하고, 보고서 한 개를 지정 GitHub 브랜치에 올린다. 사용자가 보고서 본문을 다른 채팅에 복사할 필요는 없다.**

현재 지시는 통합 전 인수인계다. `CODEX_INTEGRATION_STAGE1_KR.md`는 이후 구현 작업서이며, 이번에는 실행하지 않는다. 보고서 업로드 후 이번 작업을 마친다.

## 1. 허용 범위와 작업 위치

**시작 폴더 주의:** `/tmp/bmo-preflight.*`는 보고서용 임시 폴더이며 Git 저장소가 아니다. 그곳의 `git status` 실패나 `rg --files` 결과 없음은 프로젝트 이상을 뜻하지 않는다. 임시 폴더를 Git 저장소로 만들거나 작업서가 로컬에 있다고 가정하지 않는다. 작업서는 GitHub에서 읽고, Git 조회는 존재를 확인한 실제 프로젝트에 `git -C <경로>`를 사용한다.

```bash
# 원격 작업서 읽기: 현재 폴더에 파일이 없어도 된다.
gh api 'repos/GUNOBAE/BGH_BMO/contents/docs/development/CODEX_PI_PREFLIGHT_KR.md?ref=docs/bmo-integration-baseline-20261022' --jq .content | base64 --decode

# 경로가 실제로 존재할 때의 저장소 조회 예시
GIT_OPTIONAL_LOCKS=0 git -C /home/bgh/BGH_BMO status --short --branch
```

- 기존 `/home/bgh/BGH_BMO`, `/home/bgh/BGH_BMO_ACTIONS`, `/home/bgh/BMO_LUNA_AB`, `/home/bgh/bmo-voice-test`와 사용자 원본 파일은 읽기만 한다. 실제 존재 여부부터 확인한다.
- Codex는 별도 `/tmp/bmo-preflight.*` 폴더에서 시작한다. 보고서와 업로드 요청 JSON 등 이번 작업의 임시 파일만 그 폴더에 작성할 수 있다.
- **GitHub 쓰기 예외는 아래의 보고서 파일 한 개를 문서 커밋으로 업로드하는 것뿐이다.** 기존 체크아웃의 소스·설정·인덱스·브랜치를 수정하지 않는다. 원본 `.env`, Piper 모델, 얼굴/게임 원본은 그대로 보존한다.
- 기존 저장소에서 `checkout`, `switch`, `fetch`, `pull`, `merge`, `rebase`, `reset`, `stash`, `add`, `commit`, `push`, `clean` 또는 worktree 생성/삭제를 하지 않는다. 읽기 전용 Git 조회에는 `GIT_OPTIONAL_LOCKS=0`을 사용한다. 원격 파일은 GitHub API로 읽으면 된다.
- 앱·테스트·AI 모델 실행·OpenAI/Fish 등 추론·음성 API 호출·녹음·오디오 재생·카메라·시리얼 장치 접근·모터·펌웨어 실행/재시작은 하지 않는다. 서비스를 시작·종료하거나 설정을 바꾸지 않는다. 네트워크 작업은 GitHub 조회와 §4의 지정 보고서 업로드만 허용한다.
- 키·토큰·인증 설정·원문 `.env`·전체 프로세스 인자를 출력하거나 업로드하지 않는다. 음성 설정은 비밀이 아닌 항목만 선별하고, 값이 확인되지 않으면 추측하지 말고 미확인으로 적는다. remote URL에 인증정보가 있으면 제거한다.

`workspace-write`는 임시 보고서를 쓰기 위한 실행 권한이다. 위의 읽기 전용 점검 범위를 확대하지 않는다. 적용되는 `AGENTS.md`를 먼저 읽되, 현재 사용자 지시가 이번 작업의 범위다.

## 2. 먼저 읽을 GitHub 기준 문서

저장소는 **`GUNOBAE/BGH_BMO` 한 개**다. `BGH_BMO_ACTIONS`는 별도 GitHub 저장소 이름이 아니라 알려진 Pi 작업 폴더다.

1. `docs/bmo-voice-policy-v1-20261022` 브랜치의 [`docs/voice-policy-v1/README_KR.md`](https://github.com/GUNOBAE/BGH_BMO/blob/docs/bmo-voice-policy-v1-20261022/docs/voice-policy-v1/README_KR.md).
2. 같은 브랜치의 [`docs/voice-policy-v1/INTEGRATION_HANDOFF_KR.md`](https://github.com/GUNOBAE/BGH_BMO/blob/docs/bmo-voice-policy-v1-20261022/docs/voice-policy-v1/INTEGRATION_HANDOFF_KR.md).
3. 이 문서 및 [통합 기준](BMO_INTEGRATION_2026-10-22_KR.md).
4. `feature/bmo-actions-mock-v0`의 실제 계약 코드. `feature/bmo-action-lab`로 대체하지 않는다.

읽은 원격 브랜치의 HEAD를 기록하고 그 SHA 기준으로 파일을 대조한다. GitHub에 있는 코드와 Pi 디스크 상태는 별도로 보고한다.

## 3. 점검과 보고 내용

### 실제 Pi 상태

각 작업 폴더마다 다음을 표로 작성한다.

| 확인 항목 | 기록할 내용 |
|---|---|
| 경로 | Codex 임시 작업 경로와 실제 프로젝트 경로를 구분, 존재 여부 및 `git rev-parse --show-toplevel` |
| Git | 비밀정보를 제거한 remote, 현재 branch 또는 detached HEAD, 전체 commit SHA |
| 변경 상태 | `git status --short --branch` 결과, 미커밋/미추적 파일의 경로. 비밀 파일 본문은 제외 |
| 작업 지침 | 상위 폴더 및 관련 하위 경로에 적용되는 `AGENTS.md` 위치와 이번 점검에 관계된 지시 |
| 실행 상태 | 실행 중인 BMO 관련 프로세스의 존재와 식별 가능한 이름, 화면 세션 및 파일에서 확인 가능한 오디오 설정. 프로세스·장치를 조작하지 않음 |

없는 경로는 없다고 적고, 확인 범위 밖을 임의로 광범위하게 수집하지 않는다. Git 조회 권한 오류가 나면 전역 Git 설정을 변경하지 말고 오류 요약을 남긴다.

### 보존 기준과 인터페이스

- **음성:** Luna low + Fish Aaron Soft / Beemo / PCM gain `0.25`, 원본 Piper 백업 보존. 코드 기본값·실제 실행에 쓰이는 설정·사용자 선택을 구분한다. GPT-4.1 mini 비교/복귀 경로와 Piper opt-in 상태를 기록한다. 소리나 모델을 실행하여 확인하지 않는다.
- **얼굴:** 사용자 승인 파일 `BMO_Screen_v13_2.html`만 최종 후보로 인정한다. 확인 가능한 로컬 경로와 SHA-256을 기록한다. 승인 원본 해시는 `38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`다. 파일이 없거나 해시가 다르면 미확인으로 남기고 이전 시안으로 대신하지 않는다. 원본 파일을 공개 GitHub에 업로드하지 않는다.
- **Action:** `action`, `arguments`, `request_id` 요청과 결과 형식, `raise_left_arm`, `raise_right_arm`, `take_photo`, `play_song`, `launch_game`의 5개 Mock 계약을 확인한다. `executed=False`인 Mock 결과를 실제 실행 완료로 취급하지 않는다.
- **연결 경계:** 기존 worker/turn ID, FacePort와 `window.BMOFace`, 실제 재생 시작·종료·중간 취소, 상태/감정/입 동작, 음성 정책과 기존 프롬프트, Action 결과 표시, RP2040 통신, 게임 종료 후 얼굴 복귀를 소스에서 대조한다. 현재 연결됨/미구현/계약 충돌/현장 미확인을 구분한다.

각 중요한 판단에는 경로와 해당 commit 또는 필요한 줄 위치를 붙인다. 이전 성공 기록과 이번 실제 확인을 섞지 않는다. **이번 작업에서 앱·테스트·장치 실행은 하지 않았음**을 명시한다.

보고서 순서는 **결론 → 작업 경로·Git 상태 → 보존 설정·승인 파일 → 인터페이스와 충돌 → 첫 통합에 필요한 다음 작업**이다. 해결 방법은 제안까지만 작성하고 구현하지 않는다. 앞부분은 짧게, 근거는 뒤에 둔다.

## 4. GitHub 보고서 업로드

| 항목 | 지정 값 |
|---|---|
| 저장소 | `GUNOBAE/BGH_BMO` |
| 보고서 브랜치 | `reports/bmo-pi-preflight-20261022` — ChatGPT가 미리 준비 |
| 변경할 유일한 원격 파일 | `docs/reports/PI_PREFLIGHT_LATEST_KR.md` |
| 로컬 보고서 | 이번 `/tmp/bmo-preflight.*` 폴더 안에 작성 |
| 허용되는 원격 변경 | 해당 브랜치의 위 Markdown 파일을 작성/갱신하는 문서 커밋 |

기존에 인증된 `gh`로 GitHub Contents API를 사용한다. Pi의 기존 체크아웃에서 커밋하거나 브랜치를 전환할 필요가 없다.

1. 대상 브랜치가 존재하는지 먼저 읽는다. 없으면 임의의 다른 브랜치에 올리지 않는다.
2. 보고서에 KST 관찰 시각, 실제 관찰한 경로·SHA와 미확인 항목을 넣는다. 비밀정보와 원본 음성/이미지/모델 파일이 포함되지 않았는지 확인한다.
3. 대상 브랜치의 보고서 파일을 조회한다. 처음 생성하면 `sha` 없이, 이미 있으면 내용과 최신 blob `sha`를 읽고 기존 기록의 필요한 맥락을 보존하여 갱신한다.
4. `message`, Base64 `content`, 정확한 `branch`, 갱신 시 `sha`를 담은 JSON을 임시 파일로 만든다. `gh api --method PUT repos/GUNOBAE/BGH_BMO/contents/docs/reports/PI_PREFLIGHT_LATEST_KR.md --input <임시 JSON 경로>`로 올린다. 토큰을 명령이나 파일에 넣지 않는다.
5. 충돌하면 최신 보고서를 다시 읽는다. 다른 실행의 결과를 강제로 덮어쓰지 않는다. 업로드 뒤 지정 브랜치에서 파일을 다시 읽어 본문 일치와 반환된 commit URL을 확인한다.

보고서 브랜치를 main에 병합하거나 별도 코드 PR/이슈/댓글을 만들지 않는다. 인증 또는 네트워크 문제로 업로드하지 못하면 성공으로 표시하지 말고 원인 요약과 임시 보고서 경로만 알린다. 인증 설정을 바꾸거나 새 도구를 설치하는 작업으로 범위를 넓히지 않는다.

## 5. 사용자에게 알릴 완료 메시지

정상 완료 시 **보고서 GitHub 링크, 업로드 commit, 점검 결론 2~3줄**만 알린다. 보고서 전체를 이 채팅에 복사하라고 요구하지 않는다.

사용자가 ChatGPT에 “업로드됐어”라고 알리면 ChatGPT가 지정 GitHub 보고서를 직접 읽고 다음 통합 작업을 정리한다. 이 방식은 보고서 전달 경로이며 자동 원격 실행이나 상시 감시를 설정하는 작업은 아니다.
