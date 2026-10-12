# BMO Cloud AI MVP — Raspberry Pi 5

첫 구현 범위: **텍스트 대화 + 버튼 없이 Enter로 녹음하는 Push-to-Talk + GPT 답변 + TTS 음성 출력**.
이 코드는 실제 BMO 하드웨어에서 **아직 검증되지 않은 초안**입니다. 기존 펌웨어·화면·OS 설정을 변경하지 않습니다.

## 확인한 장비
- Raspberry Pi 5 **8GB**, Debian 13 (trixie), Python 3.13, NVMe SSD 1TB / microSD 512GB
- USB 마이크; MAX98357A I2S 스피커; 7인치 디스플레이
- RP2040-Zero PCB: 버튼 7개, MPU6050 (GP0 SDA/GP1 SCL/GP9 INT), 양팔 서보
- RP2040 제어 및 화면·카메라·IMU는 **후속 통합**, 현재 음성 MVP에서 임의로 작동시키지 않음

## 설치 (Pi 터미널)
```bash
sudo apt update
sudo apt install -y alsa-utils python3-venv
git clone https://github.com/GUNOBAE/BGH_BMO.git
cd BGH_BMO
# 이 브랜치가 병합되기 전에는:
git fetch origin feature/bmo-cloud-ai-mvp
git switch feature/bmo-cloud-ai-mvp
cd software
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export OPENAI_API_KEY='YOUR_OPENAI_API_KEY'
python -m bmo_ai --text
```

**API 키는 GitHub에 올리지 마세요.** ChatGPT 유료 구독과 OpenAI API 요금은 별도입니다.

## 오디오 장치 확인 (설정을 변경하지 않음)
```bash
arecord -l
aplay -l
arecord -D default -f S16_LE -r 16000 -c 1 -d 3 /tmp/bmo_input.wav
aplay /tmp/bmo_input.wav
```
장치 번호는 실제 Pi 출력으로 확인하세요. USB 마이크 녹음이 되지만 I2S 스피커가 안 들리면 `aplay -l`에서 MAX98357A 관련 ALSA 장치가 보이는지 먼저 확인하세요. **GPIO/I2S overlay를 추측해서 변경하지 마세요.**

## 음성 데모
```bash
python -m bmo_ai --seconds 5
# 특정 장치를 명시할 때(번호는 예시이며 본체에 맞게 변경):
python -m bmo_ai --mic-device plughw:1,0 --speaker-device plughw:0,0
# 녹음+AI만 검증하고 싶다면
python -m bmo_ai --mute
```

## 단위 테스트
```bash
PYTHONPATH=. python -m unittest discover -s tests -v
```

## 구조 및 다음 순서
`core.py`: AI 대화와 제한된 문맥 / `audio.py`: ALSA 녹음, STT, TTS / `__main__.py`: CLI orchestration.

1. 텍스트 API 호출과 예외 처리 검증
2. USB 마이크의 `arecord` 녹음 검증
3. I2S 앰프의 `aplay` 출력 검증
4. RP2040 USB 버튼/IMU/서보 프로토콜 정의 및 안전한 드라이버 구현
5. BMO 표정 애니메이션 + 카메라 + 동작 연동
6. 지연·API 비용·자동 시작 서비스 검증

초기 구현은 **상시 청취형 실시간 음성 대화가 아닙니다**. 이는 하드웨어 확인과 비용 관리가 쉬운 Push-to-Talk 기반 MVP입니다.
