# 영어 분노·말투·카메라 병렬 후속 — 2026-10-30

이 문서는 `1c30a150ba826818e4450c624b076ae613f3d471` 후보를 사용한 뒤의
사용자 실기 피드백과, 같은 PR #40에서 인수할 실제 코드 변경이다.

## 이번 사용자 관찰

- 부팅 원음 자체는 좋지만 '띵'과 겹친 반복 전자음이 거슬린다.
- 한국어 화남, 한국어/영어 슬픔은 표현된다.
- 슬픔 다음 `two plus two`에서는 정상 기본 표정으로 돌아온다.
- 영어 `get angry`에서는 기본 얼굴과 약한 `grr`처럼 느껴진다.
- 전반적인 대답이 BMO보다 일반 GPT 말투 같다.
- 대화 검수 때문에 카메라·팔 작업을 순차로 미루지 말고 병렬로 진행한다.

이 관찰을 STT transcript나 모든 표정·wake 성공으로 확대하지 않는다.

## 실제 수정

### 영어 분노

기존 matcher는 `be/act/express`를 지원하지만 `get/become`을 빠뜨렸다.
동일한 모델 `angry/source=acting` 응답을 넣어도 `get angry`는
requested=None → neutral/calm으로 떨어졌다. bounded `get/become
angry/mad/annoyed` 문법을 추가해 요청 → CharacterPlan → 기존
theatrical_angry/override_soft → player-start 표정으로 연결했다.
일반 자기 감정 설명·부정·인용·사실 질문을 연기로 오인하지 않는 검사를
유지했다. 실제 발음 인식과 Fish의 청취상 분노 강도는 자동 PASS가 아니다.

### BMO 말투

[말투 변경 설명](BMO_STYLE_REVISION_20261030_KR.md)과 새 원본
`system-prompt.en.txt`를 함께 인수한다. 작은 자신감·상상놀이·구체적인
짧은 대사로 캐릭터를 표현하고, 서비스형 도입과 자동 후속 질문을 줄인다.
연기 요청을 단순 동의나 `Grr`만으로 끝내지 않도록 새 예시를 넣었다.
실제 고민에는 장난을 접는다. 고정 blob pin도 갱신했으므로 prompt만
일부 복사하지 말고 커밋 전체를 인수해야 한다.

Luna low, Aaron, Beemo, gain 0.25, history/output 설정은 그대로다.
새 LLM/TTS 호출이나 음성팩 생성·덮어쓰기·승인은 하지 않았다.

### 카메라·팔

[같은 앱의 카메라·팔 인계](PARALLEL_CAMERA_ARM_20261030_KR.md)를 따른다.
직전 실행 명령은 카메라 옵션이 없었고, 모든 모델 경유 카메라 intent를
차단하던 구조는 현재 사용자의 명시적 음성 사진 요청까지 막고 있었다.
현재 user의 제한된 직접 요청과 runtime opt-in을 함께 검증한 경우만
한 번의 사진을 허용한다. 응답 완료 후 기존 취소 가능한 카운트다운과
CameraBroker로 촬영한다. 이전 요청·repeat·취소·모델 자체 지시는 권한이
되지 않는다. 사진을 GPT에 전송하는 기능은 추가하지 않았다.

`launch-character-life`는 기존 Desktop 하나에 대화·요청 시 사진·영상을
묶는다. 기본 실행만으로 촬영하거나 팔을 움직이지 않는다. 팔 옵션을
`--install-desktop`에 넘기면 버리고 성공하던 문제도 명시적 오류로 바꿨다.
감독 조건은 실제 해당 실행에만 쓰며 바탕화면 런처에 영구 저장하지 않는다.

현재 팔의 실물 상태는 마지막 기록 `REMOTE_ENABLED=False`, 서보 분리다.
GP10/GP11, 600–2400us, 50Hz, 좌우 반전, GP7/GP8, 기존 보정은 보존했다.
현재 보드·연결 상태가 확인되기 전 실제 motion 완료로 보고하지 않는다.

## 반복 전자음을 줄인 부팅 WAV

사용자가 제공한 WAV를 로컬에서 편집했으며 방송 녹음은 이 Git 저장소에
넣지 않는다. 최신 사용자 제공 파일은 `bmo_boot_reference.wav`다.

- 원본 SHA256: `ba72dce47853da5a3da5df9e0e65e2b7f9c147720b6653c2ac24845ea1f26082`
- 수정 SHA256: `461967e899791f05bc97bd7f2e6f3c9fede68b91d6801f9f4a507c43d84eb6e8`
- mono PCM16/48kHz/1.28초 유지. pitch·길이·정규화·gain 사전 적용 없음.
- 반복되는 약 6.44/9.24/11.36/12.04/14.16/16.96kHz 성분을 해당 시간대에서
  좁게 감쇠했다. 주요 대역별 46.79–59.79dB 감소, 4.8kHz 이하 RMS 변화는
  약 -0.00002dB, clipping 0개다. 잔향·최종 청감은 사용자가 확인한다.
- 이전 ZIP는 원본 패키지다. 새 WAV를 이전 해시로 검사하면 맞지 않는다.
  새 해시 확인 후 기존 `~/.config/bmo/startup_chime.wav` 경로에 설치한다.
  기존 override는 보존하고 source를 재생성하거나 공개 업로드하지 않는다.

## Pi Codex 인수와 병렬 실행

1. 현재 worktree/브랜치/미커밋 변경을 확인하고 같은 PR 최신 커밋을
   fast-forward로 인수한다. 별도 음성팩 변경과 기존 성공본은 보존한다.
2. 대화 후보의 영어 분노/새 말투를 검수 가능한 상태로 준비하면서
   카메라 후보도 같은 런처로 함께 준비한다. 기존 하드웨어 전체 검사는
   반복하지 않는다. 실행 중 앱은 사용자 정상 종료 흐름을 따른다.
3. 팔은 별도 병렬 작업으로 현재 deployed code SHA/REMOTE_ENABLED와
   기존 B1 경로를 확인한다. 커넥터/기구 간섭 여부는 사용자에게 필요한
   항목만 확인하고, 기존 감독 실행 조건에서 기존 명명 gesture를 검수한다.
   재보정·추정 SHA 사용·무인 펌웨어 활성화로 처리하지 않는다.
4. 한 번의 실제 통합 확인은 `Get angry`, 평범한 질문, `사진 찍어줘`다.
   영어 STT가 계속 다르면 로컬에서 그 한 발화의 인식 결과만 확인하고
   requested/model/resolved/emitted trace와 비교한다. 개인 원문은 Git에
   게시하지 않는다. 얼굴 성공과 음성 연출의 청취 평가는 따로 보고한다.
5. 별도 음성팩은 제작 세션이 끝난 후 기존 승인·fingerprint 계약으로
   연결한다. 호출어 한 호흡 입력의 첫 단어 손실 가능성과 ACK의 실물
   청취/입 동기화는 여전히 별도 미완료다. 이번 변경으로 해결했다고 하지 않는다.

후보 실행:

```bash
cd /home/bgh/BMO_CHARACTER_LIFE && ./launch-character-life
```

기존 호출어 모델을 함께 사용할 때:

```bash
cd /home/bgh/BMO_CHARACTER_LIFE && ./launch-character-life \
  --wake-word --wake-question-wait 5 \
  --wake-model-manifest /home/bgh/.local/state/bmo/wake-model-candidates/amrmantawi-bmo-openwakeword-58eb9404df18/manifest.json
```

검증은 주입한 SDK/PCM/장치 경계와 기존 제품 코드를 사용한다. 실제 Pi
촬영·서보 움직임·자연스러운 음성 청취·유료 모델 품질 평가의 PASS가 아니다.
최종 커밋의 CI 결과는 PR #40 Checks에서 확인한다.
