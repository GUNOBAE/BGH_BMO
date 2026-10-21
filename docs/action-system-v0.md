# BMO Action System v0

v0는 구조화된 요청을 검증하고 가상 실행 결과를 반환하는 독립 모듈이다. 실제 장치, 미디어, 외부 API와 연결하지 않는다. 성공 결과는 항상 `status="simulated"`, `code="mock_only"`, `executed=False`이며, 촬영 파일 생성, 음악 재생, 게임 실행, 팔 움직임은 발생하지 않는다.

## 변경 범위

- `software/bmo_ai/actions.py`: 요청·결과 모델, 검증, 중복 방지, 고정 Mock 실행기.
- `software/tests/test_actions.py`: 이 모듈만 대상으로 하는 오프라인 `unittest`.
- `docs/action-system-v0.md`: 계약과 통합 경계.

기존 대화·얼굴·음성·하드웨어 모듈과 기존 테스트는 변경하지 않는다. GPIO, RP2040, 서보, IMU, 카메라, 마이크, 스피커에 접근하지 않으며 네트워크 요청, 패키지 설치, 프로세스 실행, 서비스·부팅 설정 변경도 수행하지 않는다. `DeniedArmController`의 팔 차단을 유지한다.

## 요청 계약

`ActionRequest(action, arguments, request_id)`는 불변 요청이며 인자도 생성 시점의 불변 복사본으로 보관한다. 호출자가 원본 딕셔너리를 바꿔도 검증된 요청은 바뀌지 않는다. `ActionRequest.from_payload(...)`로 구조화된 입력을 검증하고, `.to_dict()`로 일반 딕셔너리 복사본을 얻을 수 있다.

`ActionDispatcher(*, max_requests=1024).dispatch(payload)`는 요청 딕셔너리 또는 `ActionRequest`를 받아 `ActionResult`를 반환한다. 자연어에서 명령을 검색하거나 원시 JSON 문자열을 파싱하지 않는다.

```python
from bmo_ai.actions import ActionDispatcher

dispatcher = ActionDispatcher()
result = dispatcher.dispatch({
    "action": "play_song",
    "arguments": {"song_id": "demo_song"},
    "request_id": "demo-001",
})
assert result.status == "simulated"
assert result.executed is False
```

딕셔너리에는 다음 세 필드가 정확히 있어야 한다. 필드 추가·누락과 잘못된 자료형은 거부한다.

| 필드 | 허용 값 |
| --- | --- |
| `action` | 최대 32자인 아래 허용 명령 이름 |
| `arguments` | 명령별로 정해진 딕셔너리. 최대 1개 필드, 중첩 값 금지 |
| `request_id` | 1~64자의 ASCII ID. 정규식 `[A-Za-z0-9][A-Za-z0-9_-]{0,63}` |

전체 요청은 최대 3개 필드이며 추가 필드를 무시하지 않는다. 자산 ID는 최대 32자이고 명시적 허용 목록에 속해야 한다. 빈 ID, 공백, 경로, URL, 셸 문자열, 임의 값, 중첩 자료구조, 지나치게 긴 값은 실행 대상으로 받아들이지 않는다. 오류 결과에 임의의 입력이나 예외 내용을 그대로 노출하지 않는다.

## 명령과 인자

| 명령 | 허용되는 `arguments` | v0 결과 |
| --- | --- | --- |
| `raise_left_arm` | `{}` | 왼팔 요청 가상 실행 |
| `raise_right_arm` | `{}` | 오른팔 요청 가상 실행 |
| `take_photo` | `{}` | 촬영 요청 가상 실행, 파일 없음 |
| `play_song` | `{"song_id": "demo_song"}` | 음악 요청 가상 실행 |
| `launch_game` | `{"game_id": "demo_game"}` | 게임 요청 가상 실행 |

곡과 게임의 불변 허용 목록은 각각 `demo_song`, `demo_game`만 포함한다. 이들은 논리 ID를 설명하기 위한 자리표시자이며 실제 파일, URL, 명령 또는 프로그램에 연결되지 않는다. 허용 목록에 없는 ID는 거부한다.

## 결과와 오류

`ActionResult`는 `request_id`, `action`, `status`, `code`, `message`를 갖는 불변 결과다. `simulated` 속성은 `status == "simulated"`일 때만 참이며, `executed` 속성은 언제나 거짓이다. `.to_dict()`는 위 필드와 두 속성을 포함한 딕셔너리를 반환한다. 호출자는 메시지 문구 대신 `status`와 `code`로 분기한다.

| `status` | 의미 | 대표 `code` |
| --- | --- | --- |
| `simulated` | Mock 처리 성공 | `mock_only` |
| `rejected` | 검증·정책·용량 검사에서 거부 | `invalid_request`, `invalid_arguments`, `unsupported_action`, `request_too_large`, `request_id_conflict`, `request_capacity_exceeded` |
| `duplicate` | 같은 ID와 같은 검증된 요청의 반복 | `duplicate_request` |
| `error` | 실행기 예외 또는 잘못된 실행기 반환값 | `executor_error`, `invalid_executor_result` |

잘못된 요청을 결과에 그대로 되돌려주지 않으며 정상적인 ID나 명령을 확정할 수 없는 경우 해당 결과 필드는 비워질 수 있다. 실행기 예외의 상세 문자열도 결과에 포함하지 않는다.

실행기는 고정 `MockActionExecutor`만 사용한다. v0에는 실제 실행기 주입, 콜백, 환경 변수로 실제 실행을 켜는 옵션이나 자동 장치 탐색 경로가 없다.

## 요청 ID와 중복 방지

중복 기록은 Dispatcher 인스턴스 내부 메모리에 보관하며 동시 호출에도 같은 ID의 요청은 한 번만 Mock 실행기에 전달한다. 검증에 실패한 요청은 ID를 차지하지 않는다. 검증을 통과한 요청은 실행기 호출 전에 ID를 기록하므로 실행기에서 예외가 발생해도 같은 요청을 다시 실행하지 않는다.

- 같은 ID와 같은 검증된 내용: `duplicate` / `duplicate_request`.
- 같은 ID와 다른 검증된 내용: `rejected` / `request_id_conflict`.
- 기록 용량이 찬 뒤 새로운 유효 ID: `rejected` / `request_capacity_exceeded`.

기본 용량은 1,024개이며 생성자에서 1~10,000개로 정할 수 있다. 오래된 기록을 제거하거나 기록을 초기화하는 API는 없다. 포화 상태에서도 기존 ID의 중복·충돌 판정은 유지한다. 용량 부족을 우회하려고 Dispatcher를 자동 재생성해서는 안 된다.

기록은 프로세스 및 인스턴스 단위다. 재시작하거나 새 Dispatcher를 만들면 이전 기록은 사라진다. 따라서 v0의 중복 방지는 재시작을 넘는 영속적 실행 보장을 제공하지 않는다.

## 오프라인 테스트

작업 경로 `/home/bgh/BGH_BMO_ACTIONS`에서 새 테스트 파일만 선택한다. `TMPDIR`은 같은 작업 폴더 안의 기존 쓰기 가능한 디렉터리로 지정하고 바이트코드 생성을 끈다.

```bash
TMPDIR=/home/bgh/BGH_BMO_ACTIONS PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=software python3 -B -m unittest discover -s software/tests -p test_actions.py -v
```

테스트는 정상 명령 5종, 인자·필드·크기 검증, 미지원 명령, ID 중복·충돌·용량 제한, 실행기 예외와 잘못된 반환값, 동시 호출 및 실제 장치 접근 없는 처리를 확인한다. 기존 테스트 전체와 실제 BMO 앱은 실행하지 않는다. 테스트에서 실제 촬영·음악·게임·하드웨어 동작을 호출하거나 결과 파일을 생성하지 않는다.

## 이후 통합에 필요한 작업

실제 통합은 별도 작업 범위에서 진행해야 한다. GPT Function Calling 등의 구조화된 `action` / `arguments` / `request_id`를 검증된 요청으로 변환하는 어댑터가 필요하다. 자연어 응답을 명령 문자열로 검색해서 실행해서는 안 된다. 재시도와 중복 전달에서도 같은 ID를 유지하도록 요청 생성 주체를 정해야 한다.

ConversationWorker와 연결할 때는 작업 큐와 결과 전달 경계를 정의하고, 얼굴·TTS는 결과 소비자로 연결한다. `simulated`를 실제 촬영·재생·팔 동작 성공으로 표현해서는 안 된다. 카메라 어댑터와의 장치 계약을 별도로 검토하고 기존 `DeniedArmController`를 보존한다. 이번 버전에서는 이 연결을 구현하지 않는다.

실제 실행을 추가하려면 영속적 중복 기록, 사용자 승인과 권한 정책, 실행 결과·실패·취소 계약을 먼저 정해야 한다. 실제 모터·서보 및 장치 실행은 별도 범위와 명시적 승인을 전제로 하며, v0의 Mock 성공을 실제 안전성 검증으로 간주하지 않는다.
