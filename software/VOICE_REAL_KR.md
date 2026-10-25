# Aaron 감정 음성: 실제 요청 경로와 청취 검수

이번 구현은 얼굴 emotion과 실제 음성 direction을 분리한다. Fish Aaron Soft / Beemo / gain 0.25,
기존 Luna low 및 Piper 백업을 유지한다. 원본 R10과 승인된 기본 음성을 바꾸지 않는다.
**실제 Fish 합성·스피커 재생·사용자 A/B 청취는 이번 자동 작업에서 NOT_RUN**이다.
아래 검수를 통과하기 전에는 실제로 감정이 들린다고 주장하지 않는다.

## 공식 API 확인과 적용

2026-10-26 확인한 [Fish 감정 제어 문서](https://docs.fish.audio/developer-guide/core-features/emotions)는
S2/S2.1 계열의 대괄호 cue와 감정·짧은 소리 cue를 설명한다. 이 구현은 호스트가 소유한 작은
목록만 사용한다. 모델이 만든 임의 direction·SSML·voice ID·pitch 명령을 전달하지 않는다.

[TTS 안내](https://docs.fish.audio/features/text-to-speech)는 `s2.1-pro-free`를 S2.1 계열로 명시한다.
[API 정의](https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech)에 따라 기존
`text/reference_id/format` JSON과 명시적 model header를 유지하며, 지원 여부가 확인되지 않은
`emotion` JSON 인자를 추가하지 않는다. 속도·볼륨 파라미터가 있어도 기존 gain/목소리를 보존하기
위해 변경하지 않는다. 무료 명칭과 무관하게 모든 합성은 명시적 비용 승인 경로로 제한한다.

| 내용 | 실제 요청 처리 |
|---|---|
| neutral | 기존 Aaron Soft direction과 발음만 유지 |
| happy/sad/surprised/angry/crying | 검증된 emotion을 고정 S2 cue로 변환, crying은 기본 sad 운율 |
| chuckle | happy와 맞을 때만 짧은 고정 chuckling sound 요청 |
| gasp | surprised와 맞을 때만 짧은 고정 gasp 요청 |
| playful_puff | 사용자가 장난/뿌우를 요청하고 happy일 때만 짧은 고정 소리 요청 |
| sob | 사용자가 울기 연기를 요청하고 crying일 때만 짧은 고정 sob 요청 |
| 지원 모델/검증 metadata 없음 | 추가 음성 cue 없이 평문으로 전달 |

`FeatureClient(..., expressive=True)`는 단일 AI 응답의 strict v2 schema에
`reaction=none/chuckle/gasp/playful_puff/sob`를 추가한다. v1은 그대로 지원한다.
speech에는 metadata나 대괄호 tag를 허용하지 않는다. 호스트는 감정에 맞지 않는 reaction을
버리고, crying/silly reaction은 현재 사용자 요청을 좁게 확인한다. 판별 불명확·부정 요청은
reaction을 생략한다. 이 문자열 검사는 자연어 동의 판별기의 완성이 아니며 보수적인 제한이다.

`IntegrationVoice(..., expressive=True)`가 동일 WAV에 cue와 대사를 합성한다. 별도 웃음 재생기를
겹치지 않으며 원래 WAV에서 mouth cue를 계산한다. prepared/started에는 검증된 얼굴 emotion과
실제 선택한 `voice_emotion/reaction/voice_style`만 전달한다. PCM·원문 대사·키는 진단에 넣지 않는다.
실제 Fish audio 청취가 승인됐다는 뜻은 아니다. 합성 fixture는 synthetic으로 남는다.

reaction은 성공한 새 음성 세 번당 최대 한 번만 허용한다. 랜덤 또는 모든 문장 filler는 없다.
취소·실패는 예산을 소비하지 않는다. 반복 재생은 직전 성공 WAV·표정·reaction을 그대로 보존하고
재합성·재gain·예산 증가를 하지 않는다. Piper fallback은 Fish tag가 없는 원래 평문만 읽는다.

## 사용자가 감독하는 A/B 준비·청취·승인

앱을 종료한 뒤 저장소의 `software`에서 아래 도구를 사용한다. 기존 앱을 이 도구가 자동 종료하지
않는다. 기본 `--plan`은 파일·키 조회·합성·재생 없이 여섯 개 샘플 계획만 표시한다.

```bash
python -B -m bmo_ai.voice_benchmark --plan
```

`neutral/happy/sad/surprised/playful/acting_cry`의 고정 짧은 대사 여섯 개가 있다.
처음에는 neutral과 happy만 비교한다. 합성은 **아래 명령을 사용자가 직접 실행할 때만** 허용되며
한 실행에 최대 한 번 요청한다. `--allow-paid-api`는 실제 비용이나 provider 정책을 검토했다는
명시적 선택이다. 키는 기존 비공개 설정에서 읽고 명령행 인자로 받지 않는다.

```bash
python -B -m bmo_ai.voice_benchmark --prepare neutral --allow-paid-api
python -B -m bmo_ai.voice_benchmark --prepare happy --allow-paid-api
python -B -m bmo_ai.voice_benchmark --audition neutral
python -B -m bmo_ai.voice_benchmark --audition happy
python -B -m bmo_ai.voice_benchmark --status happy
```

처음 생성한 파일은 USER_APPROVAL 상태다. 청취는 승인이 아니며, 사용자가 결과를 확인한 후
출력된 happy WAV SHA-256을 아래 `SHA256_FROM_STATUS` 자리에 넣어 승인한다.

```bash
python -B -m bmo_ai.voice_benchmark --approve happy SHA256_FROM_STATUS
```

캐시는 `~/.cache/bmo/aaron-expression-v1/<sample>/greeting.cache`에 저장한다. 파일명은 기존의
검증된 private cache 형식을 재사용한 것이며 환영 인사 캐시와 경로·fingerprint가 다르다.
디렉터리 0700/파일 0600, gain 1회, 실제 저장 PCM의 SHA와 mouth cue, 승인 SHA를 검증한다.
동일 자산을 다시 prepare하면 검증된 기존 파일을 재사용하므로 추가 요청을 하지 않는다.
동시 prepare는 별도 잠금으로 막는다. 손상·다른 spec·위험한 링크는 보존하고 오류로 종료하며
자동 재합성·자동 삭제·다른 성우 fallback은 없다. 합성 자산을 Git에 추가하지 않는다.

청취 기준: 같은 Aaron 성우인지, Beemo 발음·음량·잡음, 웃음/울음 길이와 문맥, 기본 대사의 명료성,
입모양과 시작/끝 타이밍을 확인한다. 과하거나 tag가 낭독되면 승인하지 말고 `--plain-voice`로
되돌려 기본 음성을 사용한다. 승인된 재사용 클립을 일상 대화에 무조건 삽입하지 않는다.
이 여섯 자산은 제한된 A/B 검수용이며, 실제 대화는 동일 보호 경계의 Fish 요청으로 생성한다.

## 자동 검증과 남은 실기

`tests/test_expressive_voice.py`는 단일 응답 schema, cue allowlist/문맥/빈도 제한,
기존 성우·발음, 실패와 반복, Piper 평문 fallback, 명시적 prepare/캐시 재사용/청취/승인을
합성 WAV와 주입한 provider/player로 검증한다. 네트워크·마이크·카메라·모터는 사용하지 않는다.
기존 Feature v1 SDK/voice/cache race 회귀도 유지한다.

현재 **구현: 완료 / 주입 경계 테스트: PASS / 실제 Fish 생성·청취: NOT_RUN / 오디오 자산:
ASSET_PENDING / 사용자 음성 품질 승인: USER_APPROVAL**이다. 무료 API 상태나 감정 청취 성공을
모의 통과 수로 대신하지 않는다. 롤백은 새 branch의 해당 음성 커밋을 검토 후 되돌리거나
`--plain-voice`를 쓰는 것이며 기존 Piper·캐시·백업을 삭제하지 않는다.
