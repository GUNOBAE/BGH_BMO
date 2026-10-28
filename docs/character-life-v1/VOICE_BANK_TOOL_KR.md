# Character Life 로컬 음성팩 도구

32개 후보는 `software/bmo_ai/policies/voice-bank-v1.json`에 들어 있다. 실제 음성은 저장소에 넣지 않는다. 현재 후보는 모두 `AUDIO_NOT_GENERATED`이며 실제 Aaron 음성 생성, 청취 승인, Pi 스피커 재생은 수행하지 않았다. 임시 합성 PCM 회귀 검사와 실제 목소리 청취는 구분한다.

`VoiceBank`는 기존 `GreetingCache`의 비공개 원자적 번들을 재사용한다. 모델별/문구별 WAV SHA-256, 연출 사양 fingerprint, 실제 길이, lip cue SHA와 승인 상태를 검증한 임시 스냅샷만 재생한다. Aaron ID, Beemo 발음, gain 0.25를 고정하며 gain은 게시 시 한 번 적용한다. 동일 모델/문구의 기존 번들은 덮어쓰지 않는다. 일반 부팅·idle에서 키를 읽거나 API를 호출하지 않는다.

`BankPlayer`는 기존 `IntegrationVoice._play`의 실제 재생 시작 콜백을 사용한다. 호출자가 기존 AudioFocus를 확보해야 한다. 대화 기록과 `/repeat`용 답변 캐시는 쓰지 않는다. 취소한 clip은 들은 것으로 기록하지 않는다. 성공적으로 끝난 부팅 인사의 최근 3개와 clip별 cooldown은 비공개 `selection.json`으로 유지한다. 미승인·손상·합성 전용 자산이나 손상된 선택 기록에서는 침묵을 선택한다.

개발 환경에서 비용 미리보기만 확인하는 명령은 다음과 같다. 이 기본 동작은 키 접근·네트워크·음성 생성·재생·캐시 폴더 생성을 하지 않는다.

```sh
cd /home/bgh/BMO_CHARACTER_LIFE/software
../.venv/bin/python -B -m bmo_ai.voice_bank_cli
```

기본 A/B는 boot_01, boot_05, reaction_07, reaction_11을 S2.1 free와 Drama3로 비교하는 8개 요청 후보다. 2026-10-28 현재 연출문을 포함한 UTF-8 입력은 합계 792 bytes이며, 공개 정가 상한 기준 예상 합계는 USD 0.019800이다. 이 값은 실제 청구나 실제 음질의 측정값이 아니다. 문구·공식 가격이 바뀌면 다시 계산한다. 8개를 넘는 한 번의 생성은 거부한다.

생성 전에는 별도의 명시적 생성 지시와 양수 지출 상한이 필요하다. `--generate --spend-cap-usd <승인한_USD_상한>`에서만 비공개 자격 정보를 읽는다. 명령행에 API 키를 넣지 않는다. 생성 직전 공개 공식 문서 4개를 다시 읽고 문서 SHA와 UTC 확인 시각을 남긴다. 가격·모델·감정 연출 지원을 확인하지 못하거나 상한을 넘으면 키 접근 전에 중단한다. 자동 재시도하지 않으며 첫 실패에서 멈춘다. 성공한 앞선 후보는 미승인으로 보존한다. 이번 자동 개발에서는 이 경로를 실행하지 않았다.

실제 생성 뒤에도 `LISTENING_NOT_APPROVED`이다. 사용자가 감독하는 청취는 `--audition <clip_id> --model <model> --supervised-listening`으로 명시해야 한다. 승인에는 실제 청취 후 `--approve <clip_id> --model <model> --sha256 <해당_WAV_SHA256> --listening-approved`가 필요하다. Aaron 음색·Beemo 발음·첫 음절·연기 자연스러움·불필요한 태그 낭독·클리핑을 확인한다. 분노/울음 연출에는 음색 정체성만 보존하고 강한 연기를 억제하는 고정 gentle/warm 문구를 붙이지 않는다. 승인 여부는 모델별로 독립적이며 Drama3를 기존 대화 모델로 자동 교체하지 않는다.

2026-10-28 13:17:05 UTC에 공개 공식 문서를 확인했다. 모델 개요는 S2.1 free/pro가 같은 모델임을 설명하며, 보장 조건은 다르다. [공식 모델 개요](https://docs.fish.audio/developer-guide/models-pricing/models-overview)

공개 표의 정가는 S2.1 free/pro/Drama3 순서로 USD 0/15/50 per million UTF-8 input bytes다. 생성기의 지출 계산에는 기간 한정 프로모션 할인을 적용하지 않는다. [공식 가격표](https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits)

Drama3는 정확한 모델 헤더가 필요하며, 모델 지정 오류 시 다른 모델로 처리될 수 있다. 기존 Fish 어댑터에 명시적 `model`과 기존 Aaron ID를 전달한다. [Drama3 공식 문서](https://docs.fish.audio/features/text-to-speech/drama-3)

감정 연출은 S2의 square bracket natural-language 지시를 사용한다. 문서 확인은 계정의 실제 모델 이용 가능성이나 Aaron의 감정 음질을 검증한 것이 아니다. [공식 감정 문서](https://docs.fish.audio/developer-guide/core-features/emotions)

자동 검증은 `tests.test_character_voice_bank`의 19개 검사다. 누락·손상·SHA 불일치·미승인·합성 provenance·중복 게시·원본 보존·비공개 권한·symlink/FIFO 거부·최근 3개·cooldown·조용히 있기·중단·기존 재생 경로·모델별 1회 요청·실패 시 재시도 금지·지출 상한·공식 문서 변경 시 차단을 임시 파일과 주입한 합성기로 확인했다. 실제 유료 API와 장치는 호출하지 않았다.
