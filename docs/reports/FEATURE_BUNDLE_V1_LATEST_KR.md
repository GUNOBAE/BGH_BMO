**PARTIAL — A 감정 연속성과 B Launcher 및 C 기능 묶음의 코드·Mock·로컬 headless 검증과 GitHub 제출을 완료했다. 실제 API/장치 검수는 NOT_RUN이며, 지정 인계 ZIP/master order·최종 승인 R10 일치 확인과 실제 게임/노래 자산은 대기 상태다.**

2026-10-23 KST. GitHub를 코드·검사·인계의 단일 작업 기록으로 사용한다.

## 작업 순서와 GitHub checkpoint

| 단계 | 경로/브랜치 | 검증 코드·보고서 | PR |
|---|---|---|---|
| A 감정 연속성 v2 | `/home/bgh/BMO_EMOTION`, `feature/bmo-auto-emotion-v1` | 구현 `5545880fedf1edf35268a5e1b935bd9cabb6ce5e`, 보고 checkpoint `d2a75317e8b36108ff932aea0d49ca3a40fe5b7b` | [draft #13](https://github.com/GUNOBAE/BGH_BMO/pull/13) |
| B 화면·키·Launcher | 같은 경로/브랜치, A 제출 후 진행 | 구현 `9555863ae5d7dc5d8b3d997be9ea032c2d744758`, 검증 보고 checkpoint `174dd974fdf119fc3d24a5cda0870ae70c007cf6` | 같은 draft #13 |
| C 독립 기능 통합 | `/home/bgh/BMO_FEATURES`, `feature/bmo-feature-bundle-v1` | 구현 [`78db6a53c53d6fcea4624fd9be3f3972eacf15d2`](https://github.com/GUNOBAE/BGH_BMO/commit/78db6a53c53d6fcea4624fd9be3f3972eacf15d2), 이 문서는 뒤의 별도 문서 커밋 | [draft #14](https://github.com/GUNOBAE/BGH_BMO/pull/14) |

A/B의 상세 결과는 [연속성 v2 보고서](https://github.com/GUNOBAE/BGH_BMO/blob/feature/bmo-auto-emotion-v1/docs/reports/EMOTION_CONTINUITY_V2_LATEST_KR.md),
[R10/키/Launcher 보고서](https://github.com/GUNOBAE/BGH_BMO/blob/feature/bmo-auto-emotion-v1/docs/reports/R10_LAUNCHER_V1_LATEST_KR.md)에 보존했다.
A/B 검증 후 C를 checkpoint `174dd974`에서 새로 분기했다. 문서 브랜치를 runtime 기준으로
clone하거나 과거 브랜치 전체를 merge하지 않았다. C의 base PR은 `feature/bmo-auto-emotion-v1`,
PR #13의 base는 `feature/bmo-integration-v1`이다. 두 PR은 draft이며 main에 병합하지 않았다.

## 읽은 지시와 미확보 입력

- 사용자 메시지의 A→B→C 순서, 보존 조건, 관련 AGENTS.md와 기존 작업서를 확인했다.
- GitHub 현재 HEAD/PR/보고서/CI를 다시 확인했다. A 시작 HEAD `dd12c4a2235d97da7efbf3f460c39a84c7ed609a`는 문서만 추가한 상태였으며 v2 runtime 완료본이 아니었다. 당시 main은 `161a2ebd99b21ddc31e4cbf6b1cbd4452967e264`였다.
- 연속성 작업서 `CODEX_EMOTION_CONTINUITY_V2_KR.md` 전체를 기준으로 이전 neutral/2초 정책을 교체했다.
- C 작업서 `CODEX_FEATURE_BUNDLE_V1_KR.md` 전체를 docs branch `b2bf29f1de9324eada07046511ee9df927e3c0e4`에서 읽고 정확한 blob `a5e3589604c3aeb18940f2b26fd5993ca3493a79`를 이 브랜치에 인수했다.
- **`BMO_R10_CODEX_INTEGRATION_HANDOFF_2026-10-23.zip`과 `BMO_CODEX_INTEGRATION_MASTER_ORDER_2026-10-23.md`는 접근 가능한 관련 Pi 경로·GitHub 브랜치/PR에서 찾지 못했다.** 경로/링크를 요청했으며 전체 ZIP/master order를 읽었다고 주장하지 않는다. 명확히 확보한 사용자 지시와 GitHub 작업서의 안전한 범위는 중단하지 않고 수행했다.
- R10 제목의 로컬 검토 후보는 발견했으나 최종 승인본과 동일한지는 확인 요청 중이다. `candidate_pending_user_approval`로 유지하고 기본 복구 v13.2를 바꾸지 않았다.

## 모듈별 구현/검증/실기 상태

| 구성 | CODE/MOCK/LOCAL 결과 | 실제 상태와 남은 조건 |
|---|---|---|
| ModeManager | 이전 정리→새 준비→확인, 실패 복귀, mode generation, 정리 실패 시 새 owner 차단 | 실물 앱 간 전환 NOT_RUN |
| AudioFocus | 한 owner/lease, 이전 stop 확인 전 handover 거부, stale lease·재진입 차단 | 전체 실물 오디오/게임 음량 검수 NOT_RUN |
| InputRouter | mode별 한 소비자, game B 전달, STOP 우선 큐·오래된 launch 무효화, 논리 hold/debounce | USB pad/본체 물리 mapping·GP7 충돌 미검수. 추정 연결 없음 |
| GazeArbiter | 수동 > 원본 listening/thinking > camera > reaction > idle, 좌표/TTL/gen, 감정·입 분리 | 별도 문맥 연출 튜닝·실제 시각 검수 NOT_RUN. 임의 랜덤 스케줄러 없음 |
| FeatureReply/HostIntent | 별도 version1 strict schema, 같은 첫 요청에 speech/emotion/gesture/최대1 intent, 신뢰 catalog ID/준비 상태, clean speech/history | 실제 Luna 계정 schema/의미 품질·지연/비용 NOT_RUN. 기존 두 필드 expression/수동 복귀 보존 |
| Games | catalog/준비 상태, 2개 명시 합성 runner, 메뉴→게임→다른 게임→얼굴, B/Esc/Start+Select1.2초, crash/padloss/owned cleanup | 실제 게임팩 `ASSET_PENDING`, 23개 설치 주장 없음. native/RetroArch 어댑터는 명시 verified/probe 없으면 차단, 실제 hotkey/게임 실행 NOT_RUN |
| Camera | 단일 lazy broker, tracking/preview/photo 공유, local detector 인터페이스, 합성 frame/PNG 저장·취소·초점/디스크 오류·원자성·dedup | Picamera2/OpenCV 선택 어댑터는 기본 disabled/lazy, 실제 카메라·초점·사진 품질 NOT_RUN. 현재 preview 해상도이지 full-resolution 보장 아님 |
| Target tracker | 최초 선택/다중 대상 유지·전환 억제, freshness/loss grace, mirror/rotation, dead zone/smoothing/속도 제한, 재획득 큰 점프 방지 | 얼굴 **위치** 추적이며 신원/시선 인식·물리 pan-tilt가 아니다. 실제 성능·최적 tuning 미확인 |
| Motion | none/wave/celebrate/shrug, 실제 started 시각/turn/gen/TTL/한 턴1/cooldown/queue, fake handshake/ACK/controller dedup/timeout/reboot unknown | 실제 serial backend·servo·firmware 실행 없음. 보정 profile verified여도 real disabled. ACK는 `position_unverified`, 실제 각도 도달 completed=False |
| IMU | 기존 parser 재사용, synthetic health/seq/boot/freshness/포화/고정값/편향, hysteresis/기간/cooldown 후보 | 실제 센서는 `physical_unverified`, 제품 제어·팔 안전 입력으로 사용 금지. heartbeat만 healthy 인정 안 함, 자동 offset 보정 없음 |
| Media | 기본 빈 목록, root-relative/hash/FD 검증 importer, fake play/pause/resume/next/end/stop, 명시 cue clock 연결 | 사용자 노래 `ASSET_PENDING`, 실제 노래/가창 생성/다운로드 없음. backend 미지원 codec/pause는 unsupported, 실제 음량·영상·ALSA NOT_RUN |
| Journal/결과 | private metadata/fingerprint, ID 충돌/중복/재시작 unknown, 시작 전 DB 실패 거부·시작 후 실패 unknown, no replay | 기계적 exactly-once나 위치 피드백 보장 아님. 실제 side effect 실기 없음 |
| 외부 shell | 실제 mock launcher/server/iframe, mode UI/프리뷰/사진 결과, HTTP thread는 queue만, owner poll 실행, sandbox/Origin/Host/token/한계 검증 | simulation 라벨 고정, 승인 LCD 배치/물리 버튼 검수 NOT_RUN |

기존 Action v0 5종 Mock의 7-field 결과와 executed=False는 변경하지 않았다.
새 HostResult의 accepted/running/unknown 및 simulated/executed/completed/completion을
기존 Mock 성공이나 실제 기계 완료로 혼동하지 않는다. 정지·취소는 LLM/저널을 기다리지 않는다.

## 실제 실행한 검사

검사는 새 C clone의 `.venv`와 실제 자격증명을 제외한 허용 child 환경에서 수행했다.
Python 3.13.5/aarch64, SDK 2.54.0/MockTransport, Playwright 1.64.0와 기존 Chromium을 사용했다.
개발 의존성만 새 venv에 설치했고 OS 패키지·서비스·기존 venv를 바꾸지 않았다.

| 검사 | 실제 결과 |
|---|---|
| A checkpoint | 전체 **415 PASS**, v2 승인 HTML **14 checks**, 기존 수동 E2E **4 checks** |
| B checkpoint | 전체 **471 PASS**, 두 화면 **12 checks**, 새 manual Launcher 각 profile **4 checks** |
| C 최종 전체 unittest | **657/657 PASS, skip 0**, 40.342초. A+B 기존471 + C 신규186. 테스트를 삭제/임의 skip해 통과시키지 않았다. |
| 신규186 구성 | games/media27, camera/재획득30, motion/IMU42, resources/journal/HTTP43, SDK8, Host/admission/close/stopqueue23, launcher11, 명시 media cue2 |
| 실제 C bundle E2E | **v13.2 7 checks + R10 후보 7 checks PASS**. 실제 --simulate launcher→FeatureHost→shell POST→mode/modules→선택 iframe/external adapter 경로다. |
| bundle 각 profile 수량 | brain/listen/synthesis/player/reaped **각2**, game start2/B 전달2, camera open3/close3, 합성 photo1, forbidden0. browser audio0, 임시 자산/소유 resource 정상 정리. |
| 선택 cue E2E | **v13.2 7 + R10 후보 7 PASS**. .15 low/.35 wide→pause speech off→resume 같은 발화/iframe→end crying 복귀. cue 없는 곡은 RMS로 입을 만들지 않는다. 각 browser audio0/dialogue0/실제 backend0. |
| SDK·취소 경계 | strict version1/요청1회/정책·Luna low320/clean history, refusal/incomplete/태그·경로·잘못된 intent, LLM/TTS 취소 뒤 intent0, ended 뒤1dispatch/repeat 재실행0 |
| 독립 검토·오류 수정 | partial cleanup·AudioFocus 재진입·저널 raw 오류/쓰기 실패·nonASCII token·HTTP body timeout·논리STOP 실패 오보고·미디어 next EOF·manual fixture JSON 누출·stop 뒤 queued launch·stale receipt 오염을 보강하고 회귀 통과. 모의 tracker 설정(max_speed=0.5)에서 공백 재획득 0.4 점프 실패를 재현해 한 단계 0.05 이하로 제한한 회귀 포함. |
| 정적/의존성 | pip check / compileall / git diff --check / 제출 경로·대형자산·키 패턴 검사 PASS |
| GitHub CI | 구현 `78db6a53`의 [push CI](https://github.com/GUNOBAE/BGH_BMO/actions/runs/39948001864)와 [PR CI](https://github.com/GUNOBAE/BGH_BMO/actions/runs/39948105456) PASS. Python 3.11/3.13의 unittest/pip check/compileall 성공. 승인 원본 없는 CI에 headless 실물 검증이 있다고 하지 않는다. |

check group 수와 bridge fixture 조합을 unittest 수에 합산하지 않았다. bundle의 150ms 관측
지연·8초 합성 PCM·제어 clock은 시험 fixture이며 실제 API·가청·게임·카메라 지연 측정이 아니다.
A/B가 확인한 원본 bridge 1,452 fixtures/30fps와 native 눈물·깜빡임·시선·흠 입을 보존했다.

## 화면·원본·완성본 보존

- v13.2: 159,892 bytes, SHA-256 `38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`.
- R10 후보: 196,987 bytes, SHA-256 `8c3ef98ba0e0645213c95295a24d7353fc2865b5ad9bc85390360c4ec9b78c82`, HTML 내부 검토본 표기/최종 승인 일치 INPUT_PENDING. raw served bytes와 검사 전후 SHA 동일. 별도 pin/version이며 기본 v13.2 유지.
- C 분기 뒤 `/home/bgh/BMO_EMOTION` 추적 **157개 파일 SHA 일치**, HEAD `174dd974` 유지, worktree clean.
- `/home/bgh/BMO_INTEGRATION` 추적 **128개 파일 SHA 일치**, HEAD `902b09739844ab4c78ea6234851b7bfb9cdb7634` 유지, worktree clean.
- 두 화면 원본 HTML/SVG·작화/표정/입 프레임·Piper 모델/설정·고정 prompt·Aaron Soft/beemo/gain0.25/Luna low/320/STT/4.1 mini 복귀는 보존했다. 표정별 새 TTS preset이나 laugh/cry 태그를 넣지 않았다.
- 팬·SSH·OS·기존 서비스·RP2040 firmware·GPIO·시리얼·모터·실제 카메라·다른 프로젝트와 기존 백업을 변경하거나 조작하지 않았다. 새 firmware 파일은 repository의 staging **문서**뿐이며 보드에 쓰거나 reset하지 않았다.
- Git에는 소스/테스트/안내/빈 manifest만 제출했다. 얼굴 원본·ZIP·사진·음성·모델·private credential·개인 로그를 올리지 않았다. 합성 fixture는 앱 소유 임시 자료이며 종료 때만 정리했다.

## 누락 자산·실물 차단 조건

인계 ZIP/master order와 R10 최종본 확인이 필요하다. 지정/관련 경로의 제한적인 이름 조회로
실제 BMO_Games_v3 게임팩을 확인하지 못했고, 사용자 노래 자료는 추후 제공 예정이라
두 기능은 실제 asset pending이다. 확인되지 않은 23개 게임이나 노래를 설치 완료로 쓰지 않는다.

실물 동작은 별도 입회 승인 후 기능별로 검수한다. 권장 순서는 연속성/LCD → 확인된 게임 왕복
→ 카메라 사진/위치 추적 → 검증된 팔 프로필/정지/전원·통신 → IMU 건강 후 제한 반응 → 사용자 노래다.
실제 OpenAI/Fish keys는 이 자동 작업에서 입력·열람·생성하지 않았다. 최초 비공개 설정/재사용 코드만
synthetic 파일로 검증했고, 실제 계정 지원/인증/요금·성능도 NOT_RUN이다.

## 안전한 실행·종료·롤백

자격증명/장치 없는 C 모의 실행:

```bash
cd /home/bgh/BMO_FEATURES
./launch-features --simulate --demo-assets --screen-profile v13.2 --port 0
```

출력 URL만 열고, 종료는 `/quit`/Ctrl-C/EOF다. 기존 앱과 lock이 충돌하면 기존 앱을 강제 종료하지 않는다.
동작/검사 명령과 인터페이스·manifest 등록 절차는 [software/FEATURES_KR.md](../../software/FEATURES_KR.md),
각 module README와 firmware staging 문서를 따른다.

롤백은 C를 정상 종료하고 보존된 `/home/bgh/BMO_EMOTION/launch-bmo`를 선택하면 된다.
장치 없는 복귀는 `./launch-bmo --simulate --emotion-mode auto --screen-profile v13.2 --port 0`이다.
유료 API·실물 명령은 이 자동 작업에서 실행하지 않았다. C 이전 정확한 Git 기준은 `174dd974`이며
어떤 reset --hard/clean/force-push/기존 백업 복원·삭제도 수행하지 않았다. private 키 설정은
Git rollback과 별도이며 자동 삭제하지 않는다.

다음 입력 하나: 접근 가능한 인계 ZIP 또는 master order의 Pi 경로/GitHub 링크를 확인한다.
이 보고서 본문을 다른 채팅에 복사할 필요는 없다.

## 외부 계약 확인

- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs): text.format strict 구조와 refusal/incomplete 예외. 문서/MockTransport가 실제 Luna 계정 지원을 증명하지 않는다.
- [Raspberry Pi camera software](https://www.raspberrypi.com/documentation/computers/camera_software.html): libcamera/Picamera2 계열 참고. 현재 Debian 장치/패키지를 교체하거나 센서를 검수하지 않았다.
- [CircuitPython USB CDC](https://docs.circuitpython.org/en/latest/shared-bindings/usb_cdc/): console/data 분리 참고. 현재 firmware가 제안 data protocol을 제공한다고 추정하지 않는다.
- [Libretro input/controls](https://docs.libretro.com/guides/input-and-controls/): backend별 입력/hotkey 구분 참고. 실제 pad/RetroArch 복귀는 UNVERIFIED다.
