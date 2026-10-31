# Aaron 무료 샘플 준비 — 실제 생성 결과

[PR #40 추가 지시](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6476906241)에 따라 2026-10-29 KST에 **실제 Fish 무료 모델로 6개 음성을 생성했다.** 생성은 완료했으며 청취·재생·승인은 하지 않았다. 기존 실행본, 원본 R10, 승인 Aaron ID·Beemo 발음·gain 0.25, Piper와 기존 인사 캐시는 변경하지 않았다.

| 구분 | 결과 |
|---|---|
| 실제 인증 요청 | 총 **8/8**: metadata GET 2회 + TTS POST 6회 |
| TTS 모델 | 정확히 `s2.1-pro-free`만 6회 |
| 비용 guard | 사용자 상한 USD 0, 당일 공개 정가 기준 예상 USD 0.000000 |
| 실제 청구서/잔액의 생성 후 재조회 | `NOT_RUN`. 추가 인증 요청 한도를 넘지 않음 |
| 실패·재시도·유료 fallback·다른 모델 요청 | 모두 0 |
| 승인된 새 음성 | **0개** |
| 실제 스피커 재생·사용자 청취 | `NOT_RUN / LISTENING_NOT_APPROVED` |
| 현재 bank inventory | `LISTENING_NOT_APPROVED` 6개, `AUDIO_NOT_GENERATED` 26개, `READY` 0개 |

## 요청 전 공식 문서·권한 확인

공식 문서는 **2026-10-28 18:23:23 UTC, 즉 10월 29일 KST**에 재확인했다. 공개 모델 설명은 free와 pro가 같은 S2.1 모델이며 free에는 fair-use 제약과 보장 조건 차이가 있음을 설명한다. 더 비싼 모델로 바꾸지 않았다. [모델 개요](https://docs.fish.audio/developer-guide/models-pricing/models-overview)

공식 가격표에서 `s2.1-pro-free`는 USD 0 per million UTF-8 input bytes다. Drama3는 유료이므로 이번 USD 0 실행에서 생성·A/B 비교하지 않았다. [공식 가격표](https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits)

공식 TTS 계약은 모델 헤더가 없거나 정확하지 않으면 유료 기본 모델로 처리될 수 있다고 명시한다. 이번 코드는 `model: s2.1-pro-free`를 강제하며 redirect·자동 재시도·모델 전환을 거부했다. [공식 TTS 계약](https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech)

S2 감정 연출은 대괄호 자연어 지시를 사용한다. 일반 인사는 따뜻한 Aaron 연출을 유지하고, 장난스러운 화/울음 후보에는 동일 Aaron 정체성을 유지하되 강한 연기를 상쇄하는 고정 gentle/warm prefix를 붙이지 않았다. 실제 연기의 자연스러움과 태그 낭독 여부는 청취해야 판단할 수 있다. [공식 감정 연출 문서](https://docs.fish.audio/developer-guide/core-features/emotions)

공개 OpenAPI로 TTS의 free enum과 유료 default, `/wallet/{user_id}/api-credit` GET 및 `/model/{id}` GET을 확인했다. metadata 조회는 생성·업로드가 아니다. 계정은 비공개 resolver로 읽었으며 키·계정 잔액·개인 식별값을 출력하지 않았다. 두 인증 GET으로 인증과 기존 Aaron 모델의 접근·trained 상태를 확인했다. 무료 추론 권한을 단독 증명하는 metadata 필드는 없어 첫 번째 유용한 샘플을 **한도에 포함되는 무료 probe**로 사용했다. 해당 요청과 이어진 5개 요청 모두 유효한 WAV를 반환했다. [공식 OpenAPI](https://docs.fish.audio/api-reference/openapi.json), [모델 조회](https://docs.fish.audio/api-reference/endpoint/model/get-model)

## 생성된 실제 샘플

전체 입력은 연출문·Beemo 정규화를 포함한 authored text **576 UTF-8 bytes**다. 방 안에서 녹음한 WAV나 개인 대화를 서버에 업로드하지 않았다. 아래 SHA는 gain 0.25를 한 번 적용한 **최종 검수 WAV**의 실제 값이다.

| clip | 원래 대본 | 실제 길이(s) | 최종 peak(FS) | 최종 WAV SHA-256 |
|---|---|---:|---:|---|
| boot_01 | Hey, you're back! How have you been? | 2.786395 | 0.235107 | `0ae53a3716f196db42c4299711a125fae05a2c0b6fb66b2207224a12a1ecb2d1` |
| boot_05 | BMO is ready. How are you doing? | 2.461315 | 0.234619 | `77bf1b5e23259b89369fb53c436eb011beedc84bf69d7a9f4ad5dc9b31e899be` |
| reaction_03 | Hehe. | 0.835918 | 0.223511 | `86ff9414be3a435ab87c873eec5343b73161fb311527b63bfc4217ed6c876e50` |
| reaction_07 | Hmph! | 0.464399 | 0.202148 | `46922b75cd17c8a73ea76c206e50ef9ccf2da14fda54f0d3a93ec39fd1b9c2c0` |
| reaction_08 | Hey! | 0.603719 | 0.210571 | `020ff7155ed4e3516b42cabd2d7bbaf6e32b7fc7e444c62b5b58d8426dc1c956` |
| reaction_11 | Oh, dear… | 1.486077 | 0.219238 | `514bf204137c82f610979742dc4fba82c8e04a1b4b83aa420fce33eeb51461c9` |

6개 모두 raw/final PCM에서 clipped sample 0개였으며 목표 길이 이내였다. 이는 수치 검사이며 Aaron 음색·발음·웃음/울음/화 연기의 청취 PASS가 아니다. 승인 상태는 모두 false이고 simulated=false인 실제 생성물이다. SHA·길이·cue·사양 fingerprint 검증을 통과한 private bank에 보존했다.

## 비공개 보존과 요청 한도

런타임 bank는 `/home/bgh/.cache/bmo/character-voice-bank-v1`이다. 별도의 비공개 검수 경로는 다음과 같다. 원시 JSON과 WAV는 Git에 추가하지 않는다.

```text
/home/bgh/.local/state/bmo/voice-bank-free-v1/pr40-comment-6476906241/
  requests.json               # 인증 GET/POST 전 durable 예약, 총 8회
  pre_post_gate.json          # 2 GET 완료·첫 TTS 이전의 검증 상태
  preparation_result.json     # 실제 6개 생성과 SHA/길이 결과
  review-wavs/                # boot_01/05, reaction_03/07/08/11.wav
```

디렉터리는 private 0700, 검수 WAV와 JSON은 0600이다. 검수 WAV 6개를 저장된 SHA와 다시 대조해 모두 일치했다. `pre_post_gate.json`의 free inference permission이 pending인 것은 **첫 POST 이전** 상태이며, 최종 결과의 `FREE_TTS_REQUEST_SUCCEEDED`와 시점이 다르다.

전역 ledger는 checkout이나 bank 경로를 바꿔도 같은 사용자·같은 승인 ID에 공유된다. network 전 예약을 저장하므로 실패·중단도 요청 수를 소비한다. metadata 오류·권한·결제·rate limit 등 첫 실패에서 batch를 중단하고 후속 실행을 차단한다. 이번에는 8개 모두 성공했지만 **남은 요청은 0개**다. ledger를 삭제하거나 다른 폴더로 돌려 한도를 우회하지 않는다. 이번 승인으로 추가 Fish 조회·생성을 하지 않는다.

## 코드와 오프라인 검증

- `voice_bank_cli.py`: 기본 dry-run 유지. `--free-only --generate --spend-cap-usd 0`에서만 exact free model·당일 정가 0·현재 OpenAPI·계정 metadata·전역 한도를 검증한다. 기존 양수 상한 A/B 모드는 별도로 유지하며 자동 선택하지 않는다.
- `voice_bank_free.py`: metadata와 TTS를 합한 최대 8회 예약, private flock·원자적 journal, 실패·중단 fail closed, 계정 결과 비식별화.
- `fish_tts.py`: 무료 전용 요청은 exact model을 요구하고 redirect를 거부한다. 기본 기존 경로의 계약을 유지한다.
- `tests/test_voice_bank_free.py`: 신규 10개 검사. 기존 bank/Fish 검사와 함께 **35개 목표 검사 PASS**. 실제 요청 8회와 이 합성 회귀 수를 합산하지 않는다. 최종 전체 회귀와 GitHub CI는 최종 보고서의 해당 HEAD 결과를 따른다.

## 다음 사용자 검수

이번 자동 작업에서는 듣지 않았다. 사용자는 감독하는 검수 시간에 private WAV 또는 명시적 `--audition ... --supervised-listening` 경로로 각 샘플을 확인할 수 있다. Aaron 정체성, Beemo 발음, 첫 음절, 태그를 읽지 않는지, 일반 인사와 강한 연기의 적합성, 음량을 확인한다. 청취한 샘플에만 정확한 SHA를 지정한 `--approve ... --listening-approved`를 적용한다. 지금은 어떤 샘플도 자동 재생 대상으로 승인하지 않는다.

최종 구분: **실제 무료 생성 완료 / WAV 수치·SHA 검증 완료 / 자동 재생 안 함 / 청취 미승인 / Drama3 미생성 / 실제 청구서 사후 조회 안 함**.
