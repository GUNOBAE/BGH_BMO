# 자동 표정 코드 준비 및 실기 상태 인수

**PARTIAL — 감정 출력 계약·검증·단일 호출 어댑터와 독립 검사 35개를 구현했다. 기존 앱의 자동 표정 연결과 Pi 검증은 다음 Codex 작업이다.**

기준: 2026-10-22 KST. 부모 `feature/bmo-integration-v1`의 `902b09739844ab4c78ea6234851b7bfb9cdb7634`를 GitHub 연결로 확인했다. 개발 브랜치 `feature/bmo-auto-emotion-v1`은 이 기준에서 분리한다. 이 보고서만으로 Pi 파일·프로세스가 변경된 것은 아니다.

## 사용자 실기 확인

사용자는 기존 통합 앱에서 실제 답변을 잘 들었다고 확인했다. 제공 로그에는 listening/idle → thinking/idle → speaking/started → idle/ended가 세 차례 있다. 사용자는 감정 표현이 없다고 지적했고 자동 표현의 구현을 요청했다.

이는 **음성 대화의 사용자 청취 확인 + 로그상 정상 턴 종료**다. 실제 얼굴 표시/립싱크, cancel/repeat, Piper fallback, 모델 출력 정책의 20개 사례, 물리 Action까지 통과했다고 하지 않는다. 사용자가 실행한 명령은 --live이며 --offline-voice는 없었다. 별도 로컬 AI 대화 모드로 실행했다고 볼 근거는 없다.

## 추가한 코드

- `software/bmo_ai/expression_contract.py`: speech/emotion schema, bounded JSON 검증, 중복 키/추가 필드/불완전 JSON/제어 태그 거부. 감정값만 잘못됐을 때는 깨끗한 발화문을 보존하고 neutral로 처리. frozen reply 자료형. BMOBrain 전용 비소유 client view는 create를 한 번만 위임하고 깨끗한 speech와 임시 emotion 후보를 분리한다.
- `software/tests/test_expression_contract.py`: 35개의 독립 오프라인 검사. 네트워크·SDK·장치·키·모델·얼굴 파일을 사용하지 않는다.
- `docs/development/CODEX_EMOTION_STAGE1_KR.md`: 실제 런타임 연결, 취소/타이머/캐시, 모의/승인 HTML 검사, GitHub 보고 절차.

Python 3.13.5의 ChatGPT 도구 환경에서 아래 명령을 실행했다.

```text
python -B -m unittest discover -s tests -v
Ran 35 tests in 0.007s
OK
```

여기서 tests는 이번 seed의 독립 테스트 폴더다. **기존 323개 전체 회귀, Pi headless, live 모델은 이번 환경에서 재실행하지 않았다.** 전체 clone은 작업 환경의 DNS 제한으로 준비하지 못했으며 필요한 소스와 정책은 GitHub connector로 읽었다. 이 제한을 Pi의 네트워크 문제로 해석하지 않는다.

## 보존 및 남은 검증

기존 런타임/승인 HTML/음성 프로필/Piper/Action은 변경하지 않았다. Luna low + Aaron Soft + beemo + PCM gain 0.25를 유지한다. 추가 웃음소리/울음소리/성우 변경/피치 보정은 없다. 계약 어댑터는 단독으로 얼굴을 움직이지 않는다.

다음 Codex 작업에서 brain/session/voice cache/CLI를 연결한 뒤 실제 승인 HTML과 회귀 검사를 실행한다. 실제 모델의 JSON schema 지원·감정 선택 품질·출력 길이·음성/표정 타이밍은 사용자 live 검수로 확인해야 한다. 추가 GPT 분류 호출은 설계하지 않았지만 토큰·지연 증가가 0이라고 보장하지 않는다. 형식 오류 시 자동 모델 전환/복구 API 호출 없이 안전하게 끝내고 manual 모드로 복귀 가능해야 한다.
