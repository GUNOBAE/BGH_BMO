# 통합에서 사용하는 음성 정책 출처

원본 저장소: `GUNOBAE/BGH_BMO`, 브랜치 `docs/bmo-voice-policy-v1-20261022`,
커밋 `707c66b8876130be75ad216fa10aaabf2bdcdb56`.

이 폴더의 `README_KR.md`, `INTEGRATION_HANDOFF_KR.md`, `system-prompt.en.txt`,
`acceptance-cases.json`은 위 커밋에서 가져온 원본 내용이다. 문서의 “미적용”
등 상태 문장은 해당 원본 작성 시점의 기록이며 현재 구현 상태는 통합 보고서로 구분한다.
전체 브랜치를 merge하지 않았다.

실행용 `software/bmo_ai/policies/system-prompt.en.txt`도 같은 prompt의 복사본이다.
원본 Git blob은 `a63a8e5c2c768b14b878aa1ca8468323a05fe554`다.
통합 launcher는 `integration_profile.policy_brain`에서 이 파일을 읽고
`english_voice`를 신뢰한 호스트 정책으로 전달한다. 기존 `en`의 자동 한국어
응답 규칙을 함께 붙이지 않는다. 기존 일반 CLI의 기본 동작은 유지한다.

`acceptance-cases.json`의 20개 항목은 검수 자료다. 이번 오프라인 테스트는
설정·프롬프트 로딩·우선순위·문맥만 확인하며 모델 품질 20개 통과를 뜻하지 않는다.
