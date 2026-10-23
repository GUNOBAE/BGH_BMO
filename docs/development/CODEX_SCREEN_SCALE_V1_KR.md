# Codex 추가 작업 — 승인 얼굴의 7인치 크기 보정 v1

2026-10-23 KST. **크기 전용 코드와 미리보기·오프라인 렌더링 검사 완료. Pi 런타임 적용·실물 크기 승인은 아직 아니다.**

## 사용자 요청과 변경 범위

사용자는 7인치 Pi 화면에서 얼굴이 너무 작다고 확인했다. 함께 보낸 예시 이미지는 화면 점유 크기의 참고일 뿐, 예시의 눈·입·색·스타일로 얼굴을 바꾸라는 지시가 아니다. **승인 v13.2의 비율/아트/표정/움직임은 유지하고 표시 크기만 크게 한다.**

이 요청은 기존 원본 디자인 변경 금지를 전체 해제하는 것이 아니다. 승인 파일의 바이트는 계속 보존하고 외부 표시층에서 전체를 동일 배율로 확대한다. 표정·발화 정책·목소리·감정 연속성은 이 작업으로 변경하지 않는다. 새 크기의 사용자 최종 시각 승인은 아직 받지 않았다.

## 확인한 원인과 수정

원본 SHA-256: `38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932`.

원본 CSS의 `@media(max-width:1000px)`에 `.stage{max-height:70vh}`가 있다. kiosk의 max-height 해제는 580px 이하 규칙 안에만 있다. 따라서 800×480에서는 kiosk stage가 실제 높이 336px로 제한된다. 부모 wrapper가 100%여도 iframe 내부의 이 제한은 남는다.

ChatGPT 도구 환경에서 승인 원본 자체를 Chromium으로 렌더링해 이를 재현했다. **Pi 현재 세션의 실측값을 읽은 것은 아니다.** 800×480 재현에서 기본 얼굴 바운딩 폭은 약 244.785px였다.

수정 코드는 같은 docs 브랜치의 `tools/bmo-face-scale-v1/viewport.js`다.

- kiosk/fullscreen에서 stage의 max-height를 해제하고 실제 viewport 전체를 사용한다.
- SVG viewBox를 중심 `(500,350)` 기준으로 조절하며 `xMidYMid meet`를 유지한다. 기본 등비 확대 1.50배. x/y 서로 다른 비율의 CSS stretch 금지.
- 눈·입·눈물·선 두께·시선 이동이 동일 좌표계에서 확대된다. 눈 간격만 벌리거나 입만 키우거나 원본 path/ellipse/cryFlow/시간표를 수정하지 않는다.
- 감정에 따라 배율을 바꾸지 않는다. 모든 승인 감정·입·시선 극단을 포함한 보수적 영역을 사용해 좁은/세로형 미리보기에서는 배율만 안전하게 제한한다. 대화 도중 표정별 크기 출렁임 없음.
- 800×480에서 기본 얼굴 폭은 약 524.540px, 전체 화면 폭의 약 65.6%다. 원래 높이 제한 해제까지 포함하면 기존 재현 대비 약 2.143배. 이것을 모든 기기에서 무조건 2.143배가 된다고 설명하지 않는다.
- original BMOFace API/version/animation/asset data를 변경하지 않는다. `getState().version`은 계속 13.2, layoutVersion은 별도 `7inch-uniform-v1`이다.
- 원본 파일 수정·공개 업로드·FACE_SHA256 검사 삭제 없음. 기존 Fish/Piper/게인·감정 연속성 계약도 그대로다.

코드 SHA-256: `7adbb6da257a6476c3942e0d8422e33ca3a9e43fb110f37889f3e45cff18ab0b`.

## 기존 작업과 적용 순서

감정 연속성 v2를 진행 중인 Codex 조정자에게 이 작업을 추가한다. 그 작업의 핵심 수정·검사를 마무리하고 같은 `/home/bgh/BMO_EMOTION`에서 표시층을 연결한다. 다른 Codex 세션을 새로 열어 adapter/server를 동시에 수정하지 않는다. v2가 이미 push됐으면 최신 결과를 인수하고 이어간다.

- 기본 반영 브랜치: `feature/bmo-auto-emotion-v1`, PR #13에 크기 전용 변경을 별도 커밋으로 기록한다.
- 기능 묶음 `/home/bgh/BMO_FEATURES`는 검증된 v2+이 표시층 커밋을 선택적으로 인수한다. 기능마다 원본 얼굴을 다시 복제/수정하지 않는다.
- 적용 AGENTS.md·경로·remote/HEAD/status를 확인하고 사용자 미커밋 변경을 보존한다. 전체 preflight 반복·reset --hard·clean·force-push·main 병합 금지.
- 현재 BMO를 임의 종료하지 않는다. 실행 중이면 코드 검증은 별도 fixture로 하고 사용자 정상 `/quit` 후 새 실행에서 적용한다.

## 실제 연결 방법

현재 `FaceServer`는 승인 원본 한 파일과 wrapper/adapter를 허용 목록으로 제공한다. 원본 HTML에 외부 JS script src를 삽입하지 않는다. 원본 CSP와 source hash를 유지한다.

1. 검토한 `viewport.js`를 새 앱 정적 자원 `software/bmo_ai/integration_web/viewport.js`로 인수한다.
2. `FaceServer._resources`에 `/viewport.js` 한 경로만 추가한다. 폴더 전체 노출·임의 파일 API·CORS 완화 금지.
3. wrapper의 같은 출처 스크립트를 `/viewport.js` → `/adapter.js` 순서(defer)로 로드한다.
4. adapter는 실제 iframe Document와 BMOFace가 준비된 후 부모의 `BMOViewportV1.attach(frame.contentDocument)`를 한 번 호출하고 controller를 소유한다. 기본 scale 1.5. 같은 Document의 중복 attach는 같은 controller 반환이며 누적 확대하지 않는다.
5. iframe 교체/새 load 및 adapter close에서 이전 controller.dispose를 호출하고 새 Document에 연결한다. pagehide/ResizeObserver/resize/fullscreenchange의 누수·중복을 검사한다. 이미 폐기된 Document controller를 재사용하지 않는다.
6. 실패 시 원문 파일·키·경로를 노출하지 않는 고정 진단을 남기고, 기존 얼굴로 복귀 가능하게 한다. 새 표시층이 준비되기 전 첫 프레임이 잠깐 작게 보이는 문제는 wrapper 로딩 표시 안에서 처리한다. 오디오·session 상태를 초기화하는 식으로 우회하지 않는다.
7. Pi의 실제 viewport width/height, stage rect, devicePixelRatio와 적용 scale를 비밀 없는 진단에 기록한다. 7인치라는 물리 크기만으로 픽셀 규격을 추정하지 않는다. 브라우저 자체 확대와 다른 중복 CSS scale가 있으면 확인하되 OS/사용자 설정을 임의 변경하지 않는다.

이 코드의 fit envelope는 v13.2 아트에 대한 것이다. 이후 얼굴 아트를 변경하면 새 경계 검증을 해야 한다. 배율을 더 높이려면 현재 사용자 요청 범위에서 별도 확인·클리핑 검사를 수행한다. 이번에는 1.50배 후보를 임의로 또 키우지 않는다.

## 이미 수행한 검사와 남은 검사

ChatGPT 컨테이너의 Chromium, 승인 HTML 입력을 `page.set_content`로 렌더링했다. 해당 환경은 file/HTTP navigation이 차단돼 있었으며 브라우저 정책을 변경하지 않았다. 따라서 실제 localhost HTTP wrapper 통합을 검증했다고 쓰지 않는다.

완료한 오프라인 검사:

- 11개 검사 그룹 PASS. 실행한 수와 종류를 기존 전체 회귀 검사 수에 합산하지 않는다.
- 800×480의 70vh=336px 문제 재현, 수정 후 800×480 전체 채움.
- 10개 viewport: 800×480, 1024×600, 1280×800, 1920×1080, 1000×700, 581×480, 580×480, 360×640, 480×800, 800×300. x/y 배율 동일 및 kiosk 크기 일치.
- 6감정×14입×3깜빡임×5얼굴 시선×5눈 시선 = 6,300개 조합, stroke 여유 포함 화면 안에 있음. 800×480 최소 여유 약 44.114px.
- 6감정×3활동×3깜빡임×7시각 = 378개 조합. 감정/듣기/생각/끄덕임 상태 유지 및 화면 안. 최소 여유 약 43.826px.
- 울면서 듣기 180프레임에서 원본 눈물 유지.
- 원본 assets와 기존 실행 엔진/선택 API 및 상태 스냅샷 보존, 소스 파일 hash 전후 동일, 네트워크 요청 0.
- attach 중복, dispose 반복, 원본 viewBox/70vh 복귀, remount 검사.
- 실제 배포용 크기 미리보기 HTML 자체를 열어 F/ESC 및 1.5배 확인.

사용자에게 전달한 `BMO_Screen_v13_2_7inch_preview.html`은 승인 원본에 이 표시층을 덧붙인 **크기 검토용 파일**이다. SHA-256 `790bedfbc4797dbfb998fe159e3b4fc73d4a146e54d80b2d5ce5dadace1a8238`. 이 파일을 기존 FaceServer의 `--face-file`로 바로 대체하면 원본 hash와 달라 거부된다. **미리보기 파일을 원본 대신 등록하거나 hash 검사를 없애지 말고, 원본+외부 표시층으로 통합한다.** 미리보기 원본/전체 얼굴 데이터는 공개 Git에 올리지 않았다.

Pi Codex가 추가 수행할 것:

- 실제 FaceServer→wrapper→승인 iframe→viewport→adapter 경로의 headless 검사. 원본 파일 hash는 계속 승인값, 제공된 bytes도 원본과 동일.
- API/장치 없는 전체 회귀, v2 감정 연속성, 자동 표정, 입 cue/취소/반복/재연결/resize/fullscreen 검증.
- crying 대기→listening→thinking, surprised thinking/넓은 입, 모든 시선 corner와 eyeOnly corner에서 잘림·늘어남 없음.
- 외부 viewport를 붙인 뒤에도 감정과 활동을 변경하는 주체는 기존 session/adapter다. 확대 코드에서 neutral/reset/speaking/stop을 호출하지 않음.
- 서버 정적 허용 목록, CSP, script 순서, iframe load/close lifecycle, 이전 URL/세션 재연결 검증.
- 실제 LCD 시각 승인·가청 립싱크는 사용자 확인 전 NOT_RUN으로 유지.

## 제출

코드·검사·실행 안내 및 `docs/reports/SCREEN_SCALE_V1_LATEST_KR.md`를 코드 브랜치에 올리고 PR #13을 갱신한다. 별도 이미지 재디자인·목소리 수정·실제 장치 실행은 하지 않는다. 기능 묶음 작업서는 그대로 유지하되 이 표시층을 재사용한다.

최종 응답은 적용/미적용 구분, GitHub 보고서 링크, 같은 경로에서 재실행하는 방법 한 가지만 전달한다. 사용자에게 원본 파일을 또 찾아 업로드하라고 요구하지 않는다.
