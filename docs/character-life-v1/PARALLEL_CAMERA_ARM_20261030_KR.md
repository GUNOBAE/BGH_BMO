# Character Life와 카메라·팔을 한 앱에서 진행

2026-10-30 사용자 요청으로 대화 수정과 카메라·팔 통합을 병렬로 진행한다. 기존 `BMO_REAL_ROBOT`을 교체하지 않고 후보 `BMO_CHARACTER_LIFE`에서 같은 Desktop/FeatureHost를 사용한다.

## 현재 연결과 이번 수정

- 실제 사진·영상·얼굴 위치 추적은 이미 `desktop_app.py` → `FeatureHost` → 한 `CameraBroker`에 연결돼 있다. 직전 현장 검수 명령에는 카메라 옵션이 없어서 이 경로가 활성화되지 않았다.
- 새 `launch-character-life`는 후보 `.venv-device`와 승인 R10, 기존 Luna low/Aaron/Beemo/gain 0.25, GP8/WebRTC, 요청 시 사진·영상을 한 번에 선택한다. 두 번째 앱·플레이어·마이크·카메라 소유자를 만들지 않는다. 실행만으로 카메라를 열거나 촬영하지 않는다.
- 기본 사진 경로는 `~/.local/share/bmo/photos`, 영상은 `~/.local/share/bmo/videos`다. 생성·권한·저장 한도 검사는 기존 저장 모듈이 실제 요청 시 수행한다. 기존 CLI 옵션으로 다른 개인 경로를 명시할 수 있다.
- “Take my picture please”, “Can you take a photo?”, “사진 찍어줘” 같은 짧고 직접적인 현재 요청을 로컬에서 확인해 같은 사진 경로로 연결했다. 사진 준비 대답이 끝난 뒤 기존 취소 가능한 3초 카운트다운을 사용한다. 모델이 임의로 출력한 photo intent만으로는 허용하지 않는다.
- B1 팔 adapter와 명명된 gesture도 이미 같은 앱에 연결돼 있다. 마지막 실제 기록은 `REMOTE_ENABLED=False`, 서보 분리, `HELLO → DISABLED`다. 이는 통신 확인이며 팔 움직임 완료가 아니다.

## 후보 실행

기존 앱을 정상 종료한 뒤 Pi 바탕화면 터미널에서 실행한다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE && ./launch-character-life
```

기존 호출어 후보도 함께 사용하려면:

```bash
cd /home/bgh/BMO_CHARACTER_LIFE && ./launch-character-life \
  --wake-word --wake-question-wait 5 \
  --wake-model-manifest /home/bgh/.local/state/bmo/wake-model-candidates/amrmantawi-bmo-openwakeword-58eb9404df18/manifest.json
```

사진은 GP8 대화로 **“Take my picture please.” / “사진 찍어줘.”**를 말하거나 기존 Desktop 사진 조작을 사용한다. 영상·추적은 기존 UI의 명시적 조작을 유지한다. 호출어를 켰다면 listening 표시 뒤 같은 사진 요청을 말할 수 있다.

음성 사진 요청은 현재 마지막 user 메시지의 전체 문장을 제한된 문법과 비교한다. 인용·과거 이야기·부정·가정·미래·복합 명령은 자동 허용하지 않는다. 해당 문법 밖의 표현은 기존 UI를 사용한다. 모델 JSON에는 허가 필드가 없으며, 과거 사진 요청·repeat·잘못된 metadata는 촬영 권한이 되지 않는다. 대화 취소·실패·세대 변경은 기존 실행 조건을 그대로 적용한다. 기존 모델만의 `take_photo`/tracking 요청은 계속 `camera_human_action_required`로 차단한다.

이 연결의 STT/LLM/카메라 SDK 경계는 주입한 테스트 값으로 검증했다. 실제 발음 인식·실물 촬영 성공은 Pi 검수 전까지 미확인이다. 사진을 GPT로 전송하거나 새 TTS/캐시 플레이어를 추가하지 않았다.

## 병렬 작업의 완료 기준

| 작업 | 지금 실행 가능한 것 | 아직 필요한 것 |
|---|---|---|
| 대화·표정 | 같은 앱에서 GP8 대화와 영어 분노 수정 검수 | 실제 말투·감정 청취 |
| 사진·영상 | 명시적 음성 사진 요청/기존 UI, 저장, 종료 후 대화 복귀 | 새 후보에서 사진 한 장·짧은 영상 한 번으로 통합 확인 |
| 얼굴 위치 추적 | 명시적으로 켠 동안 기존 시선 입력 사용 | 실제 피사체로 반응 확인; 신원·감정 인식 아님 |
| B1 팔 | 기존 명명 gesture/입력 소유권/취소 코드 검토 및 회귀 | 현재 연결·원격 상태 확인 후 감독하 실제 motion 단계 |

팔 활성화는 기존 `--arms-supervised --arm-legacy-bridge --arm-reviewed-code-sha <현재 검토된 전체 code.py SHA256> --arm-power-off-ready` 인자를 그대로 사용한다. 이 런처는 값을 채우거나 원격 펌웨어를 켜지 않는다. 이전 enabled SHA를 현재 disabled 보드에 넘겨 검사만 통과시키지 않는다. GP10/GP11, 600–2400µs, 50Hz, 좌우 반전, GP7/GP8과 보정은 그대로 유지한다.

`--emotion-arms`는 실제 감독 조건을 충족한 같은 실행에서 추가한다. 모델의 gesture 요청, 프로토콜 ACK, 실제 위치/동작 성공은 구분한다. 현재 SHA 검증·물리 조건이 준비되기 전에도 카메라와 대화 작업은 계속할 수 있다.

이번 검증은 실제 런처 인자의 한 Desktop 전달, 카메라 경로 선택, 기존 팔·wake opt-in 유지와 누락된 인터프리터 오류를 확인한다. 현재 요청 문법·이전 대화/모델 허가 차단·실제 Host의 응답 종료→사진→저장→repeat 재촬영 없음도 주입한 SDK에서 검사한다. 실제 장치·개인 녹음·사진·모터·유료 API는 사용하지 않았다.
