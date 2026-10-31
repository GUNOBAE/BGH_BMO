# 선택적 로컬 Hey BMO 입력 준비

**2026-10-29 최신 후속:** 아래 MODEL_MISSING·미설치·미검증 서술은 최초 단계의 기록이다. [공개 Hey Beemo 실제 후보](WAKE_MODEL_CANDIDATE_KR.md)를 고정 SHA로 준비해 격리 환경에서 실제 ONNX CPU 로드와 기존 WAV 추론을 완료했다. 사람의 영어/한국어 인식률과 상시 마이크 현장 검수는 여전히 NOT_RUN이다. [최신 인계·수동 검수](REMAINING_CORE_20261029_KR.md)

후속 [2026-10-29 실제 장치 검수](DEVICE_ACCEPTANCE_20261029_KR.md)에서 모델 없는 wake OFF 후 실제 finite PTT를 확인했다. 실제 호출어 모델/음향 시험은 계속 `MODEL_MISSING / NOT_RUN`이다. 아래는 최초 구현의 모델 계약과 당시 검증 범위다.

`LocalWakeDetector`는 `software/bmo_ai/features/input/wake_word.py`의 로컬 ONNX 어댑터다. GP8 PTT를 유지하며, 호출어는 선택 사항이다. 저장소에는 승인된 BMO 호출어 모델이나 그에 맞는 frontend 모델이 없다. 선택적 `numpy`·`onnxruntime`·`openwakeword` 런타임의 실제 설치와 ARM64 추론도 이 작업에서 검증하지 않았다. 자동 설치·모델 학습·다운로드·실제 마이크 활성화는 하지 않는다.

`Hey BMO`, `Hey Beemo`, `헤이 비모` 세 문구를 manifest에 적었다는 사실만으로 인식 성공을 주장하지 않는다. 기존 영어 사전 학습 호출어를 BMO 모델 대신 연결하지 않는다. `READY`는 로컬 파일·런타임·입출력 shape 검사를 통과한 상태이고, 실제 발음·억양·거리·소음에서 인식되었다는 뜻이 아니다. `acoustic_acceptance`와 `real_wake_word`는 실기 인수 전까지 `NOT_RUN`이다.

## 현재 코드가 받는 manifest

별도 검수한 세 ONNX 파일과 manifest를 같은 로컬 폴더에 둔다. 아래 값은 구조 설명용이며, shape·SHA·라이선스를 실제 파일에서 확인해 채워야 한다. 예시 shape를 그대로 복사해 정상 모델로 간주하지 않는다.

```json
{
  "schema": 1,
  "backend": "openwakeword_onnx",
  "custom_model": true,
  "sample_rate": 16000,
  "frame_samples": 1280,
  "frontend": "openwakeword_melspectrogram_embedding_v1",
  "phrases": ["Hey BMO", "Hey Beemo", "헤이 비모"],
  "license": "실제 모델 배포 라이선스 식별자",
  "threshold": 0.5,
  "consecutive_frames": 2,
  "artifacts": {
    "classifier": {
      "file": "hey_bmo.onnx",
      "sha256": "실제 classifier 파일의 소문자 SHA-256 64자리",
      "license": "실제 classifier 라이선스 식별자",
      "input_shape": [1, 16, 96],
      "output_shape": [1, 1]
    },
    "melspectrogram": {
      "file": "melspectrogram.onnx",
      "sha256": "실제 melspectrogram 파일의 소문자 SHA-256 64자리",
      "license": "실제 frontend 라이선스 식별자",
      "input_shape": [1, 1280],
      "output_shape": [1, 1, 5, 32]
    },
    "embedding": {
      "file": "embedding.onnx",
      "sha256": "실제 embedding 파일의 소문자 SHA-256 64자리",
      "license": "실제 embedding 라이선스 식별자",
      "input_shape": [1, 76, 32, 1],
      "output_shape": [1, 96]
    }
  }
}
```

manifest는 symlink가 아닌 일반 파일이어야 하며 최대 32 KiB다. 세 artifact 이름은 `classifier`, `melspectrogram`, `embedding`이다. 각 `file`은 경로 구분자가 없는 같은 폴더의 `.onnx` 파일명이어야 한다. 파일은 symlink가 아닌 일반 파일이고 크기가 0보다 크며 64 MiB 이하여야 한다. 실제 SHA-256과 일치하지 않으면 로드하지 않는다. 전체 모델과 각 artifact에 1–200자 라이선스 정보를 요구하지만, 이 문자열 검사는 사용 허가의 법적 확인을 대신하지 않는다.

입출력 shape는 각 1–4개 차원의 JSON 배열이며 차원은 1–100000의 정수 또는 `null`이다. `null`은 해당 차원의 wildcard다. 실제 ONNX 세션의 입력과 출력이 각각 하나이고 지정한 shape와 맞아야 한다. frontend 두 개와 classifier의 학습 조건이 서로 호환되는지는 별도 확인해야 한다.

`threshold`는 0보다 크고 1보다 작은 유한수이며 기본 0.5다. `consecutive_frames`는 1–5의 정수이며 기본 2다. 이 기본값은 실제 BMO의 튜닝 결과가 아니다. detector는 16 kHz mono signed int16 little-endian PCM을 누적해 1280 samples, 즉 80 ms 단위로 local model에 전달한다. 연속 점수가 기준을 넘을 때만 hit를 낸다. 실제 마이크를 연결하지 않고도 검수된 녹음 데이터로 별도 모델 평가가 가능하지만 이번 작업에서는 수행하지 않았다.

## 활성화·종료와 PTT 복귀 계약

일반 생성자·import·`snapshot()`은 선택적 런타임 import나 마이크 열기를 하지 않는다. 모델이 없는 상태의 `load()`는 `MODEL_MISSING`으로 끝난다. 모델 파일이 유효하지만 런타임이 없으면 `RUNTIME_MISSING`, 해시·shape·구조 오류는 `MODEL_INVALID`, 실행 중 추론 오류는 `MODEL_ERROR`다. 오류 메시지에는 로컬 자산 경로나 원래 런타임 예외를 노출하지 않는다.

`MicrophoneBroker.start_wake(live_opt_in=True)`가 명시적 활성화 경계다. detector가 `READY`가 되었을 때만 persistent reader를 시작할 수 있으므로 실제 장치를 연결한 활성화는 사용자가 감독해야 한다. 이 문서는 활성화 명령을 자동 실행하라는 지시가 아니다. 모델·런타임 미설치 상태에서는 reader나 마이크 source를 만들지 않고 GP8의 유한 녹음 경로를 유지한다.

PTT와 호출어는 broker의 `source_factory()`를 통해 하나의 source를 공유한다. 호출어 reader가 동작 중이면 GP8은 두 번째 녹음 프로세스를 열지 않고 기존 reader의 bounded cursor를 사용한다. 재생·게임·영상 등 소유자가 입력을 허용하지 않으면 호출어를 억제한다. 재생 종료 뒤 cooldown과 detector reset으로 자신의 목소리로 인한 재호출을 막는 구조다. 단, 실제 스피커 누설과 false acceptance를 검증한 것은 아니다.

`stop_wake()`는 persistent reader, 버퍼와 detector를 정리하고 `PTT_ONLY`로 돌아간다. 호출어 reader를 사용하는 동시 capture도 취소한다. 독립적으로 시작된 유한 GP8 capture를 호출어 OFF 때문에 종료하지 않는다. 종료 실패 시 `CLEANUP_FAILED`를 표시하며 새 source를 겹쳐 열지 않는다. 앱 전체 종료에는 broker의 `close()`가 필요하다. 실제 source가 남지 않았는지 `microphone_open`, `reader_alive`, `cleanup_verified`를 함께 확인한다.

실기 전 최소 검수는 승인된 custom 모델과 frontend·라이선스 확보, 실제 세 문구의 인식률/오인식률 측정, GP8과 동시 입력 시 한 turn, BMO 자신의 음성 억제, OFF 후 reader·마이크 종료, 모델 오류 후 GP8 정상 동작이다. 기존 PTT와 실행 중인 BMO를 바꾸지 않고 별도 후보 실행에서 사용자가 감독한다.

## 자동 검사와 미검증 범위

`tests.test_character_wake_word`는 파일 누락·해시·라이선스·shape·symlink·런타임 누락·추론 오류와 80 ms 입력 단위를 검사한다. 또한 주입한 source/detector로 PTT와 호출어의 단일 reader, bounded preroll, 중복 hit 차단, 재생 중 억제, OFF/종료 정리와 실패 시 차단을 검사한다. 테스트용 바이트는 실제 ONNX 모델이 아니며 주입한 detector의 PASS는 음향 인식 성능의 증거가 아니다.

상태를 구분해 보고한다: `INTERFACE_IMPLEMENTED` / `OFFLINE_TESTED` / `MODEL_MISSING` / `OPTIONAL_RUNTIME_NOT_VERIFIED` / `ACOUSTIC_NOT_RUN` / `PI_NOT_RUN`. 실제 호출어 완성이나 모델 설치 성공으로 표시하지 않는다.
