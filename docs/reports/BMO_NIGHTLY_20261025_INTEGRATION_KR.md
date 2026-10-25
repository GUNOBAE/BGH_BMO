# BMO 야간 개발 인계 — Issue #23

2026-10-25 야간 큐, 2026-10-26 KST 개발 결과다.
마스터 [#23](https://github.com/GUNOBAE/BGH_BMO/issues/23) 및 연결된 #19/#20/#21/#22의
본문·댓글, PR #24의 코드·CI를 확인한 뒤 구현했다. 기존 PR #24의 로딩 바,
`돌아왔구나! 비모가 기다렸어!` 문구, welcome-v2 캐시는 그대로 인수했다.

**코드·합성 검증·커밋·push·계단형 Draft PR 제출까지 진행했다.
최종 Python1076 PASS/skip0, 로컬 headless69 checks PASS. 실제 장치·유료 API는 실행하지 않았다.**
최종 GitHub CI 상태: **PASS (코드 PR CI, Python3.11/3.13 + public-browser)**.
자동 main 병합, 서비스/OS/desktop 설치, 실제 사용자 자산 정리는 하지 않았다.

## GitHub 작업 기록과 검증 SHA

| 단계 | PR / branch | 검증 코드 SHA | Python | CI |
|---|---|---|---:|---|
| 인수 baseline | [#24](https://github.com/GUNOBAE/BGH_BMO/pull/24), `feature/bmo-boot-ux-v2` | `4a80bc855175cb1c8259b040b70b3e87ed4e67d8` | 890 | [PASS](https://github.com/GUNOBAE/BGH_BMO/actions/runs/40205632873) |
| #19/#20 부팅 보완 | [#25](https://github.com/GUNOBAE/BGH_BMO/pull/25), `feature/bmo-nightly-boot-completion` | `9b7294d160e54cee809ab7dcfaccaba1479827b0` | 901 | [PASS](https://github.com/GUNOBAE/BGH_BMO/actions/runs/40207260416) |
| #21 PTT/VAD | [#26](https://github.com/GUNOBAE/BGH_BMO/pull/26), `feature/bmo-ptt-vad-v1` | `bf599ea04181270b92a1f2bf64e4b71092ec5195` | 970 | [PASS](https://github.com/GUNOBAE/BGH_BMO/actions/runs/40208595521) |
| #22 영상·미디어·QA | [#27](https://github.com/GUNOBAE/BGH_BMO/pull/27), `feature/bmo-camera-video-and-qa-v1` | `bc1485c127ffe3048de1852eaca9f5f3db1022b3` | **1076** | [PASS (코드 PR CI, Python3.11/3.13 + public-browser)](https://github.com/GUNOBAE/BGH_BMO/actions/runs/40210122501) |

base 연결은 **#24 → #25 → #26 → #27**이다. 기존 #13/#14/#16/#18/#24와 main은 변경하지 않았다.
#25 문서 포함 HEAD `9325963a6a457673b922f890f965131ccb305ee4`,
#26 문서 포함 HEAD `b2538d4f47e46c4fb760272a7de78004dcd28717`이다.
#27은 위 코드 검증 후 이 인계서·기능 보고서·JSON을 추가한 문서 커밋으로 마무리한다.
최종 제출 HEAD/CI는 PR #27의 현재 head와 Issue #23 완료 댓글에 남긴다.
표의 source SHA와 문서 HEAD는 구분한다.

## 실제로 구현한 기능

**기동/환영:** 등록된 stage/code만 내보내는 예외 처리와 읽기 전용
`--diagnose-startup`을 추가했다. runtime0700 정책을 유지하고 자동 chmod를 하지 않는다.
100% 로컬 준비 뒤 외부 shell에서 원본 얼굴이 두 번의 render 기회를 가진 것을 현재
run-id로 확인해야 자동/재시도 인사를 시작한다. 앱당 캐시 인사1회, reload/mode 복귀로 재합성하지 않는다.
`--status`의 오래된 문구도 새 환영 상수로 일치시켰다.

**GP8 Enter/PTT/VAD:** 기존 IntegrationSession의 같은 ConversationWorker 입력 단계에
MicPcmSource/PttRecorder를 연결했다. 두 번째 엔진·STT용 두 번째 녹음은 없다.
16kHz S16_LE mono40ms, .4초 calibration/.24초 speech/.9초 무음/3초 no-speech/12초 최대다.
검증된 발화만 STT1회, 무음은 STT/brain/TTS0회다. RMS는 음량 검출이며 화자 인식이 아니다.
키 repeat/중복 합침, 다음 누름 수동 종료 또는 취소, STOP 우선, stale/늦은 STT 차단,
게임 Enter의 PTT 금지, 작은 외부 상태/고정 오류를 구현했다.
실제 microphone은 `--live --ptt-microphone` 명시 opt-in 뒤 버튼에서만 열 수 있고
기본 idle·simulate·wakeword는 실물 마이크0이다. GP7/펌웨어는 재매핑하지 않았다.

**별도 영상:** `bmo_camera_video_v1`, 같은 CameraBroker, 명시 저장 위치·동의·시작/종료,
단일 소유권·시간/크기/여유 공간 제한,0700/0600·SHA·원자 no-overwrite·중단 journal을 연결했다.
합성 PNG 검증과 생성16×16 무음 AVI 파일을 실제로 저장·읽고 검사했다.
실제 카메라 encoder는 구현/활성화하지 않았고 live `camera_video`는 항상 denied다.
완료 영상은 앱 종료 후 보존하고, 다운로드에서 현재 bytes/SHA를 다시 확인한다.

**게임/음악/사진/진단:** 기존 모듈을 외부 제품 메뉴에 연결했다.
게임 menu START/Enter 선택과 게임 B 유지·ESC 복귀/메뉴 우선순위,
무음 fake 음악 pause/resume/next/stop/EOF 뒤 얼굴 복귀, 합성 preview/위치 추적/사진을 검증했다.
검토 WAV의 SHA 고정 private import·보존형 retirement와 명시 `--music-directory` 인수를 추가했다.
기본 실제 게임팩/노래 목록은 여전히 ASSET_PENDING이고 live 음악은 denied다.
사용자 영구 사진 저장소는 아직 미완성이며 기본 사진은 기존 임시 합성 fixture다.
전원 current/historical parser와 버튼 문서 inventory를 추가했지만 진단 UI의 실제 계측은 NOT_RUN이다.

**자원·오류 복구:** 마이크 selector/child/WAV cleanup 실패를 보존하고, 미확정 상태에서
정확한 conversation AudioLease를 유지해 새 출력/질문/repeat를 막는다.
queued PTT의 취소는 이전 idle/generation 대신 정확한 request receipt로 추적한다.
카메라 close 실패·게시 뒤 오류는 UNKNOWN, 재시작의 running 기록도 UNKNOWN이며 자동 재수행하지 않는다.
OwnedProcessRunner는 WNOWAIT로 PID를 유지하고 같은 group 종료 확인 후 마지막으로 reap한다.
전역 kill·subreaper·다른 프로그램 종료를 사용하지 않았다.

## 실제 테스트 결과

| 검증 | PASS 수 | 구분 |
|---|---:|---|
| PR #24 기존 Python | 890 | 삭제/skip/조건 완화 없이 보존 |
| 신규 부팅11 + PCM47 + PTT 통합22 | 80 | 임시 WAV·fake PCM/SDK/player |
| 신규 영상46 + 영상 Host22 + media/readiness/process28 + music Host8 + game2 | 106 | 임시 합성 파일·harmless 자식·순수 parser |
| 최종 전체 Python | **1076**, skip0 | Pi 로컬74.999초 |
| Desktop public/R10/v13.2 | **21** | 화면당7 |
| PTT public/R10/v13.2 | **27** | 화면당9 |
| camera/media public/R10/v13.2 | **21** | 화면당7 |
| headless 전체 | **69** | 승인 원본 파일은 로컬에서만 읽음 |
| compileall/pip check/diff 및 변경 파일 비밀 패턴 검사 | PASS | 원본 작화·키·개인 로그 업로드0 |

MockTransport/fake SDK가 기존 STT 함수와 같은 단일 brain/voice 경로에 실제 연결됐다.
코드 PR CI 로그에서도 Python3.11 **1076개/67.381초**, Python3.13 **1076개/67.246초**와
public-browser 네 검사기 PASS를 직접 확인했다. 공개 CI는 승인 작화 대신 텍스트 fixture만 쓴다.
fake arecord는 harmless Python 자식의 PCM pipe를 실제로 읽는 검증을 포함한다.
이는 마이크·온라인 API·발음·실물 모터 동작 성공을 뜻하지 않는다.
PTT browser당 PCM3/close3, STT1/brain1/합성1, player3/reap3, 최대 동시 입력/출력 각각1,
금지 IO0. 새 media browser당 합성 인사 player1/reap1, brain/STT/합성0,
명시 동의 영상의 저장·다운로드·앱 종료 뒤 파일 보존을 확인했다.

초기 PTT CI browser에서 queued-blur 경합이 실패했고 승인 R10에서도 재현했다.
정확한 요청 완료 기록 방식으로 수정한 뒤 검사를 유지해 공개/두 승인 화면과 CI가 통과했다.
최종 검토에서 발견한 게임 위 modal ESC, 음악 종료 modal 잔류,
변조된 유효 AVI 다운로드도 수정하고 회귀를 남겼다. 단순 재시도로 실패를 감추지 않았다.

## 원본·기존 환경 보존

| 대상 | 확인값 / 상태 |
|---|---|
| R10 원본196987 bytes | `8c3ef98ba0e0645213c95295a24d7353fc2865b5ad9bc85390360c4ec9b78c82`, 전후 동일 |
| v13.2 원본159892 bytes | `38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`, 전후 동일 |
| Piper model / JSON / binary | 시작 전 SHA와 전후 동일; 실행하지 않음 |
| 기존 BMO_FEATURES | `b4be622ec831721635332f7ce3ad39c6d16c7870`, clean 보존 |
| 기존 BMO_EMOTION | `174dd974fdf119fc3d24a5cda0870ae70c007cf6`, clean 보존 |
| 기존 BMO_INTEGRATION | `902b09739844ab4c78ea6234851b7bfb9cdb7634`, clean 보존 |
| 원격 main | `161a2ebd99b21ddc31e4cbf6b1cbd4452967e264`, 미변경/미병합 |
| 팬/SSH/VNC/Tailscale/OS/firmware/다른 프로젝트/백업 | 제어·수정·삭제0 |

작업은 새 `/home/bgh/BMO_NIGHTLY`와 기능별 독립 worktree에서 수행했다.
원본 checkout을 전환하지 않았고 기존 사용자 미추적 작업 파일을 정리·이동·변경하지 않았다.
키 store·legacy 인사 캐시·개인 로그 내용은 읽지 않았다. 승인 얼굴은 해시와 로컬 headless로만 확인했다.
Pi의 시스템 패키지를 설치하지 않았고 기존 개발 venv를 재사용했다.
GitHub의 임시 CI runner 안에서만 기존 workflow의 browser 개발 의존성을 설치한다.

## 남은 코드·자산·실기 구분

| 항목 | 현재 상태 | 사용자 입회 후 확인 |
|---|---|---|
| 새 한국어 환영 인사 | 코드/분리 캐시 완성, 유료 합성 미수행 | prepare1회 → 같은 WAV 청취 → 정확한 SHA 승인, 실제 발음/음량 |
| GP8 물리 Enter | 문서 mapping만 보존, `physical_verified=false` | 현재 펌웨어/HID 확인, GP7 누르지 않기 |
| mic/VAD | source/segmentation/단일 대화 및 mock 완성 | ALSA16kformat, 팬 소음/작은소리/첫발화/무음/echo, latency·USB hotplug |
| Hey BMO | OFF / 모델 미검수 | 모델·개인정보·오탐 검수와 별도 opt-in 전 활성화 금지 |
| 실물 영상 | synthetic 계약/저장/UI만 완성, native encoder 미완성 | Camera3Wide/libcamera/Picamera2/AF/codec 전환·driver close 설계/검수 |
| 사용자 사진 | 실제 촬영 없음, 기본 mock은 temporary | 영구 사용자 저장소와 실물 연결 검수 |
| 게임 | 실제 ZIP/Tetris 세 알려진 경로 ASSET_PENDING | 합법 자산·license/hash·runtime·gamepad·native/RetroArch exit |
| 음악 | default ASSET_PENDING, 검토/import/silent mock 선택 완성 | 사용자 음원·형식/volume 승인, 실제 출력·PTT echo |
| 팔/IMU | 실제 backend disabled, 기존 상태·펌웨어 보존 | 사용자 입회·기구 제한·센서 fault 조사와 별도 승인 |
| 시스템 | 코드·bounded fake 안정성 검사 완성 | LCD/VNC/labwc focus·장시간 CPU/RAM·발열/전원·정전·재로그인0700 |

**이 표의 실제 장치·유료 API·실기 칸은 모두 NOT_RUN/HUMAN_TEST_PENDING이다.**
사용자가 과거 live 대화 성공을 보고한 사실은 보존하지만 이번 새 기능의 실기 합격으로 계산하지 않았다.
하드웨어/네이티브 driver나 동기 SDK hang을 강제로 회수할 수 있다고 보장하지 않는다.

## 아침의 첫 두 확인 명령 — 읽기 전용

```sh
BMO_PYTHON=/home/bgh/BMO_FEATURES/.venv/bin/python /home/bgh/BMO_NIGHTLY/launch-desktop --diagnose-startup
BMO_PYTHON=/home/bgh/BMO_FEATURES/.venv/bin/python /home/bgh/BMO_NIGHTLY/launch-desktop --simulate --status
```

이 두 명령은 키·마이크·카메라·스피커·일반 Chromium을 열거나 앱을 설치하지 않는다.
실제 마이크/유료 API 명령은 사용자가 깨어서 가이드를 검토하고 입회하는 별도 단계다.
준비되지 않은 인사 캐시는 자동 유료 합성하지 않는다. 권한 오류도 자동 chmod하지 않는다.

## 안전한 복구와 후속 검토 순서

새 앱을 정상 종료한 뒤 보존된 기존 `/home/bgh/BMO_FEATURES` 경로의 launcher를 사용한다.
기존 브랜치를 reset/checkout하거나 새 영상·음악·백업을 삭제할 필요가 없다.
먼저 보존 상태를 확인하려면 `git -C /home/bgh/BMO_FEATURES status --short --branch`를 사용한다.
새 PTT만 끄려면 앱을 종료하고 `--ptt-microphone` 없이 실행한다.
기존 메뉴의 고정 수동 녹음 경로는 별도로 남아 있다.

검토 순서는 #24 인수 → #25 부팅 → #26 PTT → #27 별도 미디어다.
모든 PR은 Draft로 두며 사용자의 검수/승인 없이 main에 병합하지 않는다.
단계별 상세 문서:

- [부팅 보고서](BMO_BOOT_UX_V2_LATEST_KR.md), [부팅 가이드](../../software/BOOT_UX_KR.md)
- [PTT 보고서](BMO_PTT_VAD_V1_LATEST_KR.md), [PTT 가이드](../../software/PTT_VAD_KR.md)
- [영상·미디어 보고서](BMO_CAMERA_MEDIA_QA_V1_LATEST_KR.md), [영상 가이드](../../software/CAMERA_MEDIA_KR.md), [음원/진단 가이드](../../software/MEDIA_QA_KR.md)
- [수락 기준표](../development/BMO_ACCEPTANCE_MATRIX_KR.md)
- [변경 파일39개의 경로·SHA 및 검증 JSON](BMO_NIGHTLY_20261025_VERIFICATION.json): 위 source SHA 기준 누적 변경이며 이 마지막 보고서/JSON 등 문서 커밋은 별도다.
