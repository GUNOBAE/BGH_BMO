# GP8 / Enter PTT + VAD v1

Issue #21의 소프트웨어 구현이다. 기본 입력은 **버튼으로 시작 → 로컬 무음 감지로 종료**다.
Hey BMO/상시 듣기는 구현·활성화하지 않았다. 장치·펌웨어 매핑 및 실제 인식 품질은 NOT_RUN이다.

## 입력과 단일 대화 경로

- 제품 `--simulate`: 합성 16 kHz PCM을 사용하고 STT/GPT/Fish 네트워크 호출은 0이다.
- 제품 `--live`: 새 PTT 마이크는 기본 denied.
  **사용자 입회 후 `--ptt-microphone`을 명시한 경우에만** Enter 요청에서 마이크를 연다.
  환경변수·모델 intent로 권한을 켤 수 없다. 다른 실물 기능의 deny는 그대로다.
- 얼굴/대화에서 Enter 첫 edge가 녹음을 시작한다. key repeat, 같은 키의 iframe/parent 중복,
  250 ms 이내 중복 요청을 합친다. 키 해제는 녹음을 끝내지 않는다.
- 250 ms 뒤의 의도적 다음 누름은 녹음 중 수동 종료(검증된 발화만 STT),
  생각/재생 중에는 취소다. 새 녹음을 자동 연속 시작하지 않는다.
- 게임 메뉴/게임 Enter는 기존 선택·게임 입력이다. PTT가 열리지 않는다.
  GP7 빨간 버튼은 매핑하지 않았고 기존 양팔 토글·펌웨어를 변경하지 않았다.
- USB HID Enter와 키보드 Enter의 물리 장치 구분은 브라우저에서 불가능하다.
  GP8의 현재 펌웨어 매핑은 사용자 입회 확인 전 `physical_verified=false`다.
- Esc/메뉴/창 blur/iframe reload/숨김/종료는 입력 취소를 요청한다.
  이미 대기 중인 PTT도 STOP/cancel 우선 큐가 폐기한다. keepalive 취소는 best effort이고,
  연결 손실 시 기존 서버 heartbeat timeout이 취소를 보완한다.

`IntegrationSession.start_with_input`은 현재 `ConversationWorker`의 입력 단계만 주입한다.
같은 brain, 대화 이력, voice, WAV cache, 얼굴 어댑터를 사용한다. 기존 고정 5초
`IntegrationVoice.listen()`을 다시 호출하지 않는다. 성공한 발화만 같은 STT 함수에 한 번 전달한다.
인사 재생/게임/음악 등 다른 AudioFocus가 있으면 입력을 열 수 없다.

## 오디오 및 VAD 계약

`MicPcmSource`는 승인될 때만 `/usr/bin/arecord` 단일 소유 자식을 시작한다.
장치 기본값은 기존 `plughw:CARD=Device,DEV=0`이며 16 kHz / S16_LE / mono / 40 ms 프레임이다.
20–100 ms 프레임, 짧은 read 조합, 순번·generation·monotonic timestamp,
최대 12초·384000 PCM bytes, 취소·pipe 종료·timeout·자식 reap을 제한한다.
stderr·전체 환경·PCM·인식 문장을 공개 로그에 남기지 않는다.

첫 0.4초의 조용한 구간에서 에너지 기준을 보정한 뒤 0.24초 연속 음성을 요구한다.
0.9초 무음 종료, 시작부터 3초 no-speech timeout, 전체 최대 12초다.
이 값은 초기 개발값이며 실물 보장이 아니다. **RMS는 음량 구간 검출이며 화자/단어 인식이 아니다.**
팬/환경 소음·발화 첫 부분·작은 소리·스피커 피드백에서 실제 튜닝이 필요하다.
출력 중에는 보정하거나 녹음하지 않는다.

검증된 PCM만 private 0600 임시 WAV로 만들어 기존 STT adapter에 한 번 전달하고 삭제한다.
같은 결과 재소비, 취소 뒤의 늦은 transcript, 이전 generation은 거부한다.
no-speech는 STT/brain/TTS 0회다. 대화 raw 오디오/문장을 저장하거나 Git에 올리지 않는다.
프로세스/임시 WAV 정리가 확인되지 않으면 `cleanup_pending`과 fault를 유지하고,
정확한 conversation AudioLease를 보존하여 새 대화·반복·출력을 막는다.
앱 종료의 확인된 cleanup만 lease를 해제한다. 드라이버·동기 STT hang을 강제 종료할 수 있다고 주장하지 않는다.

## 안전한 확인 명령

현재 야간 개발 중 실행한 것은 임시 디렉터리·합성 PCM·fake SDK/player·headless뿐이다.
먼저 장치 없는 상태 확인:

```sh
BMO_PYTHON=/home/bgh/BMO_FEATURES/.venv/bin/python ./launch-desktop --simulate --status
```

실제 마이크/유료 STT·GPT·Fish는 사용자가 깨어서 승인한 다음에만 다음 명령을 사용한다.
키는 기존 private store에서 읽으며 인자로 넣지 않는다.

```sh
BMO_PYTHON=/home/bgh/BMO_FEATURES/.venv/bin/python ./launch-desktop --live --windowed --ptt-microphone
```

검수: idle 입력 off → GP8 실제 HID Enter 확인(GP7 누르지 않기) → 0.4초 조용히 준비 → 발화 →
무음 자동 종료 → 응답 1회 → idle 감정 유지 → repeat에 추가 API 없음 → 게임 중 Enter는 PTT 아님 →
짧은/긴 누름·취소·VNC 포커스/숨김·USB hotplug·앱 종료와 출력 중 녹음 금지를 확인한다.
물리 arecord format·감도·발음·latency는 계측 전 NOT_RUN이다.

## 비활성화와 복구

앱을 정상 종료하고 `--ptt-microphone` 없이 재실행하면 새 live PTT 경로가 꺼진다.
기존 메뉴의 수동 고정 녹음 동작은 PR #18부터 제공되던 별도 사용자 요청 경로다.
전체 복구는 보존된 `/home/bgh/BMO_FEATURES` PR #18 checkout을 사용한다.
desktop/autostart/systemd/OS 설정과 firmware에는 이 코드가 설치되지 않았다.
새 브랜치·Draft PR은 수동 검토용이며 main에 병합하지 않았다.
