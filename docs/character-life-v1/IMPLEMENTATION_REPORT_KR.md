# BMO Character Life v1 구현·검증 기록

## 작업 기준과 보존

Issue #39, PR #40의 최신 준비 커밋 `83b1c1479e0cc01fa678c5bea031ec7caba31246`에서 `/home/bgh/BMO_CHARACTER_LIFE` 별도 worktree로 이어간다. Pi Codex 단독 구현 인수를 두 스레드에 기록했다. Cloud 재설정이나 중복 구현 요청은 하지 않았다.

기존 진단 댓글: https://github.com/GUNOBAE/BGH_BMO/issues/39#issuecomment-6469479607

진단 당시 실제 실행본 소스는 `/home/bgh/BMO_REAL_ROBOT`, HEAD `0f46e3203afcc0d2bb7455cf49b3ac7ba0d4e978`이었다. 후보 브랜치는 더 새로운 통합 기반 `0933d9f75b533f3c0c4d285de7b942c201ea3cba` 위에 있으므로, 아직 배포하지 않은 후보 코드가 기존 증상을 일으켰다고 판단하지 않는다. 마지막 저장된 실행 옵션은 `.venv-camera/bin/python`, `--live --windowed --ptt-microphone --ptt-trace`이며 현재 프로세스 실행 증거를 뜻하지 않는다.

실행본·가상환경·대화 프로세스·원본 R10·펌웨어·보정값·백업·서비스·OS는 변경하지 않는다. 후보 코드 자동 배포, 실물 장치/유료 API 호출, main 병합은 수행하지 않는다.

## 1단계: 대화 대기와 감정 전달

- `--character-life` opt-in으로 기존 한 개의 GPT 응답에 allowlist CharacterPlan을 포함한다. 별도 감정 분류 API나 두 번째 brain은 없다.
- 명시적인 화내기/울기 연기와 실제 상실·고민의 표현을 구별한다. 잘못 축소된 angry/crying을 명시적 요청에서 복구하고 실제 고민에서는 과한 웃음/축하/몸짓을 억제한다.
- 얼굴·음성 delivery·시선·짧은 반응·기존 팔 제스처를 한 계획으로 조정한다. 반응음과 몸짓을 무조건 겹치지 않는다. 원본 얼굴은 외부 API로만 제어하며 listening/thinking 동안 이전 감정, 실제 player 시작 시 전환, repeat/cancel/수동 override를 보존한다.
- GPT-6 Luna low, Aaron ID, 기본 S2.1 Pro Free, Beemo 발음, gain 0.25, 기존 Piper와 팔 통신/보정은 유지한다. Drama3는 명시적인 별도 샘플 선택만 지원한다. 강한 연기에서는 검증된 host delivery가 상충하는 고정 Soft 접두사를 대체하며 `--plain-voice`를 존중한다. 실제 음질·연기 강도는 청취 미승인이다.
- 기본 speech-safe PTT의 고정 threshold를 쓰는 경로는 사용하지 않던 calibration 대기를 생략한다. 기본 silence 0.9초/threshold 0.008은 유지한다. 첫 음절 보존을 위해 최대 0.4초 preroll을 남긴다. `baseline(.9)/balanced(.6)/quick(.45)` 비교는 명시적인 옵션이며 성능 개선 실측값은 없다.
- host 입력 수신, arecord pipe 준비, 첫 PCM, 마지막 voiced frame, VAD 닫힘, capture 정리, STT 실제 요청, GPT, Fish 첫 HTTP byte/다운로드/오디오 준비, Python player 시작을 분리한다. 물리 버튼 시점·실제 마이크 준비·스피커 첫 소리는 근거가 없으면 `UNMEASURED`이다. 합성/Piper 경로에 Fish 완료를 만들어 넣지 않는다.

진단에서 재사용한 baseline은 calibration 0.4s, silence 0.9s, min speech 0.24s, no speech 3s, max 12s, 16kHz mono S16/40ms이다. 이전 GPT-only 벤치마크의 지연은 현재 PTT 전체 지연으로 재사용하지 않는다. 새 계측은 다음 감독 실행의 근거를 남기기 위한 코드이며 실제 성능 발표가 아니다.

1단계 검증: Python 3.13.5에서 기존 전체 회귀와 1단계 신규 검사를 합쳐 **1357개 PASS (80.264초)**. 아직 별도 개발 중인 bank/presence/wake 모듈 검사는 이 커밋의 수에 넣지 않았다. `checks/check_character_life_browser.py`의 production Desktop/Host/session/director/voice/face API 경로 **5개 검사 PASS**. 입력 PCM·STT·GPT·player 경계만 합성으로 대체했고 실제 키·유료 API·장치는 차단했다. `git diff --check` PASS. 브라우저는 공개 합성 얼굴 계약 fixture로 먼저 검증했고, 원본 R10 headless는 후속 단계에 기록한다. 최종 HEAD와 Python 3.11/3.13 CI는 단계별 PR #40 댓글 및 최종 인계에 기록한다. 장치 없는 Python/브라우저 PASS와 실제 Pi 동작 PASS를 구별한다.

## 상태 구분

- 대화/감정 코드: IMPLEMENTED, 오프라인 회귀·브라우저 검증 후 제출.
- 실제 대화 단계별 지연: UNMEASURED.
- 신규 캐시 음성 생성: NOT_RUN / AUDIO_NOT_GENERATED.
- Aaron 강한 감정 연기 청취: NOT_RUN / LISTENING_NOT_APPROVED.
- 호출어 실제 모델: MODEL_MISSING, 실제 마이크 수신·정확도 NOT_RUN.
- 팔/카메라/USB/스피커 실기: NOT_RUN. 기존 성공 결과를 재시험하거나 변경하지 않는다.

후속 캐시/idle/호출어 구현과 최종 실행·롤백 절차는 검증된 단계에서 아래에 추가한다.
