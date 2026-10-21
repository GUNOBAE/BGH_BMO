# BMO 프로젝트 통합 인수인계 — 2026-10-21 KST

> **목적:** 여러 ChatGPT 대화와 Raspberry Pi Codex 작업이 서로 충돌하지 않도록, 확인된 사용자 결정을 코드의 실제 구현 상태와 분리해 기록한다.  
> **문서 성격:** 개발 기준·인수인계. 이 문서를 올렸다는 이유로 장치 배포, 음성 변경, 앱 구현 또는 기존 브랜치 병합이 완료된 것은 아니다.  
> **갱신 기준:** 2026-10-21 사용자 발화 및 접근 가능한 이전 BMO 대화 맥락, 공개 GitHub 브랜치 파일을 교차 검토. 독립 채팅의 모든 원문이나 Pi의 현재 파일·프로세스를 실시간으로 읽은 것은 아니다.

## 0. 읽는 순서 및 충돌 해결 규칙

1. **최신 명시적 사용자 결정**이 이전 대화의 잠정 계획보다 우선한다.
2. **실제 작동/구현 여부**는 해당 브랜치의 코드, 테스트 결과, 실물 실행 확인을 별도로 본다. 사용자 의사결정이 곧 배포 완료라는 뜻은 아니다.
3. 현재의 기능 설계 문서(docs/bmo-product-plan-kr)는 목표 동작을 서술하는 초안으로, 구현 증빙이 아니다.
4. 다른 채팅/에이전트는 이 문서를 읽은 후 자신이 다룰 **범위와 브랜치만** 작업한다. 미확인 차이가 있으면 현 파일을 보존하고 비교·보고한다. 임의 전체 통합 금지.
5. 기존 제품 README, 코덱스 메모, 코드와 충돌할 경우 바뀐 것은 무엇인지 사용자 최신 선택과 코드 기본값을 나란히 표시한다. 자동으로 이전 코드를 덮어쓰지 않는다.

## 1. 확정된 음성 정책 (최우선)

**메인 음성 = Fish Audio Aaron Soft. 로컬 백업 음성 = ziadnotme BMO Piper 원본(무보정).**

| 항목 | 현재 결정 | 주의 |
|---|---|---|
| 대화용 LLM | OpenAI cloud, 현재 사용/검증 모델 gpt-4.1-mini | 최종 영구 모델 고정 아님; 빠르고 정확한 대화 선호 |
| 음성인식(STT) | OpenAI gpt-4o-mini-transcribe | 로컬 Piper를 선택해도 STT가 자동 오프라인화되지는 않음 |
| 메인 TTS | **Fish Audio** Aaron voice reference_id: 106f41ea392c4cc48c6eee76f69ff17e | Fish만 응답 합성; 여러 TTS를 동시에 재생하지 않음 |
| Fish 말투 | Aaron **Soft**, delivery=soft, BMO 발음 치환=beemo | 감정에 따라 다른 인물의 목소리로 자동 전환하지 않음 |
| Fish 모델 | **s2.1-pro-free** (당시 검증된 설정) | 무료 정책/기간은 공급자 변경 가능, 유료 s2.1-pro/Drama 3로 임의 승격 금지 |
| Fish 출력 | 사용자 실기에서 선호/사용한 약 **25% software gain** | 해당 코드 브랜치 config.py 기본값은 **0.30**; 선호 실기 설정 0.25와 다름. 사용자 설정/실행 옵션 확인 후 맞출 것 |
| 로컬 백업 TTS | **Piper + en_US-bmo_voice.onnx** (ziadnotme/bmo-tts-piper) | Fish Aaron의 완전 대체가 아니라 무료/오프라인 음성 합성 예비안 |
| 로컬 음성 튜닝 | **원본 ONNX 및 기본 추론 설정 유지** | PSOLA 피치 강제 상승, 헬륨 같은 D/E, 자동 피치 보정, v3 실험 설정을 기본에 합치지 않음 |
| 테스트 듣기 볼륨 | 개별 테스트 스크립트 재생을 **50%**로 희망 | 서비스/BMO 전체 system mixer나 앰프, Fish 런타임 gain을 50%로 바꾸라는 의미가 아님 |
| 언어 | 원작에 가까운 **영어 BMO 우선**, 한국어 질문/답변은 제품의 별도 설정 | 해당 로컬 Piper 모델은 영어용. 한국어를 정상 발음한다고 표시하지 않음 |

Fish API 음성은 호출할 때마다 정책·이용량에 따른 비용이 발생할 수 있다. Fish의 무료 모델 정책이 끝나더라도 **사용자 동의 없이 유료 모델로 전환하지 않는다**. 일반 GPT/STT의 클라우드 API 비용도 별개다.

### 로컬 음성 실험 종료 결과

- BMO Piper 음색 자체는 사용자 평가에서 **마음에 듦**. 특히 “Hello! I'm BMO!” 도입부는 매우 만족.
- 긴 문장 중간/끝에서 억양과 감정이 평탄해지는 문제는 존재한다.
- 질문만 따로 분리한 합성, 문장부호·변동성·속도 조절은 보편적으로 개선되지 않았다.
- C/D 일부 구간의 수동 피치 상승은 순간적으로 나았지만, 다른 버전에서는 **헬륨을 마신 듯한 부자연스러운 고음** 발생.
- v3 파라미터 3/4는 특정 문장에서는 조금 나을 수도 있었지만 여러 문장에 일관된 개선 증거는 없음.
- **결정:** 로컬 Piper 원본 음색/기본 설정을 보존하고 음성 보정 실험은 여기서 종료. 임시·백업용으로 사용. RVC, 재학습, 추가 변조는 현재 작업 범위에 넣지 않는다.
- 테스트 자산은 제품 코드 밖에 보존; ZIP, WAV, 피치 변조된 실험본을 런타임 기본값으로 넣지 않는다.

로컬 보존 위치(기존 독립 테스트, 임의 수정 금지):
- Piper 실행기: /home/bgh/bmo-voice-test/piper/piper
- BMO 모델: /home/bgh/bmo-voice-test/bmo-piper/en_US-bmo_voice.onnx
- 구성 JSON: /home/bgh/bmo-voice-test/bmo-piper/en_US-bmo_voice.onnx.json
- 프로젝트 코드: /home/bgh/BGH_BMO/software

**공개 소스 링크** (GitHub에 제3자 가중치·음원 재배포하지 않음): https://huggingface.co/ziadnotme/bmo-tts-piper ; 원본 음성 캐릭터의 권리 및 해당 모델 가중치 이용/재배포 라이선스는 별도 확인이 필요하다.

## 2. 실제 음성 데이터 흐름과 제한

현재 Pi에서 검증된 메인 대화 경로:

USB 마이크 → ALSA 녹음 → OpenAI STT (gpt-4o-mini-transcribe) → OpenAI GPT (gpt-4.1-mini) → Fish Audio Aaron Soft (s2.1-pro-free) → WAV 검증 → ALSA → MAX98357A I2S 앰프/스피커.

- Pi에서 **Fish Aaron Soft + GPT + STT + 스피커로 실제 음성 대화 성공**(2026-10-21 사용자 확인).
- 단일 턴 기반 Push-to-Talk CLI가 구현 경로다. 향후 버튼/VAD/웹 얼굴 일체형 경험이 즉시 구현됐다는 뜻은 아니다.
- Fish 출력 경로는 software/bmo_ai/fish_tts.py. Fish WAV 스트리밍 헤더 크기 보정 처리가 있다. 사용자 API 키는 환경변수 FISH_API_KEY이며 GitHub에 저장하지 않는다.
- Fish 장애 fallback 코드는 별도 **feature/bmo-local-piper-fallback** 브랜치의 software/bmo_ai/app.py + local_piper.py에 있다.
- 이 브랜치에서 기본 설정은 BMO_TTS_PROVIDER=openai, BMO_OFFLINE_VOICE=false, BMO_FISH_GAIN=0.30. **제품에서 원하는 Fish 메인/로컬 예비 정책과 코드의 기본값을 혼동하지 말 것.** Fish는 --tts-provider fish 등으로 명시 선택하며, 로컬 fallback은 --offline-voice 또는 BMO_OFFLINE_VOICE=true로 켜야 한다.
- Fish 합성 단계에서 처리 가능한 AudioSynthesisError가 발생하고 fallback이 활성화돼 있다면 영어 답변에 Piper를 사용하도록 구현돼 있다. 모든 가능한 오류가 fallback되는 것은 아니며 **Pi에서 실제 Fish 실패를 유도한 자동 전환 테스트는 아직 완료로 확인되지 않았다**.
- GPT/STT 네트워크 오류 때 Piper는 새 질문을 이해하거나 새 답을 생성하지 못한다. 코드의 **고정된 영어 오프라인 안내**만 읽을 수 있다. 즉, 로컬 Piper는 로컬 AI 두뇌가 아니다.
- 자동으로 OpenAI 유료 TTS나 유료 Fish 모델을 대신 호출하지 않는다. 한 턴에 TTS 하나의 재생 경로가 원칙이다.
- 반복 발화나 재합성은 추가 API 요금이 생길 수 있다. 재시도/비용/캔슬 처리 확대는 별도 검증이 필요하다.
- Fish 음성 장애가 아닌 LLM/STT 장애일 때 전면 오프라인 대화가 가능하다고 광고하지 않는다.

실행/기술 기준:
- GitHub: https://github.com/GUNOBAE/BGH_BMO
- 메인 Fish 코드: https://github.com/GUNOBAE/BGH_BMO/tree/feature/bmo-fish-aaron-tts
- Piper 연동 코드: https://github.com/GUNOBAE/BGH_BMO/tree/feature/bmo-local-piper-fallback
- 해당 브랜치 구현 확인 경로: software/bmo_ai/fish_tts.py, software/bmo_ai/local_piper.py, software/bmo_ai/app.py, software/bmo_ai/config.py, software/bmo_ai/__main__.py
- 이 문서는 코드를 수정하지 않는다. 메인 병합과 실제 장치 업데이트는 별도 결정이다.

## 3. 얼굴·화면 작업: 다른 채팅에서 진행 중, 절대 덮어쓰지 않음

- 사용자 선호: Adventure Time BMO의 정면 비율/색감과 표정. Base1 정면 배치 및 기본 눈 크기를 기준으로 원본 움직임만 섬세하게 반영.
- 별도 화면 채팅에서 BMO_SourceTalk_v6.html까지 실제 말하기 영상의 입·눈 자료를 반영한 실험이 진행되었다. 2026-10-21 후속에는 **BMO_Screen_v11.html** 시안까지 생성됐다는 대화 기록이 있음.
- 최신 시안의 눈물 위치(눈알 바로 밑), 놀랄 때 눈 크기 변화, 듣기 표정, 대기 입 닫힘 등의 검토가 이어지고 있다. 시안 생성과 **최종 사용자 승인/실제 Pi 배포**는 구분한다.
- GitHub software/bmo_ai/display_app.py 및 face.py의 Pygame 수동 화면 테스트와 최신 HTML 시안은 **같은 프로그램이라고 가정하지 말 것**. 실제 통합 완료 검증이 없다.
- 현재 음성 연동 작업을 핑계로 얼굴 파일, 원본 PNG, 영상 셀, 팔이나 버튼 코드를 임의 수정하지 않는다. 얼굴 담당 채팅에서 최종 확정본/리소스를 인수받은 뒤 별도 통합 작업으로 진행.
- 말할 때 얼굴/입 모양 연동은 장기 목표다. 아직 최신 화면 시안과 Fish/Piper 런타임 음성이 통합됐다고 쓰지 않는다.

## 4. 하드웨어 확정사항과 안전 경계

| 항목 | 확인된 기준 / 경계 |
|---|---|
| 메인 보드 | Raspberry Pi 5 8GB (AI 가속기 **제거**, 장착/필수라고 쓰지 않음) |
| 저장장치 | NVMe SSD 1TB + microSD 512GB |
| 운영 환경 | Debian GNU/Linux 13 (trixie), Python 3.13.5, aarch64 |
| 화면 | 7인치 터치 디스플레이; 기존 관측 800×480 DSI/Wayland |
| 음성 입력 | USB 마이크 (USB 스피커로 혼동 금지) |
| 음성 출력 | MAX98357A I2S 앰프 + 스피커; 장치명은 실제 ALSA 인벤토리 기준 |
| 버튼/제어 PCB | **RP2040-Zero**, Raspberry Pi Zero가 아님 |
| IMU | MPU-6050은 RP2040의 GP0 SDA / GP1 SCL / GP9 INT 연결. Pi GPIO에 직접 연결한 I2C 센서 아님 |
| 서보 | 양팔 각각 1개, 총 2개; 실제 제어/보정 안전성은 별도 검증 |
| 팬 | 기존 bmo-case-fan.service 자동 제어가 운영되었음; 다른 작업에서 건드리지 않음 |

- RP2040의 실제 직렬 프로토콜/펌웨어, 버튼 매핑, IMU 측정 정상성은 보수적으로 다룬다. 기존 IMU 진단 문서에는 바이어스/자체진단 이상 근거가 있어 **자동 반응이나 팔 동작에 사용하지 않는다**.
- software/bmo_ai/hardware.py의 팔 제어는 안전상 거부 구현이며, 노래/게임/사진/제스처는 **목표 기능이지 완료 기능이 아님**.
- 목표 경험: 즐거울 때 팔 들기, 사진 요청 시 카메라 촬영, 저장된 노래 재생, 저장된 게임 실행, 버튼 기반 대화/메뉴, 감정 표정 등. 모터 명령을 GPT 자유 텍스트로 실행하지 않는다.
- 카메라/IMU/서보/게임/시스템 서비스는 명시적으로 승인한 별도 실물 검증 전까지 새 에이전트가 조작·설치·업데이트하지 않는다.

## 5. GitHub 브랜치 및 진행상태 (2026-10-21 조회)

| 브랜치 | 역할 | 주의 |
|---|---|---|
| main | 공개 기본 README/PCB/계획 문서 중심 | 최신 런타임 코드를 포함한다고 가정하지 않는다. 구형 README의 AI HAT 문구는 현재 실물 상태와 다를 수 있다 |
| feature/bmo-robust-conversation | 대화/기본 오디오/얼굴 Pygame 기반 | 독립 작업 결과 보존. 현재 Fish/로컬 최종 상태로 오인하지 않는다 |
| feature/bmo-fish-aaron-tts | Fish Aaron 음성 연동 및 실기 성공 계열 | 메인 음성 변경 작업의 출처 |
| feature/bmo-local-piper-fallback | Fish 실패 시 Piper 대체 어댑터 및 테스트 | 실제 장애 전환/배포 확정 전 추가 검증 필요 |
| docs/bmo-product-plan-kr | 제품·버튼·동작의 미래 설계 초안 | 구현/승인 완료 아님. 현 사용자 결정과 상충 시 확인 |
| docs/bmo-cross-chat-handoff-20261021 | 이 문서만 담는 충돌 방지 문서 브랜치 | 런타임 변경 없음, 기존 브랜치 자동 병합 없음 |

공개 저장소 README나 software/README_KR.md에는 작업 당시 시점의 “테스트 미완료” 설명이나 다른 기본값이 남아 있을 수 있다. **이 문서는 2026-10-21의 사용자 결정·실기 관찰을 최신으로 정리하지만, 변경된 코드/실제 설정 여부는 해당 작업자가 읽기 전용으로 대조해야 한다.**

## 6. 다른 채팅·Codex가 반드시 지킬 작업 경계

1. **메인 Fish 채팅:** Aaron Soft, Fish 연결·과금, 클라우드 GPT/STT, 재생; 로컬 파이퍼 모델 원본 수정 금지.
2. **로컬 음성 채팅:** 실험 종료. 원본 모델/설정 백업만 보존. 재학습·추가 피치/음성 튜닝 재개하지 않음(새 사용자 지시가 있으면 예외).
3. **얼굴 채팅:** 최신 HTML/SVG 표정 시안과 검수. GitHub의 구형 Pygame 얼굴이나 음성 코드를 독단적으로 덮어쓰지 않음.
4. **하드웨어·게임/기능 채팅:** 계획/검증과 구현을 분리. 실제 IMU/서보/펌웨어·버튼·팬·시스템 오디오 변경은 별도 확인.
5. **Pi Codex:** 다른 세션에서 요청된 읽기 전용 진단 규칙 유지. 명시적 새 지시 없이 파일 작성/삭제, 장치 변경, 설치, 코드 실행, Git 변경을 하지 않는다.
6. 비밀 FISH_API_KEY/OPENAI_API_KEY, .env 실파일, 실사용 녹음, 개인 로그, 모델 가중치, 권리 미확인 캐릭터 자산, 로컬 백업을 GitHub에 업로드하지 않는다.
7. Git의 포괄 stage, force push, 이력 변경, main 병합 및 서비스 자동 재시작 금지. 관련 브랜치 상태·미커밋 파일을 읽고 충돌 없이 범위 별로 진행.
8. 사용자는 **코딩 초보**로 실행 안내가 필요하다. 파일/스크립트가 실제로 필요할 때만 Windows PowerShell vs Pi 터미널을 구분하고 복사·붙여넣기 명령, 성공 판정, 복구 경로를 함께 제공.
9. **말투/목소리 일관성:** 감정마다 Aaron ↔ 다른 Fish 보이스 자동 전환하지 않는다. 로컬 Piper는 메인 보이스와 완전히 같은 음색이 아님을 인정한 장애 대비 수단이다.

## 7. 남은 일 — 완료로 표시하지 말 것

- [x] Fish Aaron Soft + 클라우드 GPT/STT의 Pi 대화/출력 실기 사용 확인 (이전 대화 보고)
- [x] ziadnotme BMO Piper 원본 음색 선택 및 별도 음성 합성 청취
- [x] 로컬 BMO 추가 피치·변동성 보정 **중단 결정**, 원본 보존
- [x] GitHub에 Fish·Piper fallback **별도 브랜치 코드** 존재 확인
- [ ] 현재 Pi 실행 브랜치/설정과 GitHub의 **local-piper-fallback** 실제 일치 확인
- [ ] 실제 Fish TTS 장애 시 Piper로 자동 전환하는 실기 테스트 (의도적 유료 요청/파괴적 오류 테스트 금지; 승인·안전한 모의 방식 설계)
- [ ] Fish gain 25% 선호와 코드 기본 30% 불일치 해결 범위/방법 결정 (설정으로 먼저 해결, 코드는 함부로 수정하지 않음)
- [ ] 최신 얼굴 HTML(v11 포함)을 사용자가 최종 채택했는지 및 Pi 런타임 연동 확인
- [ ] 버튼/카메라/노래/게임/서보/IMU 통합 및 단계별 안전 검증
- [ ] 최종 확정된 코드 브랜치 병합·배포 및 공식 README 상태 정비

## 8. 다른 채팅에 넘길 4문장 요약

**BMO 메인 음성은 Fish Audio Aaron Soft (voice 106f41ea392c4cc48c6eee76f69ff17e, s2.1-pro-free, beemo 발음, 선호 gain 0.25)이고, GPT-4.1-mini + gpt-4o-mini-transcribe는 클라우드 기반이다.**  
**로컬 백업 음성은 ziadnotme BMO Piper ONNX 원본으로 확정했다. 피치·변동성 보정은 부자연스러워 폐기했고 로컬 TTS 개발은 종료한다.**  
**Fish TTS가 실패할 때만 로컬 영어 Piper를 쓰는 opt-in 코드 브랜치가 있으나 실제 Pi 자동 장애 전환 완료는 미확인이고, 인터넷이 없으면 새 GPT 대화도 되지 않는다.**  
**얼굴 HTML 최신 작업, RP2040·IMU·서보·게임·카메라 개발은 별도 진행이므로 이 인수인계만 보고 코드를 변경·병합·배포하지 말 것.**

---

이 문서 작성은 **문서 기록만** 수행한다. GitHub 계정·클라우드 API·RP2040·사운드 설정·systemd 서비스·Pi 파일의 실시간 상태를 자동으로 변경하지 않는다.
