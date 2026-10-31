# Character Life v1 — 실제 장치 검수 결과 (2026-10-29 KST)

**후속 분석:** 아래 실물 검수 결과와 당시 비용/요청 수는 보존한다. 이후 새 장치 검사 없이 저장 WAV를 분석해 [PTT VAD 후보·실제 무료 Aaron 6개·공개 ONNX 준비](REMAINING_CORE_20261029_KR.md)를 진행했다. 뒤 단계의 무료 8요청과 이전 장치 검수의 API 0회는 시점이 다르다.

## 범위와 근거

[사용자 권한 갱신](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6473188150)에 따라 `/home/bgh/BMO_CHARACTER_LIFE`에서 기존 구현을 이어서 검사했다. 기반 HEAD는 `492472af425083ba7337abf3b577b092f051a5e5`이며, 실기 중 발견한 오류를 이 worktree에서 수정했다. 최종 제출 SHA와 해당 CI는 PR #40의 완료 댓글에 기록한다. 과거 `NOT_RUN` 기록은 당시 상태이며 이번 결과와 구분한다.

원본 `/home/bgh/BMO_REAL_ROBOT`의 코드·가상환경·서비스는 변경하지 않았다. 장치를 점유하는 기존 BMO 앱은 확인되지 않아 종료한 운영 앱이 없다. 기존 PipeWire/WirePlumber의 PID 1531/1537과 미디어/control fd는 보존했다. 전역 종료·서비스 변경·장치 강탈·운영 앱 재시작·자동 배포·main 병합은 하지 않았다.

원본 녹음/사진/영상 및 상세 JSON은 아래 **로컬 비공개 경로만** 사용한다. Git에는 검수 코드·회귀 테스트·이 요약만 제출한다.

```text
/home/bgh/.local/state/bmo/device-acceptance/20261028T154305Z
```

검수 폴더는 0700, 증거 파일은 0600이다. 외부 STT/GPT/Fish 전송·무료 샘플 요청·신규 유료 요청은 모두 0회, 신규 API 지출은 USD 0이다. 아래 PCM·영상 PASS를 실제 사람의 발화/청취/추적 승인으로 확대하지 않는다.

## 결과 구분

| 항목 | 결과 | 확인한 범위 / 남은 범위 |
|---|---|---|
| 실제 마이크 장치/PCM | `MIC_DEVICE_PASS` | 후보 Host → PTT → MicrophoneBroker → 기존 MicPcmSource. 실제 비영 PCM·포맷·취소·재열기·정리. 첫 시험의 상한 종료 오류는 보존하고 수정함 |
| 실제 카메라 프레임/사진/영상 | `CAMERA_CAPTURE_PASS` | 기존 Picamera2/candidate Host, 사진 3장 및 영상 1개, 파일 디코드·실제 SensorTimestamp 진행·닫기/재열기 |
| 스피커 명령 | `SPEAKER_COMMAND_PASS` | 고정 저레벨 로컬 시험음 0.6초 정확히 1회, 기존 Python player/APLAY, 종료·회수 |
| 마이크로 받은 시험음 | `LOOPBACK_PASS` | 1200 Hz 시험음이 실제 녹음에 검출됨. 명령 성공과 별도 수치 검사이며 Aaron 음질 승인이 아님 |
| 실제 호출어 모델의 저장 WAV 추론 | `NOT_RUN / MODEL_MISSING` | 검증된 classifier/frontend가 없음. 다른 pretrained 호출어로 대체하지 않음 |
| 실제 모델의 실물 마이크 호출어 시험 | `NOT_RUN / MODEL_MISSING` | 상시 reader나 5분 시험을 시작하지 않음. 사람의 Hey BMO/헤이 비모 인식률도 NOT_RUN |
| wake OFF → 유한 PTT | 제한된 실제 경로 `PASS` | 모델 없는 OFF 상태에서 실제 PTT 녹음 1회. READY 모델의 persistent reader 종료/자가 호출 음향 시험은 NOT_RUN |
| B1 명령 전달 | `B1_TRANSPORT_PASS` | 비구동 HELLO 1회에 정확한 DISABLED ACK, serial 닫힘. PING/HOLD/STOP/DO는 전송하지 않음 |
| 실제 팔 이동/각도 | `NEEDS_OPERATOR / NOT_RUN` | 서보 연결·전원·이동 공간을 추측하지 않음. ACK를 움직임 성공으로 기록하지 않음 |
| 실제 STT/GPT/Fish 대화/지연 | `NOT_RUN / COST_POLICY_USD_0` | 녹음이 클라우드로 가지 않도록 local empty sink와 denied client/voice 경계 사용 |
| 사람의 판단 | `NOT_RUN` | 문장/첫 음절/VAD 정확도, Aaron 감정·Beemo 발음·자연스러움, 사람 얼굴 추적 품질, 물리 팔 이동 |

## 마이크: 10초 이하 3회, 추가 녹음 없음

기존 USB `plughw:CARD=Device,DEV=0`을 사용했다. 전달 PCM/WAV는 16 kHz, mono, S16_LE다. 실제 열린 동안 `/proc/asound/card3/pcm0c/sub0/hw_params`를 읽어 **하드웨어 ALSA 협상은 48 kHz/mono/S16_LE, period 6000 samples, buffer 24003 samples**임을 확인했다. plughw 변환 뒤 받은 16 kHz와 구분한다. ADC의 물리 클럭을 외부 계측한 것은 아니다.

| 회차 | 용도 | 저장 PCM 길이 | Host PTT 요청→첫 완전 PCM | 실제 종료와 결과 |
|---|---|---:|---:|---|
| 1 | baseline 수신 | 9.84초, 246 frames | 151.118 ms | 실제 PCM은 정상이지만 이전 코드가 10초 상한의 부분 버퍼를 `pcm_truncated`로 오인. 당시 원본 결과 `MIC_DEVICE_NOT_VERIFIED`를 보존 |
| 2 | 명시적 취소·닫기/재열기 | 0.84초, 21 frames | 138.920 ms | `MIC_DEVICE_PASS`, `user_cancelled`, 전체 검사 1.0847초, 정리 완료 |
| 3 | wake OFF 후 재열기·시험음 루프백·상한 | 9.84초, 246 frames | 147.247 ms | `MIC_DEVICE_PASS`, source는 `pcm_duration_limit / wall_duration`, 부분 1120 bytes 폐기 기록. 10초 budget에 의한 취소 및 정리 완료 |

세 회차 모두 실제 비영 샘플을 받았고, 클리핑 샘플 0개, 전달 프레임 길이/sequence/수신 시각 오류 0개였다. 전체 0인 PCM을 조용한 방으로 처리하지 않았다. 위 첫 프레임 값은 **소프트웨어 Host 요청과 첫 PCM 수신 시각**이며 물리 GP8 접점·ADC 준비·스피커 첫 소리 지연이 아니다.

첫 회 RMS는 0.012826, frame RMS 최저는 0.009185였고 기존 threshold 0.008을 넘었다. 따라서 이번 기록을 “조용한 방 no-speech 성공”이나 사람 음성 검출 성공으로 바꾸지 않는다. 이 조건에서는 연속 threshold 초과가 상한 종료를 유발했다. threshold/silence/calibration을 자동 조절하지 않았다. 실제 발화와 문장 끝 인수는 사람이 말하는 검수가 남는다.

호스트 전달은 최대 약 125.026 ms의 burst 간격을 보였다. 실제 하드웨어 period 6000/48000=125 ms와 함께 기록하지만, 소프트웨어 sequence가 연속이라는 사실로 드라이버 드롭 0을 주장하지 않는다. **드라이버 dropped frames 및 xrun은 미측정**이다. 기존 source는 stderr를 버리므로 xrun 문자열을 사후 복원할 수 없다. 계획된 끝의 부분 버퍼 폐기는 별도로 기록하며 드라이버 손실로 세지 않는다.

각 시도는 실제 source를 한 번만 열었다. 모든 STT/GPT/Fish/voice 실행은 차단했고 원본 WAV는 로컬에만 남겼다. 회차 3의 당시 JSON은 `EXPLICIT_CANCELLED`라고 표시했지만 `deadline_reached=true`인 자동 budget 종료였다. 해당 원본은 수정하지 않았고 이후 helper 분류를 `BOUNDED_DEADLINE_CANCELLED`로 고쳤다. 캡처 오류 코드는 이 분류로 덮지 않는다.

## 카메라: 기존 사진 보존 후 남은 단계만 계속

기존 Picamera2 0.3.37/libcamera/Camera 3 Wide 경로를 사용했다. 초기 후보 Host에서 실제 사진 **3장(각 1280×720 PNG)** 저장·해시·디코드와 사진 countdown 취소가 성공했다. 그 후 tracking 단계가 BLOCKED였고 Host cleanup은 성공했다. 이 첫 실행의 wrapper가 전체 반환 JSON을 보존하지 않아 당시 정확한 오류 코드를 사후 단정하지 않는다.

조사에서 Debian OpenCV 4.10.0에 `cv2.data` 속성이 없고, 설치된 `/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml`은 정상 로드됨을 확인했다. 기존 detector의 wheel 전용 경로 가정을 수정했다. OS/패키지/카메라 설정 파일은 바꾸지 않았다. SDK tuple metadata 처리는 방어적으로 추가했으며, 설치된 capture_file의 실제 dict 계약과 구분한다. 첫 실패를 tuple 문제였다고 보고하지 않는다.

사진을 다시 찍지 않았다. 후속 helper는 기존 3장의 저널·바이트 수·해시·디코드, 빈 video 경로를 검증하고 1회 resume marker를 만든 후 **새 still 0회**로 나머지만 수행했다. 전체 결과는 이제 private exclusive JSON에 자동 저장된다.

| 후속 관찰 | 실제 결과 |
|---|---|
| tracking 프레임 | 실제 SensorTimestamp 3개가 엄격히 증가, span 1341.946 ms |
| 대상 | `TARGET_NOT_VISIBLE` — 가려진 조건이며 얼굴 추적 품질 PASS가 아님 |
| preview OFF/재열기 | PASS, 해당 stream의 SensorTimestamp 2개 증가 |
| 실제 영상 | 320×240, RGB AVI, 2 fps 선언, **5 frames / 2.5초**, audio streams 0 |
| 영상 검증 | 기존 codec·SHA 및 ffprobe PASS. 실제 frame metadata 5개 증가, source span 2214.210 ms |
| SDK 정리 | 후속 단계 camera 생성/stop/close 각각 3회. sensor_closed=true, photo_active=false, close_unknown=false |
| 금지 경로 | API 0, voice 0, motion submit 0 |

영상 목표 상한은 3초였으며 파일의 실제 길이는 2.5초다. 3초를 실제 길이로 기록하지 않는다. 최초 3장의 개별 SensorTimestamp는 전체 결과가 저장되지 않아 `UNMEASURED_PRIOR_BATCH`로 남긴다. 영상의 비슷한/어두운 픽셀만으로 frozen 판정을 하지 않았으며, stale SensorTimestamp 거부는 그대로 유지했다.

저장된 실제 PNG 하나를 로컬 OpenCV로 다시 읽었을 때 detected_count=0이었다. 추가 촬영 없이 **명시적인 소프트웨어 seed 표적**으로 production TargetTracker/Host target callback/GazeArbiter의 `tracking→grace→lost→중앙(0,0), camera owner 해제`를 검사했다. 이 시간은 simulated monotonic이며 실제 사람 얼굴의 소실/시선 성능이나 지연을 측정한 것이 아니다.

## 스피커: 1회 재생과 실제 루프백을 구분

기존 `plughw:CARD=MAX98357A,DEV=0`과 `IntegrationVoice._play`/기존 owned APLAY 경계를 사용했다. 1200 Hz, 0.6초 로컬 시험음의 source peak 0.08에 고정 gain 0.25를 한 번 적용하여 peak 상한 0.02 FS로 재생했다. 믹서·OS volume·gain을 높이거나 반복 재생하지 않았다. 최대 2회 중 **1회만 사용**했다.

`SPEAKER_COMMAND_PASS`: player command 1회, player reaped=true, voice_closed=true, AudioFocus 반환. 프로세스 시작 콜백은 실제 소리 시작 시각의 증거가 아니다.

`LOOPBACK_PASS`: 세 번째 실제 마이크 WAV와 첫 baseline WAV를 외부 전송 없이 로컬 비교했다. 사전에 정한 1200±20 Hz, baseline 대비 최소 6 dB, 인접 대역 대비 최소 9 dB, 연속 3개 window 기준을 적용했다. 관찰 최대 증가 52.675 dB, 최대 인접 대역비 45.907 dB, 연속 39개 window였다. 녹음을 재생하거나 증폭해 다시 시험하지 않았다. 실제 음향 시작 지연·Aaron의 발음/감정/자연스러움은 미측정·미승인이다.

## 호출어와 팔

실제 BMO custom classifier/frontend 및 선택 런타임이 없으므로 저장 WAV 추론과 실물 마이크 호출어 시험은 `MODEL_MISSING / NOT_RUN`이다. 위 tone loopback은 호출어 모델 검사가 아니며 제품의 self-wake 억제를 끄지 않았다. 세 번째 캡처는 `wake_enabled=false`, `reader_alive=false`, `microphone_open=false`, `PTT_ONLY / detector DISABLED`를 확인한 뒤 실제 finite PTT를 한 번 열었다. READY 모델의 persistent reader 해제와 실제 자가 호출 억제는 검증하지 않았다.

B1은 기존 data interface `/dev/ttyACM1`에서 HELLO **1회만** 보내 `DISABLED / POSITION_UNVERIFIED` ACK를 검증하고 0.0143초에 포트를 닫았다. 소프트웨어 명령에서 모터 동작은 0회다. 파일의 `REMOTE_ENABLED=False`와 실제 DISABLED 응답을 확인했으며 enable/restore/write/reset하지 않았다. 600–2400µs/50Hz/좌우 반전/GP7/GP8 및 원본·백업은 보존했다. watchdog/취소/중복 방지의 장치 없는 회귀는 유지하지만 이번 실제 통신으로 그 물리 기능이나 팔 이동을 승인하지 않는다. [상세 비구동 통신 기록](DEVICE_ACCEPTANCE_ARM_WAKE_KR.md)

## 종료와 개발 환경

모든 검수 Host/마이크/broker/player/serial을 닫았다. 마지막 `/proc`·ALSA 관찰에서 arecord/aplay/ffprobe 자식과 검수 serial 소유자가 없고, USB capture/I2S playback 상태는 모두 `closed`였다. PipeWire/WirePlumber의 기존 media fd는 남아 있는 것이 정상이며 해당 서비스를 종료하지 않았다. 운영 앱을 자동 재시작하거나 밤에 인사를 재생하지 않았다.

기존 `.venv`/`.venv-camera`를 수정하지 않고 후보의 `.venv-device`만 추가했다. Debian 카메라 확장과 같은 Python 3.13을 쓰는 system-site 환경에 저장소의 개발 lock 의존성을 설치했다. Picamera2/libcamera/cv2/numpy/OpenAI/Playwright 모듈이 확인된다. `.venv`의 pip check는 PASS다. `.venv-device`의 전역 pip check는 system-site에서 보이는 apt-listchanges/debconf 및 무관한 type-stub metadata의 누락 경고를 반환한다. 이를 OS 수정이나 무관한 패키지 설치로 고치지 않았으며 후보의 장치 실행 성공·앱 의존성 확인과 구분한다.

## 소프트웨어 회귀와 제출

최종 후보 Python 3.13.5 전체 **1474개 PASS (85.217초)**, compileall 및 기존 isolated `.venv` pip check PASS. 기존 shell/Desktop/PTT/media/release 5종 42그룹과 Character Life 공개/R10/v13.2 각 9그룹을 순차 실행하여 **브라우저 8회·69개 검사 그룹 PASS**였다. 이는 동일 시나리오의 얼굴 profile별 반복을 포함하며 고유 69개 기능이라는 뜻이 아니다. 승인 얼굴 SHA는 검사 전후 일치했다.

예정된 부분 프레임 끝/비정상 EOF 구분, 실제 cadence 재현, raw PCM과 PTT 결과 분리, Debian asset fallback, 재촬영 없는 camera resume, 원본 보존·비공개 저장, tone 상한·루프백 오탐 방지 회귀를 추가했다. 최종 HEAD의 Python 3.11/3.13 CI는 완료 시 PR #40 댓글에 확정한다. CI 자체는 실제 Pi 장치를 실행하지 않는다.

## 다음 사용자 검수와 복귀 명령

추가 무인 캡처/재생은 종료했다. 다음에는 (1) 사람이 실제 문장을 말해 첫 음절/머뭇거림/문장 끝과 현재 VAD threshold를 확인, (2) 기존 Aaron 음성의 Beemo 발음·angry/crying 자연스러움 청취, (3) 카메라 덮개를 벗긴 뒤 사람 얼굴 위치 시선 품질, (4) 현재 연결·이동 공간·전체 전원 차단 접근을 확인한 **단 1회 감독 팔 시험**만 구분해 진행한다. 호출어 정확도는 실제 승인 모델 준비 후 별도 검수다. 유료 대화/생성은 현재 USD 0 자동 검수에 포함하지 않았고 별도 사용 허용이 필요하다.

후보를 자동 실행하지 않았다. 사용자가 현장과 API 사용 조건을 확인한 후 쓸 명령이다. 팔은 기본 OFF이며 카메라는 요청 버튼을 눌러야 연다.

```sh
cd /home/bgh/BMO_CHARACTER_LIFE && BMO_PYTHON="$PWD/.venv-device/bin/python" ./launch-desktop --live --character-life --ptt-microphone --ptt-trace --idle-voice --camera-supervised --photo-directory "$HOME/.local/state/bmo/user-camera/photos" --camera-video-supervised --video-directory "$HOME/.local/state/bmo/user-camera/videos"
```

복귀는 후보 메뉴→종료 후 변경하지 않은 기존 실행본을 사용한다. 서비스/펌웨어 재시작이나 Git reset은 필요하지 않다. 이 복귀 명령 역시 자동 실행하지 않았다.

```sh
cd /home/bgh/BMO_REAL_ROBOT && BMO_PYTHON="$PWD/.venv-camera/bin/python" ./launch-desktop --live --windowed --ptt-microphone --ptt-trace
```
