# Project Outline: BGH_BMO

**BGH_BMO** is a custom, scaled-up AI companion robot inspired by the character BMO from *Adventure Time*.

After watching [brenpoly's be-more-agent](https://github.com/brenpoly/be-more-agent), I decided to build my own version. The chassis was re-engineered into solid CAD geometry to accommodate a **7-inch display** and an optimized internal layout, using a **Raspberry Pi 5 (8GB)** as the host. The current conversational implementation uses cloud GPT after earlier local-AI experiments.

**Software update — 2026-10-13:** English/Korean conversation, push-to-talk STT/TTS, safe device discovery, configuration, logging, tests and hardware extension contracts are implemented on the development branch. Live cloud and physical audio remain unverified; arm movement is disabled. Start with the [English software guide](software/README.md), [Korean guide](software/README_KR.md), [architecture](docs/architecture.md) or [development report](docs/reports/20261013-development-report.md). The BOM below includes historical hardware choices rather than a newly verified deployment inventory.

---

### Core Objectives & Learning Goals

This project serves as a hands-on physical computing sandbox to master end-to-end hardware and software development:

* **Custom PCB Design & PCBA (KiCad):** Designing a custom front-panel controller using KiCad, routing an onboard MPU6050 IMU chip, and ordering full SMT assembly (PCBA) through PCBWay.
* **Hybrid 3D Modeling (Blender & CAD):** Using Blender to preprocess, repair, and clean complex STL mesh geometries that are otherwise difficult to manipulate natively in solid CAD software.
* **Conversational AI:** Evaluating earlier local-AI experiments and implementing bounded cloud GPT conversations with English and Korean support.
* **Edge System Integration:** Orchestrating power delivery (UPS/18650), I2S digital audio, camera vision, dual-servo arm actuation, and sensor processing into a unified embedded enclosure.

---

## ✨ Key Features & Improvements over Original

* **Cloud conversation software**: Added GPT text and push-to-talk speech pipelines with offline tests; live cloud and hardware performance remain to be verified.
* **135% Scaled Housing**: Redesigned 3D printable shell accommodating a **7-inch DSI capacitive touch display** (upgraded from 5-inch).
* **Integrated SMT Controller PCB**: Replaced the bulky Adafruit Feather board with a **custom RP2040-Zero carrier board** featuring an onboard MPU6050 IMU (SMT assembled) to drive 7 tactile buttons, dual-arm SG90S micro servos, and orientation tracking.
* **Personal Hub Expansion**: Designed to serve as an edge computing hub, local portfolio host, and a platform for physical computing.

---

# 🏗️ BOM
### 3d printing
- BAMBU LAB x2d
- PLA (BAMBU)

### Hardware
- M2.5, M3 Bolt & nu- Raspberry Pi 5 (8GB) – x1

### Electrinics
- Raspberry Pi 5 8GB x1
- Raspberry Pi Official Active Cooler – x1
- Raspberry Pi AI HAT+ 2 (Hailo-10H, VLM/GenAI) – x1
- 7-inch DSI Capacitive Touch LCD (165 × 100 × 8mm) – x1
- Raspberry Pi Camera Module 3 Wide (IMX708, 120° FOV) – x1
    - 22-pin to 15-pin FPC Camera Cable (for Pi 5 CSI port) – x1
- Geekworm X1203 UPS Shield Board – x1
    - Geekworm X12-A1 4-Cell 18650 Battery Holder Board – x1
    - Samsung 18650 Li-ion Battery (Flat-top, removed shield) – x4 
- 5010 PWM 5V Cooling Fan – x1
- MAX98357A Amplifier Module – x1
- 40mm 3W 4Ω Speaker – x1
- 2.54mm Dupont Pin Header / JST-XH 2-pin Connector Kit – x1
- SG90S servo x2
- C Type Charge Cable x1
- Dual USB Male - Female twin ports (USB 30, M3, 0.25cm) x1

### PCB (PCBWAY)
- RP2040-Zero Microcontroller – x1
- 6×6mm Micro Tactile Switch (for D-Pad and Action Buttons) – x 7
- SMT (Mpu6050, capacitor, regisister, etc)

# 🔗 References & Credits
- Original concept: brenpoly/be-more-agent
- PCBWAY
