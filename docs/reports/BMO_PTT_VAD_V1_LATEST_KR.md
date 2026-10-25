# BMO PTT / VAD v1 구현·검증 보고

작업서 [#21](https://github.com/GUNOBAE/BGH_BMO/issues/21), 마스터 [#23](https://github.com/GUNOBAE/BGH_BMO/issues/23).
제출 [Draft PR #26](https://github.com/GUNOBAE/BGH_BMO/pull/26), `feature/bmo-ptt-vad-v1`.
base는 [PR #25](https://github.com/GUNOBAE/BGH_BMO/pull/25)의 `9325963a6a457673b922f890f965131ccb305ee4`이며,
이는 PR #24의 검증된 `4a80bc8` 진행률/환영 캐시를 인수한다.
Python 통합 코드 `dddccec74198e456b693039b1e20801123e87f16`,
최종 브라우저 경합 수정 `bf599ea04181270b92a1f2bf64e4b71092ec5195`.

## 구현한 경로

GP8/키보드 Enter → 단일 `ConversationWorker` input provider → finite PCM source →
기존 `PcmTurnGate` 기반 RMS VAD → private WAV → 기존 STT 한 번 →
같은 brain/session/IntegrationVoice/얼굴. 고정 5초 녹음을 다시 호출하지 않는다.
Luna low, Aaron Soft/beemo/gain .25, 기존 Piper opt-in, R10/v13.2와 대화/반복 캐시는 유지했다.

실물 입력은 `--live --ptt-microphone` 명시 허용 뒤의 버튼 요청 때만 가능하다.
시작·idle·simulate는 실물 입력 0이다. 게임 Enter는 기존 선택/게임 키이고 GP7은 건드리지 않았다.
현재 GP8 펌웨어 및 HID 장치 식별은 미검수, 브라우저는 키보드 Enter와 구분하지 못한다.
Hey BMO 모델/상시 대기는 disabled다.

최초 누름으로 시작, 250 ms 이내 중복 합침, 다음 의도적 누름은 입력 중 수동 종료 또는
생각/재생 취소다. STOP/Esc/메뉴/blur/숨김/iframe reload/종료는 안전 취소가 우선한다.
UI는 승인 HTML을 수정하지 않고 작은 외부 상태 및 고정 오류만 표시한다.

소스는 16kHz/S16_LE/mono/40ms, .4초 calibration, .24초 speech, .9초 silence,
3초 no-speech, 12초/384000bytes 상한이다. RMS 음량 검출은 화자/단어 인식이 아니다.
초기 개발값이며 실물 튜닝은 완료하지 않았다.

## 검증

| 검사 | 결과 |
|---|---|
| Python 전체 기존 901 + PCM/VAD 47 + session 통합 22 | **970 PASS**, skip 0, 로컬 74.213초 |
| Desktop 기존 headless (public/R10/v13.2 각7) | **21 PASS** |
| 새 PTT browser E2E (public/R10/v13.2 각9) | **27 PASS** |
| Python 3.11/3.13 초기 통합 CI unit | **970 PASS** 각 버전 |
| 최종 GitHub CI | PR #26 checks 및 상위 야간 인계서의 최종 HEAD/run 참조 |

합성/가짜 arecord E2E에서는 실제 harmless Python 자식의 raw PCM pipe를 읽고 VAD 뒤
기존 `audio.transcribe`를 fake SDK에 한 번 연결했다. 실제 마이크·네트워크는 사용하지 않았다.
22개 통합 검사는 STT1/무음0, 같은 brain/cache, 취소한 늦은 STT,32개 포화 큐 STOP,
게임 중 PTT 금지, listening/thinking 기존 감정 및 player 시작 때 새 감정,
repeat의 추가 STT/합성0, 종료와 불확실한 정리 시 lease 보존을 검사한다.

각 PTT headless: host1, greeting1, PCM source3/close3, STT1, brain1, 합성 응답1,
player3/reap3, fake browser1/reap1, 최대 mic/player 각각1, 활성 mic 종료0, 금지 IO0.
3개 PCM 중 정상 발화1, 취소1, fake missing mic1이다. queued blur 취소는 source를 열지 않는다.
원본 R10와 v13.2 전후 SHA가 각각
`8c3ef98ba0e0645213c95295a24d7353fc2865b5ad9bc85390360c4ec9b78c82`,
`38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`로 동일하다.

## 발견하고 수정한 오류

- selector setup/close 실패가 자식 정리를 건너뛰던 경로: 각각 정리 및 private ownership 유지.
- WAV unlink 실패: 파일 소유권 보존/close 재시도, 새 캡처 차단.
- 입력 정리가 불확실한 finished: conversation lease 유지, 새 대화/repeat/출력 차단.
- 수신 idle snapshot으로 queued PTT가 취소 대상에서 사라지던 경합:
  정확한 HTTP command request receipt가 끝날 때만 pending 해제.
- 마이크 실패 무안내: 작은 고정 한국어 오류와 시작 요청의 최종 실패 receipt.

초기 CI의 unit 두 버전은 통과했으나 public browser가 queued-blur 검사를 실패했다.
Pi R10에서도 같은 경합을 재현해 위 요청 receipt 방식으로 수정했고 세 화면 모두 재검증했다.
테스트 조건을 제거하거나 단순 재실행으로 통과 처리하지 않았다.

## NOT_RUN / 복구

실물 USB mic/HID GP8/ALSA format·VAD 품질·스피커 피드백·유료 STT/GPT/Fish·실제 VNC focus/hotplug,
카메라/서보/IMU/펌웨어·desktop 설치는 **NOT_RUN**이다. 기존 live 대화 사용자 성공 보고와 구분한다.
물리 backend의 native hang과 동기 SDK를 강제 종료할 수 있다고 보장하지 않는다.

수동 검수/비활성화 절차는 [PTT_VAD_KR.md](../../software/PTT_VAD_KR.md).
앱을 정상 종료하고 `--ptt-microphone` 없이 실행하면 새 live PTT를 끈다.
기존 `/home/bgh/BMO_FEATURES` PR #18 checkout은 clean 상태로 보존했다.
새 코드의 서비스/OS 자동 설치나 main 병합은 하지 않았다.
