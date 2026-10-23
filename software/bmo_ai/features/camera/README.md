# 카메라 기능 모듈 v1

이 모듈의 검증 경로는 **합성 PNG·주입한 얼굴 위치·임시 저장소**다. 실제 카메라,
OpenCV 검출, autofocus, Raspberry Pi 화면·사진 품질은 검수하지 않았다.
기본 tracking/preview는 off이며 import와 생성자는 장치를 열지 않는다.

## 호스트 연결

```python
from bmo_ai.features.camera import CameraFeature, PhotoStore

# private_root는 호스트가 정한 비공개 위치다. 모델/HTTP 요청에서 경로를 받지 않는다.
def handle_camera_target(target):
    if target.status in ('tracking', 'grace'):
        gaze_arbiter.offer('camera', target.x, target.y,
                          generation=target.generation, expires_at=target.valid_until)
    else:
        gaze_arbiter.clear('camera')

camera = CameraFeature(store=PhotoStore(private_root), on_target=handle_camera_target)
camera.set_tracking(True, generation=mode_generation)  # 기본 backend는 합성 DemoBackend
camera.set_preview(True, generation=mode_generation)
state = camera.poll()
png = camera.preview_bytes()  # bytes | None; 외부 shell만 표시
result = camera.take_photo(request_id, generation=mode_generation,
                           cancel_check=host_cancel_check)
camera.off()
camera.close()
```

`generation`은 호스트 ModeManager의 세대다. `Frame.stream_generation`은 broker가
열리고 닫히는 별도 수명이며 호스트 세대와 같다고 가정하지 않는다. 낮은 호스트
세대 명령은 거부하고 종료된 세대의 검출/촬영 결과는 적용하지 않는다.

- `set_tracking(bool, *, generation)`, `set_preview(bool, *, generation)`은 안전한
  상태 dict를 반환한다. `poll()`/`snapshot()`도 동일 형태다.
- `take_photo(request_id, *, generation, cancel_check=None)`는 `PhotoResult`를 반환한다.
  호스트가 카운트다운·취소 UI를 소유하며 모듈은 동기 capture와 저장을 수행한다.
- `cancel_photo(request_id)`는 진행 중 요청에 취소 신호를 보낸다. 신호 수용은
  이미 저장된 사진이 없다는 증거가 아니다. 최종 `completed`/`completion`을 확인한다.
- `preview_bytes()`와 `photo_bytes(photo_id)`는 PNG bytes를 반환한다. 파일 경로를
  노출하지 않으며 결과 JSON에도 이미지·경로·전체 대화는 없다.
- `on_target(GazeTarget)`만 호출한다. 모듈이 `BMOFace`, 표정·입·팔·음성에 접근하지
  않는다. 활동/수동 우선순위와 기본 시선 복귀는 호스트 GazeArbiter가 소유한다.

합성 저장 성공은 `status=simulated`, `simulated=True`, `executed=False`,
`completed=True`, `completion=synthetic_saved`다. PNG 파일 검증 완료를 뜻하며 실제
카메라로 사진을 찍었다는 뜻이 아니다. 실제 backend의 실패/취소와 완료도 분리한다.

## 하나의 센서 소유자

`CameraBroker` 한 개만 backend를 만든다. tracking/preview를 동시에 켜도 open은
하나이며 사진도 같은 backend를 사용한다. 사진 중 `photo_active=True`로 detector
poll을 멈춘 뒤 원래 선택되어 있던 tracking/preview로 돌아간다. 그 사이 off나
mode generation 변경이 오면 이전 옵션을 재활성화하지 않는다.

off는 세대를 무효화하고 capture 취소, latest preview/target 제거, detector와
소유 backend 종료를 수행한다. 별도의 영구 worker·예약 timer를 생성하지 않는다.
캡처와 검출은 호스트가 `poll()`을 호출할 때 수행한다. 실제 장치에서 UI 응답성은
backend 지연과 호스트 worker 배치까지 별도로 검수해야 한다.

게임·음악 등으로 전환하면서 호스트가 `off()`를 호출하면, 얼굴로 돌아와도 자동
켜지지 않는다. 사용자가 tracking/preview를 명시적으로 켜야 한다.

## 얼굴 위치와 유효 시간

`FaceBox`는 원본 영상의 [0,1] 사각형이다. 신원/사람의 시선/embedding을 다루지
않는다. 최초에는 큰 얼굴, 중심 거리, 좌표 순으로 결정하고 이후 가까운 위치와
IoU로 현재 target을 유지한다. 먼 위치는 기본 3회 연속 확인 후에만 전환한다.
target ID는 해당 stream 안에서만 쓰는 임시 위치 번호다.

기본 tuning 값은 `max_age=.5s`, loss grace `.4s`, dead zone `.08`, smoothing time
constant `.18s`, vector speed limit `1.8 units/s`, 최대 poll 10Hz다. 실제 성능·최적값
보장이 아니며 `TrackerConfig`와 `max_hz`로 조정한다. 여러 faces·상실·재등장을
사람의 신원 유지라고 설명하지 않는다.

시계와 seq가 오래되거나 역순/미래이면 새 target을 만들지 않는다. grace에도 마지막
실제 검출의 timestamp와 `valid_until`을 유지하고 만료 시 `x/y=None, status=lost`를
전달한다. 정지 프레임의 seq만 증가해도 이전 timestamp의 TTL을 연장하지 않는다.

회전 0/90/180/270도를 영상 정규화 좌표에 적용하고 mirror를 적용한 뒤 [-1,1] gaze로
변환한다. x 음수는 왼쪽, y 음수는 위다. dead zone → 시간 기반 smoothing → vector
speed limit 순으로 처리한다. 이는 좌표 변환이며 preview PNG 자체를 재작화하지 않는다.
검출 공백 뒤 첫 프레임의 필터 시간은 최대 0.1초로 제한한다. grace 시간 동안
이동 허용량을 누적해 한 프레임에서 큰 폭으로 뛰는 것을 막는 회귀를 포함한다.

## 사진 저장과 재시도

`PhotoStore`는 root 0700, 임시·사진·journal 파일 0600을 사용한다. PNG signature,
CRC, 크기, scanline, 압축 해제 한계와 focus 실패를 검사하고 fsync한 임시 파일을
동일 파일시스템의 atomic hard link로 게시한다. 기존 사진/심볼릭 링크를 덮어쓰지
않으며 실패·취소 전 임시 파일만 지운다. 완료 사진은 자동 삭제/공유하지 않는다.

기본 `JsonPhotoLedger`는 이미지 없는 메타데이터와 request fingerprint만 저장한다.
프로세스 간 flock lease가 reserve→capture→finish를 감싸므로 동시에 같은 요청을
촬영하지 않는다. 성공/실패/취소 결과 재요청은 기존 결과를 반환한다. 이전 프로세스가
running에서 끊겼으면 unknown으로 남기고 자동 재촬영하지 않는다. journal에 키,
전체 문장, 원본 영상은 넣지 않는다. 이 계약이 모든 파일시스템 장애에서 물리적
exactly-once 촬영을 보장하는 것은 아니다.

호스트 공통 journal을 쓰려면 `PhotoStore(root, ledger=adapter)`로 `PhotoLedger`
프로토콜을 주입한다. `begin(id,fingerprint,simulated=...)`는 이전 결과 또는 새 예약의
None을 반환하고 `finish(result)`는 실패해도 lease를 해제해야 한다. 완료된 파일 뒤
메타데이터 쓰기가 실패하면 `photo_saved_journal_pending`으로 저장 사실을 보존하고
재시작 시 미해결 running 요청은 unknown이므로 재실행하지 않는다.

## 실제 backend의 enable 조건과 미검수 범위

`Picamera2Backend(enabled=False)`와 `OpenCVFaceDetector(enabled=False)`는 기본
차단이다. enabled=True 생성자도 import/open을 하지 않으며 실제 open/detect는
감독하의 별도 명시 단계다. 시스템 패키지를 이 모듈이 설치하거나 교체하지 않는다.
현재 Debian 13의 Picamera2/OpenCV/Pillow/libcamera 준비 상태는 확인하지 않았다.

선택적 live adapter는 한 센서 인스턴스·preview configuration·유한 capture/autofocus
wait를 사용한다. libcamera SensorTimestamp를 stream별 상대 host clock으로 매핑하며
두 monotonic 원점이 같다고 가정하지 않는다. 실제 카메라에서 이 경계는 미검수다.
사진은 설정한 preview 해상도이며 full-resolution 촬영 기능이라고 표시하지 않는다.

API 참고: [Raspberry Pi camera software](https://www.raspberrypi.com/documentation/computers/camera_software.html),
[공식 Picamera2 구현](https://github.com/raspberrypi/picamera2/blob/main/picamera2/picamera2.py).
이 문서 확인은 설치/센서/초점/영상 검수 통과를 뜻하지 않는다.

검사: 새 clone의 venv에서 `PYTHONPATH=software .venv/bin/python -B -m unittest discover
-s software/tests -p test_feature_camera.py -v`. 실제 자격증명 없는 child 환경에서
실행한다. 검증은 합성 프레임·임시 PNG 저장·fake detector·취소/디스크 오류이며
실제 카메라·모터·음성·클라우드 호출은 하지 않는다.
