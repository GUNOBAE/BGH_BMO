# BMO 오프라인 통합 v2 최종 보고서

2026-10-24 KST. 실행 계약: [Issue #15](https://github.com/GUNOBAE/BGH_BMO/issues/15).
제출: [Draft PR #16](https://github.com/GUNOBAE/BGH_BMO/pull/16), base `feature/bmo-feature-bundle-v1`.

## 결론과 검증 기준

통합 런처·입력/PCM·화면·카메라 어댑터·자산 검토/설치 준비·영상 fixture·진단·안전 제어의 남은 오프라인 연결을 구현했다.
단일 기존 brain/session/voice cache를 재사용한다. 최종 로컬 전체 회귀 **795/795 PASS, skip 0**, 승인 로컬 얼굴을 포함한 headless **80/80 PASS**다.
실물·유료 API·실제 키 입력/파일 읽기·게임팩 설치는 **NOT_RUN/0회**이며 Mock PASS에서 추정하지 않는다.

- 기존 기준 SHA: `c86da89a1d33bb98694345e842eaebd941f00308` (PR #14, 657/657).
- 최종 검증 소스 SHA: `88076164cc6e2440f2ce0d3ab7e4aebf73481287`.
- 코드 CI: [Python 3.11·3.13 및 공개 synthetic browser 검사](https://github.com/GUNOBAE/BGH_BMO/actions/runs/40053752056) — **PASS**.
- 최종 문서 제출 HEAD·원격 동기화·그 HEAD의 CI 링크는 PR #16 본문의 제출 검증 항목에도 기록한다. 이 파일은 자기 커밋의 SHA를 본문에 미리 계산하지 않는다.
- 최초 `/home/bgh/BMO_FEATURES`는 clean/c86da89였다. 동일 clone에서 새 `feature/bmo-offline-integration-v2`를 만들었다. 기존 PR #13/#14 및 main을 병합·변경하지 않았다.

[사용·복구 가이드](../../software/OFFLINE_INTEGRATION_KR.md)와 [기능별 수락 기준표](../development/BMO_ACCEPTANCE_MATRIX_KR.md)에 실행 준비와 미검증 범위를 나누어 기록했다.

## 구현과 재사용

1. `launch-features`가 RuntimePolicy/config/brain/client/voice/입력 source를 주입받는 FeatureHost와 기존 IntegrationSession을 사용한다. 별도 두 번째 brain을 만들지 않는다. 기존 BMOBrain/strict FeatureReply/음성 WAV-cue-emotion cache/단일 Python 출력·원본 Piper opt-in/4.1 mini fallback을 재사용했다. Luna low, Aaron Soft/beemo/gain .25/320 정책은 동일하다.
2. 명시적 `--live` 대화 경로를 준비했다. 기존 0700/0600 credentials.json resolver를 공유 잠금 획득 후에만 호출한다. help/status/simulate/doctor는 키 IO 0이다. 가짜 resolver·STT·Fish·player를 주입한 live wiring만 검사했다. 실제 production live 대화는 NOT_RUN이다. arms/IMU/camera/external_games/music capability는 별도 denied이며 모델 출력이나 CLI로 해제할 수 없다.
3. 새 앱 기본은 승인 R10이다. `approved_design`과 `pi_live_verified=false`를 분리했다. 기존 launch-bmo/독립 서버 기본 v13.2 및 원본 작화는 보존했다. 외부 셸만 전체 800×480 iframe·메뉴 숨김을 제공하며 R10 배율 소유자는 원본 하나다.
4. 기존 InputRouter/Games 종료 판단을 재사용하는 합성 source·검증 mapping·debounce/seq/freshness/STOP 어댑터를 추가했다. GP7 충돌은 거부한다. fake PCM PTT/VAD는 유한 바이트·시간·ID와 AudioFocus/self-audio 차단을 갖추며, 같은 Host의 주입 transcriber→동일 대화 세션으로 한 번만 소비한다. 호출어는 모델 미확보로 disabled다.
5. Picamera2/OpenCV에 fake SDK 주입점과 AF/read timeout/cancel/실패 cleanup·still→preview 복구를 보강했다. 실제 import/open은 하지 않았다. 4608×2592 합성 PNG와 bounded codec·저장·옵션 복구를 검사했다. 네이티브 open/close 무한 정지를 Python만으로 강제 중단할 수 있다고 주장하지 않는다.
6. 기존 fake 게임·owned process runner를 재사용하고 게임팩 검토/설치 준비 도구를 추가했다. bounded immutable ZIP snapshot+중앙 디렉터리 사전 검사, traversal/symlink/executable/hash/license/중복/폭탄·TOCTOU 거부, private stage와 기존 버전 보존 rollback을 구현했다. 다운로드·원본 ZIP 설치·게임 실행은 0이다. 실제 ZIP/Tetris는 ASSET_PENDING이다.
7. 기존 motion/IMU 모듈을 재사용하고 staging JSON/LF 512바이트·선택 CRC32 codec을 fake command/ACK 왕복에 연결했다. 기존 firmware/USB/REPL/reset에는 연결하지 않았다. ACK는 위치 증명이 아니며 실제 팔은 disabled/position_unverified다. IMU는 synthetic 건강 후보만 검사하며 offset·팔 제어 입력이 없다.
8. 기존 WAV/cue importer와 clock/focus 제어를 재사용하고 비용 없는 생성기 전용 AVI+FakeVideoTimeline을 추가했다. 시작 offset/flags/시간·헤더 변조를 거부한다. 실제 영상 codec·화면 출력은 미검수이며 기본 WAV/ALSA backend는 영상을 unsupported로 거부한다. 사용자 노래·영상은 ASSET_PENDING이다.
9. metadata-only doctor와 dry-run 전용 desktop template을 제공한다. 실제 doctor는 두 얼굴 SHA MATCH/개발 의존성 metadata를 확인해 READY_FOR_HUMAN_TEST를 반환했지만 tests/CI/live는 추정하지 않는다. 데스크톱·autostart·서비스·OS 설치는 하지 않았다.

## 검토에서 발견하여 수정한 경계

- 기존 질문 receipt를 stop/reset이 덮어써 영구 running으로 남기던 문제: 원 질문과 cleanup waiter를 분리해 모두 terminal로 남긴다.
- 일반 큐 32개/nonce 기록 512개 포화 시 STOP/cancel도 거부되던 문제: 동작별 두 예약 슬롯에서 합쳐 일반 용량과 독립 수용한다. 일반 nonce ledger는 삭제하지 않으며 과거 action을 재실행하지 않는다. STOP/cancel은 고정 안전 제어로 별도 멱등 경로다.
- started 뒤 취소/실패를 executed=False로 표시하던 문제: 시작 사실을 mode generation과 별도로 보존하고 partial_playback을 기록한다. 실제 위치·하드웨어 완료와 혼동하지 않는다.
- PCM ready가 queued STOP보다 먼저 소비되거나 transcriber 재진입 후 취소를 무시하던 문제: 안전 제어를 먼저 처리하고 전후 epoch/세대/closed/busy를 재검증한다.
- AVI dwStart/disabled flag 등 생성기와 다른 시간 필드 허용: 생성기 전용 헤더 전체를 검증한다.
- ZipFile 생성 후 적용하던 항목 제한 및 검사 뒤 원본 변경 경합: 제한된 동일 불변 snapshot을 hash/preflight/parser/extraction에 사용한다. 초과 항목에서 ZipInfo 할당 전 거부함을 회귀로 확인했다.

## 실제 실행한 검증

| 검사 | 결과·범위 |
|---|---|
| 최종 전체 unittest | **795/795 PASS**, skip 0, 43.989초; key-free 환경 |
| pip check / compileall | PASS; 기존 프로젝트 venv 재사용, 컴파일은 임시 cache 경로 |
| 새 800×480 shell | 공개 텍스트 fixture 6 + 로컬 R10 6 + 로컬 v13.2 6 = **18 PASS** |
| 기존 기능 shell | v13.2 7 + R10 7 = **14 PASS** |
| 제공 cue/no-cue media | 두 얼굴 각 7 = **14 PASS** |
| 원본 profile/bridge | 두 얼굴 총 **12 PASS**, 각 bridge 1452·여섯 감정·SHA 전후 동일 |
| 감정 연속성/기존 브라우저 | 원본 v13.2 **14 PASS** (기존 8 + auto 6) |
| 보존 수동 launch-bmo | v13.2 4 + R10 4 = **8 PASS**, credential resolver 호출 0 |
| 합성 soak | 가상 0~47시간/48 turn·단일 brain·bounded queue/ledger·FD/thread 회수 PASS; 실시간 장시간 측정 아님 |
| CI | Python 3.11·3.13 각각 전체 unittest/pip check/compileall + 공개 fixture headless PASS |
| doctor/game inventory/desktop | 실제 metadata/dry-run exit 0, 키/포트/장치/설치 IO 0 |

새 shell 각 profile에서 brain/listen/synthesis/player/reaped는 각각 2, 합성 사진 저장 1,
카메라 open=close=2, forbidden=0이었다. 기존 기능 shell은 두 가짜 게임 실행·B 입력·
카메라 open=close=3을 확인했다. 감정 검사에서 소유 player 11개 모두 회수했고
브라우저가 별도 오디오를 재생하지 않았다. 이는 가짜 응답·합성 PCM/PNG·유한 clock/
무해한 소유 Python child 관측이며 GPT 지연·음성 품질·카메라 성능 실측이 아니다.

HTTP exact Host/Origin/token, bounded JSON/중복 필드/경로·private file 거부, 게임
sandbox·악성 postMessage 무시, disconnect/visibility/refresh/cancel/stale generation,
재시작 journal unknown/무재생, 저장/cleanup 실패와 중복 요청을 검사했다.
게임 postMessage 제어 채널은 구현하지 않아 origin/source가 어떻든 새 명령을 받지 않는다.

## 보존 확인과 미실행 사항

| 보존 대상 | 확인 |
|---|---|
| BMO_EMOTION | HEAD `174dd974fdf119fc3d24a5cda0870ae70c007cf6`, tracked 157개 해시 차이 0, clean |
| BMO_INTEGRATION | HEAD `902b09739844ab4c78ea6234851b7bfb9cdb7634`, tracked 128개 해시 차이 0, clean |
| 원본 R10 | 196987 bytes, SHA `8c3ef98ba0e0645213c95295a24d7353fc2865b5ad9bc85390360c4ec9b78c82`, 전후 동일 |
| 원본 v13.2 | 159892 bytes, SHA `38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`, 전후 동일 |
| 원본 Piper | 모델·JSON·binary 세 파일 기존 SHA 모두 동일, 실행 0 |
| PR #13 / PR #14 | 기존 HEAD 유지, open draft; 이 PR로 병합하지 않음 |
| main | `161a2ebd99b21ddc31e4cbf6b1cbd4452967e264` 유지, merge 0 |

팬/SSH/Tailscale/기존 서비스·OS·다른 프로젝트·기존 백업은 변경하지 않았다.
실제 credentials.json/.env/개인 로그 내용을 읽거나 출력·복사하지 않았다. 승인 작화나
사용자 음원·영상·ROM·사진을 Git에 추가하지 않았다. firmware/staging의 문서·호스트
codec 준비만 변경했으며 보드에 업로드·재시작하지 않았다.

실제 유료 GPT/Fish/STT, 마이크/스피커, 카메라/AF, 서보/IMU, pad/serial, native/
RetroArch, 실제 영상 decoder/LCD·장시간 성능은 모두 **NOT_RUN**이다. 라이선스와
게임팩·사용자 노래/영상이 필요하다. 양팔 기구 한계·방향·stop 프로필, GP7/물리 매핑,
IMU 건강 trace, live schema/음성 청취/비용 및 실제 LCD 검수는 입회·별도 승인 대상으로 남긴다.

## 커밋·변경 범위·복구

- `e17683c89b3d0bb4f451dff0d8ddb99a688463e8`: 통합 runtime·권한·R10·voice 환경·wire·soak.
- `960a3c8ccfa57d4ca02685d09c2c93c7462c5377`: fake camera SDK/고해상도·800×480 공개 browser CI.
- `2066e0d7e0d51b31c6eea5208447e56cb2535d7a`: 입력/PCM 연결·STOP 예약·receipt·부분 재생.
- `798766fff03edf14bf7734801a97568121354dd9`: 자산 사전 검사·AVI·doctor/desktop 준비.
- `88076164cc6e2440f2ce0d3ab7e4aebf73481287`: 최종 ZIP 불변 snapshot 경합 차단 및 검증.

변경 파일은 문서 포함 57개이며, 목록은 [PR Files changed](https://github.com/GUNOBAE/BGH_BMO/pull/16/files)와
소스 SHA의 diff로 확인한다. 구현은 software/와 CI, 새 개발/보고 문서 및 firmware/staging
계약에 한정한다. 기존 테스트를 삭제하지 않았고, 화면 승인 metadata와 명시 live 신규 계약의
기대값 두 파일을 갱신했다. 나머지는 새 회귀다.

소유 앱 `/quit` → clean 확인 → 보존된 feature/bmo-feature-bundle-v1로 switch하거나
기존 BMO_EMOTION/BMO_INTEGRATION 수동 런처를 사람의 승인 범위에서 선택하면 된다.
hard reset/clean/force-push/브랜치 삭제·main 병합은 필요하지 않다. 영속 journal의
미완료 작업은 재시작 후 unknown이며 자동 재실행하지 않는다. ZIP 교체 중 SIGKILL/
전원 상실은 실제 시험하지 않았고 stage/rollback을 보존한 수동 복구 대상이다. 기존
백업을 덮어쓰거나 삭제하지 않는다. 실제 장치 enable을 rollback 명령에 포함하지 않는다.
