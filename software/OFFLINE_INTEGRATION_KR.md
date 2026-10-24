# BMO 오프라인 통합 v2 사용·검수 가이드

실행 계약은 [Issue #15](https://github.com/GUNOBAE/BGH_BMO/issues/15)다.
작업 경로는 `/home/bgh/BMO_FEATURES`, 브랜치는
`feature/bmo-offline-integration-v2`, 검토 대상은 [Draft PR #16](https://github.com/GUNOBAE/BGH_BMO/pull/16)이다.
최신 수치·소스 SHA·CI는 [최종 보고서](../docs/reports/OFFLINE_INTEGRATION_V2_LATEST_KR.md),
기능별 검수 구분은 [수락 기준표](../docs/development/BMO_ACCEPTANCE_MATRIX_KR.md)를 따른다.

## 통합 실행 경로

`launch-features → FeatureHost → IntegrationSession → 기존 BMOBrain/FeatureClient →
IntegrationVoice → Python 소유 player → 외부 화면 어댑터`를 사용한다.
기능별 별도 brain을 만들지 않는다. 게임·음악·사진 전환 뒤에도 history와 감정을
유지한다. 새 감정은 검증된 응답의 실제 player started에서 바뀌며, ended·idle·
listening·thinking·일반 취소가 neutral을 강제하지 않는다. `/reset`은 명시 초기화다.
반복 재생은 기존 gained WAV/cue/emotion을 재사용하고 GPT·합성·기능 intent를 반복하지 않는다.

새 통합 런처만 R10을 기본 선택한다. 복구용 `launch-bmo`와 독립 얼굴 서버의 기본
v13.2는 보존했다. R10의 `approval_status=approved_design`, `pi_live_verified=false`는
디자인 승인과 실제 LCD 검수가 다름을 뜻한다. 원본 HTML/SVG·표정·입·비율은 변경하지 않는다.
외부 셸은 800×480 전체 iframe과 숨길 수 있는 메뉴를 제공한다. 원본 R10의 배율 조정은
원본이 한 번만 소유하며 외부에서 추가 배율을 적용하지 않는다.

```bash
cd /home/bgh/BMO_FEATURES
./launch-features --status
./launch-features --simulate
./launch-features --simulate --demo-assets
./launch-features --simulate --screen-profile v13.2
```

`--demo-assets`가 없으면 게임·노래 목록은 비어 있다. 합성 게임·WAV·사진은 명시된
모의 실행에서만 임시 경로에 만든다. 키 저장소·실제 장치·유료 API를 읽거나 호출하지 않는다.
Enter 또는 화면 질문 버튼으로 한 턴을 요청한다. `/games`, `/game ID`, `/face`,
`/photo`, `/track`, `/camera-off`, `/song ID`, `/media pause|resume|next|stop`,
`/emotion auto|manual|NAME`, `/repeat`, `/cancel`, `/reset`, `/stop`, `/quit`를 지원한다.

## 키 저장과 이후 입회 대화 검수

아래 명령은 이번 자동 작업에서 실행하지 않았다. 사람이 실물·비용 범위를 별도
승인한 뒤 실행할 준비 경로다. 기존 저장소 하나만 사용한다.

```bash
# 사용자 최초 설정: 화면에 키를 숨기는 기존 수동 입력, API 검증 호출 없음
./launch-bmo --setup-credentials
# 별도 입회 승인 이후에만: 유료 대화·STT·Fish·마이크·ALSA 경로
./launch-features --live
```

같은 `$XDG_CONFIG_HOME/bmo/credentials.json` 또는 `~/.config/bmo/credentials.json`의
0700 디렉터리/0600 파일을 기존 resolver가 사용한다. 새 키 저장소나 자동 복사,
명령행 키 옵션은 없다. env > 저장값 > 누락값 수동 입력 순서이며 환경 키를 자동 저장하지 않는다.
help/status/effective-config/모의 실행/doctor는 키를 읽지 않는다. live도 기존 앱과
공유하는 잠금을 확보한 뒤에만 resolver를 호출한다. 이번 검사에서는 가짜 resolver만 주입했다.

대화 정책은 Luna low, 320 tokens, gpt-4o-mini-transcribe, Fish Aaron Soft / beemo /
gain 0.25 그대로다. `--baseline`은 4.1 mini+reasoning unset,
`--offline-voice`는 보존된 원본 영어 Piper의 명시 opt-in이다. 미디어 음량에 대화 gain을
강제하지 않는다. player 환경은 필요한 오디오·언어·세션 항목만 허용한다.

`--live`는 카메라·팔·IMU·외부 게임·음악 권한을 부여하지 않는다. 각각
`RuntimePolicy`의 별도 capability가 denied이며, 모델이 요청하거나 설정 파일에
값을 추가해 해제할 수 없다. 이 단계에는 실물 enable 옵션 자체가 없다. 자동 마이크
재청취와 Hey BMO도 켜지지 않는다. 키 설정만으로 장치 검수가 끝났다고 표시하지 않는다.

## 입력·VAD·시선

`features.input`은 주입된 합성 USB pad/RP2040 HID/CDC 이벤트만 처리한다.
물리 reader를 만들거나 포트를 열지 않는다. 명시 검증 맵, 출처, 순서·freshness·
debounce·중복·STOP 우선 처리를 거쳐 기존 InputRouter에 전달한다. 게임 중 B는
게임 입력이고, Esc/Start+Select 1.2초 종료 판단은 기존 GamesFeature가 소유한다.
GP7 충돌은 차단 상태이며 새 물리 매핑을 활성화하지 않는다.

`FeatureHost(input_source=..., input_mapping=..., pcm_transcriber=...)`는 모의 IO
주입점이다. `host.start_ptt(id)` 이후 `host.pcm`에 합성 PCM을 넣어 한 번의
silence/timeout 결과를 기존 대화 세션에 연결할 수 있다. 기본은 disabled다.
AudioFocus 소유자나 진행 중 voice가 있으면 시작·중간 결과를 차단한다. STOP/cancel·
모드 변경·재진입 이후 오래된 transcript를 버린다. PCM 바이트·프레임·시간·ID를
제한하고 공개 snapshot이나 repr에 PCM/대화를 담지 않는다. 실제 VAD 임계값 보정,
echo cancellation, 호출어 모델은 NOT_RUN이다.

시선은 수동 > 원본 듣기/생각 > 검출 얼굴 위치 > 짧은 발화 리액션 > 원본 idle
순서다. 실제 started에서 짧은 한 번의 look/lookEyes/blink만 허용한다. 랜덤 jitter,
감정·입 작화 변경, 센서별 GPT 호출은 없다. stale/cancel/mode 종료는 연출을 무효화한다.

## 자산·장치 어댑터 준비

게임팩·Tetris의 알려진 로컬 ZIP 후보는 제한적으로 확인했으며 확보되지 않아
ASSET_PENDING이다. 23개 목록을 설치된 게임으로 취급하지 않는다. 다음 명령은
이미 보유한 파일의 검토만 하며 다운로드·실행·설치를 하지 않는다.

```bash
cd /home/bgh/BMO_FEATURES/software
../.venv/bin/python -B -m bmo_ai.game_assets --dry-run
# 실제 보유 ZIP과 별도로 신뢰한 checksum을 나중에 지정하는 검토 예
# ../.venv/bin/python -B -m bmo_ai.game_assets --archive /APPROVED/PACK.zip --sha256 TRUSTED_SHA256 --dry-run
```

설치 준비 코드는 ZIP traversal·symlink/hardlink·실행 파일·압축 폭탄·중복·checksum·
license/web-content 검토를 검사하고, private staging·원자적 publish·rollback을 제공한다.
일반 실행에는 연결하지 않았으며 이번 실제 ZIP 설치는 0회다. 기존 rollback·백업은
덮어쓰거나 삭제하지 않는다. 실제 게임 코드·라이선스 검토, native/RetroArch hotkey·
출력·게임 패드 검수는 별도다. 소유 프로세스 그룹만 정리하는 기존 runner를 재사용한다.

카메라는 기본 off다. Picamera2/OpenCV는 지연 import와 명시 enable 경계를 유지한다.
이번 검사는 fake SDK로 open/configure/start/read/AF/timeout/cancel/close와 고해상도
still→preview 복구를 확인했다. Camera Module 3의 4608×2592 still을 담을 수 있는
bounded PNG 검증도 합성 파일로 검사한다. 네이티브 라이브러리의 open/close가 OS에서
영구 정지하는 상황을 Python timeout으로 강제 해결한다고 주장하지 않는다. 실제 optional
라이브러리가 이 venv에 없어도 나머지 개발과 모의 검사는 가능하다.

팔은 staging JSON/LF 512바이트 codec·선택 CRC32·fake handshake/ACK/dedup/
timeout/reboot만 검사한다. CRC는 인증·위치 피드백이 아니다. 기존 펌웨어는 바꾸지
않았으며 serial reader/writer·reset·firmware handler가 없다. 좌우 기구 한계·방향·
속도·안전 정지 프로필과 별도 입회 승인 없이 실제 팔 경로를 활성화하지 않는다.
ACK는 위치 도달 증거가 아니므로 `position_unverified`를 유지한다.

IMU는 기존 synthetic bias/saturation/frozen/seq/loss/reboot/hysteresis/cooldown
검사를 재사용한다. 실제 건강은 미승인이고 offset으로 이상을 숨기지 않는다. IMU는
팔 제어·안전장치 입력이 아니며, 팔 준비를 IMU 정상화와 영구 결합하지 않는다.

미디어는 빈 사용자 catalog가 기본이다. 합성 WAV/cue와 표준 라이브러리로 만든 짧은
AVI를 가짜 clock backend로만 검사한다. 실제 AVI codec·영상 표시·사용자 음원 재생은
미검증이다. 기본 WAV/ALSA backend는 영상 codec을 unsupported로 거부한다.
제공된 cue만 같은 playback clock으로 입에 반영하며, cue 없는 음악에 RMS 립싱크를
꾸미지 않는다. 원작 음원·ROM·사용자 영상·노래 생성/크롤링은 추가하지 않았다.

## 진단·검사·복구

```bash
cd /home/bgh/BMO_FEATURES/software
../.venv/bin/python -B -m bmo_ai.offline_doctor --offline
../.venv/bin/python -B -m bmo_ai.desktop_plan --dry-run
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
  ../.venv/bin/python -B -m unittest discover -s tests -q
../.venv/bin/python -B checks/check_offline_shell_browser.py
```

doctor의 READY_FOR_HUMAN_TEST는 로컬 SHA/의존성 메타데이터를 갖췄다는 뜻이다.
테스트/CI/실물 PASS를 추정하지 않는다. credential·장치·포트를 열지 않으며 optional
모듈을 실행하지 않는다. desktop 도구는 dry-run만 지원한다. 데스크톱·autostart·서비스·
OS에 파일을 설치하지 않는다. 공개 CI에는 승인 작화가 없는 텍스트 API fixture만 올린다.

일반 HTTP 큐는 32개, nonce 기록은 512개로 제한한다. 기록을 지워 오래된 동작을
재실행하지 않는다. STOP/cancel은 별도 두 예약 슬롯에 동작별로 합쳐 항상 입장시킨다.
안전 정지는 일반 nonce ledger와 별도이며 같은 정지를 반복해도 새 기능을 실행하지 않는다.
일반 action journal은 4096개에서 fail closed하며 기존 receipt를 보존한다. 포화 시 자동
삭제하지 말고 소유 작업을 종료하고 검토한다. 영속 journal을 주입한 재시작은 미완료
작업을 unknown으로 두며 자동 재생하지 않는다.

되돌릴 때는 새 앱에서 `/quit`로 소유 자원만 정리한다. clean 상태를 확인한 뒤
기존 `feature/bmo-feature-bundle-v1`로 switch하거나, 그대로 보존한
`/home/bgh/BMO_EMOTION/launch-bmo` / `/home/bgh/BMO_INTEGRATION/launch-bmo`를
사람이 필요한 승인 범위에서 선택한다. `reset --hard`, `clean`, force-push, main 병합,
기존 백업 삭제는 필요하지 않다. 과거 stage 보고서는 당시 상태 기록으로 보존한다.
