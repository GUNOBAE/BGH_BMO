# 카메라 실기 경로와 팔 차단 조건 — Issue #30

이번 버전은 기존 16×16 합성 녹화를 실물로 포장하지 않는다. 실제 카메라 경로는
`camera/live.py`와 `camera/live_video.py`의 별도 클래스이며, 사용자 입회 옵션을
명시적으로 선택하기 전에는 Picamera2 생성·카메라 활성화·영상 파일 생성이 없다.
실물 촬영·팔 움직임은 이번 자동 개발에서 **NOT_RUN**이다.

## 확인한 사실과 아직 확인하지 않은 사실

2026-10-26 KST의 읽기 전용 `find_spec` 관측에서 **시스템 Python**의 picamera2,
libcamera, cv2, PIL 모듈 검색 결과가 존재했다. 모듈 import나 센서 목록 조회, 센서
open은 하지 않았다. 이것은 선택된 가상환경에서 import가 되는지, Camera3 Wide가
연결·동작하는지 또는 화질·AF·지연이 정상이라는 증거가 아니다. 실행할 Python에서
`probe_camera_capability()`를 다시 확인해야 한다. 자동 설치나 OS 변경은 없다.

현재 마운트된 RP2040 `code.py`는 읽기 전용으로 AST 검사했다. 2,153바이트이며
SHA-256은 `fdc65bebef17b78b945281549ddce80fb749e1a94262751d8d2f0146c394a01e`였다.
이 파일을 복사·수정·재실행하거나 포트를 열지 않았다. 관측된 코드는 다음과 같다.

- GP8은 HID Enter, GP7은 기존 양팔 토글이다. GP7을 TALK나 새로운 host STOP으로 바꾸지 않는다.
- right PWM은 GP10, left PWM은 GP11이며 50Hz다. 보정된 실제 좌우/기계 한계를 뜻하지 않는다.
- 부팅 시 `set_arms(0)`을 호출하고 GP7은 0/180을 토글한다. 재부팅/포트 reset이 안전한 조회가 아니다.
- USB HID를 사용하며 전용 host data parser·개별 팔 명령·watchdog·host hard STOP은 확인되지 않았다.

따라서 현재 host에서 안전한 한 팔 명령을 보낼 수 있는 **검증된 프로토콜이 없다**.
`motion/admission.py`는 `BLOCKED / arm_live_protocol_unimplemented`를 반환한다.
모든 체크리스트와 보정 flag를 True로 만들어도 물리 실행 경로를 열지 않는다.
기존 `MotionFeature`와 JSON/LF 코덱은 여전히 제안 규격/모의 transport이며 실제 펌웨어 API가 아니다.

## 카메라 실행 구조

`LiveCameraOptions(enabled=True, supervised=True)`와 사용자가 선택한 절대 private
사진 경로를 `build_live_camera(options, photo_root)`에 전달하면 `CameraFeature`를
구성한다. 구성 시 장치·폴더를 만들지 않는다. 실제 `take_photo` 또는 명시적
preview/tracking 동작에서 하나의 `LiveCameraBroker`가 센서를 취득한다.

- 사진 기본 1280×720, preview/녹화 기본 320×240. AF job은 2.5초, 사진 job은 5초,
  프레임 job은 1.5초를 상한으로 기다린다. 라이브러리 cancel/flush 후 해당 instance를 닫는다.
- 사진당 8MiB, 선택한 사진 폴더 256MiB, 여유 공간 32MiB를 기본 요구한다.
  개인 사진을 자동 삭제하지 않고 한도에 도달하면 새 촬영을 거부한다.
- 원자적 no-overwrite 게시, SHA-256, private 폴더0700/파일0600와 기존 photo journal을
  재사용한다. 이미지·개인 경로·얼굴 identity를 로그/API 진단에 포함하지 않는다.
- 얼굴 위치 추적은 별도 tracking 선택에만 OpenCV 로컬 detector를 사용한다.
  얼굴 인식/사용자 식별/외부 업로드는 없다. 입회 카메라 선택만으로 tracking을 켜지 않는다.

같은 broker를 `LiveVideoRecorder`에 전달하므로 사진·preview·tracking·녹화가 서로
카메라를 빼앗지 않는다. 녹화는 기존 preview/추적이 센서를 소유 중이면 `camera_busy`로
거부하며, 사용자가 먼저 preview/추적을 꺼야 한다. 매 녹화에도 `consent=True`가 필요하다.

실제 PNG의 RGB 픽셀을 5종 PNG filter까지 복원하여 무손실 BGR DIB AVI에 기록한다.
가짜 색이나 합성 16×16 프레임으로 대체하지 않는다. 기본 2fps/12초, 최대
640×480/4fps/120frame/8MiB이며 무음이다. 압축 H264·고속 영상·오디오 녹화는 아니다.
poll 지연이 있으면 고정 fps로 포장되는 **저속 샘플 영상**이며 실제 시간 동기 품질은
사용자가 확인해야 한다. 새 live video contract·private journal로 합성 저장소와 구분한다.
녹화 폴더 기본 상한은 256MiB이며 보존 파일을 자동 삭제하지 않는다.

종료/취소 시 현재 broker lease만 반환하며, 완료 파일은 보존한다. 전원 상실 뒤 running
journal은 UNKNOWN으로 남겨 같은 request를 자동 재촬영하지 않는다. SDK close가 실패하면
공유 live backend가 UNKNOWN을 유지하며 같은 프로세스의 자동 재open을 막는다.

**제약:** 위 숫자는 job 결과 대기의 상한이다. libcamera의 native configure/start/stop/close가
정지한 경우의 hard deadline은 이번 구현으로 보장하지 않는다. 그런 장애에서 자원 회수
완료를 주장하지 않으며 사용자 점검이 필요하다. 실제 Pi 지연·취소·센서 해제는 NOT_RUN이다.

구현은 Raspberry Pi의 [Picamera2 공식 매뉴얼](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)의
비동기 capture/AF와 PNG 저장 API를 따른다. 공식 문서는 Python 배열의 RGB888 바이트 순서와
파일의 색 의미를 구분한다. 이 코덱은 capture_file이 저장한 PNG를 읽어 자체적으로 BGR AVI로
변환하며, 센서 배열을 RGB라고 가정해 복사하지 않는다.

## 사용자 감독하의 검수 순서

전체 앱의 정확한 명령은 [REAL_ROBOT_TEST_KR.md](REAL_ROBOT_TEST_KR.md)를 따른다.
처음부터 카메라와 팔을 동시에 켜지 않는다.

1. 선택 Python의 카메라 모듈 진단 결과와 private 사진/영상 경로·여유 공간을 확인한다.
2. 사용자 입회 카메라 옵션으로 앱을 실행한 뒤 **사용자가 사진 한 장**을 요청한다.
3. 결과의 `simulated=False`, `executed=True`, `completed=True`, SHA와 실제 화질/색/AF를 확인한다.
4. preview/추적을 껐다가 사용자가 별도 동의로 짧은 무음 녹화를 시작·종료한다.
5. 취소/종료 후 센서 재진입, 앱 종료의 자원 보고를 검수한다. unknown이면 자동 재시도하지 않는다.
6. 팔은 현재 BLOCKED다. 아래 조건이 별도 검토될 때까지 GP7·reset·포트 open으로 우회하지 않는다.

팔의 다음 별도 작업에는 기존 HID 보존, 부팅 무동작, 개별 팔 allowlist 명령,
boot/request dedup·전용 data endpoint, 양팔 독립 범위·속도 제한, 짧은 watchdog,
검수된 STOP, ACK와 실제 위치 미확인 구분이 필요하다. 펌웨어 변경은 사용자가 보는
별도 작업에서만 한다. 외부 서보 전원·공통 GND·전류 제한·전원 무결성·물리 차단 스위치·
좌우 방향/간섭 범위를 먼저 검수하고, 한 팔의 작은 저속 수동 범위부터 확인해야 한다.
IMU는 이 과정의 제어/안전 근거로 사용하지 않는다.

## 오프라인 검증

기존 SDK double을 재사용하여 실제 camera import/open 없이 센서 경계, AF/capture timeout,
private 사진, 원본 픽셀 색과 PNG filter 복원, AVI header/index, broker 충돌, 녹화 동의,
저널 재진입 및 close UNKNOWN을 검사한다. 팔 검사는 체크리스트가 실제 구동을 해제할 수
없음을 확인한다. 모의 PASS는 실제 Camera3/서보 성공을 의미하지 않는다.

```bash
PYTHONPATH=software:software/tests python3 -B -m unittest \
  test_real_camera_backend test_real_camera_host test_real_arm_admission \
  test_camera_video test_video_host_integration test_feature_camera_adapters -v
```

복구는 이 통합 브랜치의 코드 변경만 Git에서 검토 후 되돌리는 방식이다. 원본 펌웨어·
기존 사진/영상·백업을 덮어쓰거나 지우지 않는다. 이 개발 작업에서 서비스·OS·장치 설정을
변경하지 않았으므로 그에 대한 자동 복구 명령도 없다.
