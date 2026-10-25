# BMO 오프라인 통합 v2 수락 기준표

계약: [Issue #15](https://github.com/GUNOBAE/BGH_BMO/issues/15).
제출: [Draft PR #16](https://github.com/GUNOBAE/BGH_BMO/pull/16), base
`feature/bmo-feature-bundle-v1`. 최종 수치·검증 SHA는
[최신 보고서](../reports/OFFLINE_INTEGRATION_V2_LATEST_KR.md)를 따른다.
Mock PASS, CI PASS, 디자인 승인, 실제 Pi 검수는 서로 다른 칸이다.

| 항목 | 코드·주입 연결 | 오프라인 검증 근거 | 공개 CI 범위 | 디자인/자산 승인 | Pi 실물 |
|---|---|---|---|---|---|
| 통합 런처·단일 brain/session | 구현, 기존 voice/cache/CLI 재사용 | launcher/SDK MockTransport/runtime/캐시 회귀 | Python 3.11·3.13 unittest | 기존 대화 정책 보존 | NOT_RUN |
| live 대화 준비·키 재사용 | 명시 --live, 같은 private store, shared lock 이후 resolve | 가짜 resolver/record/STT/Fish/player 주입; 실제 키 0 | fake credential·파일/권한 negative 검사 | Aaron Soft/beemo/.25/320 보존 | 유료 API/마이크/ALSA NOT_RUN |
| 감정 연속성 v2 | 기존 코드 재사용 | 여섯 감정·취소·repeat·다음 started; 2초 초기화 없음 | session·voice·synthetic shell | 기존 승인 감정 작화 보존 | NOT_RUN |
| 승인 R10 | 새 앱 기본, SHA pin, approved_design | 로컬 원본 실제 iframe 800×480·activity/mouth·mode 복귀·refresh | 원본 업로드 없이 텍스트 BMOFace fixture | **승인됨**, SHA 8c3ef98…b78c82 | **false / NOT_RUN** |
| v13.2 복구 | 원본·기존 런처 기본값 보존 | 원본 SHA·실제 iframe 검사 | 합성 profile/adapter 계약 검사 | 기존 승인 유지 | 이번 작업 NOT_RUN |
| 모드·AudioFocus·입력·시선 | 기존 공통 구조 재사용, started 시 짧은 연출 연결 | 전경/소리 단일 소유·cleanup 실패·stale·반복·heartbeat 분리 | unit + public shell E2E | 새 look/lookEyes/blink 연출은 실물 시각 검수 별도 | NOT_RUN |
| STOP/cancel·receipt | 일반 큐와 별도 2개 예약 슬롯, 원 질문/cleanup 분리 | queue32/record512 포화, Q/S/R terminal, partial_playback 회귀 | unit | 안전 제어는 모델과 독립 | 실제 기계 정지 보장 NOT_RUN |
| pad/RP2040 HID·CDC 입력 | 합성 source+검증 mapping adapter | debounce/STOP/Start+Select1.2/B/순서·stale·GP7 거부 | unit, 기존 InputRouter/Games 재사용 | 물리 map 미승인 | 장치 open/reset 0, NOT_RUN |
| PTT/VAD | bounded fake PCM→주입 transcriber→같은 session | silence/timeout/duplicate/focus/self-audio/취소 재진입 | unit | 임계값은 합성 설정 | 실제 VAD/마이크 NOT_RUN |
| Hey BMO | 명시 disabled 경계 | PCM으로 호출/재청취하지 않음 | unit | 모델 없음 | NOT_RUN |
| 게임 목록·실행·복귀 | 기존 mock GamesFeature/owned runner 재사용 | 두 가짜 게임/B/hold/패드 loss/crash/cleanup | unit + public shell | 실제 ZIP/Tetris **ASSET_PENDING** | native/RetroArch·pad NOT_RUN |
| 게임팩 준비 | 로컬 검토·명시 license/hash/staging/rollback 도구 | ZIP bounded preflight/path/symlink/실행파일/hash/교체 실패 검사 | unit, 임시 합성 ZIP만 | 실제 code/license 미검토 | 실제 설치·게임 실행 0 |
| 카메라 broker·촬영·추적 | 기본 off, SDK 주입점/preview·still 분리 | fake Picamera2/OpenCV open/read/AF/cancel/timeout/close + 합성 PNG/face box | unit, synthetic photo shell | 실제 위치 추적·AF·성능 미검수 | 카메라 활성화 0, NOT_RUN |
| 팔 제스처·통신 | fake scheduler+staging JSON/LF/선택 CRC | boot/handshake/ACK/dedup/timeout/ledger/취소 | unit | 좌우 한계·방향·정지 미승인 | real disabled, position_unverified |
| IMU 건강 | 기존 synthetic health 모듈 재사용 | bias/포화/frozen/재부팅/누락/중복/hysteresis/cooldown | unit | 실제 건강 승인 없음 | 센서 폴링/offset/제어 0 |
| 노래·cue | 기본 빈 catalog, WAV/cue FD 검증 재사용 | pause/resume/next/EOF/실패/face·cue·no-cue | unit + public shell | 사용자 노래 **ASSET_PENDING** | 음원·스피커 재생 NOT_RUN |
| 영상 준비 | 생성기 전용 AVI+FakeVideoTimeline | header·시간 필드 거부/clock/focus/unsupported | unit | 사용자 영상 **ASSET_PENDING** | 실제 codec/영상 출력 NOT_RUN |
| HTTP·private path | 기존 exact Host/Origin/token·8KiB/중복 schema 유지 | CSRF/헤더/경로/private/photo·zip/hash/TOCTOU negative | unit | game iframe sandbox, postMessage 제어 채널 없음 | 외부 바인딩/원격 제어 NOT_RUN |
| 재시작·멱등 | journal accepted/running→unknown, 무재생 | 임시 private SQLite·중복·capacity·실패 회귀 | unit | receipt 자동 삭제 없음 | 전원 상실/MCU 실물 NOT_RUN |
| soak·종료 | 기존 worker/player 소유권 + bounded resources | 가상 0~47시간·48 turn, queue overflow·FD/thread·브라우저 재연결·소유 child 회수 | unit + browser | 합성 clock 수치, 실측 지연 아님 | 장시간 Pi 성능 NOT_RUN |
| doctor·Desktop | metadata-only / dry-run only | 가짜 config·symlink·키 접근 금지·상태 분리; 실제 metadata dry-run | unit | 메뉴 설치·autostart 미승인 | 설치/서비스 변경 0 |

## 실제 입회 검수에 필요한 입력

게임팩/라이선스·사용자 미디어, 물리 버튼과 pad mapping, Picamera2/OpenCV 설치 및
실제 AF/preview/still 검수, 좌우 팔 기구 제한/방향/안전 정지 검수, 센서 건강 trace,
승인 R10의 실제 LCD 시각 검수, 대화 모델/schema/비용·음성 청취 검수가 남아 있다.
이 미확인 항목은 코드/Mock/CI PASS에서 추론하지 않는다. 기본 capability deny를
해제하려면 해당 기능별 별도 설계·입회 승인이 필요하다.

기존 펌웨어·팬·SSH·서비스·다른 프로젝트·백업·PR #13/#14·main은 변경하지 않는다.
코드가 준비됐다는 이유로 실물 장치를 자동 활성화하거나 기존 앱을 종료하지 않는다.

## 야간 큐 #23 후속 (2026-10-26 KST)

위 표는 PR #16 시점의 기준으로 보존한다. 다음은 Issue #19~#22의 추가 구현이다.
정확한 source SHA/최종 테스트 수/CI/PR은
[야간 인계서](../reports/BMO_NIGHTLY_20261025_INTEGRATION_KR.md)를 따른다.

| 항목 | 현재 코드 / 모의 증거 | 원본·자산 상태 | 실제 검수 |
|---|---|---|---|
| 부팅 진단·로딩·환영 | PR #24 인수, stage/code allowlist, 읽기 전용 preflight, 얼굴 표시 확인 뒤 cache greeting1 | legacy/welcome-v2 분리, 한국어 문구 고정 | Fish prepare/청취/승인·LCD/kiosk NOT_RUN |
| GP8 Enter PTT/VAD | 같은 worker 입력 provider, finite16kPCM, RMS calibration/silence, STT1/무음0, cancel/stale/queue/lease 회귀 | GP7 변경0, 현재 HID 펌웨어 미확인 | mic/ALSA/VAD/USB/VNC focus·유료 API NOT_RUN |
| 별도 영상 v1 | 명시 consent+선택 dir, shared broker, 합성16×16 무음 AVI, SHA/private atomic store·recovery | `camera_video` live 항상 denied, 실제 영상 encoder 없음 | Camera3Wide/AF/codec/driver/hotplug NOT_RUN |
| 사진·시선 | 기존 demo camera/photo/tracker 외부 메뉴와 mode 복귀, 합성 PNG/headless | 기본 off, 기본 사진은 이전의 임시 합성 fixture; 실제 사용자 사진 생성 없음 | 실물·영구 사용자 사진 경로 NOT_RUN |
| 게임 | 기존 mock/owned runner/import 재사용, menu START/Enter 선택·B·ESC·복귀 | 실제 ZIP/Tetris3개 알려진 후보 ASSET_PENDING | 실제 게임·emulator·pad/exit NOT_RUN |
| 음악 | 검토 WAV private import/retirement, 명시 music-directory 인수, 무음 fake pause/resume/next/stop | 기본 빈 목록, 실제 사용자 음원 ASSET_PENDING, live music denied | 실제 음량/codec/노래/echo NOT_RUN |
| 프로세스·복구 | direct child WNOWAIT, 같은 group 종료 확인 후 reap, 영상·마이크 cleanup UNKNOWN 보존 | 전역 kill·subreaper·OS/서비스 변경 없음 | native 드라이버 hang/정전/장기 안정성 NOT_RUN |
| 전원·버튼 상태 | 순수 parser/injected runner, `0x50000` 과거/현재 구분, 메뉴 NOT_RUN 표시 | 실제 vcgencmd/USB/serial 실행0, GP7/팔/IMU preserved | 전원·발열·장시간 CPU/RAM 계측 NOT_RUN |

Mock/CI PASS는 위 마지막 열을 자동으로 통과시키지 않는다. 실제 장치나 유료 API를
승인 없이 켜지 않으며, 콘텐츠 소유·라이선스는 도구가 추정하지 않는다.
