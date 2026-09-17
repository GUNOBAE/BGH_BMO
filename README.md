# Project Outline: BGH_BMO

**BGH_BMO** is a custom, scaled-up, on-device AI companion robot inspired by the character BMO from *Adventure Time*.  

After watching [brenpoly's be-more-agent](https://github.com/brenpoly/be-more-agent), I decided to build my own version with significant engineering improvements. Instead of directly reusing mesh assets, the original chassis was re-engineered into solid CAD geometry to accommodate a larger **7-inch display** and an optimized internal layout. Rather than relying on cloud APIs or high-power computing, this build leverages a **Raspberry Pi 5 (8GB)** paired with the **Raspberry Pi AI HAT+ (Hailo-10H NPU)** for efficient, low-latency edge inference.

---

### Core Objectives & Learning Goals

This project serves as a hands-on physical computing sandbox to master end-to-end hardware and software development:

* **Custom PCB Design & PCBA (KiCad):** Designing a custom front-panel controller using KiCad, routing an onboard MPU6050 IMU chip, and ordering full SMT assembly (PCBA) through PCBWay.
* **Hybrid 3D Modeling (Blender & CAD):** Using Blender to preprocess, repair, and clean complex STL mesh geometries that are otherwise difficult to manipulate natively in solid CAD software.
* **On-Device Physical AI:** Deploying local LLM/VLM pipelines directly onto the Hailo-10H NPU to enable natural, conversational human-robot interaction without cloud dependencies.
* **Edge System Integration:** Orchestrating power delivery (UPS/18650), I2S digital audio, camera vision, dual-servo arm actuation, and sensor processing into a unified embedded enclosure.

---

## ✨ Key Features & Improvements over Original

* **NPU Acceleration**: Replaced high-load cloud/CPU pipelines with an on-device VLM/LLM architecture running on the **Hailo-10H AI accelerator (40 TOPS)**.
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
