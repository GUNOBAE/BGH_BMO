# PR #40 남은 핵심 3항목 구현·검수 인계 — 2026-10-29 KST

[최신 검토 댓글](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6476906241)을 기준으로 기존 후보 HEAD `141ca6e705c724db4a0fe742f5e84e36e5e01ef4`에서 계속 구현했다. 작업 경로는 `/home/bgh/BMO_CHARACTER_LIFE`, 브랜치는 `feature/bmo-character-life-v1`, 제출은 같은 Draft PR #40이다. Cloud 중복 작업·새 프로젝트·새 PR·main 병합·자동 배포를 하지 않았다. 정확한 제출 SHA와 해당 HEAD의 최종 CI 링크는 PR 완료 댓글에 기록한다.

## 현재 결론

| 구분 | 이번 실제 결과 | 남아 있는 판단 |
|---|---|---|
| PTT 종료 지연 | 저장 원본으로 RMS 상한 종료 재현, 선택적 WebRTC VAD를 실제 Desktop/Host/PTT에 연결 | 사람의 첫 음절·작은 목소리·머뭇거림 및 실제 전체 응답 지연 미측정 |
| Aaron 비교 WAV | **실제 무료 생성 6개**, gain 0.25, SHA·길이·clipping 검증 | 자동 재생 0회, 청취 승인 0개; Drama3는 유료여서 미생성 |
| Hey Beemo 모델 | 실제 공개 ONNX 3개 파일 확보·해시·shape 확인, Pi CPU 로드와 기존 WAV 추론 | 사람의 영어/한국어 양성 발화·인식률·현장 self-wake 억제 미검증 |
| 지난 장치 검수 | 마이크·카메라·스피커·DISABLED B1 조회의 기존 결과 재사용 | 이번 작업에서는 새 녹음·촬영·재생·serial 접근 0회, 물리 팔 이동 안 함 |

**이전 장치 검사 완료와 전체 야간 요청 완료는 다르다.** 이번 핵심 소프트웨어·실제 로컬 자산 준비는 진행됐지만 사람의 발화·청취·추적·팔 움직임과 전체 STT/GPT/Fish 대화 성능을 승인한 것은 아니다.

## 1. 저장 WAV로 재현한 PTT 수정

진단 당시 운영본 HEAD `0f46e3203afcc0d2bb7455cf49b3ac7ba0d4e978`과 후보 출발점 `141ca6e`를 구분했다. 이미 저장한 진단과 설정을 재사용했으며, 후보의 아직 배포하지 않은 코드를 운영본 증상의 원인이라고 단정하지 않았다. `/home/bgh/BMO_REAL_ROBOT` 코드·가상환경·서비스·프로세스는 변경하지 않았다.

normal 저장 PCM의 40ms frame 246/246개가 threshold 0.008 이상이었다. DC 제거 후에도 AC RMS 최저 0.009134여서 단순 DC 제거로 해결되지 않았다. 낮은 주파수에 에너지가 집중됐지만 팬·OS gain·물리 잡음원을 식별한 것은 아니다. high-pass도 비교했으나 작은 저음 보존 문제 때문에 입력/STT PCM에 적용하지 않았다.

`--character-life --ptt-vad-mode webrtc`를 명시하면 기존 녹음·broker·session·STT 경로에서 로컬 WebRTC mode1 판정을 사용한다. 기존 RMS 기본값과 threshold 0.008, silence 0.9초, min speech 0.24초, no-speech 3초 및 원본 PCM을 유지한다. 새 모드는 0.6초 내 positive subframe 합계가 0.24초일 때 entry하고 첫 positive fragment와 preroll을 보존한다. 0.6초를 무조건 기다리는 새 고정 sleep은 없다. 런타임이 없거나 실패하면 명시적으로 실패하며 RMS로 몰래 fallback하지 않는다.

실제 `PttRecorder`에 저장 bytes를 공급한 재현에서 기존 RMS는 저장 상한 9.84초까지 유지됐고 WebRTC는 no-speech 3.00초 sample clock에 종료됐다. 알려진 1200Hz 시험음을 포함한 기존 WAV는 WebRTC에서 silence 1.88초였다. **둘 다 USB 수신 시각이나 사람 발화 종료 지연 실측이 아니다.** WAV에 원래 frame 수신 시각이 없어 이를 만들어내지 않았다.

공개 MIT/BSD reference를 저장 noise에 섞은 구성 시험은 RMS 0.004/0.006/0.008/0.012에서 첫 fragment를 포함한 reference 전체 bytes가 보존됐다. 가장 약한 0.002 overlay는 **MISS**였으며, 깨끗한 background의 0.002 reference·저음 reference·480ms 머뭇거림·취소·오래된 입력 차단을 별도로 검사했다. 이를 실제 한국어 작은 목소리 정확도 PASS로 바꾸지 않는다. 자세한 SHA·재현 명령·한계는 [PTT 분석 보고서](PTT_VAD_REPLAY_KR.md)를 따른다.

## 2. 실제 무료 Aaron 샘플

실행 시점 공식 Fish 모델·가격·OpenAPI와 계정 접근을 확인했다. `model: s2.1-pro-free`만 사용하며 redirect·자동 재시도·유료 fallback은 금지했다. 인증 metadata GET 2회와 TTS POST 6회를 합쳐 **전체 8/8 요청을 소진**했다. 요청 전 durable 전역 ledger를 기록하므로 checkout/cache 변경으로 예산을 초기화하지 않는다. **이번 승인으로 추가 요청을 하지 않는다.**

실제 clip은 `boot_01`, `boot_05`, `reaction_03`, `reaction_07`, `reaction_08`, `reaction_11`이다. 모두 기존 Aaron ID·Beemo 정규화·gain 0.25이며 clipping 0, 실제 길이 0.464399–2.786395초, `simulated=false`, `approved=false`다. 공식 무료 단가 기준 예상 USD 0이며 생성 후 청구서·잔액 재조회는 요청 한도 때문에 하지 않았다.

private runtime bank는 `/home/bgh/.cache/bmo/character-voice-bank-v1`, private 검수 WAV·ledger는 `/home/bgh/.local/state/bmo/voice-bank-free-v1/pr40-comment-6476906241/`에 있다. 원음·키·계정 잔액을 Git·로그·명령행·보고서에 넣지 않았다. 카탈로그 나머지 26개는 음성 미생성이다. Drama3 모델 간 A/B는 USD 0 조건에서 가격으로 차단됐으며 같은 무료 모델 내 문구/연출 비교만 준비했다. 실제 SHA·공식 출처·전 요청 내역 요약은 [무료 Aaron 준비 보고서](FREE_AARON_PREPARATION_KR.md)에 있다.

## 3. 실제 공개 호출어 모델과 ONNX 추론

공개 `amrmantawi/bmo-openwakeword`의 고정 revision `58eb9404df18ea1ca64b73ecc9614b1e6ee8f2d6`을 검토했다. classifier는 `hey_beemo.onnx`, SHA-256 `df035b9be9357106b3398368b44dd4f65c4ee88f8cd20612e9b7d850c8b0c015`다. 공유 mel·embedding도 고정 SHA로 검증했으며 파일 크기·실제 입력/출력 shape·CPUExecutionProvider를 확인했다. upstream의 일반 Jarvis 분류기를 BMO로 바꿔 부르지 않았다.

Python 3.13 ARM64 별도 `.venv-wake`와 후보 `.venv-device`에서 openWakeWord 0.6.0/ONNX Runtime 1.31.1을 사용했다. Linux metadata의 TFLite 필수 선언은 ONNX 전용 경로에서 사용하지 않아 openwakeword만 `--no-deps`로 설치했다. 따라서 선택 환경의 `pip check`를 완전 PASS라고 표시하지 않는다. 기본 `.venv`의 lock 검사 및 CI 환경은 이 선택 런타임과 별개다. OS·실행본 환경·Torch·TensorFlow는 설치/변경하지 않았다.

기존 저장 WAV 3개를 실제 CPU 추론해 활성화 0회와 최대 점수 약 0.00086–0.00098을 얻었다. 정답 라벨이 없는 방 소리이므로 인식률·오탐률 평가가 아니다. 사용자 검수용 `.venv-device`에서도 같은 normal WAV를 추론해 모델 SHA·세 CPU 공급자·0.00089917 점수·활성화 0회를 확인했다. 새 마이크·스피커·네트워크 오디오 업로드는 없다.

모델 카드는 합성 영어 `hey beemo` 학습과 Apache-2.0을 선언한다. 학습 데이터 권리를 독립 감사하지 않았으며 openWakeWord 코드·공유 frontend·기본 분류기 라이선스를 구분했다. `supported_phrases=['Hey Beemo']`이고 `Hey BMO`/`헤이 비모`는 별도 미검증이다. 공급자가 확인한 양성 WAV가 없어 사람의 저장 양성 WAV 추론은 아직 미실행이다. **실제 모델 설치 완료와 음향 인수 완료를 구분한다.** 출처·파일별 SHA·선택 설치법은 [실제 모델 준비 보고서](WAKE_MODEL_CANDIDATE_KR.md)를 따른다.

## 실제 수정 파일과 검증

- `features/input/ambient_vad.py`, `microphone.py`, `pcm.py`: WebRTC evidence, bounded speech entry, raw PCM/첫 fragment 보존, 오류·취소·정리.
- `ptt_replay.py`, 공개 라이선스 reference와 신규 VAD 회귀: 저장 자료 재현과 작은 voice/머뭇거림 검사. 사용자 원음 대신 공개 reference만 CI에 포함.
- `features/host.py`, `desktop_app.py`, `desktop_install.py`, `turn_diagnostics.py`: 명시적 VAD 모드를 실제 앱/설치 계획/안전한 진단에 연결.
- `voice_bank_free.py`, `voice_bank_cli.py`, `fish_tts.py`: exact 무료 모델·현행 공식 계약·계정 검증·영구 전역 8요청 ceiling, 유료 fallback/재시도 차단.
- `wake_assets.py`, `features/input/wake_word.py`, 선택 dependency 목록: 실제 고정 ONNX 자산, 실제 shape·부분 문구 지원·공급자·저장 WAV 추론.
- `character_review.py`, `device_acceptance/microphone.py`: 아래 한 번의 수동 검수 진입점, finite Host/broker/PTT와 로컬 모델 추론 재사용, 기본 PLAN_ONLY.
- Playwright PTT 검사와 GitHub workflow: 기존 RMS 검사를 유지하면서 실제 C WebRTC backend/public reference 경로를 추가. STT/GPT/Fish/player 경계는 합성이며 장치 검사가 아니다.

로컬 Python 3.13.5의 전체 회귀는 1차 **1515개 PASS / 86.717초**다. 이후 수동 검수 도구의 정리 실패 차단·실패 종료 코드 회귀를 추가했고 해당 통합 목표 검사 **8개 PASS**, **최종 전체 1516개 PASS / 86.613초**를 확인했다. Playwright는 공개 fixture 7회와 승인 R10 원본 2회의 **9회 실행·84개 검사 그룹 PASS**다. 기본 RMS, 실제 C WebRTC/public PCM, Character Life, Desktop, camera/media 합성, B1 합성 release 경로를 순차 검사했다. 승인 R10은 headless로 읽기만 했으며 before/after SHA가 일치했다. 기본 `.venv`의 `pip check`, Python AST 261개 파일 검사, `git diff --check`도 통과했다. 선택 모델 환경의 metadata 제약과 구분한다.

Python 3.11/3.13은 시스템 Python을 교체하지 않고 기존 GitHub Actions의 **최종 제출 HEAD**에서 확인한다. workflow에 실제 C WebRTC/PTT 브라우저 검사도 추가했다. CI의 exact SHA·run·job별 결과는 PR 완료 댓글에 기록한다. 이전 141ca6e의 1474 PASS를 이번 커밋의 결과로 재사용하지 않는다.

## 한 번에 시작하는 최소 사용자 검수

기존 앱이 마이크를 사용한다면 그 앱의 정상 종료 메뉴로 먼저 종료한다. 이 도구는 기존 프로세스를 종료하거나 장치를 강탈하지 않는다. 다음 **한 명령**으로 새 후보의 비공개 음성 목록·VAD·호출어를 검수한다. 시작만으로 녹음·재생·호출어 상시 reader가 열리지 않는다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE/software && ../.venv-device/bin/python -B -m bmo_ai.character_review --review --operator-present --wake-model-manifest /home/bgh/.local/state/bmo/wake-model-candidates/amrmantawi-bmo-openwakeword-58eb9404df18/manifest.json
```

메뉴에서 필요한 동작만 선택한다.

1. `list`로 실제 6개 unapproved 샘플을 확인하고 `play boot_05`, `play reaction_07` 등 최대 2개를 직접 선택해 낮은 기존 gain으로 듣는다. 재생 완료가 승인으로 저장되지 않는다. Aaron 정체성·Beemo 발음·연출·첫 음절·태그 낭독 여부를 사람이 판단한다. 나머지는 private WAV로 후속 명시적 청취한다.
2. `record`에서 짧은 첫 음절·작은 목소리·약 0.48초 머뭇거림을 포함해 말한다. 최대 10초, WebRTC mode1/기존 silence 0.9초이며 실제 Host/broker/PTT를 사용한다. `VAD_NO_SPEECH`는 무음 결과이지 문장 인식 성공이 아니다. STT/GPT/Fish로 전송하지 않는다.
3. `wake`에서 “Hey Beemo” 또는 “헤이 비모”를 직접 말한다. 같은 finite PTT reader로 저장 후 실제 로컬 ONNX를 추론한다. `infer`는 마지막 저장 WAV만 다시 계산한다. record/wake 합계 최대 3회, 각 최대 10초다. 양성 검수·음향 수신 점수와 사용자 발음 정확도 승인은 별개다. 이 유한 review는 상시 제품 reader의 현장 self-wake 인수를 대신하지 않는다.
4. `quit`으로 종료한다. Ctrl+C에서도 소유 player/마이크를 정리한다. 카메라·serial·팔·유료 API를 열지 않는다. 저장 원음은 Git 외부 private 폴더만 사용한다. 이 메뉴는 실제 GP8 HID 재검증이나 클라우드 대화 검수가 아니다.

기본 실행의 `--ptt-vad-mode rms`는 그대로 보존되어 있다. 후보 Desktop의 VAD 검수에서 문제가 발견되면 새 옵션을 빼거나 rms를 명시해 비교한다. 운영본은 교체하지 않았으므로 후보 검수 종료 후 기존 `/home/bgh/BMO_REAL_ROBOT`의 승인된 런처를 수동 실행하면 된다. 마지막 조사 때 사용한 복귀 경로는 `cd /home/bgh/BMO_REAL_ROBOT/software && ../.venv-camera/bin/python -B -m bmo_ai.desktop_app --live --windowed --ptt-microphone --ptt-trace`이며 사용자 실행 시 기존 비용/장치 정책을 적용한다. 밤에 자동 재시작하지 않았다. 이미 생성한 새 private 샘플·모델·요청 ledger는 롤백을 이유로 삭제하지 않는다.

## 전체 야간 지시의 남은 인수 항목

기존 [24개 대응표](ACCEPTANCE_STATUS_KR.md)의 소프트웨어 회귀는 보존한다. 감정 유지·명시적 angry/crying·캐시/idle 취소·반복·quiet·wake/PTT 경합은 오프라인 범위에서 검사했다. 사람의 실제 GP8→마이크→VAD→STT→GPT→Fish→스피커 전체 지연은 **UNMEASURED**, 새 Aaron 샘플은 **GENERATED / LISTENING_NOT_APPROVED**, 실제 호출어 모델은 **INSTALLED / CPU_INFERENCE_PASS / HUMAN_ACCURACY_NOT_RUN**, 한국식 발음은 **UNVERIFIED**, 얼굴 추적 품질은 **TARGET_NOT_VISIBLE / NOT_RUN**, 실제 팔 이동은 **NEEDS_OPERATOR**다. 전체 야간 기능을 모두 실기 승인했다고 보고하지 않는다.
