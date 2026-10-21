# 새 통합 세션용 BMO 음성 인수인계

기준일: 2026-10-22 KST
이 자료는 통합 입력물이다. 자료가 준비되었다는 이유로 실제 통합·배포·하드웨어 활성화가 완료되거나 승인된 것은 아니다.

## 1. 이 세션에서 마친 것

문서 전용 브랜치 `docs/bmo-voice-policy-v1-20261022`에 다음 4개 자료만 추가했다.

1. `README_KR.md`: BMO 대화·음성 운용 규칙 v1.
2. `system-prompt.en.txt`: 위 규칙에 맞춘 분리된 System Prompt. 현재 앱에서 로딩하지 않는다.
3. `acceptance-cases.json`: 20개 비실행 검수 시나리오. 테스트 합격 결과가 아니다.
4. 이 인수인계 문서.

경로는 모두 `docs/voice-policy-v1/` 아래다. main이나 기존 기능 브랜치의 코드·테스트·설정·프롬프트는 수정하지 않았다. API 키 입력, 유료 API 호출, 음성 합성, 장치 접근, 패키지 설치, Pi 배포도 하지 않았다.

## 2. 유지할 기준

- 두뇌: 사용자가 선택하고 시험한 `gpt-6-luna`, 추론 `low`. 기존 GPT-4.1 mini는 수동 복귀 기준으로 보존한다.
- STT: 기존 `gpt-4o-mini-transcribe`.
- TTS: Fish Aaron Soft, voice ID `106f41ea392c4cc48c6eee76f69ff17e`, 시험 엔진 `s2.1-pro-free`, 발음 `beemo`, PCM 진폭 게인 `0.25`.
- Soft 지시: `[gentle, warm, slightly lower pitch, still lightly playful]`.
- 로컬 예비: `en_US-bmo_voice.onnx` 원본 Piper와 기본 추론 설정. 새 피치 보정·음성 재학습·성우 교체 없음.
- 얼굴: 사용자 최신 보고상 완성. 최종 승인 파일과 해시, 실제 상태·감정·입 API는 새 세션에서 인수한다. 과거 v7/v11을 임의로 최종이라고 정하지 않는다.
- Action: Codex의 `feature/bmo-actions-mock-v0`만 기준. `actions.py`는 5종 Mock이며 실제 장치 실행은 없음. 이전 `action_lab.py`는 중복 연결하지 않는다.
- 말투: 짧고 정확한 로봇 친구, 약한 장난기, 과잉 흥분·반복 상투어 금지. 실제 수행을 하지 않았으면 했다고 말하지 않는다.
- 언어 목표: 영어 음색 우선, 한국어 명시 요청에는 한국어. 기존 자동 한국어 응답과 다르므로 통합 시 단일 정책으로 정리한다.

## 3. 확인한 소스 스냅샷

| 목적 | 브랜치 | 확인한 HEAD |
|---|---|---|
| 음성 A/B + Luna 추론 옵션 | feature/bmo-luna-low-voice-ab | 054ced8f894f25f4cd0a178c2b097b5afe2c8576 |
| Fish Aaron Soft | feature/bmo-fish-aaron-tts | 7fc3d4366a4b5bd753f04895bf91e512e9c49def |
| 로컬 Piper fallback | feature/bmo-local-piper-fallback | 7bed6d02c4ac239671b56d601e7fd2845b5fe0c0 |
| Codex Mock Action v0 | feature/bmo-actions-mock-v0 | b346cc3017ca0ee4cb2162384d1fa1499659739a |
| 다른 창 결정 기록 | docs/bmo-cross-chat-handoff-20261021 | e7a71db4505aafe3a973bfa9943d89dad2075ff9 |
| 이번 문서 브랜치의 분기 기준 | main | 161a2ebd99b21ddc31e4cbf6b1cbd4452967e264 |

이는 읽은 시점의 기록이지 새 세션의 최신 상태 보증이 아니다. 통합 전에 실제 HEAD·로컬 차이를 다시 읽는다. 여러 브랜치를 한꺼번에 merge하거나 main을 실행 기준으로 추정하지 않는다. 기존 Luna 음성 브랜치에는 Fish와 로컬 백업의 앞선 변경이 포함되어 있으므로 동일 기능을 다시 중복 이식하지 않는다.

## 4. 통합 시작 전에 먼저 보고할 충돌표

| 점검 | 현재 차이 / 확인 필요 |
|---|---|
| 언어 | 현재 core.py의 `en`은 한국어 입력에 한국어 답변. 새 프롬프트의 영어 우선과 그대로 함께 넣으면 충돌 |
| 기본 모델 | Luna를 선택했어도 해당 시험 브랜치 기본값은 gpt-4.1-mini. 실제 요청의 model/effort를 명시 확인 |
| 토큰 상한 | 기존 기본 180, 음성 A/B 시험 320, 텍스트 벤치마크 600. 한 숫자를 무조건 제품 기본값으로 승격하지 않음 |
| 음성·게인 | 코드 기본 provider/gain과 사용자 선택 Fish/0.25가 다를 수 있음. 실행 설정을 읽어 확인 |
| 플레이어 | Python 출력 유지가 초기 추천. 최종 얼굴이 브라우저 시계에 의존하는지 확인하고 단일 재생 주체 하나로 결정 |
| 얼굴 | 최종 파일·SHA·JS API를 받기 전에는 새 얼굴 작성이나 임의 함수명 매핑 금지 |
| Action | v0 status=simulated / executed=False. GPT 연결은 실제 실행기 연결과 별개. demo_song/demo_game은 미디어 파일 아님 |
| 반복 재생 | 기존 /repeat는 재합성할 수 있음. 캐시 재생은 아직 목표 |
| 취소 | 플레이어 시작 콜백, 실제 가청 시각, 작업 취소 요청, 작업 종료를 구분 |
| 오프라인 | Piper 발성 성공과 모든 장애 자동 전환 성공, 완전 오프라인 대화는 각각 다른 상태 |
| 물리 입력 | GP7 기존 팔 토글과 TALK 버튼 충돌 확인 전에는 빨간 버튼이나 펌웨어를 임의로 사용하지 않음 |

새 세션은 실제 작업 폴더에서 `pwd`, 브랜치, `git status`와 접근 가능한 `AGENTS.md`부터 읽는다. 기존 `/home/bgh/BGH_BMO`, Codex `/home/bgh/BGH_BMO_ACTIONS`, 로컬 음성 `~/bmo-voice-test`를 임의로 변경하지 않는다. 통합 작업 경로·브랜치를 명시한 뒤 승인된 범위에서 진행한다. 이 자료에는 자동 실행 명령이 없다.

## 5. 통합 검수 순서

### A. 읽기 전용 인수인계

최종 얼굴, 실제 실행 코드, Action v0, 문서 정책을 나란히 대조한다. 차이는 숨기지 말고 표로 보고한다. 경로/브랜치가 맞지 않으면 쓰기 작업을 시작하지 않는다.

### B. 음성 없는 모의 연결

상태·감정·입 제어, utterance/turn ID, 종료·취소·늦은 이벤트 폐기를 mock으로 검증한다. 합성 완료를 재생 시작으로 착각하지 않도록 한다. Mock Action의 simulated를 실제 성공으로 읽지 않는지 확인한다. 실제 GPIO·카메라·서보·마이크·스피커는 이 단계에서 사용하지 않는다.

### C. 승인 후 주 음성 1개 연결

Luna low와 Fish Aaron Soft의 기존 검증 경로를 보존한다. 한 재생기만 음성을 출력하고 얼굴은 그 진행 이벤트를 소비한다. 평상시/질문/위로 장면에서도 speaking이 감정을 덮어쓰지 않는지 확인한다. 필요한 실제 API 호출과 마이크/스피커 시험 범위는 별도로 명시한다.

### D. 취소·오프라인·반복 검증

재생 전 Fish 장애와 재생 중 장애를 구분한다. Piper는 원본 영어 답변만 대신 읽고, 이미 일부 발성한 문장을 자동으로 처음부터 중복 재생하지 않는다. STT/GPT도 꺼졌다면 고정 안내만 사용한다. 취소 이후의 음성·얼굴 이벤트는 폐기한다.

### E. 후속 최적화

실제 얼굴·소리·취소가 안정된 뒤 VAD, 호출어, 캐시, 문장/오디오 스트리밍을 별도 범위로 다룬다. 실제 팔·게임·음악·카메라 실행은 독립 어댑터와 별도 승인/검증이 있어야 한다. Mock Action v0를 몰래 실제 실행기로 바꾸지 않는다.

## 6. 새 통합 채팅 첫 메시지로 사용할 문장

```text
BMO 통합 전 인수인계를 시작하자.
GitHub GUNOBAE/BGH_BMO의 docs/bmo-voice-policy-v1-20261022 브랜치에서
 docs/voice-policy-v1/README_KR.md와 INTEGRATION_HANDOFF_KR.md를 먼저 읽어줘.
최종 얼굴 파일은 이 세션에서 인수하고, 이전 얼굴 버전을 임의로 쓰지 마.
Luna low + Fish Aaron Soft/Beemo/gain 0.25와 원본 Piper 백업을 보존해.
Action은 feature/bmo-actions-mock-v0의 5개 Mock 계약만 사용해.
처음에는 실제 경로·브랜치·git status·AGENTS.md와 구현 차이를 읽고,
작업 경로/충돌/적용 순서를 보고해. 바로 전체 merge나 장치 실행은 하지 마.
```

## 7. 이번 결과를 어떻게 해석할지

문서·프롬프트·검수 자료 준비와 실제 모델의 새 말투 적용은 다르다. 이 작업에서 확인할 수 있는 것은 자료 구조, 서로 일치하는 설정, source snapshot, 변경 파일 범위뿐이다. 새 프롬프트의 발화 품질, 감정 연기, 최종 얼굴 동기화, 새 언어 정책, 실물 통합은 아직 시험하지 않았다.
