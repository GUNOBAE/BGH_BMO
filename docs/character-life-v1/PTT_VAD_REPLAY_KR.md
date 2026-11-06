# 저장 PCM 재현과 선택적 WebRTC VAD — 2026-10-29

이번 변경은 저장된 비공개 WAV를 로컬에서 다시 읽어 PTT 종료 조건을 개선한 것이다. 새 마이크 녹음, 스피커 재생, 카메라·serial 접근, STT/GPT/Fish 호출은 실행하지 않았다. 실제 사람의 작은 목소리·한국어 발화·발음 정확도와 전체 대화 지연은 아직 승인되지 않았다.

## 1. 운영본과 후보의 차이

기존 읽기 전용 조사 결과를 재사용했다. `/home/bgh/BMO_REAL_ROBOT`의 조사 당시 HEAD는 `0f46e3203afcc0d2bb7455cf49b3ac7ba0d4e978`이며, 이번 후보의 출발점은 `141ca6e705c724db4a0fe742f5e84e36e5e01ef4`이다. 당시 확인한 명령은 저장된 실행 기록으로, 현재 실행 중인 프로세스의 증거가 아니다. 기존 운영본과 가상환경은 수정하지 않았다. [기존 진단 댓글](https://github.com/GUNOBAE/BGH_BMO/issues/39#issuecomment-6469479607)

| 항목 | 기존 운영본의 Desktop 경로 | `141ca6e` 후보의 Desktop 경로 | 이번 선택적 모드 |
|---|---|---|---|
| 입력 | 16kHz mono S16_LE, 40ms frame | 동일 | 동일 |
| speech-safe | True | True | True |
| RMS threshold | profile 미지정 시 0.008 | 0.008 | 설정값 유지, WebRTC 판정에는 적용하지 않음 |
| calibration 설정 | 0.4초 | 0.4초 | 설정값 보존 |
| 유효한 고정 calibration 대기 | 초기 frame 보존 후 재입력 | speech-safe의 불필요한 대기는 이전 단계에서 제거됨 | 추가 대기 없음 |
| 최소 speech evidence | 0.24초 연속 RMS 초과 | 동일 | 0.6초 이내 실제 positive subframe 합계 0.24초 |
| silence / no-speech | 0.9초 / 3초 | 동일 | 동일 |
| 일반 PTT 최대 길이 | 12초 | 동일 | 동일 |
| 원래 유한 장치 검수 상한 | 해당 없음 | 별도 검수 source 10초 | 새 녹음 없이 저장 자료만 재생성 |

두 Desktop 모두 `ptt_speech_safe=True`를 전달하고 profile이 없으면 threshold 0.008을 선택한다. `PttRecorder` 클래스 단독 기본값 threshold 0.02와 혼동하면 안 된다. 기존 RMS ambient profile 경로는 `max(0.008, ambient_rms × 2.5)`이다. 새 모드는 별도 local detector를 사용하며 `rms_threshold_active=False`, `ambient_profile_applied=False`를 노출한다. 단순히 profile이나 threshold를 올려 녹음을 일찍 끊는 변경이 아니다.

읽기 전용으로 비교한 파일 SHA-256:

| 대상 | SHA-256 |
|---|---|
| 기존 운영본 `microphone.py` | `bc6fe164f2ecf2538702d0be42166286b4fd49f9ae754cff559d465f45d96e23` |
| 기존 운영본 `pcm.py` | `70f949c9b954dc74d5818b05f4fd6e64efd5e0e157c360404ae24476a87bf91c` |
| `141ca6e`의 `microphone.py` | `f957f43d2e57db7e1f10c4dbba50601c8f9362eb80554897ee1ddd2725d47378` |
| `141ca6e`의 `pcm.py` | `70f949c9b954dc74d5818b05f4fd6e64efd5e0e157c360404ae24476a87bf91c` |
| 이번 `microphone.py` | `316c8bae55d91be1b5bc01176f92359701edd252dae2ad80c027bfbdc91bce54` |
| 이번 `pcm.py` | `d1885e1ff1aeefb1e1627b267a4ec2fbb07ddc78290ded8ca96e96be6b9d903d` |
| 새 `ambient_vad.py` | `01e3899da3831a1f49f44b1ef25038ae2a26a967facb9d8bd8b5afa45d2ca1c2` |
| 새 `ptt_replay.py` | `d8195a52d96f1dac00ce936b0ea6d89e5bb61f8f05b389d6637f6e316c8d00fc` |

위 SHA는 문서 작성 시의 파일 바이트 기준이다. 제출 커밋·최종 CI는 상위 인계 보고서의 정확한 HEAD와 함께 확인한다.

## 2. 저장 녹음에서 관측한 문제

원본 3개 WAV와 상세 JSON은 `/home/bgh/.local/state/bmo/device-acceptance/20261028T154305Z`에 보존했다. 새 분석 JSON은 0600으로 생성했으며 원본을 덮어쓰지 않았다. 원음·방 내용·음성 인식 결과는 Git에 넣거나 외부로 전송하지 않았다.

| 저장 자료 | 원본 WAV SHA-256 | 관련 관측 |
|---|---|---|
| normal | `61377ac0d01edc1bcc8a2afff4c11fef8f98c538f2257d8d3b3a4027e97782c7` | 246개의 완전한 40ms frame, 9.84초 PCM |
| cancel | `b6710a596571ce895739cb7638a632470218ce2de0a9462f8714e9f5d3a612c2` | 21 frame, 0.84초 PCM |
| reopen / 알려진 시험음 포함 | `19f2c453dedc844b414e8d1227542e99c48f5dd5ffc1bcb81fe03260d95e8b6e` | 246 frame, 9.84초 PCM, 별도 기록된 1200Hz 시험음 |

normal의 frame RMS 최저는 **0.009185**, 중앙값은 **0.012588**, 전체 RMS는 **0.012826**이다. 246/246 frame이 threshold 0.008 이상이다. 기존 RMS 판정에서는 계속 voiced로 남아 silence 종료가 발생하지 않는 현상을 재현할 수 있다. 이는 이 저장 자료와 설정의 결과이며, 사용자가 과거에 경험한 모든 지연의 원인으로 확정하지 않는다.

DC 성분의 에너지 비중은 약 **3.41%**였다. frame 평균을 뺀 AC RMS 최저도 약 **0.009134**여서 DC 제거만으로 0.008 초과 현상을 해결하지 못한다. 640-sample Hann window의 로컬 FFT 분석에서는 50Hz·75Hz bin이 평균 spectral power의 약 **61.95%**를 차지했다. 125/175/150/100Hz 주변 성분도 관측했다. **이 수치로 팬·전원·마이크 고장 등 물리적 발생원을 식별하거나 실제 사람 발화 여부를 판정하지 않았다.**

분석용 1차 high-pass 후보도 비교했다. 160Hz에서 filtered RMS 중앙값은 약 0.00610, threshold 0.008 초과 frame은 8/246으로 줄었지만 작은 목소리의 낮은 주파수 성분도 줄일 수 있다. quiet reference와 noise를 섞은 별도 비교에서 high-pass를 추가하는 방식은 안정적인 개선을 보이지 않았다. 이 필터를 제품 입력이나 STT WAV에 적용하지 않았다. 팬·OS·ALSA gain·Fish gain도 바꾸지 않았다.

로컬 상세 근거:

- `ptt-vad-analysis-20261029-v1.json`: DC·스펙트럼 집계.
- `ptt-saved-frame-replay-20261029-v1.json`: 초기 mode 비교.
- `ptt-vad-comparison-20261029-v2.json`: bounded entry window를 포함한 실제 recorder 재현과 reference overlay 비교. 분석 시점의 코드 SHA도 포함한다. 이후 checks 전용 public reference loader와 특이 frame 길이의 evidence 상한이 추가되었으므로 전체 파일 SHA는 위 최종 작성 시 SHA와 구분한다. 실제 저장 자료의 40ms 판정은 동일하다.

## 3. 구현과 판정 범위

선택적 `webrtc` 모드는 **`webrtcvad-wheels==2.0.14.post1`**, aggressiveness **1**을 사용한다. 이번 격리 `.venv`에 설치한 파일은 CPython3.13/aarch64용 약 86kB wheel이다. 운영본 가상환경이나 OS 패키지는 변경하지 않았다. [패키지의 공식 배포 정보](https://pypi.org/project/webrtcvad-wheels/2.0.14.post1/)

WebRTC는 16-bit mono PCM의 10/20/30ms 단위를 지원한다. 일반 source의 40ms frame은 20ms 두 개로 분석한다. 그 외 허용 frame은 20/10ms와 작은 remainder로 처리한다. detector의 own sub-band noise model을 사용하며 별도 감정·대화·STT API는 없다. [원본 Python API 문서](https://github.com/wiseman/py-webrtcvad), [WebRTC filterbank 원본](https://webrtc.googlesource.com/src/+/refs/heads/main/common_audio/vad/vad_filterbank.c)

25ms처럼 remainder가 생기는 frame에서는 이전 remainder를 현재 frame의 positive 시간으로 과다 계산하지 않도록 현재 frame 길이를 상한으로 삼는다. 일부 시간을 보수적으로 인정하지 않을 수 있으며, 기본 40ms frame에는 영향이 없다.

`PttRecorder(vad_mode="rms"|"webrtc")`에서 기본값은 기존 `rms`이다. 새 모드의 핵심은 다음과 같다.

- `PcmTurnGate`의 기존 버퍼·수명·취소·focus·최대 길이·결과 전달 경로를 재사용한다. STT에 보내는 PCM은 필터링·증폭·변조하지 않는다.
- WebRTC에서 positive인 실제 20/10ms subframe의 길이를 합산한다. **0.6초 bounded window 안에서 총 0.24초**가 필요하다. 짧은 빈틈이 있는 작은 발화도 고려하되 과거 positive가 무한 누적되지는 않는다.
- 0.6초는 항상 기다리는 새 고정 지연이 아니다. 충분한 evidence가 모이면 즉시 entry하며, 종료 silence는 기존 0.9초이다. RMS 모드는 기존 연속 0.24초 규칙을 유지한다.
- qualifying window의 가장 이른 positive sample offset을 보존한다. 판정이 늦게 확정되더라도 첫 fragment를 0.4초 preroll 기준으로 잘라버리지 않는다. sparse noise가 여러 번 나오는 경우에는 window 밖의 evidence를 버린다.
- 명시한 local runtime이 없거나 실패하면 `vad_backend_missing` 또는 `vad_backend_failed`로 실패한다. 조용히 RMS로 돌아가거나 오류를 무음으로 꾸미지 않는다. runtime 확인 실패는 source 생성 전에 발생한다.
- speech detector는 사람·화자·호출어 인식기가 아니다. 음악·시험음 등이 positive가 될 수 있다. 새 snapshot의 `human_accuracy`는 `NOT_RUN`이다.

## 4. 실제 recorder 재현 결과와 한계

`ptt_replay.py`는 저장 bytes를 40ms `PcmFrame`으로 나누어 실제 `PttRecorder`와 `PcmTurnGate`에 공급한다. 새 arecord나 cloud client는 만들지 않는다. **WAV에는 원래 USB frame별 수신 시각이 없으므로 PCM 길이 기반 clock을 사용한다. 아래 시간은 실제 USB 지연·음성 응답 속도 측정이 아니다.** 원래 첫 PCM 수신 약 151ms나 125ms burst 관측을 임의로 복원하지 않았다.

| 재현 자료 | RMS baseline | 선택적 WebRTC mode1 |
|---|---|---|
| normal 원본 | 저장 자료 끝 9.84초에서 `ready/max_duration` | 3.00초 sample clock에서 `cancelled/no_speech`; STT 대상 PCM 없음 |
| 짧은 cancel 원본 | 저장 끝 0.84초에서 baseline entry가 유지됨 | `INCOMPLETE_SAVED_SOURCE`; 3초를 관측하지 않았으므로 no-speech라고 확정하지 않음 |
| 알려진 1200Hz loopback 포함 | 9.84초 저장 끝까지 entry 유지 | 1.88초 sample clock에서 `ready/silence` |

loopback 결과는 짧은 에너지 뒤 종료를 확인한 것이며 **사람 발화 검증이 아니다**. 짧은 파일 끝에 가짜 무음을 붙이거나 부족한 PCM을 padding해서 성공 처리하지 않는다.

### 공개 reference와 구성한 overlay

CI용 reference는 공개 `wiseman/py-webrtcvad`의 `test-audio.raw`이다. pinned commit은 `e283ca41df3a84b0e87fb1f5cb9b21580a286b09`, 파일 SHA-256은 `3dbb730b90e0266d78d4fe03f00aba37a83780a6a5240d9084b5c01266194c1e`이다. upstream test에서 **8kHz mono S16_LE**임을 확인했고 선형 2배 보간으로 16kHz를 구성했다. 원본과 MIT wrapper/BSD WebRTC 고지, 출처 metadata를 `software/tests/fixtures/webrtcvad_reference*`에 함께 보존했다. 이는 Aaron 음성팩이나 사용자 목소리가 아니다. [pinned format 확인 코드](https://github.com/wiseman/py-webrtcvad/blob/e283ca41df3a84b0e87fb1f5cb9b21580a286b09/test_webrtcvad.py#L55), [pinned 라이선스](https://github.com/wiseman/py-webrtcvad/blob/e283ca41df3a84b0e87fb1f5cb9b21580a286b09/LICENSE)

저장 normal PCM 위에 이 공개 reference를 수학적으로 섞었다. 이는 **구성한 reference overlay**이며 실제 사람이 방에서 말한 자료가 아니다. 새 음성을 생성·재생하거나 결과 WAV를 외부로 전송하지 않았다.

| 구성한 reference RMS | 결과 | 구성한 16kHz mixture의 reference 구간 전체 byte 보존 |
|---:|---|---|
| 0.002 | `no_speech` — **MISS** | 아니오 |
| 0.004 | `silence` | 예 |
| 0.006 | `silence` | 예 |
| 0.008 | `silence` | 예 |
| 0.012 | `silence` | 예 |

0.002 reference는 조용한 합성 background에서는 entry하지만 저장 noise와 섞으면 놓쳤다. 이 한계를 숨기지 않는다. bounded window는 fragmented quiet evidence를 보완하는 대신 근접한 non-speech burst가 entry할 가능성도 늘릴 수 있다. 사용자 앞에서 실제 첫 음절·낮은 목소리·짧은 머뭇거림을 확인해야 한다.

장치 없는 회귀에서는 공개 reference의 낮은 RMS, 1.4배 stretch한 낮은 pitch, 480ms pause를 둔 두 구절, 긴 leading silence 뒤 preroll, cancel, 정확한 raw byte 보존을 확인했다. 0.6초 qualification 동안 첫 fragment가 기존 0.4초 preroll보다 앞서 있는 사례도 보존했다. sparse positive noise만으로 무한 누적 entry하지 않는 검사도 추가했다. 이는 인간 발화 정확도 승인이나 모든 noisy pause에 대한 보장이 아니다.

관련 회귀 163개가 통과했고, public loader 및 remainder 상한 추가 후 새 VAD/reference 검사 17개도 통과했다. 최종 전체 회귀·브라우저·Python3.11/3.13 CI는 상위 제출 보고서에 최신 HEAD 기준으로 기록한다.

## 5. 재현 명령과 복귀

다음 명령은 기존 로컬 WAV만 읽으며 장치를 열거나 API를 호출하지 않는다. 출력 파일은 기존 것을 덮어쓰지 않으므로 새 이름을 선택한다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE/software
PTT_REPLAY_WAV=/home/bgh/.local/state/bmo/device-acceptance/20261028T154305Z/mic-normal-aa89cc7ade05417ba5b9a27ac9be7210.wav
../.venv/bin/python -B -m bmo_ai.ptt_replay \
  --wav "$PTT_REPLAY_WAV" --vad-mode compare \
  --private-report /home/bgh/.local/state/bmo/device-acceptance/20261028T154305Z/operator-ptt-replay.json
```

같은 저장 자료에 공개 reference를 섞는 오프라인 비교:

```bash
../.venv/bin/python -B -m bmo_ai.ptt_replay \
  --wav "$PTT_REPLAY_WAV" --vad-mode compare \
  --reference-fixture tests/fixtures/webrtcvad_reference_8khz_s16le.raw \
  --overlay-rms 0.004 \
  --private-report /home/bgh/.local/state/bmo/device-acceptance/20261028T154305Z/operator-reference-overlay.json
```

브라우저 checks는 `load_public_reference_pcm16k(target_rms=.004)`를 호출해 공개 fixture만 사용할 수 있다. 반환값은 16kHz mono S16_LE이며 trailing silence는 없다. 테스트가 필요할 때만 명시적으로 synthetic silence를 붙인다. 일반 제품 부팅은 이 함수를 호출하지 않는다.

후보 실행에서 `--ptt-vad-mode webrtc`를 **명시했을 때만** 새 모드를 쓴다. 기존 동작으로 돌아가려면 `--ptt-vad-mode rms`를 선택하거나 해당 옵션을 생략한다. `--ptt-vad-profile baseline`과 기존 0.9초 silence는 유지한다. 운영본 checkout·가상환경·서비스를 업데이트하는 롤백 절차는 필요하지 않다. 실제 장치 검수는 상위 인계서의 단일 수동 검수 경로에서 사용자가 선택한 동작만 실행하며, 이번 분석을 이유로 이전 실물 검수를 반복하지 않는다.
