# BMO GPT 두뇌 A/B 성능 비교 — 독립 작업 (2026-10-21)

## 이번 브랜치의 범위

**브랜치:** `feature/bmo-gpt-benchmark-v1`, 기존 `feature/bmo-fish-aaron-tts` 기준에서 독립 분기.

- 기존 BMO 메인 대화(GPT-4.1-mini + STT + Fish Aaron Soft), 얼굴, 로컬 Piper 음성, 시스템 서비스, 라즈베리파이 팬·서보·RP2040 설정, Codex Action System 등은 **변경하지 않는다**.
- 이 작업은 **GPT 텍스트만 비교**한다. STT, Fish TTS, 스피커 음성 재생, GPIO·카메라·버튼·음악·게임 등은 전혀 접근하지 않는다. 새로운 Action System과 충돌하지 않도록 Function Calling도 이번 단계에서는 하지 않는다.
- 별도 `software/bmo_ai/gpt_benchmark.py` 및 독립 `software/tests/test_gpt_benchmark.py`만 사용한다. 프로그램과 테스트 모두 Python 표준 라이브러리만 사용.
- `core.py`의 실제 `SYSTEM_PROMPT` / 언어 지시문을 가져오되, 해당 파일은 **읽기만 한다**. 프로덕션 코드는 수정하지 않는다.

## 네 가지 후보

| 구성 | reasoning.effort | 확인 사항 | 가격: 입력 / 출력 1M tokens |
| --- | --- | --- | --- |
| gpt-4.1-mini | 없음(비추론) | 기존 기준 | $0.40 / $1.60 |
| gpt-6-luna | none | 최저 지연 | $0.10 / $0.50 |
| gpt-6-luna | low | 조금 더 안정적인 판단 | $0.10 / $0.50 |
| gpt-6.1-sol | low | 경량 대비 지능 향상 | $2.00 / $10.00 |

모델은 계속 업데이트될 수 있다. 최근 공식 모델 가이드:
https://developers.openai.com/api/docs/models
https://developers.openai.com/api/docs/guides/reasoning
https://developers.openai.com/api/docs/pricing

## 테스트 항목

영어 안부 / 한국어 공감 / 오리지널 농담 / 간단 계산 / 대화 내 기억(Snake) / 카메라 실제 실행 여부에 대한 정직성 / 팔 움직임 실제 실행 여부에 대한 정직성 / 복합 한국어 명령 / 안전 설명 / 영어 답변 전환. 총 10개 상황.

- **자동 검사:** 계산 정답, 게임 기억, 한국어·영어 응답 존재 여부 등 명확한 기준만 검사.
- **사람이 검사:** 자연스러움, 감정 표현, BMO 말투, 카메라·팔 행동을 실제로 했다는 허위 주장, 어조, 너무 긴 답변 등.
- **측정 지표:** 첫 번째 실제 텍스트 도착까지의 시간(첫 문자/토큰 근사), GPT 전체 응답 종료 시간, 완료율, 입력·출력·추론 토큰, 개별 API 호출 비용 추정치.
- **측정의 한계:** 첫 텍스트 지연은 **첫 소리가 스피커에서 나는 시간과 다르다**. 실제 로봇 응답 지연은 녹음 대기 + STT + GPT + Fish 전체 합을 나중에 별도 측정한다.

`stream=true`로 OpenAI Responses SSE를 사용하고, `response.output_text.delta` / `response.completed` 이벤트로 두 지연시간을 각각 기록한다. 모델 공통 출력 상한은 600토큰(추론 토큰 포함), `store=false`, 재시도 없음. 추론이 600 토큰을 모두 쓰면 그 모델은 답변 생성 실패/불완전으로 표시한다. 실제 서비스의 180토큰 상한과 다르므로 후보 선택 후 그 환경에서도 재검증해야 한다.

## 완전히 별도 폴더에서 실행하기 (Raspberry Pi)

**중요:** Codex의 `/home/bgh/BGH_BMO_ACTIONS` 작업이나 기존 실행 중인 `/home/bgh/BGH_BMO`를 절대 건드릴 필요가 없다.

```bash
cd /home/bgh

# 독립 폴더로 GitHub 브랜치 읽어오기. 기존 프로젝트 checkout은 그대로 둠.
git clone --single-branch --branch feature/bmo-gpt-benchmark-v1 \
  https://github.com/GUNOBAE/BGH_BMO.git BMO_GPT_BENCH

cd /home/bgh/BMO_GPT_BENCH/software

# 0원: 설명과 비용 계획만 출력
python3 -m bmo_ai.gpt_benchmark

# 0원: 해당 모듈의 오프라인 단위 테스트만 실행
python3 -m unittest discover -s tests -p 'test_gpt_benchmark.py' -v
```

### 실제 GPT API 실측 (유료, 명시적 승인 플래그 필요)

API 키를 이 채팅이나 명령어에 직접 입력하지 않는다.

```bash
cd /home/bgh/BMO_GPT_BENCH/software

read -rsp 'OpenAI API key: ' OPENAI_API_KEY; echo
export OPENAI_API_KEY

# 4개 설정 x 10개 예제 = 최대 40회의 실제 텍스트 API 요청.
# 1회차 시나리오 계산 비용 약 $0.1406 USD (실제 청구액 아님).
python3 -m bmo_ai.gpt_benchmark --run --confirm-paid \
  --output "$HOME/bmo_gpt_benchmark_run1.json"
```

결과를 확인하거나 분석을 요청할 때는 `~/bmo_gpt_benchmark_run1.json` 파일을 채팅에 업로드한다. JSON에는 예제 질문과 모델 답변이 포함되지만 API 키는 저장하지 않는다. 외부 공유 전 결과를 확인할 것.

- 추정비용 `$0.1406`은 각 요청에 2500 입력토큰, 최대 600 출력토큰을 적용한 시나리오 계산일 뿐 **하드 청구 상한은 아니다**. 요청당 실제 출력·추론 토큰과 처리 티어에 따라 달라진다.
- `--max-estimate-usd 0.25`는 실행 전 경고/차단용이며 계정 과금을 막는 기능이 아니다.
- 모델 접근 실패(403/404/400), 잘못된 API 키(401), 사용량 한도(429) 등은 짧은 안전한 오류 코드로 기록한다. 키·HTTP 원문은 출력하지 않는다.
- 결과 파일을 덮어쓰지 않는다. 다시 실행하려면 새로운 `--output` 파일명 사용.
- 원한다면 두 번 반복해 분산도 비교할 수 있지만, 두 패스의 예상치 `$0.2812`는 기본 `$0.25` 사전 가드를 초과한다. 반복 테스트는 첫 결과 검토 후 결정할 것.

## 그다음 연결

1. Codex의 독립 `feature/bmo-actions-mock-v0`가 완료되면 **그쪽 ActionRequest/ActionResult**를 기준으로 GPT Function Calling 평가를 따로 추가한다. 이번 실험의 모델 출력은 **절대 명령으로 실행하지 않는다**.
2. 후보 선정 후 기존 BMO GPT 두뇌를 해당 모델/추론 설정과 호환되도록 조정하고, **사용자가 검증한 Fish Aaron Soft는 유지**한다.
3. 다음 병목: VAD로 고정 5초 마이크 녹음을 대체하고, Fish의 실시간/부분음성 합성이 가능한지 계정·모델을 확인한다.
4. 성공하기 전까지 실사용 `gpt-4.1-mini` 브랜치와 얼굴/로컬 음성/Action System을 유지한다.
