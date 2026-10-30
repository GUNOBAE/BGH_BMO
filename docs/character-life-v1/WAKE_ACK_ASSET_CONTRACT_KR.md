# 호출 확인음의 공유 자산 계약

별도 음성팩 제작 세션과 Host는 아래 계약을 공유한다. 이 작업에서는 음성을 생성·승인·덮어쓰거나 실제 재생하지 않는다.

| 항목 | 값 |
|---|---|
| schema | `bmo_wake_ack_v1` |
| clip ID | `wake_ack_01` |
| 정확한 대본 | `Yeah? What's up?` |
| 비공개 저장 위치 | `~/.cache/bmo/wake-ack-v1/greeting.cache` |
| 컨테이너/검증 | 기존 `GreetingCache`의 `BMO_GREETING_V1` 번들, WAV SHA·cue SHA·spec fingerprint·승인 검증 |
| 음색/발음/gain | 현재 승인된 config의 Aaron ID·Beemo 설정·gain 0.25 |
| 모델/연출 | config의 명시적 Fish 모델·delivery. 다른 모델을 자동 선택하지 않음 |
| 최대 길이 | 3초 |
| 누락·미승인·부정합 | `VISUAL_LISTENING`, 유료 생성·다른 인사음 fallback 없음 |

제작자는 `WakeAckSpec.from_config(config).public()`와 `.fingerprint`를 그대로 사용한다. 일반 boot greeting 또는 기존 voice-bank clip의 spec을 복사하면 지문이 다르므로 Host가 거부한다. 현재 config가 `s2.1-pro-free`인데 다른 모델로 제작한 번들을 동일 자산처럼 연결하지 않는다. 비교·제작·지출·사람 청취 승인은 별도 세션의 명시적 절차를 따른다.

원시 WAV를 기존 `GreetingCache`에 별도로 명시적으로 import할 때 gain을 한 번 적용한다. 이미 gain이 적용된 검수 WAV에 다시 적용하거나 runtime load에서 증폭/감쇠하지 않는다. 실제 청취 후 정확한 WAV SHA에 대한 승인만 인정한다. 이 문서와 `wake_ack.py`는 publish/prepare/approve API를 제공하지 않는다.

Host는 `WakeAckCache(config).load(simulate=False, cancel_check=...)`를 context manager로 사용한다. 승인 자산이면 기존 `GreetingAsset` 임시 스냅샷을 받고, 자기 `AudioFocus`와 기존 `session.voice._play`로 한 번 재생한다. `None`이면 얼굴/눈의 명확한 listening으로 대체한다. 실제 확인음 종료 뒤 fresh 질문 청취를 시작하며, 호출어·확인음 tail을 질문 녹음으로 재사용하지 않는다. 이미 후속 질문이 시작한 분기는 확인음을 건너뛴다. 이 모듈은 player/마이크/brain/history/repeat-cache를 소유하지 않는다.

`snapshot()`은 원음·대화·경로·키 없이 상태·spec 지문·검증된 SHA만 반환한다. 취소 또는 실제 player 오류는 누락 자산으로 숨기거나 다른 음성 재생으로 재시도하지 않는다. runtime load가 쓰는 것은 기존 loader가 관리하는 일시적 private snapshot뿐이며 원래 번들은 읽기만 한다.
