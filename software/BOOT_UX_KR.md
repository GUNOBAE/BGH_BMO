# BMO 부팅 UX v2 — PR #24 인수와 기동 진단

Issue #19/#20, 마스터 #23의 코드 후속 작업이다. PR #24의
`4a80bc855175cb1c8259b040b70b3e87ed4e67d8`에서 기존 진행률과 환영 인사를 그대로 인수했다.
100%는 로컬 얼굴/엔진 준비 점수이며 GPT/Fish 연결 성공이나 남은 시간을 뜻하지 않는다.

## 이미 보존한 동작과 보완

- 실제 lock/server/browser/engine/SHA handshake의 완료 이벤트로만 비감소 진행률을 표시한다.
- R10 바깥 shell의 직사각형 바, 퍼센트, 접근성 속성을 사용한다. 원본 작화는 수정하지 않는다.
- 제품 앱은 100%에서 로딩 overlay를 숨긴 뒤 두 번의 animation frame을 거쳐
  현재 run의 `face_presented`를 보낸다. 이 확인 전에는 인사 worker를 시작하지 않는다.
  이것은 브라우저 render 기회 확인이며 LCD 광학 측정은 아니다.
- 새 문구는 `돌아왔구나! 비모가 기다렸어!`. `--status`도 같은 상수를 표시한다.
- 새 캐시는 `~/.cache/bmo/greeting-welcome-v2/greeting.cache`
  (`XDG_CACHE_HOME`이 명시됐다면 그 아래). 이전 `bmo/greeting/` 파일을 읽거나 지우지 않는다.
- 승인된 캐시가 없으면 자동 합성 없이 얼굴과 고정 안내를 표시한다.
  앱당 한 번, reload/게임 복귀로 재생하지 않으며 `/repeat` 답변 캐시와 대화 이력을 보존한다.

## 먼저 할 읽기 전용 확인

프로젝트의 검증된 Python 환경을 사용한다. 새 worktree는 자동 설치되지 않는다.

```sh
BMO_PYTHON=/home/bgh/BMO_FEATURES/.venv/bin/python ./launch-desktop --diagnose-startup
BMO_PYTHON=/home/bgh/BMO_FEATURES/.venv/bin/python ./launch-desktop --status
```

진단은 세션 메타데이터·Chromium 안전 속성·R10 SHA 일치·lock 점유 여부만 확인한다.
파일/프로필/포트/프로세스를 생성하지 않고 키 저장소와 장치를 열지 않는다.
기동 실패는 `stage=browser_start code=runtime_directory_permissions`처럼
등록된 단계와 오류 코드만 출력한다. 예외 문자열·경로·토큰·환경 전체를 출력하지 않는다.

`runtime_directory_permissions`는 해당 디렉터리의 사용자 소유 및 정확한 0700 요구다.
사용자가 이전에 0770→0700으로 직접 수정해 성공했지만, 이 코드는 chmod나 OS/로그인 설정을
변경하지 않는다. 재로그인 후 재발 원인·VNC 그룹 공유 영향은 사용자 입회 조사 항목이다.

## 유료 합성 및 실제 청취 — 사용자 입회 전 NOT_RUN

최초 키 설정은 기존 비공개 영구 설정을 사용한다. 키를 CLI 인자나 이 문서에 넣지 않는다.
아래는 사용자가 명시적으로 실행하는 후속 절차이며 야간 개발 중 실행하지 않았다.

```sh
./launch-desktop --live --prepare-greeting
./launch-desktop --live --audition-greeting
./launch-desktop --live --approve-greeting <준비 결과의 WAV_SHA256>
./launch-desktop --live --windowed
```

`prepare`: Fish 1회, 재생 없음, 미승인. `audition`: 같은 WAV 청취, 추가 합성 없음.
사용자가 한국어 발음·음량을 확인한 뒤 정확한 SHA를 승인한다.
Luna low / Aaron Soft / beemo / gain .25 / Piper 기존 opt-in 정책은 유지한다.
현재 문구의 실제 한국어 품질은 미검증이며 자동 영어 전환·다른 성우 대체는 없다.

## 복구

앱을 정상 종료한 뒤 기존 `/home/bgh/BMO_FEATURES`의 PR #18 실행 경로를 사용하면 된다.
새 worktree와 기존 원본/캐시는 독립이다. 이전 인사 캐시는 그대로 남아 있다.
이 단계는 desktop entry, autostart, 서비스, firmware, OS를 설치하거나 변경하지 않는다.
main 병합·force push·원본 경로 checkout 변경도 없다.
