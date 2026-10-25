# BMO 부팅 UX v2 야간 후속 검증

- 작업서: [#19](https://github.com/GUNOBAE/BGH_BMO/issues/19), [#20](https://github.com/GUNOBAE/BGH_BMO/issues/20), [#23](https://github.com/GUNOBAE/BGH_BMO/issues/23).
- 인수: [PR #24](https://github.com/GUNOBAE/BGH_BMO/pull/24), `4a80bc855175cb1c8259b040b70b3e87ed4e67d8`.
  기존 890 tests 및 [기존 PR CI](https://github.com/GUNOBAE/BGH_BMO/actions/runs/40205632873) PASS를 재확인했다.
- 제출: [Draft PR #25](https://github.com/GUNOBAE/BGH_BMO/pull/25), `feature/bmo-nightly-boot-completion`.
- 검증한 코드 SHA: `9b7294d160e54cee809ab7dcfaccaba1479827b0`.

## 실제 구현

PR #24의 진행률/막대/문구/분리 캐시는 다시 만들지 않았다.
`--status`의 오래된 인사 문구를 같은 welcome-v2 상수로 통일하고,
`progress_status/progress_error`를 추가했다. 제품은 준비된 얼굴의 두 번의 render 기회 및
현재 run `face_presented` 확인 뒤에만 인사를 시작한다. STOP 뒤 수동 retry도 이 경계를
우회하지 못한다. reload는 새 인사를 시작하지 않는다.

`--diagnose-startup`은 읽기 전용 세션 속성, 안전한 Chromium 실행 파일 여부,
R10 SHA 일치 및 기존 lock 점유만 검사한다. `main`은 allowlist의 stage/code만 출력하고
예외 원문·경로·환경·키를 출력하지 않는다. `runtime_directory_permissions`로
정확한 사용자 소유/0700 요구를 구분하며 자동 chmod·보안 완화는 없다.

현재 세션 메타데이터만 읽은 결과 runtime 디렉터리는 현재 UID 소유의 0700,
Wayland display 존재였다. 로그인 후 권한 재발 원인, 실제 창과 LCD 품질은 검증하지 않았다.

## 실행 증거

| 검사 | 결과 |
|---|---|
| 기존 890 + 신규 11 Python unittest | **901 PASS**, skip 0, 로컬 73.125초 |
| public synthetic Desktop headless | **7 PASS** |
| 원본 R10 Desktop headless | **7 PASS** |
| 원본 v13.2 Desktop headless | **7 PASS** |
| pip check / git diff --check | PASS |
| [코드 push CI](https://github.com/GUNOBAE/BGH_BMO/actions/runs/40207216548) | SUCCESS |
| [코드 PR CI](https://github.com/GUNOBAE/BGH_BMO/actions/runs/40207260416) | 생성 확인, 최종 상태는 GitHub/상위 인계서 참조 |

각 headless 실행에서 host 1, fake browser 생성/회수 1/1,
인사 호출/시작/끝 1/1/1, dialogue brain 1, synthetic synthesis 1,
소유된 harmless player 생성/회수 3/3, 최대 동시 player 1, 금지 IO 0을 확인했다.
합성 WAV/가짜 자식 프로세스 및 loopback 서버를 실제로 연결한 검증이다.
브라우저의 오디오 API 호출은 0이다. 이는 실물 출력·온라인 Fish 성공 증거가 아니다.

R10 원본 `196987 bytes` SHA-256:
`8c3ef98ba0e0645213c95295a24d7353fc2865b5ad9bc85390360c4ec9b78c82`.
v13.2 `159892 bytes` SHA-256:
`38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`.
headless 검사 전후 동일했다. 원본 작화는 GitHub에 새로 올리지 않았다.

## NOT_RUN / 복구

새 한국어 Fish 인사 prepare/청취/승인, 실물 mic/speaker/HID/카메라/모터/IMU/펌웨어,
desktop 설치, labwc/VNC/LCD kiosk 검수는 **NOT_RUN**이다. 온라인 API 호출은 0이다.
사용자 보고의 기존 live 대화 성공은 새 인사 실기 합격으로 계산하지 않았다.
수동 절차는 [BOOT_UX_KR.md](../../software/BOOT_UX_KR.md)를 따른다.

기존 `/home/bgh/BMO_FEATURES`는 PR #18 HEAD `b4be622`에서 clean 상태 그대로다.
새 작업은 `/home/bgh/BMO_NIGHTLY`에서 수행했다. 앱 정상 종료 후 기존 작업 경로에서
기존 launcher를 실행하면 복구된다. 이전 인사 캐시, 팬/SSH/VNC/OS/서비스/기존 Git 작업을
변경하지 않았고 main 병합·reset/clean/force push를 하지 않았다.
