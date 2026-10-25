# BMO 실제 로봇 milestone v1 — 구현 및 검수 인계

기준은 [Issue #30](https://github.com/GUNOBAE/BGH_BMO/issues/30) 전체와
[PR #27](https://github.com/GUNOBAE/BGH_BMO/pull/27)의
`0287bb23c1fafc357c9a18419f62b2ebdf0de767`다.
새 브랜치는 `feature/bmo-real-robot-milestone-v1`, 작업 경로는
`/home/bgh/BMO_REAL_ROBOT`이다. 기존 checkout과 실행 중인 BMO는 덮어쓰거나 종료하지 않았다.

**결론: 실제 실행 경로를 갖춘 사용자 감독 검수용 통합판을 구현했다. 실물 완성 판정은 아니다.**
마이크/유료 대화·음성, 실물 카메라, LCD/labwc/VNC, 서보는 무인으로 실행하지 않았다.
팔은 현재 펌웨어에 안전한 호스트 명령 규약이 없어 BLOCKED다. 다른 기능 개발은 계속했다.
단일 Draft PR에서 검토하며 main에는 병합하지 않는다.
제출 PR은 [Draft PR #31](https://github.com/GUNOBAE/BGH_BMO/pull/31)이며 base는 PR #27의
`feature/bmo-camera-video-and-qa-v1`이다. 기존 연속 PR의 검토 순서를 보존한다.

## 1. 변경과 증거 수준

| 기능 | 코드 구현 | 오프라인 검증 | 이번 실물 검수 / 남은 조건 |
|---|---|---|---|
| GP8 한 번 녹음→VAD→STT→Luna→Fish | 기존 단일 worker/brain/cache 재사용, 단계별 진단, 첫 말 보존 | 합성 PCM + 실제 SDK MockTransport + 주입 재생 검증 | NOT_RUN. 실제 GP8 입력·ALSA·응답 청취 필요 |
| Desktop 실행/종료 | app/kiosk, 소유 root/group 회수 경합 보완, 자원별 종료 보고 | fake browser·소유한 무해한 자식·headless 검증 | 실제 GUI/window 종료/재진입 NOT_RUN |
| 로딩/인사 | 기존 실제 checkpoint와 face-presented handshake·welcome-v2 재사용 | 기존 순서/한 번 인사/캐시 회귀 유지 | 원본 Fish 인사 청취·아이콘 설치 NOT_RUN |
| 감정 있는 Aaron | Fish S2 cue·제한 reaction·v2 구조화 응답·반복 보존 | 요청 payload/SDK/캐시/입모양 경계 검증 | 실제 소리 ASSET_PENDING / USER_APPROVAL |
| 카메라 사진/추적 | 감독 opt-in Picamera2, 단일 broker, private 사진과 결과 표시/다운로드 | SDK double의 실제 운영 정책 경로 검증 | Camera3 사진·AF·실제 얼굴 위치 추적 NOT_RUN |
| 카메라 영상 | 실제 PNG 픽셀→저속 무음 DIB AVI, 합성과 계약 분리 | 실제 픽셀 codec/저널/SHA/취소/다운로드 검증 | 센서 녹화·native 종료 상한 NOT_RUN |
| 양팔 | 읽기 전용 소스 증거, 체크리스트·limits/프로토콜 차단 경계 | 전 항목을 체크해도 우회 불가 검증 | BLOCKED: 개별 팔·watchdog·hard STOP 규약 부재 |
| IMU | 기존 비제어 정책 보존 | 기존 회귀 보존 | 자세/팔/안전 판단에 사용 금지 |

## 2. GP8 무응답: 고친 부분과 미확정 원인

실제 사용자가 본 무응답의 원인을 이번 자동 작업에서 장치 없이 확정하지 않았다.
현재 펌웨어 파일의 GP8→Enter 정의와 브라우저의 Enter 수신은 실제 물리 USB 매핑 검수와 구분한다.
아래는 코드에서 확인해 수정하거나 진단 가능하게 만든 부분이다.

1. 기존 아이콘은 명시적 마이크 옵션 없이 설치될 수 있었다. 새 설치 요청에
   `--install-desktop --ptt-microphone`을 사용하면 finite PTT 옵션을 새 아이콘에 보존한다.
   기존 다른 아이콘은 덮어쓰지 않는다. 옛 아이콘을 누르면 옛 실행 경로가 계속 사용될 수 있다.
2. 기존 RMS VAD는 첫 0.4초를 전부 주변 소음으로 학습하고 버렸다. 바로 시작한 짧은 말을
   잡음으로 간주하거나 기준값을 너무 높일 수 있었다. Desktop 제품 경로는 첫 샘플을 보존하고,
   별도로 측정한 ambient profile만 기준을 올리는 speech-safe 모드를 사용한다.
   기존 PCM/FeatureHost 호출 기본값은 호환성을 보존하며 Desktop이 새 정책을 명시한다.
3. 기본 threshold는 제품 경로에서 0.008, speech 최소 0.24초, 종료 무음 0.9초, 상한 12초다.
   이는 해당 실물 마이크 보정 성공을 뜻하지 않는다. RMS는 언어/화자 판별기가 아니므로,
   팬 소음이 크면 사용자가 조용한 상태에서 별도 ambient 측정을 해야 한다.
4. Enter 눌림 latch·반복 억제·iframe 전달·명령 receipt·blur/visibility/재연결 취소를 유지한다.
   부모에서 iframe으로 이동하는 정상 focus 전이는 외부 focus 상실과 구분한다.
   게임 Enter는 대화나 녹음을 시작하지 않는다. GP7은 수정하거나 TALK로 바꾸지 않았다.

### 진단 판독

제품 화면의 준비 상태 패널에 원문 없는 요약이 있다. `--ptt-trace`를 명시하면 최대 96개
stage/code·상대 시간·제한된 RMS/threshold/byte/frame 지표를 메모리에 제공한다.
trace가 꺼지면 상세 이벤트/RMS는 공개하지 않고 마지막 성공/실패 요약만 제공한다.
서버는 기존 loopback/Host/Origin/token/nonce 경계를 유지한다. 원문·PCM·키·개인 경로를 수집하지 않는다.

| 관측 | 의미 / 다음 확인 |
|---|---|
| Enter count가 늘지 않음 | 실제 HID/포커스 경로 미도달. 키보드 Enter와 비교; 물리 GP8로 단정 금지 |
| 수신됨, request 없음 | opt-in·제품 phase·모드/메뉴 gate 확인 |
| microphone / tool/start/stream 실패 | 녹음 도구·권한·ALSA 장치/format 확인. 듣는 표정은 PCM 개방 증명이 아님 |
| first_frame_received=true | 실제 입력 provider가 PCM frame을 전달했음. source 식별·실제 음성 품질 보장은 아님 |
| vad / no_speech | STT·AI·Fish 0회. 작은 목소리·noise floor·발화 시점 검수 |
| stt / stt_empty 또는 authentication 등 | STT 결과/서비스 실패를 AI 실패와 구분; 빈 결과는 AI/Fish 0회 |
| ai / timeout 등 | STT 뒤 Luna 실패. 대화 원문 기록 없이 판별 |
| fish / fish_balance·fish_timeout 등 | 합성 단계 실패. 얼굴 emotion과 실제 음성 감정은 별개 |
| playback / device_unavailable 등 | 합성 이후 소유 player 경계 실패. started도 청취 성공 증명은 아님 |
| cleanup / unknown·pending | 새 녹음/실행을 재시도하지 말고 자원 회수 검수 |

마이크 소스에는 S16_LE/16kHz/mono를 명시하고 frame 검증을 한다. 실제 ALSA 실패를 확인하지 않고
원인을 format으로 단정하지 않는다. ambient 도구는 0.8초 측정/최대 2초의 명시적 마이크 작업이며
PCM/WAV 없이 0600 통계만 새 파일에 보존한다. API client를 만들지 않고 기존 파일을 덮어쓰지 않는다.

## 3. Desktop, 로딩, 인사와 종료

Qt 이식이나 얼굴 재작화 없이 Python 엔진 + 보호된 로컬 adapter + 전용 Chromium app/kiosk를 재사용한다.
기본 화면은 R10이며 제품 메뉴는 평소 숨긴다. 개발 창의 `--windowed`와 기본 kiosk를 구분한다.
PR #24의 로딩 바·백분율·캐시 경로와 PR #25의 실제 face-presented 순서를 중복 구현하지 않았다.
로딩 100%는 로컬 준비 완료이며 유료 API 건강 검사가 아니다. 첫 표시가 10%인 것은 이미 획득한
실행 lock checkpoint이며 임의 시간 기반 숫자를 보태지 않았다.

인사 `돌아왔구나! 비모가 기다렸어!`는 기존 welcome-v2 private cache만 사용한다.
준비/청취/SHA 승인은 명시적인 별도 작업이다. 실행 중 자동 합성이나 API 재시도가 없고,
캐시가 없거나 손상되면 얼굴은 준비 상태와 함께 계속 사용할 수 있다.

browser group이 조용해진 순간과 root가 wait 가능한 순간 사이의 경합을 bounded wait로 보완했다.
다른 Chromium을 찾거나 전역 종료하지 않는다. 종료 보고는 browser/root/group/profile,
server/host/greeting, session worker/voice client, 마이크 pending, 카메라 close unknown을 구분한다.
소유 프로세스 소멸·exit0을 전체 자원 반환으로 해석하지 않는다. native 또는 취소 provider가 남으면
`resources_cleaned=false`다. 이는 NEXUS의 인증된 lifecycle ACK 구현이 아니다.

읽기 전용 조사 시 runtime mode는 0700·소유권 일치였고 runtime-dir unit은 active/exited,
추가 drop-in은 없었다. 세션 여러 개와 linger 설정만으로 0770 변경 주체를 추정하지 않았다.
기존 로그인/profile/PAM/unit 관련 8개 파일에서 해당 변경 참조를 제한 검색했으나 찾지 못했다.
**권한을 바꾸는 주체는 UNKNOWN**이며 자동 chmod·systemd/VNC/OS 변경은 하지 않았다.

## 4. 음성 감정

Luna low, Fish Aaron Soft, Beemo 발음, gain 0.25와 Piper를 보존했다.
얼굴 emotion을 정하는 것과 실제 음성 운율을 생성하는 것은 다른 단계다.
Desktop의 v2 응답은 제한된 reaction enum을 받고, 호스트만 검증된 S2 cue를 조합한다.
모델이 임의 태그/SSML·성우·pitch·gain 명령을 보내도록 허용하지 않는다. v1 응답은 안전한
reaction=none으로 호환하며 검증/모델 조건이 맞지 않으면 plain speech로 대체한다.

chuckle/gasp/playful_puff/sob는 감정·현재 사용자 요청과 일치할 때만 허용하고,
성공한 새 발화 세 번당 최대 한 번으로 제한한다. 반복은 이미 저장한 WAV·감정·입모양을 그대로
사용하며 새 합성이나 반응 빈도 증가가 없다. 합성 중 metadata 변경도 해당 WAV의 표정을 바꾸지 않는다.

공식 근거는 [Fish emotion 문서](https://docs.fish.audio/developer-guide/core-features/emotions),
[S2.1 모델·음성 API 설명](https://docs.fish.audio/features/text-to-speech),
[API parameter](https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech)다.
문서는 S2 bracket cue 및 s2.1-pro-free 모델 관계를 확인하는 근거이며 실제 청취 합격 증거가 아니다.
이번에는 unsupported JSON parameter나 WAV pitch shifting을 추가하지 않았다.

6개 고정 A/B sample은 `voice_benchmark`에서 한 개씩 명시적으로 준비/청취/SHA 승인한다.
기존 캐시/손상 파일 보존·동시 요청 차단·중복 비용 방지 경계를 갖췄다.
실제 웃음/울음/뿌우의 품질·태그 미낭독·Aaron 동일성은 **USER_APPROVAL**, 음원은 **ASSET_PENDING**이다.

## 5. 카메라와 팔

시스템 Python에서 Picamera2/libcamera/PIL/cv2를 찾을 수 있었다. 이는 sensor open이 아니다.
일반 개발용 격리 venv에서는 distro 카메라 모듈이 보이지 않을 수 있으므로, 선택 interpreter에서
별도 capability probe를 제공하고 UI에서도 unavailable을 표시한다. 카메라가 없어도 대화는 계속된다.
system-site-packages 실험에서는 기존 distro/stub package의 별개 pip 의존 경고를 확인했으나
OS 패키지를 수정하지 않았다. 일반 개발 venv는 다시 격리 상태로 복구했고 pip check는 통과한다.
사용자가 선택하는 별도 카메라 환경 절차는 실행 가이드에 있다.

`--camera-supervised --photo-directory`로 backend를 선택해도 생성/import 시에는 sensor를 열지 않는다.
사용자의 사진·미리보기·추적 액션에서만 열며 모델의 자동 카메라 intent는 거부한다.
photo/preview/tracking/video는 한 broker를 공유한다. 사진의 크기·총량·여유 공간, AF/capture job
대기를 제한하고 private 파일을 저장한다. 별도 영상은 실제 PNG 픽셀의 저속 무음 AVI다.
합성 16×16 영상과 result contract/실행 플래그를 분리하고 다운로드 SHA를 재검증한다.
완료 사진/영상·중단 journal을 자동 삭제하지 않는다. 카메라·촬영 결과의 자동 업로드/신원 인식은 없다.

libcamera의 native configure/start/stop/close가 영원히 반환하지 않을 때의 강제 격리/종료 상한은
검증하지 못했다. SDK close 실패는 sticky UNKNOWN으로 보존하고 같은 process에서 재open을 막는다.
실제 사진/영상·AF·노출·성능·종료·재진입은 사용자 입회 검수가 필요하다.

읽기 전용으로 확인한 RP2040 파일은 2,153바이트, SHA-256
`fdc65bebef17b78b945281549ddce80fb749e1a94262751d8d2f0146c394a01e`였다.
소스상 GP8 Enter, GP7 양팔 0/180 toggle, right GP10/left GP11 PWM50, 부팅 set_arms(0)이 있다.
전용 host data·개별 팔·watchdog·hard STOP 규약은 확인되지 않았다.
기존 제안 JSON v1을 실제 펌웨어 규약으로 취급하지 않았다. 포트·CDC/REPL·DTR·보드 reset을 열지 않았다.
따라서 사람 체크리스트가 모두 true여도 현재 real arm admission은 **BLOCKED**다.
전원 분리·공통 GND·전류 제한·물리 kill·관절별 범위·천천히 수동 보정과 별도 검토된 펌웨어 계약이
먼저 필요하다. 실행 가능한 것처럼 보이는 임의 PWM/serial 명령을 제공하지 않는다.

## 6. 검증 기록

검증 코드 SHA: `88e2c69648e27d0ed48d6d73aaf874a61d2be58e`.
로컬 Python 3.13.5에서 **전체 1,160 PASS / failure 0 / error 0 / skip 0**, 77.023초다.
기존 1,076개에 새 84개(음성 22, camera 21, arm 7, camera host 10, Desktop 13, PTT 11)를 더했다.
[코드 push CI #40220019304 SUCCESS](https://github.com/GUNOBAE/BGH_BMO/actions/runs/40220019304):
Python 3.11은 1,160개/66.307초, 3.13은 1,160개/69.810초로 통과했고 public-browser도 통과했다.
pip check, compileall, 기준 commit 대비 diff --check를 통과했다.
소스별 SHA 및 검증 범위는 [검증 JSON](BMO_REAL_ROBOT_MILESTONE_V1_VERIFICATION.json)에 보존한다.
보고서만 추가한 최종 HEAD의 PR CI는 완료 후 PR/Issue #30 댓글에 별도로 연결한다.

확인된 headless 결과는 공개 fixture·승인 R10·v13.2 각각
offline 6 + Desktop 7 + PTT 12 + camera/media 7 = **32개**, 총 **96 PASS**다.
R10 SHA `8c3ef98ba0e0645213c95295a24d7353fc2865b5ad9bc85390360c4ec9b78c82`,
v13.2 SHA `38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`와 일치하며
원본을 수정하지 않았다. 브라우저는 테스트 소유 headless 창·loopback·무음이며 실제 GUI 검수가 아니다.

기존 테스트를 지우거나 skip/완화하지 않았다. 새로운 tests 6개 파일과 browser trace 검증을 추가했다.
기존 SDK/경로·표정/반복/키 보안/취소/장치 비활성·원본 보호 회귀를 포함한다.
초기 전체 실행에서 simulation v2 호환과 legacy noisy calibration 호환 두 실패를 수정했다.
테스트 실행 위치의 잘못된 import 시도는 회귀 PASS로 세지 않았다.

## 7. 사용자 실행과 복구

하나의 단계별 절차는 [software/REAL_ROBOT_TEST_KR.md](../../software/REAL_ROBOT_TEST_KR.md)에 있다.
먼저 기존 앱을 사용자 메뉴로 종료하고 그래픽 세션에서 preflight를 확인한 뒤 다음 제품 경로를 검수한다.
이 명령은 이번 자동 작업에서 실행하지 않았다.

```bash
cd /home/bgh/BMO_REAL_ROBOT
./launch-desktop --live --ptt-microphone --ptt-trace
```

runtime/원본 SHA/중복 lock gate가 실패하면 멈추고 원인을 확인한다. 원문/키를 보고서에 붙이지 않는다.
카메라는 별도 opt-in/저장 경로·사용자 액션, 음성 A/B는 별도 비용·청취 승인이다. 팔은 여전히 차단된다.
기존 아이콘과 내용이 다르면 설치기는 덮어쓰지 않는다. 기존 항목을 보존하는 검토 후 새 설치를 결정한다.

호스트 설정 rollback은 없다. 기존 PR #27 checkout·R10/v13.2·credentials·Piper·펌웨어·백업·팬·
SSH/VNC/Tailscale/다른 프로그램은 바꾸지 않았다. 사용자 검수 중 앱 메뉴 종료 후 기존 보존 checkout의
기존 진입점을 사용하면 소프트웨어 선택을 되돌릴 수 있다. 새로 설치한 아이콘만 별도 지침에 따라
내용/소유권 확인 후 제거하며 private 음성/사진/영상·기존 백업을 삭제하지 않는다.
Git rollback은 검토 후 이 PR의 커밋만 revert한다. reset --hard/clean/force push/main 병합은 하지 않았다.
