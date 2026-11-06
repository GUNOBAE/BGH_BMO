# v10 음악 후보 인수 절차

현재 개발 브랜치 `feature/bmo-character-life-v1`, 기존 PR #40을 이어간다.
배포 SHA와 해당 SHA의 CI는 PR의 최신 구현 인계 댓글이 최종 기준이다.
문서 브랜치로 checkout하지 않는다. Cloud는 코드 writer, Pi는 private 자산·
설정·설치·실기 담당이다. 정상본 `/home/bgh/BMO_REAL_ROBOT`은 보존한다.

근거: [공통 작업서](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6534211753),
[v10 문서](https://github.com/GUNOBAE/BGH_BMO/blob/2023f3a9c12ce4c0628afdfc8deda4a8831ec241/docs/voice-pack-v10/README_KR.md),
[Pi 실제 인계](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6534420182).

## 2026-11-06 일상 사용 마감 — 같은 BMO Candidate 아이콘

최신 [Pi 인계6671555201](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6671555201)는
**34175607…의 목표·설치·runner·실제 아이콘 프로세스 SHA 일치**를 확인했다. full-use142,
normal, M04→M06→M05 실제 재생·EN/KO 자막·기존 음악 입 동작을 관측했다. 사용자의
추가 요청으로 마지막 상태는 level0.05/muted=true였다. 최신 idle 연출은 그 음소거 이후
NOT_RUN이며 사람 청취/팔 검수/실제 대화 지연은 여전히 별도다. 이전 설치본432/5a 기록은
아래 역사 항목에 남긴다. 새 READY SHA의 설치를 이 관측으로 대신하지 않는다.

이번 제품 경로:

- 평상시 **얼굴 한 번 터치 → 메뉴 → 혼잣말 테스트 / 팔 움직이기**. 드래그·취소는
  메뉴를 열지 않는다. 볼륨 HUD·음악·게임·사진 입력은 자기 화면이 소유한다.
  **얼굴로 돌아가기** 후 Enter/GP8은 다시 PTT다. Esc/왼쪽 위 접근도 유지한다.
- 정상 얼굴의 선택적 인사 캐시 안내띠를 제거했다. **준비 상태**에는 말하는 시작 인사와
  **승인 시작 효과음** 상태를 따로 표시한다. 같은 기존 WAV를 얼굴 표시 뒤 앱당 한 번,
  같은 MasterVolume/Mute와 단일 출력으로 재생한다. 메뉴 복귀/재연결/인사 재시도는
  효과음을 다시 재생하지 않는다. 새 자산이나 유료 생성은 없다.
- 자동 혼잣말은 **3–5분 기회, 이동하는15분 실제 player-start 최대3회**다. 시작 전
  실패/취소는0회, 시작 후 중단은1회다. 자동 횟수와 수동 테스트 횟수는 분리한다.
  쿼터 만료·활동 종료 후에는 새 간격을 두며 몰아 재생하지 않는다. 같은 클립30분 억제,
  민감한 대화·대화/음악/게임/녹음·메뉴·음소거 억제는 유지한다.
- **혼잣말 테스트**는 메뉴를 닫고 얼굴에서 저장 클립을 한 번 재생한다. 자동 OFF여도
  가능하며 자동 설정·횟수를 바꾸지 않는다. 실제 ID/SHA의 표정·시선·WAV 입·선택 자막을
  player-start에 적용한다. 연타/다른 audio owner/음소거는 이유를 표시하고 추가 예약하지
  않는다. GP8/새 질문이 우선하며 종료·취소·오류 때 얼굴과 오디오를 정리한다.
- **팔 움직이기**는 손 흔들기/왼팔 들기/오른팔 들기/양팔 들기/양팔 내리기/만세/
  어깨 으쓱/팔 중지를 항상 표시한다. 버튼 존재와 현재 요청 가능 여부는 별개다.
  자연어와 버튼은 같은 B1 named-gesture 경로를 쓴다. 요청 접수, B1 응답, 실제 움직임을
  구분하며 대답 문구가 실물 완료를 미리 주장하지 않는다.
- **팔 사용**과 **대화 중 몸짓**은 별도 저장 선호다. 처음 실물 검사를 통과한 뒤에만
  아래 검수 기록을 만들고 같은 기본 아이콘에서 팔 사용을 켠다. 과거의 입회/전원 차단
  준비를 현재 true로 저장하지 않는다. 저장하는 것은 사용자 선호와 과거 검수한 exact
  보드 SHA/의존 파일 SHA/장치 identity/기존 보정 식별자다. 매 실행의 첫 실제 요청에
  현재 파일·장치와 HELLO를 확인한다. 실패는 팔만 차단하고 자동 재시도하지 않는다.
  OFF→ON/앱 재실행은 STOP/GP7/watchdog latch를 해제하지 않는다.
- 음량/Mute·자막·혼잣말은 명시적 메뉴/음성 변경만 private
  `${XDG_CONFIG_HOME:-~/.config}/bmo/preferences/preferences.json`에 저장한다.
  처음 저장값이 없으면 기존 시작25%/자막OFF/런처 presence 기본값을 사용한다. 저장된
  사용자 선택이 이후 같은 아이콘의 초기 옵션보다 우선한다. 팔 설정은 별도
  `~/.config/bmo/arms/daily.json`이다. 잘못된 파일은 덮어쓰지 않고 해당 저장 상태를
  준비 상태에서 알린다. 테스트는 주입 임시 저장소를 쓰며 실제 사용자 완료 턴에 쓰지 않는다.

### Pi 적용과 기존 정상본 보존

최신 CLOUD READY와 **그 exact SHA의 push/PR 각각3개 필수검사 성공**을 먼저 확인한다.
현재 앱에서 **메뉴 → 종료** 후 기존 private release gate/runner로 같은 아이콘의 후보만
ff-only 인수한다. dirty/분기/다른 실행 중 앱이면 멈추고 보존하며 reset/stash/force하지 않는다.
**목표 SHA = 설치 SHA = runner 인수 SHA = 아이콘 실행본 SHA**를 run_id와 함께 기록한다.
아래 read-only 확인은 실행 중 코드를 바꾸지 않는다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE
git status --short
git branch --show-current
git rev-parse HEAD
# 기존 private 인수 gate가 READY SHA/CI와 clean 상태를 검증한 후 동일 아이콘 실행
python3 -B /home/bgh/BMO_private/tools/run_bmo_candidate.py
```

첫 전환 때 구버전의 메모리 설정은 자동 복원할 수 없다. Pi는 종료 전 실제 mute/level/
자막/presence를 확인해 보존한다. 특히 마지막 사용자 음소거를 임의 해제하지 않는다.
필요하면 새 코드의 `DailyPreferences().update(...)`로 확인한 값만 이관하고 같은 아이콘을
실행한다. 이후 제품이 명시적 설정을 저장한다. OS mixer/키/기존 자산/정상본은 바꾸지 않는다.

팔은 기존 아이콘의 **감독 팔 검사 (이번 실행만)** 에서 실제 현재 조건을 확인하고 검사한다.
아래 기록 명령은 검사를 대신하지 않는다. 각 플래그는 **실제로 통과한 뒤 Pi 담당이** 넣는다.
기본 명령은 읽기 전용이며 helper는 serial/서보/펌웨어에 쓰지 않는다.

```bash
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.arm_usage
# 실제 입회 검사 완료 뒤에만 실제 검수 세션 ID와 검토 SHA를 입력:
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.arm_usage \
 --record-first-test --reviewed-code-sha <실제-검토한-64자리-SHA256> \
 --test-session-id <실제-감독-검수-ID> \
 --motion-observed --stop-verified --gp7-verified --watchdog-verified --physical-cutoff-verified
```

그 후 같은 기본 아이콘을 재실행하고 **팔 움직이기 → 팔 사용**을 켠다. 대화 몸짓은
명시 동작·중지 검수 뒤 별도로 켠다. STOP 이후에는 현재 보드 상태에 따라 계속 차단될 수
있으며 이 기록이나 재시작이 해제하지 않는다. 원래 GP7/8·GP10/11·600–2400µs·50Hz·좌우반전은
그대로다. 정상본 복귀가 필요하면 후보를 메뉴에서 종료하고 기존 BMO_REAL_ROBOT의 검증된
아이콘/실행 설정을 사용한다. 최신 전용 옵션을 옛 런처에 넘기거나 정상본을 덮어쓰지 않는다.

실제 대화 단계별 시간은 최신 Pi에서도 UNMEASURED다. worktree SHA null과 합성 완료 기록
오염은 코드에서 수정했지만 이것을 응답 속도 개선이라고 보고하지 않는다. 기존 사람 대화의
수치가 전달되면 가장 큰 단계부터 비교하며 모델/음색/VAD를 근거 없이 바꾸지 않는다.

## 이전 34175607 인계 기록 — 실행본 증거·완료 지연·혼잣말 연출·팔 상태

시작 HEAD `5a926451ae08358921d7b48dce72be4484106c85`, clean, push/PR 6검사
PASS를 확인했다. 사용자 음성 볼륨 PASS를 보존한다. 음악 반복/idle 침묵에 대해
최신 후보 설치를 추정하지 않는다. 마지막 Pi 설치 기록은 `43262705…`/release61이며
전체 사용 허용·셔플·idle 구현을 다시 만들지 않는다. 같은 **BMO Candidate** 아이콘이 기준이다.

- 실행 중 준비 상태의 `runtime_provenance.source_revision_at_start`는 owner 시작 때
  읽은 checkout HEAD다. clean 또는 private runner 인수까지 검증했다는 뜻은 아니다.
  Pi는 **목표 SHA = 설치 SHA = runner 인수 SHA = 아이콘 실행본 SHA** 네 값을
  확인하고 run_id와 연결한다. 실행 중 자동 Git 갱신은 하지 않는다.
- live Character Life는 완료 턴의 수치/상태/실행 SHA만 private
  `${XDG_STATE_HOME:-~/.local/state}/bmo/turn-diagnostics/completed-turns.json`에
  최대32개 보존한다(디렉터리0700/파일0600, 원문·PCM·키·provider 응답 없음).
  저장 실패는 진단만 비활성화하고 대화를 막거나 재시도하지 않는다. 발화 마지막
  voiced frame→VAD close→capture close→STT→LLM→Fish 첫 바이트/다운로드→player-start를
  분리하며 미관측은 UNMEASURED다. 실제 스피커 첫 소리는 측정하지 않는다.
  과거 단계별 수치는 전달되지 않아 병목/속도 개선을 단정하지 않는다. desktop speech-safe의
  기존 calibration skip, 모델/reasoning/Fish/단일 owner/repeat/입 clock은 유지한다.
- 기본 VAD baseline0.9는 유지한다. 기존 balanced0.6 첫 비교는 다음 명령으로
  **기존 private WAV**만 사용한다. 공개 reference의 약한 발화+480ms 쉼은
  balanced에서 다음 구절을 잃는 경계를 재현했다. 짧은 설정값을 실제 지연 개선이나
  발화 보존 PASS로 대신하지 않으며 Pi 실제 작은 목소리/머뭇거림 판단 전 자동 적용하지 않는다.

```bash
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.ptt_replay \
 --wav /absolute/private/existing-question.wav --vad-mode webrtc --vad-profile compare
```

- C01–C18의 정확한 원문과 실제 PCM ID/SHA에 작은 로컬 `performance_plan`을
  연결한다. 자동 idle 적격은 기존 길이 제한 내16개이며 C09/C12는 조건부다.
  prepared에서 연기하지 않고 player-start에 기존 R10 표정, WAV 입, 선택적 한 번의
  작은 시선을 적용한다. 구절 cue/새 도형/음성 재생성은 없다. camera/manual/native
  활동이 우선하고 idle speaking과 조용한 idle gaze는 다른 lease를 쓴다. 종료·GP8·새 질문·
  mode OFF·오류는 해당 연출을 정리한다. 음악 입 경로는 그대로다.
- 준비 상태에서 mode/적격 수/next_in_seconds/억제 이유, release 범위, pin/origin,
  마지막 선택을 확인한다. 진단 조회는 선택 이력을 바꾸거나 매번 PCM을 재검증하지 않는다.
  실제 재생 load의 SHA 검증은 유지한다. full-use/normal인지 먼저 확인하고,
  같은 최신 설정에서도 실패할 때만 clip/pin/owner/timing 증거로 추가 결함을 판단한다.
- [Pi 댓글6653292587의 후속](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6653292587)은
  **실제 보드 REMOTE_ENABLED=True**와 적용 SHA `0f69420e2e19500e40a925f0ad71033d86780b8c5933302713795103d1fa4a2e`를
  확인했다. 이는 일반 아이콘의 감독 실행 또는 emotion-arms 활성화를 뜻하지 않는다.
  이번 상태 표시는 요청/유효 감독 설정, emotion-arms, 현재 turn plan/player-start,
  adapter/최근 ACK/차단 이유를 분리한다. static board 상태는 Cloud가 읽지 않아 null이다.
  선택 팔 초기화 실패에 emotion-arms가 남아 앱 전체를 막던 경로는 팔만 OFF로 낮추고
  대화·얼굴을 유지한다. 요청값과 실패 원인은 남긴다.
- STOP/GP7/watchdog latch는 **보드 runtime 상태**다. HELLO와 Desktop 재실행은 해제하지
  않으며 STOP/HOLD는 전원 차단이 아니다. 보드 재시작을 무인 해제 절차로 안내하지 않는다.
  HELLO STOPPED도 host의 stopped 증거에 반영한다. 펌웨어·보정·실제 포트/서보 조작은 없다.

Pi 인수: 기존 아래 clean/ff-only 절차와 최신 READY 댓글의 exact SHA를 사용하고 private
runner receipt도 같은 SHA로 검토 갱신한다. 같은 아이콘과 GUI/VNC 복구를 유지한다.
142개 재import/대표10 재청취 없이 기존 `accept-full-use --user-accepted`를 사용하고,
아이콘의 기존 실행 옵션에 `--presence-mode normal`을 Pi가 반영한다. 팔 옵션은 현재
물리 입회/전원 차단 조건이 있을 때만 별도 감독 실행으로 검수하며 외출 중 자동 적용하지 않는다.
Cloud는 Pi 설치/아이콘 실제 실행/사람 청취·지연·서보 움직임을 PASS로 보고하지 않는다.

## 2026-11-05 후속 후보 — 저장 음성·자막·혼잣말·얼굴 조작

시작 기준 원격은 `d833470d73d6a1b7eed01a0e437236efc6bb47af`, clean이며
push/PR 필수 검사 6개가 모두 PASS였다. [후속 Pi 인계](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6652308031)는
기존 private 아이콘/GUI 세션 복구를 확인했다. 사용자는 아이콘부터 음악 두 모드,
pause/seek/resume·volume/mute·GP8 short/hold·대화 복귀·자연 종료·아이콘 재실행까지
14개를 실제 PASS했다. [최신 Pi 인계6653292587](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6653292587)는
이 PASS를 설치본 `43262705b9cb520056694d34521cde74bec01220`에 연결했다. 원격 d833 또는
이번 새 후보의 실기 PASS로 옮기지 않는다. 아래 과거
NOT_RUN/차단 기록은 역사이며 현재 미해결 상태로 승계하지 않는다.

이번 후보의 변경:

- 실제 선택된 클립 ID·원문과 SHA 검증 PCM을 묶어 로컬 EN/KO 자막을 전달한다.
  prepared에서는 숨기고 player-start에 표시한다. 종료/취소/오류/새 질문/OFF에서
  정리한다. boot/idle overlay도 현재 자막 설정을 따른다. 재생마다 번역/TTS 없음.
- 전체142 사용 허용은 `listened`/`approved`와 별도다. 대표10 재청취를 요구하지
  않는다. M04–06은 선택곡 준비, M01–03은 BMO의 곡 선택에 쓴다. 작은 후보군은
  한 바퀴씩 섞고 직전 반복을 피한다. 조회는 선택 이력/파일을 변경하지 않는다.
  선택은 실제 이벤트 load에서 한 번만 확정한다. 명시 수동 pin/OFF/해제/SHA를
  유지한다. 과거 batch와 같은 값의 자동 pin은 origins 기록으로 이관하며 원본
  selection.json을 보존한다. 구버전에서 같은 값으로 수동 저장한 경우 출처를
  구분할 기록이 없다는 한계가 있다. 새 수동 pin은 항상 구분해 보존한다.
- low intensity에서도 맥락에 맞는 짧은 v10 반응을 기존 단일 WAV/플레이어에
  합성한다. 발화3회 간격·같은 반응 반복 억제·진지한 고민·이미 포함된 웃음
  중복 억제를 유지한다. Ta-da 성공 문구는 실제 저장 성공 뒤에만 허용한다.
  raw 자산은 바꾸지 않는다. 실제 음량/길이/억양 비교와 필요한 상대 trim은 청취 대기다.
- 기존 PresenceScheduler normal은 3–7분 기회 판단, 실제 시작 기준 15분에
  최대2회, 같은 idle 클립 30분 제외다. 대화/음악/게임/녹화/mute/최근 고민/설정
  조작 중 억제하고 GP8이 우선한다. history/repeat를 바꾸거나 GPT/Fish를 부르지
  않는다. 기존 한 번의 의미 응답으로 혼잣말 off/quiet/normal과 메뉴 open/close를
  연결한다. 재시작 기본값은 기존 off를 유지하며 Pi가 normal을 명시 선택한다.
- 얼굴 오른쪽 아래 터치 → −/현재%/+/Mute, 5%p 단위, 약3초 무조작 후 숨김.
  기존 MasterVolume의 실제 값과 음악 overlay를 공유한다. 음악은 즉시 적용,
  대화 WAV는 다음 재생부터 적용, mute/0은 현재 발화를 취소한다. 시작25%,
  GP7/GP8, 승인 얼굴과 기존 음악 입 움직임은 보존한다.

검증: 로컬 Python3.13 전체 **1,946개 PASS**, 기존 음악/v10·character-life 및
새 얼굴 조작/저장 음성 자막의 실제 synthetic Chromium 회귀 PASS. 새 브라우저 검사를
public-browser CI에도 포함한다. 통합 후보의 push CI에서 기존 게임 재진입 실패가
추가로 드러났다. 실제 얼굴 iframe blur callback을 다음 게임 시작 뒤 전달하면 현재
세대의 PAD_LOST가 잘못 전송되어 pad_disconnected로 종료됨을 재현했다. 내부 포커스
이동은 분리로 처리하지 않으며 실제 외부 blur/패드 분리 정리는 보존하는 회귀를 추가했다.
새 게임/엔진은 추가하지 않았다. 최종 exact HEAD의 push/PR 결과는 최신 PR 인계 기준이다.

### Pi 최소 인수

1. Exit로 종료하고 아래 기존 dirty/ff-only 후보 갱신 절차에 최신 PR 인계의
   **exact SHA**를 넣는다. Pi 담당이 기존 private runner의 후보 인수 기록/고정 SHA도
   같은 SHA로 검증 갱신한다. private 도구의 새 옵션이나 기록 형식을 추정하지 않는다.
   `BMO_REAL_ROBOT`과 private runner/GUI 세션 복구는 유지한다.
2. 이미 등록한142개를 다시 import하지 않는다. 사용자가 요청한 전체 사용 허용을
   적용하거나, 음성 메뉴의 **Use all saved voices**에서 한 번 적용한다. 개별 청취
   기록을 만들지 않으며 재생·유료 API도 호출하지 않는다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.voice_pack \
 --root /home/bgh/BMO_private/runtime/voice-v10 accept-full-use --user-accepted
python3 -B /home/bgh/BMO_private/tools/run_bmo_candidate.py --check
python3 -B /home/bgh/BMO_private/tools/run_bmo_candidate.py
```

3. 복구된 같은 아이콘/위 private 실행 도구를 사용한다. 혼잣말은 메뉴에서 normal
   (드물게 말하기), 또는 “다시 가끔 말해”로 현재 세션에 켠다. 다음 실행에도 켤
   경우 Pi 담당이 기존 private launcher 인자에 `--presence-mode normal`을 추가한다.
   GUI 세션 wrapper를 우회하거나 OS/서비스/원래 runtime directory 권한을 바꾸지 않는다.
4. 전체 사용 해제는 기존 메뉴 버튼 또는 아래 명령이며 개별 승인과 원본 파일은
   유지한다. 정상본 복귀 명령은 이 문서 아래 기존 절차와 같다.

```bash
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.voice_pack \
 --root /home/bgh/BMO_private/runtime/voice-v10 disable-release
```

추가 실기는 네 묶음만 확인한다: (1) KO 캐시 자막/음성 일치와 취소/OFF 정리,
(2) 선택곡 준비·BMO 곡 선택의 다양한 대사와 자연스러운 짧은 반응,
(3) normal 혼잣말의 간격·GP8 우선·끄기/다시 켜기,
(4) 얼굴 볼륨창·음악 즉시 적용·mute 취소 후 다음 대화.
Cloud는 주입 IO와 합성 PCM/제품 Chromium을 검사하며 실제 청취/실기 승인을
대신하지 않는다. 새 노래 얼굴은 다른 WORK의 승인 결과가 올 때까지 보존한다.

### 142개 연결 상태

아래 **연결75 / 조건부66 / 제외1**은 코드 이벤트 생산자 기준이다. 등록·전체
사용 허용·개별 청취 승인·실제 재생은 다른 상태다. 조건부는 해당 맥락 또는
추가 실제 이벤트 생산자가 필요하며 임의로 다른 이벤트의 대사로 섞지 않는다.
정확한 원문/번역/후보군/감정 조건은
[로컬 메타데이터](../../software/bmo_ai/policies/voice-pack-v10-context.json)에 있다.

<details>
<summary>전체142개 ID별 이벤트와 이유</summary>

| ID | 상태 | 이벤트 | 이유 |
|---|---|---|---|
| B01 | 조건부 | boot | Requires explicit boot-voice selection; the currently approved boot greeting and sound are preserved. |
| B02 | 조건부 | boot | Requires explicit boot-voice selection; the currently approved boot greeting and sound are preserved. |
| B03 | 조건부 | boot | Requires explicit boot-voice selection; the currently approved boot greeting and sound are preserved. |
| B04 | 조건부 | boot | Requires explicit boot-voice selection; the currently approved boot greeting and sound are preserved. |
| B05 | 조건부 | boot | Requires explicit boot-voice selection; the currently approved boot greeting and sound are preserved. |
| B06 | 조건부 | boot | Requires explicit boot-voice selection; the currently approved boot greeting and sound are preserved. |
| G01 | 연결 | game_menu | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| G02 | 연결 | game_start | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| G03 | 연결 | game_exit | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| G04 | 연결 | game_launch_failed | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| G05 | 연결 | game_win | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| G06 | 연결 | game_loss | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| G07 | 조건부 | game_controller_lost | Requires an observed controller disconnect; never infer from game loss. |
| M01 | 연결 | music_random | BMO chooses a ready song through the explicit random-song action; not used for a user-selected song. |
| M02 | 연결 | music_random | BMO chooses a ready song through the explicit random-song action; not used for a user-selected song. |
| M03 | 연결 | music_random | BMO chooses a ready song through the explicit random-song action; not used for a user-selected song. |
| M04 | 연결 | music_selected | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| M05 | 연결 | music_selected | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| M06 | 연결 | music_selected | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| M07 | 조건부 | music_paused | Requires confirmed pause and a free audio owner; never overlap music. |
| M08 | 조건부 | music_resumed | Requires an owned resume announcement before music resumes. |
| M09 | 조건부 | music_next | Requires explicit next-song selection; not natural playlist completion. |
| M10 | 연결 | music_exit | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_READY_01 | 연결 | photo_ready | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_READY_02 | 연결 | photo_ready | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_READY_03 | 연결 | photo_ready | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_READY_04 | 조건부 | photo_group_ready | Requires an explicit group-photo context; do not infer multiple people. |
| PH_READY_05 | 연결 | photo_ready | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_READY_06 | 연결 | photo_ready | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_COUNTDOWN_05S | 연결 | photo_countdown | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_REVIEW_01 | 연결 | photo_saved | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_REVIEW_02 | 연결 | photo_saved | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_REVIEW_03 | 연결 | photo_saved | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_REVIEW_04 | 연결 | photo_saved | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_REVIEW_05 | 연결 | photo_saved | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_REVIEW_06 | 연결 | photo_saved | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_RETAKE_01 | 조건부 | photo_retake | Requires a new explicit retake request; repeat cannot take a photo. |
| PH_RETAKE_02 | 조건부 | photo_retake | Requires a new explicit retake request; repeat cannot take a photo. |
| PH_RETURN_GUIDE_01 | 조건부 | photo_return_guide | Requires a saved-photo review and a one-time return hint. |
| PH_EXIT_01 | 연결 | photo_exit | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_EXIT_02 | 연결 | photo_exit | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_EXIT_03 | 연결 | photo_exit | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PH_CANCEL_01 | 연결 | photo_cancelled | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| E01 | 조건부 | error_unrecognized | Retained for an explicit unrecognized-speech event; no automatic STT-error cache producer is connected. |
| E02 | 조건부 | error_no_speech | Retained for a no-speech event; current empty-input handling remains silent. |
| E03 | 조건부 | error_microphone | Retained for a confirmed microphone failure; no automatic microphone-error cache producer is connected. |
| E04 | 조건부 | clarification | Requires a current clarification question; not a device failure. |
| E05 | 조건부 | waiting_reply | Requires a measured pending reply; never loop a waiting announcement. |
| E06 | 조건부 | error_timeout | Requires an actual response timeout; not an arbitrary service failure. |
| E07 | 조건부 | error_network | Retained for a confirmed network failure; no automatic network-error cache producer is connected. |
| E08 | 조건부 | error_service, error_authentication, error_rate_limit | Retained for a confirmed response-service failure; no automatic provider-error cache producer is connected. |
| E09 | 조건부 | unsupported_action | Requires an unsupported requested action. |
| E10 | 조건부 | error_action | Requires a confirmed generic action failure. |
| E11 | 조건부 | error_music_missing | Retained for an unknown-song result; current Host reports this through its generic music-error path. |
| E12 | 조건부 | error_music_empty | Retained for an empty music library; no separate empty-library voice producer is connected. |
| E13 | 연결 | error_music | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| E14 | 연결 | error_camera | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| E15 | 연결 | error_capture | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| E16 | 연결 | error_storage | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| RX01 | 연결 | reaction_chuckle | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| RX02 | 연결 | reaction_gasp | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| RX03 | 연결 | reaction_playful_puff | Explicit acting only; never a spontaneous reaction to a serious concern. |
| RX04 | 연결 | reaction_sob | Explicit acting only; never a spontaneous reaction to a serious concern. |
| RX05 | 조건부 | reaction_cheer | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| RX06 | 연결 | reaction_hmm | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| RX07 | 연결 | reaction_aha | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| RX08 | 조건부 | reaction_whoa | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| C01 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C02 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C03 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C04 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C05 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C06 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C07 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C08 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C09 | 조건부 | idle | 13.189s exceeds the initial 10s automatic-idle length limit; retained for explicit review. |
| C10 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C11 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C12 | 조건부 | idle | 11.099s exceeds the initial 10s automatic-idle length limit; retained for explicit review. |
| C13 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C14 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C15 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C16 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| F01 | 조건부 | goodbye | Requires an explicit farewell; not app termination by inference. |
| F02 | 조건부 | goodbye | Requires an explicit farewell; not app termination by inference. |
| F03 | 조건부 | goodbye | Requires an explicit farewell; not app termination by inference. |
| F04 | 조건부 | sleep | Requires an explicit sleep transition. |
| F05 | 조건부 | goodnight | Requires an explicit goodnight context. |
| F06 | 조건부 | goodnight | Requires an explicit goodnight context. |
| C17 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| C18 | 연결 | idle | Local idle only: normal mode, settled context, neutral/happy emotion, quiet owner, rate and per-clip cooldown. |
| A01 | 조건부 | utility | Requires an explicit talk-button help context. |
| A02 | 조건부 | quiet | Retained for an explicit quiet acknowledgement; current presence changes do not start a second cached reply. |
| A03 | 조건부 | recovered | Requires an observed recovery; never claim success after an error. |
| wake_ack_01 | 연결 | wake_ack | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| REACTION_LINE_CHUCKLE_SILLY | 조건부 | reaction_silly | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_OOH_INTERESTING | 연결 | reaction_interesting | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_AHA_IDEA | 조건부 | reaction_idea | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_OH_SURPRISE | 조건부 | reaction_surprise | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_HMM_MYSTERY | 조건부 | reaction_mystery | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_POUT_NO_FAIR | 조건부 | reaction_unfair | Explicit acting only; never a spontaneous reaction to a serious concern. |
| REACTION_LINE_WHEW_RELIEF | 조건부 | reaction_relief | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_TADA_SUCCESS | 연결 | reaction_tada, photo_saved, video_saved | Only after a verified successful photo or video save; generic dialogue cannot claim a successful action. |
| REACTION_LINE_GIGGLE_FUNNY | 조건부 | reaction_funny | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_AWW_KIND | 조건부 | reaction_kind | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_OOPS_MY_MISTAKE | 연결 | reaction_oops | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_GASP_AMAZING | 조건부 | reaction_amazing | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_PROUD_HELP | 조건부 | reaction_proud | Requires matching non-sensitive dialogue context and the shared one-in-three reaction budget; never a fixed prefix. |
| REACTION_LINE_SOFT_SOB_ACTING | 조건부 | reaction_sob_acting | Explicit acting only; never a spontaneous reaction to a serious concern. |
| VIDEO_PREP_MOVIE | 연결 | video_ready | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| VIDEO_PREP_READY | 연결 | video_ready | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| VIDEO_PREP_ACTING | 연결 | video_ready | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| VIDEO_RECORDING_STARTED | 제외 | video_recording_started | Recording suppresses speech; retain this asset for manual review but do not auto-play it during capture. |
| VIDEO_TAKE_FINISHED | 조건부 | video_stopped | Retained for an observed recording-stop event; current flow waits for save feedback and does not announce cut separately. |
| VIDEO_SAVED_PRODUCTION | 연결 | video_saved | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| VIDEO_REVIEW_WATCH | 조건부 | video_review | Requires a saved playable video and an explicit review hint. |
| VIDEO_RETAKE | 조건부 | video_retake | Requires a new explicit video retake request. |
| VIDEO_ERROR_START | 연결 | video_start_failed | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| VIDEO_ERROR_SAVE | 연결 | video_save_failed | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| VIDEO_LIMIT_REACHED | 조건부 | video_limit | Retained for an observed recording time limit; no separate time-limit voice producer is connected. |
| VIDEO_CAMERA_BUSY | 조건부 | video_camera_busy | Retained for a camera-busy result; no separate busy-camera voice producer is connected. |
| VIDEO_NOT_RECORDING | 조건부 | video_not_recording | Retained for a stop request with no recording; no separate not-recording voice producer is connected. |
| VIDEO_REVIEW_EXIT | 조건부 | video_exit | Retained for a completed video-review exit; current Back returns silently. |
| TRACK_START | 연결 | tracking_started | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| TRACK_TARGET_FOUND | 조건부 | tracking_found | Requires a real tracker found event with deduplication. |
| TRACK_TARGET_LOST | 조건부 | tracking_lost | Requires a real tracker lost event with deduplication. |
| TRACK_TARGET_RETURNED | 조건부 | tracking_returned | Requires a real tracker return event with deduplication. |
| TRACK_STOP | 연결 | tracking_stopped | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| TRACK_UNAVAILABLE | 연결 | tracking_failed | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| TRACK_NO_TARGET_YET | 조건부 | tracking_no_target | Requires a qualified no-target state; no repeated search chatter. |
| MUSIC_CHOOSE_MODE | 조건부 | music_choose_mode | Requires an explicit choice prompt before song selection. |
| MUSIC_REPEAT_SELECTED | 조건부 | music_repeat | Requires explicit song replay; not dialogue repeat. |
| MUSIC_PLAYLIST_FINISHED | 연결 | music_finished | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| SYSTEM_VOLUME_UP | 연결 | volume_up | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| SYSTEM_VOLUME_DOWN | 연결 | volume_down | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PHOTO_FOCUS_UNCERTAIN | 연결 | focus_uncertain | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| PHOTO_REFOCUS_ONCE | 조건부 | focus_retry | Retained for a new explicit refocus attempt; current refocus does not announce this cue. |
| SYSTEM_STORAGE_FULL | 연결 | storage_full | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| SYSTEM_SAVED_FILE_OPEN_FAILED | 연결 | error_file | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| SYSTEM_CAMERA_OFF | 조건부 | camera_off | Retained for a completed camera-off operation; current off control returns silently. |
| SYSTEM_VOLUME_MAX | 연결 | volume_max | Bound to the matching observed event; runtime authorization, cancellation and SHA checks still apply. |
| SYSTEM_NOTHING_TO_REPEAT | 조건부 | nothing_to_repeat | Retained for an empty repeat cache; current empty-repeat handling does not play a separate cue. |

</details>

<details>
<summary>이전 집중 검토 기록 — 아래 상태는 당시 기준</summary>

## 2026-11-05 집중 검토 — Pi 후보와 Cloud 후속 구분

[구현 인계](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6609896494)와
[최신 Pi 인계](https://github.com/GUNOBAE/BGH_BMO/pull/40#issuecomment-6613424628)의
설치 기준은 `43262705b9cb520056694d34521cde74bec01220`이다. 이 SHA의 CI는
6/6 성공이며, Pi는 등록142/우선60/음악1곡·2모드 보존을 확인했다. 새 음악
UI·GP8 hold·묶음 활성화는 당시 NOT_RUN이다. 후속 Cloud 수정은 별도 후보이며
Pi를 자동 갱신하지 않는다. 실제 영상·음악 재생의 이전 사용자 성공과 새 UX의
검수 대기를 구분한다. 승인된 새 노래 표정 결과물은 아직 전달되지 않았다.

집중 검토에서 재현한 수정은 현재 미리듣기 요청의 실제 시작·성공/취소/실패
receipt, pause 뒤 늦은 video play 실패의 폐기, 중복 Stop의 종료 음성 방지,
현재 캐시 이벤트 OFF/묶음 해제의 취소, wake ACK OFF 상태 표시다.
과거 played 기록을 이번 대표 미리듣기 완료로 사용하지 않는다. 자동 검사와
exact HEAD CI 결과는 최신 PR 인계에 기록하며 실제 청취 승인으로 대체하지 않는다.

### 공개 시연 준비 — 코드에서 확인한 범위

| 항목 | 확인됨 | 미검증 / 한계 |
|---|---|---|
| API 키 | private store 0700/0600·symlink 거부, redacted repr, 고정 오류 코드, Chromium 환경 allowlist | 저장소는 평문이다. 실제 Pi 로그·권한·전체 Git 역사 비노출은 이번 Cloud 검증 범위 밖이다. 비밀 값은 읽어 출력하지 않았다. |
| 사용자 데이터 | STT 임시 WAV는 성공/취소/실패 후 정리. 대화 history는 bounded 메모리, API store=False. repeat WAV는 교체/종료 시 정리 | 강제 종료의 잔여 temp·provider 보관 정책은 보장하지 않는다. 명시적 one-turn 진단은 원문을 담는 private 파일이므로 별도 보관 관리가 필요하다. |
| 사진/영상 | 아래 launcher의 private photos/videos에 보관. 사진 8MiB/장·총256MiB, 영상 60초·96MiB/건·총512MiB, 여유32MiB 검사 | 자동 기간 삭제 없음. 실제 Pi 사용량·권한은 미검증이다. |
| 로컬 서버 | 127.0.0.1 고정, Host/Origin 검사, POST token·body 상한·nonce 중복 방지 | 같은 컴퓨터의 로컬 프로세스까지 인증하는 구조가 아니다. VNC/포워딩/방화벽의 실제 외부 노출은 미검증이다. |
| 재시도/비용 | OpenAI SDK 기본 retry1(허용0~5), Fish 자동 재시도 없음. 인증 오류는 명시적 복구 필요. 선택적 요청수·cooldown·추정비용 gate 존재 | 비용 gate 기본 OFF, 프로세스 메모리 기준이다. 실제 결제 상한이 아니며 메뉴/후속 TTS를 각각 계량하지 않는다. 임의 금액/설정 변경 없음. |
| 종료/복구 | owner 취소·종료·오류 후 정리와 정상본 실행 경로 존재. 아래 구버전 복귀 명령에서 지원하지 않는 frontend 옵션 제거 | 실물 release/다음 대화는 Pi 검수. runtime_directory_permissions의 0700 guard는 유지하며 세션 권한 변경 주체는 미확정이다. |

정상 telemetry는 원문 대신 이벤트/시간을 기록하며, 선택 로그는 1MB 및 백업3개로
회전한다. 이번 점검은 관련 코드·기존 검사에 한정하며 공개 시연의 전체 승인이나
실제 Pi 설정 검수를 뜻하지 않는다.

</details>

## 코드에서 연결한 경로와 남은 증거

- 기존 한 번의 GPT 응답의 v5 `plan`에 최대 3개의 allowlisted 동작을 담는다.
  이전 v1–v4 단일 intent도 읽는다. Host가 turn/step ID·세대·기한을 부여하며
  완료 receipt 뒤에만 다음 단계를 실행한다. 실패·불명·취소·새 질문·repeat는
  tail을 실행하지 않는다. stop tracking → video 전에 명시 OFF를 적용하므로
  Back으로 OFF를 되살리지 않는다. 절별 부정·인용·과거·미래를 검사한다.
  사진/영상 review나 게임은 사용자의 복귀를 요구한다. 계획이 해당 화면을
  임의로 닫지 않으며, 그 상태에서 허용되지 않는 후속 단계는 거부한다.
- 실제 Pi에서 빈 게임 목록은 같은 후보의 번들 세 파일 mode0664 때문에
  `unsafe_game_bundle`로 재현됐다. SHA·파일 존재는 정상이었고 private 실행
  도구도 기존 launcher에 위임했다. 과거 실제 실행 PID/argv는 미관측이다.
  새 코드는 permission/missing/hash/disabled 이유를 구분하고, 실행 profile을
  통일한다. `game_pack_prepare`는 정확한 세 파일만 사전 검증·권한 수정한다.
  Pi 후속 인계에서는 0644 정리 후 catalog3 READY, 제품 Chromium 게임
  10그룹 PASS까지 확인했다. 실제 pad/native 사용자 승인은 별도다.
- v10 `entries/spoken_text/file/requested_model`를 기존 importer로 정규화한다.
  142종을 보관하고 60종을 우선 표시한다. imported/selected/listened/approved/
  played는 별도이며 자동 승인하지 않는다. RX01의 빈 speech는 웃음이고
  자막·합성문을 만들지 않는다. 제작 출처와 countdown 편집 기록을 보존한다.
- 네트워크 E07/서비스 E08/카메라 E14/촬영 E15/사진 저장 E16/음악 E13,
  game menu G01/start G02/launch fail G04/win G05/loss G06으로 교정했다.
  승인된 해당 캐시는 같은 이벤트의 live TTS를 대신한다. 오류 원인은 화면에
  구분하며 깨진 출력 장치에 음성 오류를 재시도하지 않는다.
- 사진 준비 캐시는 실제 preview 표시 후 재생한다. 승인된 5초 카운트의
  실제 player-start가 UI/timer 공통 t0다. 오직 timer가 한 셔터를 소유한다.
  WAV ended·repeat·late callback은 셔터를 호출하지 않는다. 미승인·mute·
  시작 실패는 전체 시각 5초다. 사진 저장·review 확인 뒤 성공 대사를 낸다.
- 승인된 `wake_ack_01`을 기존 wake owner/플레이어에 실제 drama-3 provenance와
  source gain으로 전달한다. live 모델을 바꾸지 않는다. 질문 시작이 관측되면
  ACK를 생략한다. 기존 ACK로 닫힌 마이크 경계의 무간격 질문 전체 복원은
  새로 증명하지 않았다. wake/idle은 명시 옵션이고 기본 OFF다.
- `space_potatoes`는 한 항목의 face/video 변형이다. video의 내장 AAC를
  검증 PCM으로 디코드해 기존 AudioFocus를 가진 Python ALSA 출력에서 낸다.
  browser video는 항상 muted다. 별도 MP3를 겹치지 않는다. ALSA submitted
  frames minus delay를 기준으로 영상/입을 갱신한다. 이 clock은 출력 스트림
  진행 증거이며 실제 스피커 청취를 증명하지 않는다. Face whole-mix RMS는
  반주에도 반응하는 근사치이고 정확한 가사/음소 lip sync가 아니다.
- media GP8는 짧게 눌렀다 놓으면 pause/resume, 1.5초 hold는 종료 후 얼굴
  복귀이며 해당 keyup을 소비한다. Stop/Back/Esc도 종료한다. seek/replay,
  volume0/mute, 늦은 decoder 실패와 held keyup 후 다음 대화를 검사한다.
  기존 촬영 GP8 stop-save/review-back은 유지한다.
- 팔은 기존 B1·보정 그대로다. `arm_candidate`는 기본 read-only, prepare는
  정확한 단일 flag 후보만 만들고 apply/rollback은 현재 감독 조건·SHA·백업을
  요구한다. 보드 쓰기는 auto-reload의 set_arms(0)을 일으킬 수 있다.
  [감독 절차](../../firmware/staging/bmo_arm_legacy_bridge_v1/README_KR.md)를 따른다.
- Tracking은 기본 OFF의 선택 기능이다. 기존 rotation→mirror→normalized
  좌표와 R10 look API를 보존했다. 사람/BMO 관점 라벨만으로 반전을 바꾸지
  않았다. 실제 방향·작동 범위는 Pi 선택 검수다.

아래는 10/31 최초 통합 시점의 기록이며 최신 상태는 위 인계를 우선한다.
Cloud는 합성 짧은 미디어·주입 IO·실제 제품 Chromium을 검사한다. 사용자
대화·자막·음량·취소·영상 PASS는 최신 사용자 관찰로 보존한다. Pi는 두 ZIP
전체 해시·PCM·decode를 검증했지만 당시 runtime 등록0/청취 승인0/재생0이다.
Pi는 실제 제공 MP4의 무음 Chromium decode/pause/seek도 별도 확인했다.
이 결과는 새 제품 음악 두 모드·스피커 청취 승인이 아니다. Cloud가 private
ZIP을 받거나 실제 Pi에 접속·재생·촬영·구동한 것은 아니다.

## 후보 갱신과 private import (Pi 담당)

앱의 Exit로 정상 종료한다. dirty/분기면 아래 검사가 중단하며 stash/reset/
clean을 자동 실행하지 않는다. READY의 exact SHA를 `BMO_RC_SHA`에 입력한다.

```bash
(
 set -eu
 cd /home/bgh/BMO_CHARACTER_LIFE
 test "$(git branch --show-current)" = feature/bmo-character-life-v1
 test -z "$(git status --porcelain=v1 --untracked-files=normal)"
 git fetch origin refs/heads/feature/bmo-character-life-v1
 BMO_RC_SHA='<최신 Cloud 인계에서 검토한 exact SHA>'
 git merge-base --is-ancestor HEAD "$BMO_RC_SHA"
 git merge --ff-only "$BMO_RC_SHA"
 test "$(git rev-parse HEAD)" = "$BMO_RC_SHA"
)
```

먼저 게임 번들을 읽기 전용으로 확인한다. permissions인 경우에만 검토 뒤
`--apply`한다. 이는 후보의 pinned 세 파일 group/other write만 제거한다.

```bash
cd /home/bgh/BMO_CHARACTER_LIFE
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.game_pack_prepare --dry-run
# 위 결과가 permissions이면 Pi가 기존 메타데이터 백업 후:
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.game_pack_prepare --apply
```

확인된 두 원본 ZIP은 그대로 보존하고 이전 팩·라이브러리와 다른 디렉터리에
명시 import한다. 아래 명령은 재생·승인·유료 API를 호출하지 않는다.
같은 import가 이미 완료됐으면 덮어쓰지 말고 list/검증 결과를 재사용한다.

```bash
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.voice_pack \
 --root /home/bgh/BMO_private/runtime/voice-v10 import \
 /home/bgh/Downloads/BMO_Voice_Release_v10_142.zip \
 --sha256 6bcbf926b2d5519e63eb502c141fab4571e06eafc72315597cb147121c3a189e
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.voice_pack \
 --root /home/bgh/BMO_private/runtime/voice-v10 list

PYTHONPATH=software .venv-device/bin/python -m bmo_ai.media_assets \
 --media-seed /home/bgh/Downloads/BMO_Media_Seed_SpacePotatoes_v1.zip \
 --sha256 5de21e50e275e37207f185247f7062f14bd58d46686eb92c8b7fd0f78ff2cde2 --dry-run
PYTHONPATH=software .venv-device/bin/python -m bmo_ai.media_assets \
 --media-seed /home/bgh/Downloads/BMO_Media_Seed_SpacePotatoes_v1.zip \
 --sha256 5de21e50e275e37207f185247f7062f14bd58d46686eb92c8b7fd0f78ff2cde2 \
 --content-root /home/bgh/BMO_private/runtime/media-space-potatoes-v1 \
 --private-use-confirmed --install
```

`MEDIA_SEED.json`을 runtime manifest로 읽지 않는다. 정확한 ZIP/component
SHA를 확인한 adapter가 기존 library v2 레코드를 만든다. 원본 MP3·handoff·
energy 후보는 private 보관하며 video용 오디오는 MP4 내장음원에서만 만든다.
ffmpeg/ffprobe와 시스템 libasound를 재사용한다. 새 Python 의존성은 없다.
음악 권리는 user_provided/공개 권한 미확정이고 소유/라이선스를 날조하지 않는다.

## 한 앱 실행·선택 승인·아이콘·복귀

```bash
env -u FISH_API_KEY XDG_CONFIG_HOME=/home/bgh/.config \
 BMO_PYTHON=/home/bgh/BMO_CHARACTER_LIFE/.venv-device/bin/python \
 /home/bgh/BMO_CHARACTER_LIFE/launch-character-life \
 --voice-pack-directory /home/bgh/BMO_private/runtime/voice-v10 \
 --music-directory /home/bgh/BMO_private/runtime/media-space-potatoes-v1 --music-supervised
```

기존 초기60+자연종료1개 묶음은 호환 유지한다. 현재 전체142 사용 허용은
위 인수 절차를 따르며 대표 클립 재청취를 요구하지 않는다. 묶음 해제/이벤트별 OFF·ON이
가능하며, 개별 SHA 미리듣기·청취 승인은 별도로 유지한다. 묶음 수락을 개별
청취61회로 기록하지 않는다. ACK·5초 카운트도 해당 권한이 있어야 한다.
`--wake-word`는 custom detector 경로가 준비되고
사람이 원하는 세션에만 덧붙인다. idle는 위 normal 명시 선택 절차를 따르며 새 음성 생성은 하지 않는다.
음악/기능 캐시가 마지막 대화 repeat WAV를 바꾸지 않는다.

기존 installer를 같은 인자로 dry-run한다. 사용자 기존 Desktop/정상본
아이콘을 덮지 않는다. 설치 실행은 Pi 설정 담당자가 검토한다.

```bash
./launch-character-life --voice-pack-directory /home/bgh/BMO_private/runtime/voice-v10 \
 --music-directory /home/bgh/BMO_private/runtime/media-space-potatoes-v1 --music-supervised \
 --install-desktop --desktop-install-target applications-only --dry-run
```

정상 종료는 앱 Exit다. 자산 복귀는 이전 팩/음악 디렉터리 옵션으로 실행하거나
새 옵션을 빼는 방식이며 원본·승인 기록은 삭제하지 않는다. 정상본 복귀:

```bash
env -u FISH_API_KEY XDG_CONFIG_HOME=/home/bgh/.config \
 BMO_PYTHON=/home/bgh/BMO_REAL_ROBOT/.venv/bin/python \
 /home/bgh/BMO_REAL_ROBOT/launch-desktop --live \
 --screen-profile r10 --ptt-microphone
```

## 이전 후보 검수 목록 (역사 기록; 현재 추가 검수는 위 네 묶음)

1. 음악 Video 화면/overlay, GP8 short pause/resume, hold 종료 후 keyup이
   대화를 시작하지 않는지 확인하고 새 GP8로 대화한다.
2. 대표 미리듣기 중 취소 시 다음 클립/묶음 완료로 넘어가지 않는지 확인한다.
   실제 청취 뒤 사용자가 묶음을 수락하고 OFF/ON·전체 해제와 개별 기록 보존을 본다.
3. M04→음악, 수동 종료→M10 1회, 별도 자연 종료→종료 음성1회. 출력 중첩 없이
   pause/seek/replay/Back 후 다음 대화가 가능한지 실제 청취한다.
4. 이전 runtime_directory_permissions가 재발하면 현재 소유자/권한만 확인하고
   세션 변경 주체를 좁게 진단한다. guard 우회·전체 chmod·정상본 변경은 하지 않는다.
