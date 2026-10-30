# v10 검수와 얼굴·사용 승인 범위

## 이번 GitHub 인계 시 재확인한 결과

사용자에게 이미 전달한 최종 ZIP을 로컬에서 읽고 포함된 CHECK_FILES.py를 실행했다. 네트워크/기기/API 요청 없이 다음 결과를 얻었다.

```json
{"result":"PASS","selected_wavs":142,"unchanged_source_wavs":141,"local_countdown_edits":1,"initial_candidates":60,"total_seconds":495.637642,"api_calls":0,"listening_approval":"NOT_PROVEN_BY_THIS_CHECK","deployment":"NOT_PERFORMED"}
```

검사 범위: 142개 ID·경로 중복/누락 없음, 각 파일 SHA 일치, RIFF/WAV chunk와 frame 길이 유효, 모두 44.1kHz mono PCM16, 포화 샘플 없음, RAW gain1.0, 선택 B 없음, 141개 원본 bytes 유지, 카운트만220500frames 로컬 편집, 초기60과 catalogue 일치.

이것은 파일/수치 검증이다. 내용 누락·이름 발음·태그 낭독·잡음·웃음/울음의 자연스러움·화자 일관성·원작과의 청취 유사성은 이 검사로 증명되지 않는다. 자동 청취 전수 합격이나 개별 listening_approved=true로 바꾸지 않는다. 이번에 Fish/마이크/스피커/카메라/팔은 실행하지 않았다.

## 검수 우선순위

- wake_ack_01: 1.764717초. 기존 계약의 3초 범위 안이며 파일은 있음. 연결/청취 승인 여부는 별도.
- PH_COUNTDOWN_05S: 5.000초. waveform으로 숫자 구간을 나누고 간격 편집한 결과이므로 숫자 누락·호흡·경계·화면 타이밍을 실제로 들어 확인한다.
- RX01~RX04 및 완성 반응문장: 웃음이 글자 낭독처럼 들리지 않는지, 지시문 낭독/불필요한 소리가 없는지 확인한다.
- SYSTEM_VOLUME_UP, REACTION_LINE_TADA_SUCCESS, TRACK_TARGET_RETURNED, 기존 RX05: 같은 출력 설정에서 다른 문장보다 갑자기 크게 튀는지 비교한다. 수치상 차이를 이유로 모든 감정 세기를 평준화하지 않는다.
- C13~C18: Moe/MO/Y5/Ooo 발음. C09/C12 긴 문장의 중간 음색 변화 여부.

## 얼굴 연출

원본 R10과 듣기→생각 감정 유지 정책을 보존한다. neutral/happy/angry/sad/crying/surprised만 기본 감정으로 쓰고 자부심·회상·호기심 때문에 새 얼굴을 만들지 않는다. 실제 발화 중 Hmm이라고 말해도 phase는 speaking이며 thinking으로 바꿔 입을 끊지 않는다.

1차: clip의 기본 감정 + 기존 WAV mouth cue를 재사용한다. 2차: 최종 WAV에서 구절별 시간 anchor를 확인하고 시선을 얹는다. 계획된 anchor를 임의의 초로 확정하거나 전체 길이 비율로만 의미 전환을 맞추지 않는다.

기존 catalogue/방향 문서의 예:
- C01: Finn?에서 왼쪽, Jake?에서 오른쪽, I wonder에서 정면.
- C05: Football 쪽으로 옆을 보다가 But I am Beemo에서 정면. 과장된 화남 금지.
- C11: I did the camera work에서 작은 미소, Beemo has experience까지 유지.
- C12: Finn은 왼쪽, Jake는 오른쪽, They argued에서 정면 기본, 자신의 영화 이야기에서 미소.
- C15: 가족을 회상하다 Finn and Jake에서 정면 미소. 임의 울음 금지.

좌우는 관객이 화면을 보는 기준이다. 눈을 매 프레임 무작위로 흔들지 않고 중요한 구절에서 이동 후 유지한다. 기존 깜빡임·놀람 눈/입 연동·lip cue 알고리즘을 존중한다. tracking이 시선을 소유하면 독백의 좌우/회상 시선은 양보한다. 새 보디/팔 동작을 음성 승인에 끼워 넣지 않는다.

## 사용 정책

- 단일 플레이어/오디오 소유권. 같은 상황의 캐시 안내와 GPT/TTS 확인을 중복 발화하지 않는다.
- 감정 반응은 적절한 맥락/명시 연기에만. 실제 고민에 장난 울음·웃음 자동 삽입 금지.
- 완성 반응문장은 이미 웃음/gasp가 포함될 수 있으므로 앞에 별도 반응 WAV를 또 붙이지 않는다.
- 사진/영상 review에서 화면이 얼굴이 아니면 억지로 얼굴 오버레이를 만들지 않는다. 복귀 확인 후 얼굴로 말한다.
- 사진/영상/게임/음악/대화/quiet에는 자동 idle이 끼어들지 않는다. idle 재생 주기는 여전히 미정이다.
- 중단하면 audio와 남은 시선/표정 cue를 같이 취소하고 늦은 이벤트를 버린다. 입은 닫되 강제 2초 neutral 복귀를 새로 만들지 않는다.
- 현재 승인된 부팅음은 보존. 일반 재시작/reload/미디어 복귀를 새 부팅으로 처리하지 않는다.

통합 완료는 자산 전달, import, 청취 승인, 이벤트 연결, Pi 실제 재생, 세부 cue 검수를 각각 나눠 보고한다.
