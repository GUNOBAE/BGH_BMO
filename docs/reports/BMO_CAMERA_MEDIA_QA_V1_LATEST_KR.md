# 별도 카메라 영상·미디어 QA v1

작업서 [Issue #22](https://github.com/GUNOBAE/BGH_BMO/issues/22), 마스터 [#23](https://github.com/GUNOBAE/BGH_BMO/issues/23).
[Draft PR #27](https://github.com/GUNOBAE/BGH_BMO/pull/27), branch `feature/bmo-camera-video-and-qa-v1`.
base [PR #26](https://github.com/GUNOBAE/BGH_BMO/pull/26) `b2538d4f47e46c4fb760272a7de78004dcd28717`.
검증 소스 SHA **`bc1485c127ffe3048de1852eaca9f5f3db1022b3`**.

## 코드와 동작

`bmo_camera_video_v1` 계약, VideoRecorder/VideoStore를 기존 CameraBroker 하나와 연결했다.
명시 저장 위치 + 별도 동의 + 영상 시작 버튼에서만 합성 녹화를 시작한다.
GP8 마이크 녹음과 분리했고 영상에는 오디오 스트림이 없다.
실물 `camera_video`는 live에서 항상 denied다. 원본 R10에는 수정하지 않고 외부 메뉴/표시만 추가했다.

모의 녹화는 DemoBackend의 합성 PNG 프레임을 검증하고 그 수에 대응하는 **16×16
무압축 DIB AVI**를 생성한다. 실제 카메라 픽셀을 인코딩한 결과나 Pi codec 검증이 아니다.
최대 12초/8MiB/게시 후16MiB 여유 공간,2fps 기본값이며 실제 경과·합성 timeline을 구분한다.
같은 broker의 preview/tracker/photo와 중재하며 video 중 새 PTT/대화/repeat를 거부한다.
모드 변경/STOP/앱 종료는 미완성 녹화를 취소한다. 사용자가 저장한 완료 AVI는 종료 후 보존한다.

저장 위치는 명시한 private leaf0700, 파일0600이다. 경로·symlink·기존 파일 교체·디스크 부족을
거부한다. 임시 파일 → 검증 → no-overwrite link/fsync → metadata journal 순서이며
전원 장애의 running 기록은 UNKNOWN으로 남기고 자동 재촬영하지 않는다.
게시 후 임시 링크 정리 실패도 UNKNOWN으로 남겨 이미 존재하는 파일을 미실행으로 보고하지 않는다.
다운로드는 read-only이며 AVI 구조·현재 크기·완료 SHA를 다시 대조하고 불일치 파일을 보존한 채 거부한다.
native close 미확인은 소유권과 경고를 유지하며 새 broker 소유자를 허용하지 않는다.

기존 게임의 ZIP 검토/import/rollback과 두 fixture runner를 재사용했다. menu START/Enter 선택,
게임 B 유지, ESC 게임 종료와 covering 메뉴 ESC 분리를 구현했다. 실제 알려진 ZIP/Tetris 후보는
세 개 모두 stat 결과 ASSET_PENDING이다. 자산을 다운로드하거나 설치 완료라고 주장하지 않았다.

음악은 검토한 SHA 고정 PCM WAV를 private import/보존형 retirement하는 도구를 추가했다.
명시 `--music-directory`는 선택한 목록을 read-only로 인수한다. simulate는 기존 무음 fake
backend에서 pause/resume/next/stop/EOF와 얼굴 복귀를 수행하고, live music은 denied다.
기본 사용자 목록은 빈 상태다. 없는 cue를 RMS 립싱크로 꾸미지 않는다.
기존 OwnedProcessRunner를 WNOWAIT 관찰 → 같은 session/group 종료 확인 → 마지막 direct-child reap으로
보완했다. 테스트의 harmless process group만 신호 대상으로 사용했다.

사진/시선/미리보기와 준비 상태도 외부 제품 메뉴로 연결했다. 기본 사진은 기존 임시 합성 fixture다.
사용자 영구 사진 경로·실물 사진 backend는 아직 미완성이다. 실제 사용자 사진은 생성/삭제하지 않았다.
전원 parser는 `0x50000` 과거 undervoltage/throttle과 현재 flag를 분리한다.
진단 UI는 기본 NOT_RUN이며 실제 vcgencmd/USB/serial 명령은 실행하지 않았다.

## 실행한 검증

| 검사 | 결과 |
|---|---|
| 이전 970 + 새 모듈/통합 106 | **1076 PASS**, skip0, 로컬74.999초 |
| 새 영상 module | 46 PASS |
| 영상 Host lifecycle·다운로드 무결성 | 22 PASS |
| WAV library/readiness/process 독립 검사 | 28 PASS |
| music-directory Host 연결 | 8 PASS |
| 게임 Enter context | 2 PASS |
| Desktop public/R10/v13.2 | 각7, 합21 PASS |
| PTT public/R10/v13.2 | 각9, 합27 PASS |
| Camera/media public/R10/v13.2 | 각7, 합21 PASS |
| 총 headless | **69 checks PASS**, 마지막 음악 dialog·다운로드 보완 후 재검사 포함 |
| compileall / pip check / diff check | PASS |

코드 [push CI](https://github.com/GUNOBAE/BGH_BMO/actions/runs/40210089759),
[PR CI](https://github.com/GUNOBAE/BGH_BMO/actions/runs/40210122501) **SUCCESS**를 확인했다.
Python3.11/3.13 unit과 public fixture 브라우저가 모두 통과했다.
보고서만 추가한 최종 제출 HEAD의 상태는 PR #27 checks/Issue #23 완료 댓글에서 확인할 수 있다.

각 새 미디어 headless는 정상 인사 캐시1/player1/reap1/fake browser1/reap1,
대화 brain/STT/합성0, 실제 장치/유료 API/브라우저 오디오0을 검증한다.
명시 동의 녹화 → private 실제 합성 AVI 파일 → 다운로드 bytes/SHA → 종료 후 같은 파일 보존까지 확인했다.
실제 libcamera·스피커·게임을 실행한 결과가 아니다.

39개 source 변경 파일과 각 SHA(PR #24 대비 전체 누적), 테스트 요약/브라우저 결과는
[검증 JSON](BMO_NIGHTLY_20261025_VERIFICATION.json)에 있다. raw artwork·키·실제 음성/개인 로그는 없다.
R10와 v13.2 전후 SHA는 각각
`8c3ef98ba0e0645213c95295a24d7353fc2865b5ad9bc85390360c4ec9b78c82`,
`38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`로 유지했다.

## 남은 항목과 복구

Camera Module3Wide 실제 encoding/AF/preview→tracking→photo→video 전환·driver close·hotplug,
실제 게임팩/라이선스·native/RetroArch·패드와 노래/음량/codec·PTT echo,
카메라 영구 사용자 사진 저장소, 실제 전원/발열/장기 CPU/RAM/정전 검수는 **NOT_RUN/ASSET_PENDING**이다.
이번 모의 PASS만으로 실물 capability를 열지 않는다. R10/v13.2/Piper/펌웨어/팬/SSH/VNC/OS/다른 프로젝트를 보존했다.

사용 및 안전 복구는 [CAMERA_MEDIA_KR.md](../../software/CAMERA_MEDIA_KR.md),
[MEDIA_QA_KR.md](../../software/MEDIA_QA_KR.md)를 따른다.
새 앱을 정상 종료하고 기존 `/home/bgh/BMO_FEATURES` PR #18 실행 경로로 돌아가면 된다.
선택한 영상·음악 파일과 백업을 삭제할 필요가 없다. 서비스/desktop 자동 설치나 main 병합은 하지 않았다.
