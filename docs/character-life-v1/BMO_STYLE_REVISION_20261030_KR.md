# 현장 피드백에 따른 BMO 말투 후보 — 2026-10-30

사용자가 실제 후보를 듣고 "답변이 비모스럽지 않고 지피티 말투 같다"고
보고하며 수정을 요청했다. 이에 기존 모델·음색을 바꾸지 않고 실행용
대화 정책만 좁게 수정했다. **실제 모델 답변 품질과 청취는 미검수**다.

**현재 담당:** 이 정책의 실행 경로 검증·결함 수정·커밋은 일반/Cloud Codex에서
진행하고, Pi의 실제 말투·얼굴·억양 검수는 사용자가 한다. 앞선 Pi Codex
구현 지시를 대체하는 [현재 후속 기록](CLOUD_IMPLEMENTATION_FOLLOWUP_20261030_KR.md)을
함께 따른다. 재현되는 코드 결함 없이 prompt를 추가 개작하지 않는다.

## 바뀐 지시

- 일반적인 친절함에 더해, 장난스러운 자신감·진지하게 하는 상상놀이·작고
  구체적인 자기 생각을 말투로 표현한다. 매번 농담을 덧붙이는 방식은 아니다.
- "도와드릴 수 있어요"식 서비스 문구, 자동 후속 질문, 과장된 아기 말투와
  발음 흉내를 줄인다. BMO라는 이름과 감탄사를 반복시키지 않는다.
- harmless acting 요청은 "할 수 있다"는 설명이나 `Grr`만으로 대신하지 않고
  짧은 장면의 대사로 연기한다. 실제 고민에는 장난을 접고 간결히 위로한다.
- 분노 연기·힘든 날·단순 계산의 **새로 쓴 예시 세 개**로 표현의 범위를
  보여준다. 작품 대사 인용이나 고정 응답·음성팩은 아니다.

표정 enum과 TTS 연출의 선택·동기화는 기존 CharacterPlan 경로가 맡는다.
프롬프트만으로 STT 인식이나 잘못된 영어 연기 matcher가 해결됐다고 하지
않는다. 해당 코드 회귀와 실제 음성 청취는 별도로 구분한다.

## 참고한 공식 자료와 적용 범위

- [Warner Bros. Discovery의 BMO 소개](https://press.wbd.com/na/bio/bmo):
  작은 로봇의 당돌함, 모험가적 성격, 친구에 대한 충실함을 묘사한다.
- [Cartoon Network 공식 Best of BMO](https://www.youtube.com/watch?v=UqRvCEnCKXo):
  공식 영상 설명에서 카우보이 역할놀이, 양말을 찾는 탐정놀이 등 BMO의
  상상놀이를 소개한다.

위 자료의 설명을 캐릭터 방향의 근거로 삼았으며, 영상을 직접 시청하거나
전체 대사를 분석했다는 주장은 하지 않는다. 구체적인 프롬프트와 예시는
이 로봇을 위한 새로운 작성물이다.

## 정책 식별과 보존

새 원본은 이 폴더의 `system-prompt.en.txt`, 실행 복사본은
`software/bmo_ai/policies/system-prompt.en.txt`다. revision은
`character-life-20261030-v2`, Git blob은
`2b2b5263253ffbce8c3f6c37ec312c06a221ff64`다.

`integration_profile.load_voice_policy()`의 고정 blob 검증을 갱신했다.
두 복사본이 함께 바뀌어도 예상하지 않은 bytes는 계속 거부한다. 아직
존재하지 않는 포함 커밋을 꾸며내지 않고, snapshot은 revision/blob과 기존
기준 커밋을 따로 표시한다.

`docs/voice-policy-v1/system-prompt.en.txt`와 당시 검수 사례는 원래 bytes를
보존한다. 앞선 `FIELD_PROMPT_COMPARISON_20261030_KR.md`의 "동일한 prompt"는
이번 사용자 피드백 전 후보에 대한 역사적 비교이며 새 후보에는 해당하지
않는다. 새 정책은 3,664자에서 4,626자로 늘었으며 별도 모델 호출은 없다.

Luna low, output 320, history, Aaron·Beemo·gain 0.25, 언어 선택,
허위 장치 실행 금지, 카메라·팔 권한, 취소와 기억 규칙을 유지했다.
신규 TTS 생성·음성팩 수정·유료 모델 평가를 실행하지 않았다.
