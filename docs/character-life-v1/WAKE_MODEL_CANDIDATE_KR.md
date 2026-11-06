# Hey Beemo 실제 모델 후보 준비 결과

2026-10-29 기준 공개된 실제 분류 모델을 별도 비공개 캐시에 준비하고 Raspberry Pi의 CPU에서 로드·저장 WAV 추론을 실행했다. 새 녹음·음성 재생·카메라·모터·유료 API는 실행하지 않았다. 모델 파일은 Git에 넣지 않았다.

## 출처와 라이선스

후보는 [amrmantawi/bmo-openwakeword의 고정 리비전](https://huggingface.co/amrmantawi/bmo-openwakeword/tree/58eb9404df18ea1ca64b73ecc9614b1e6ee8f2d6)이다. [모델 카드](https://huggingface.co/amrmantawi/bmo-openwakeword/blob/58eb9404df18ea1ca64b73ecc9614b1e6ee8f2d6/README.md)는 `hey beemo` 합성 발화로 학습한 모델과 Apache-2.0 선언을 제공한다. 별도 학습 데이터 목록이나 양성 검증 WAV는 제공하지 않는다. 학습 데이터의 권리·완전한 출처를 독립 감사했다는 의미는 아니다.

[openWakeWord v0.6.0](https://github.com/dscripka/openWakeWord/tree/v0.6.0)의 코드는 Apache-2.0이다. 공유 임베딩 기반은 upstream이 Apache-2.0으로 설명하지만, **upstream 기본 호출어 분류 모델들은 CC BY-NC-SA 4.0**이다. 이번에는 Jarvis 등의 기본 분류기를 사용하지 않았다. 코드·공유 프런트엔드·사용자 정의 분류기·학습 데이터의 조건을 하나의 상업 이용 허가로 뭉뚱그리지 않는다.

| 파일 | 크기(byte) | SHA-256 |
|---|---:|---|
| hey_beemo.onnx | 206276 | df035b9be9357106b3398368b44dd4f65c4ee88f8cd20612e9b7d850c8b0c015 |
| melspectrogram.onnx | 1087958 | ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f |
| embedding_model.onnx | 1328103 | ba754db3cd768a524c655ea90655ee5e6055a43b8dfd29366a11e93716ae9e51 |

준비 도구는 위 리비전·크기·해시를 고정한다. 기존 파일의 내용이 다르면 덮어쓰지 않고 중단한다. 임의 URL·pickle 검증기·자동 학습·일반 부팅 중 다운로드는 사용하지 않는다.

## 실제 런타임 검증

별도 `.venv-wake`를 만들었으며 현재 실행본과 가상환경은 수정하지 않았다. Python 3.13.5/aarch64, openwakeword 0.6.0, onnxruntime 1.31.1, NumPy 2.2.4, SciPy 1.15.3, scikit-learn 1.9.1 조합에서 세 세션 모두 `CPUExecutionProvider`로 로드됐다. ARM64 CPython 3.13 ONNX Runtime 바이너리 wheel이 존재한다. Linux 패키지 메타데이터의 TFLite 의존성은 이 ONNX 전용 실행에 사용하지 않아, openwakeword만 `--no-deps`로 설치했다. Torch·TensorFlow·OS 패키지를 설치하지 않았다.

실제 세션의 입출력은 분류기 `[1,16,96] → [1,1]`, mel `[dynamic,dynamic] → [dynamic,1,dynamic,32]`, 임베딩 `[dynamic,76,32,1] → [dynamic,1,1,96]`이다. manifest는 기존 가짜 모델 테스트의 2차원 임베딩 출력을 복사하지 않고 실제 4차원 출력에 맞춘다.

기존 비공개 마이크 WAV 3개(9.84초·9.84초·0.84초)에 **실제 ONNX 추론**을 수행했다. 활성화는 각각 0회, 최대 점수는 0.00089917·0.00097680·0.00085980이었다. 당시 한 번 측정한 모델 로드 시간은 1.063514초, 각 파일의 CPU 추론 시간은 0.774381·0.819130·0.048586초였다. 이는 저장 파일 계산 시간이며 실제 호출어 반응 지연이 아니다. 음성 내용은 전사·출력하지 않았다.

세 WAV는 정답 라벨을 검수하지 않았으므로 오탐률·인식률 평가가 아니다. 라이선스가 확인된 upstream 양성 WAV를 찾지 못했다. 사람의 “Hey Beemo” 발음 검수와 한국어 “헤이 비모” 검수는 **NOT_RUN / UNVERIFIED**다. manifest의 `supported_phrases`는 `Hey Beemo` 하나이며, 요청된 세 문구는 상태 정보에서 별도로 표시한다. 모델 READY는 음향 인수 완료가 아니다.

## 도구와 안전한 다음 단계

- `bmo_ai.wake_assets.prepare_candidate(directory, allow_download=True)`: 최초 명시적 준비. 모델은 Git 외부의 비공개 디렉터리에 저장한다.
- `bmo_ai.wake_assets.infer_wav(manifest, path)`: 기존 16kHz/mono/16bit WAV 최대 12초의 CPU 추론 요약. 마이크·재생·API를 실행하지 않는다.
- `inspect_saved_wavs(manifest, paths)`: 최대 3개 파일. 점수 창·해시·공급자·문구 지원 범위를 반환하며 음성 원문은 반환하지 않는다.
- 설치용 선택 의존성은 `software/requirements-wake-cpu.txt`에 기록했다. base lock과 실제 사용한 NumPy·SciPy·requests 등의 버전도 포함한다. 후보 `.venv-device`에도 같은 ONNX 패키지를 격리 설치해 사용자 검수 명령에서 사용할 수 있게 했다. 기존 운영본의 런타임은 변경하지 않았으며 최초 사람 호출어 검수는 별도로 수행한다.

새 격리 환경에서의 명시적 설치 순서는 다음과 같다. `openwakeword`의 Linux 메타데이터는 사용하지 않는 TFLite도 필수로 선언하므로, 해당 패키지만 별도로 `--no-deps`를 사용한다. 이 메타데이터 제약 때문에 이 ONNX 전용 환경의 `pip check`를 완전 PASS라고 표시하지 않는다. 실제 세 세션 CPU 로드와 저장 PCM 추론을 별도로 확인했다. 아래 설치는 운영본 가상환경이나 OS에 적용하지 않는다.

```bash
python3 -m venv /선택한/새로운/격리환경
/선택한/새로운/격리환경/bin/python -m pip install --only-binary=:all: -r software/requirements-wake-cpu.txt
/선택한/새로운/격리환경/bin/python -m pip install --only-binary=:all: --no-deps openwakeword==0.6.0
```

실제 사람의 양성 발화 검수와 단일 마이크 broker 경합 검수 전에는 상시 마이크를 자동 활성화하지 않는다. 기존 GP8 입력을 보존한다.
