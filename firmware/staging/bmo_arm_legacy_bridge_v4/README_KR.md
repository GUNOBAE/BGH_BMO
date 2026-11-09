# B1 v4 검토 후보 — MPU6050 상대 움직임 입력

이 폴더는 **실물 설치·축별 검수 전 후보**다. Cloud는 RP2040·I2C·서보를 열거나
움직이지 않았다. 기존 v3의 발화 팔 연출/HID/B1을 유지하고 GP0=SDA, GP1=SCL의
MPU6050 읽기만 추가한다. 이 배선은 과거 기록 기준이며 현재 Pi 담당이 전원·배선을
확인해야 한다. 센서 반응은 별도 실제 자료 검증과 설정 없이는 활성화하지 않는다.

기존 GP2–GP8 HID, GP7 수동 팔, GP10/11, 600–2400µs, 50Hz, 좌우 반전,
90°/s·한 업데이트2° 상한과 부팅/수동 팔 코드 원본을 보존한다. 기본
`REMOTE_ENABLED=False`는 그대로다. IMU 조회는 팔 OFF·STOP·모터 fault 중에도
가능하지만 STOP 해제·팔 전원 승인·모터 heartbeat 역할을 하지 않는다.

## 센서 입력과 제한

- 하드웨어 I2C 라이브러리의 동기 전송이 멈출 수 있다는 문제를 피하기 위해
  두 open-drain GPIO의 유한 단계로 I2C를 진행한다. HID와 B1 팔 처리를 먼저 하고
  한 poll에 최대192단계/monotonic-ns 기준1ms 작업 기회만 쓴다. 센서 응답을
  기다리는 동안 sleep·무한 busy wait·별도 serial owner를 만들지 않는다.
- 클럭 stretch는250µs, 한 전송은 대기 구간 포함500ms로 제한한다. 오류 시
  센서 버스만 release하고1초 후 재검출/초기화한다. RP2040 reset·팔 Prepare·
  서보 복귀를 하지 않는다. 타이머가 멈춰도192단계 상한은 유지한다. 실제 GPIO
  수행 시간·HID latency·sensor sample rate는 Pi에서 측정해야 한다.
- WHO_AM_I와0x68/0x69 주소를 실제 확인하고 ±2g/±250°/s를 설정·readback한다.
  센서 clock/DLPF는 고정 설정이며100ms wake 안정 시간을 blocking 없이 둔다.
  14-byte burst의 AX/AY/AZ/GX/GY/GZ signed16만 보내고 온도는 축에 섞지 않는다.
  bias 차감·축 재보정·norm·3D 자세·방향·낙하·팔 안전 판단을 만들지 않는다.
- `READY`는 센서 레지스터 바이트가 읽혔다는 뜻이며 **센서 건강·어느 축의
  사용 승인도 아니다**. 포화한 값도 원래 raw 값으로 전달한다. 읽기 실패·미수신·
  오래된 값은 여섯 축 모두 `n`이며 고장 축을0으로 채우지 않는다. 원래 ABSENT/
  BUS_ERROR를 STALE로 덮어 상세 원인을 없애지 않는다.
- 현재 I2C 주소/WHO identity는 `I68A68` 같은 값이며 unit serial이 아니다.
  같은 주소의 다른 센서·배선·장착 상태를 구별하지 못하므로 센서/배선/장착 변경
  후에는 축별 상대 변화 검수를 다시 해야 한다.

## 같은 B1 소유자의 읽기 전용 계약

```text
B1 IMU <request_id>\n
B1 IMU <request_id> <boot> <seq> <timestamp_ms> <status> <AX> <AY> <AZ> <GX> <GY> <GZ> A2 G250 <identity>\n
```

응답은 최대256bytes, status는 WARMUP/READY/ABSENT/BUS_ERROR/STALE,
identity는 actual WHO/address의 `I68A68`/`I68A69` 또는 미관측 `In`이다. raw 축은
signed16 또는 `n`. seq·timestamp는 새 burst 완료 시만 증가/갱신한다. timestamp는
보드 monotonic millisecond의32bit wrap이며 스피커·사람 동작 시각과 같다는 뜻이 아니다.

새 IMU command는 cached snapshot만 읽고 I2C를 직접 수행하지 않는다. unsolicited
serial packet이 없으므로 기존 ACK를 밀어내지 않는다. motor ACK cache/high-water/
watchdog는 조회로 바뀌지 않는다. 기존 HELLO/CAPS/ACT/END/PULSE/DO/STOP/STATUS/
PREPARE 계약은 유지되며 IMU 실패가 bridge fault를 만들지 않는다.

## 정확한 후보와 설치·복귀

- `code.py` 기본OFF SHA256:
  `890bc2642beb1f5b87be3158692f8cac826709aa71b343585697c48d8699d033`
- 별도 기존 motor 사용 승인을 유지하는 code SHA256:
  `137b4cd1ce3395fec0b4de5c8af126f5655a6a443b5ae4205808e37084c340e1`
- `bmo_arm_bridge.py`:
  `3ba074229b36d43bf499e6e4cb2723e9f9c672ecb933b9769cde7250f43747db`
- `bmo_imu.py`:
  `f49fe1672375bbac38fa2f3fc4ae54a87740f6314eae0638a374dabeb32fcc0d`

저장소의 `boot.py`와`original_code.py`는 기존 v3와 같다. 설치 도구와 실제 IMU
backend는 다음 **두 정확한 boot 변형만** 지원하며 현재 설치된 바이트를 보존한다.

- canonical legacy v1–v4: `1540f755a400f40babdf0457481b77559855ee95018092901b42d44baeacdbea`
- 기존 supervised v1: `f59e20f97feef6eab364bdb305cb3d7d0639c7f3aa88a0f5e3ff517912c122eb`
  (`firmware/staging/bmo_arm_supervised_v1/boot.py`의 원본 바이트)

두 파일의 차이는 검토된 주석/docstring이다. 일반 주석 제거·정규화로 다른 파일을
허용하지 않는다. `review.json`, prepare된 `boot.py`, backup, 적용 후 검증과
실제 IMU profile binding에는 **현재 설치된 boot의 해시**를 사용한다. apply와
rollback은 boot를 덮어쓰지 않으며, 검토 뒤 다른 known 변형으로 바뀌어도 거부한다.

Pi용 `bmo_ai.imu_candidate`의 기본 dry-run/prepare로 현재 정확한 v1/v2/v3 조합과
차이를 검토한다. v1에서 v4로 바로 준비할 수 있으며 v3 선행 설치는 필요 없다. 기존 파일과
해시를 private backup에 남기고 새 센서 helper→bridge→code 순으로 적용한다.
여러 파일의 교체는 원자적이지 않다. 일부 실패 시 전원 격리를 유지하고 같은
검토 후보의 명시적 rollback으로 기존 정확한 조합을 되돌린다. 모르는 파일이나
unreviewed boot는 덮지 않는다. 센서용 파일도 정확히 이 후보가 추가한 바이트인
경우만 rollback에서 제거한다.

펌웨어 쓰기는 기존 부팅 PWM을 실행할 수 있다. **현재 입회·서보 전원 격리·공간
확보·즉시 전원 차단·auto-reload 위험 확인**이 실제 충족될 때만 Pi 담당이 apply한다.
과거 감독 확인을 true로 저장하거나 재사용하지 않는다. 기본 sensor-only 후보는
motor 사용을 켜지 않는다. 이전 exact-enabled 사용을 별도 보존하는 경우에도 새
code/helper/센서 SHA 검토와 재검수가 필요하다.

## Pi 축별 검수와 제품 확인

1. 목표/설치/runtime/같은 BMO Candidate 아이콘 SHA, 보드 세 파일 SHA, CDC
   identity와 현재 GP0/GP1 배선을 기록한다. MPU6050 raw 전송과6축 건강 상태를
   구분한다. 과거 AX 포화·다른 축 편향/self-test 실패를 무시하지 않는다.
2. 각 축의 stationary raw 범위·편향·반복/고정·포화·누락을 따로 관찰한다. 정확한
   좌표/중력/자이로 방향을 추정하지 않는다. 센서 전체가 degraded여도 반복된
   실물 상대 변화와 안정 복귀를 따로 검증한 축만 해당 반응에 사용할 수 있다.
3. 팬만/스피커만/팔만/가벼운 몸체 이동/다시 안정 순서의 분리 trial로 false
   reaction·변화 폭·지연을 기록한다. 핀이나 센서 이동 없이도 방향 대응은 별도
   확인이 필요하다. 방향 확인 전에는 기울임에 따른 시선을 활성화하지 않는다.
4. IMU ON/OFF와 설정 재실행, 연속 움직임1회 묶음, stale/재연결 후 warmup,
   음악·녹음·대답 중 음성 비끼어듦을 확인한다. 센서 제거/불량은 IMU 반응만 멈추고
   대화·음악·GP7/GP8·기존 팔 동작과 STOP latch가 계속 동작해야 한다.

소프트웨어 검사는 injected register/GPIO/CDC/HID/PWM 경계에서 수행한다.
실물 센서 성공·축 사용 승인·음향·팔 움직임·팬 진동 구별을 PASS로 대신하지 않는다.
