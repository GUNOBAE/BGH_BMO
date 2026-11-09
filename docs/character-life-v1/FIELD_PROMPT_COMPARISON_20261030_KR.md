# 운영본과 Character Life 대화 경로 비교 — 2026-10-30

이번 비교는 기존 성공본 `/home/bgh/BMO_REAL_ROBOT`의 소스 HEAD `0f46e3203afcc0d2bb7455cf49b3ac7ba0d4e978`과 후보 브랜치의 파일을 읽은 결과다. 운영본 코드·가상환경·실행 프로세스를 변경하지 않았다. 두 번째 확인은 실제 production `FeatureClient`와 `policy_brain`에 순수 로컬 가짜 provider를 연결한 두 턴 요청 캡처다. 실제 API·장치·개인 대화는 사용하지 않았다. 사용자 체감 품질의 원인을 유료 모델 평가로 확정한 기록이 아니다.

## 동일한 부분

`core.py`, `config.py`, `integration_profile.py`, `policies/system-prompt.en.txt`, `expression_contract.py`, `cloud.py`는 양쪽의 바이트가 동일했다. 고정 음성 정책 SHA-256은 `0959043244635b03e74992bf3ff5446fc96638ee79cc67cf649405bdf60d1e82`이다.

| 항목 | 성공본 소스 | Character Life 후보 |
|---|---|---|
| 기본 통합 GPT | `gpt-6-luna` | 동일 |
| reasoning | `low` | 동일 |
| 통합 max output budget | 320 tokens | 동일 |
| 유지 턴 / 입력 / history | 8턴 / 4000자 / 16000자 | 동일 |
| 두 질문의 provider 요청 수 | 2 | 2 |
| 두 번째 요청의 history 역할 | user, assistant, user | 동일 |
| 완료 뒤 유지 history | 두 user/assistant 쌍 | 동일 |
| Fish/Aaron/발음/gain | 기존 config의 `s2.1-pro-free`, Aaron, Beemo, 0.25 | 동일 |

클래스 `BMOBrain` 단독 기본 budget 180과 실제 `integration_config()`의 320을 혼동하지 않는다. history는 성공한 완전한 응답 뒤에만 추가되고 실패한 턴에서는 기존 쌍이 유지된다. 정책은 기본 짧은 답변을 요청하지만, 사용자가 원하거나 정확성에 필요하면 더 자세히 말하도록 이미 허용한다. 새 모델·reasoning 상향·음색 변경을 하지 않았다.

## 달라진 실제 요청 계약

동일한 빈 capability fixture `{}`와 같은 두 합성 질문으로 확인했다. 이 숫자는 현장 실행 프로세스의 실제 capability payload 크기나 token usage가 아니다.

| 항목 | 성공본 expressive v2 | 후보 Character Life v3 |
|---|---:|---:|
| 최종 instructions 문자 수 | 5493 | 5833 |
| strict schema JSON 문자 수 | 968 | 1546 |
| transport schema 이름 | `bmo_feature_reply_v2` | `bmo_feature_reply_v3` |

v3는 delivery/intensity/gaze/source를 추가한다. `FeatureResponses.create()`는 기존 `expression_request()`의 검증을 재사용한 뒤 최종 instructions와 schema를 **한 개의 v3 계약으로 교체**한다. v1/v2/v3 장문 지시를 모두 중첩하지 않는다. 고정 승인 character/language 정책과 trusted capability context는 계속 포함한다. 한 응답으로 감정·동작 계획을 선택하며 별도 감정 분류 API나 두 번째 brain은 없다.

동일한 320 output budget 안에 v3 metadata도 포함되므로 새 구조의 overhead는 실제 존재한다. 그러나 이번 작업에서 실제 모델 usage·incomplete 발생률·자연스러운 답변 품질을 측정하지 않았으며 이것이 체감 품질 저하의 원인이라고 결론 내리지 않는다. 근거 없이 budget을 올리거나 승인 prompt를 다시 쓰지 않았다.

## 오류·감정 fallback

양쪽 모두 malformed/partial JSON·refusal·실패 응답은 추가 API나 raw-text 음성 fallback 없이 실패한다. `BMOBrain`은 완료 상태와 비어 있지 않은 speech를 확인한 뒤 history를 commit한다. 후보 v3는 잘못된 character metadata까지 검증하며, 부정합이면 degraded neutral/무반응/무제스처로 처리한다. 이는 실제 새 검증 조건으로, model/config 이름이 같다는 설명만으로 prompt·출력 경로가 완전히 동일하다고 말할 수 없다.

현장의 명시적 연기 요청 matcher 문제와 model `source=acting` 처리 문제는 별도 실제 원인이다. 인수한 `1c726fe`의 현장 문구 수정과 이번 실제 제품 경로 표정 추적을 기준으로 requested/model/resolved/emitted/face 상태를 확인한다. 미지원 model source만으로 과잉 연기를 허용하지 않으면서, 정상적인 행복·위로·놀라움을 불필요하게 neutral로 덮는 문제는 구분한다. 이 문서는 새 실제 STT transcript나 모델의 사적인 출력 내용을 공개하지 않는다.

## 부팅 자산 전달 상태

관련 사용자 전달 경로 `/home/bgh/Downloads`, `/home/bgh/Desktop`, `/tmp`의 제한된 사용자 파일 검색에서 `BMO_Boot_Reference_20261030.zip` 또는 `bmo_boot_reference.wav`를 찾지 못했다. `/mnt/data`도 이 Pi에는 없다. 일부 systemd 전용 tmp 디렉터리는 접근 제한을 보존했고 우회하지 않았다. `~/.config/bmo/startup_chime.wav`도 없으므로 **ASSET_NOT_TRANSFERRED**다. 제공된 기대 SHA-256 `ba72dce47853da5a3da5df9e0e65e2b7f9c147720b6653c2ac24845ea1f26082`과 실제 파일의 대조·설치는 아직 할 수 없다.

후보 Character Life는 custom 파일이 없으면 기존 합성 3음 파일을 재사용/생성하지 않고 조용히 생략한다. 별도 음성팩 생성·cache 승인·재생·원음 공개는 하지 않았다. 실제 사용자 파일이 전달된 뒤 기존 startup override 경로에 원본 바이트를 설치하는 단계만 남긴다.
