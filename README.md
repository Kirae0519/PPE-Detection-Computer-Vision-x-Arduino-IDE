# PPE Checker

[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-3776AB?logo=python&logoColor=white)](https://python.org)
[![Ultralytics YOLO](https://img.shields.io/badge/YOLO-v9--e-00FFFF?logo=yolo&logoColor=black)](https://github.com/ultralytics/ultralytics)
[![GUI](https://img.shields.io/badge/GUI-CustomTkinter-blue)](https://github.com/TomSchimansky/CustomTkinter)
[![Hardware](https://img.shields.io/badge/Hardware-Arduino%20Uno-00979D?logo=arduino&logoColor=white)](ARDUINO_SETUP.md)

**PPE Checker** (`v2.7`) is a real-time computer vision desktop application for personal protective equipment (PPE) compliance monitoring, safety violation alerting, incident audit logging, and optional Arduino-based physical safety station integration.

---

## Key Features

- **Real-Time PPE Detection & Tracking**
  - Powered by **YOLOv9-e** (`sh17_yolov9e.pt`, trained on the SH17 dataset) and **ByteTrack** (`bytetrack_ppe.yaml`) for persistent per-worker tracking.
  - Detects 6 PPE categories:
    - Hard Hat Helmet (`helmet`)
    - Safety Vest (`vest`)
    - Protective Mask (`mask`)
    - Safety Goggles (`glasses`)
    - Work Gloves (`gloves`)
    - Sound Earmuffs (`earmuffs`)
- **1-Click Industry Policy Presets**
  - **Full Gear (All 6):** Helmet, Vest, Mask, Glasses, Gloves, Earmuffs
  - **Construction:** Helmet, Vest, Glasses, Gloves
  - **Factory Floor:** Vest, Glasses, Gloves, Earmuffs
  - **Cleanroom / Lab:** Mask, Glasses, Gloves
- **BrickSafe Desktop UI**
  - Built with CustomTkinter featuring Light & Dark themes, live KPI summary cards (Total Violations, Compliant Workers, Audit Logs Saved), per-worker compliance gauges, equipment checklists, and actionable inspection guidance.
- **Incident Audit Logging**
  - Captures annotated worker snapshots (`.jpg`), text checklists (`.txt`), and structured records (`logs/logs.csv`) with one click.
- **Arduino Safety Station Integration (Optional)**
  - Auto-detects an Arduino Uno over USB serial (`arduino_bridge.py`).
  - Drives **Red/Green status LEDs**, a **piezo buzzer**, a **16x2 I2C LCD**, and a **mini thermal receipt printer** for physical pass/violation report cards. See [ARDUINO_SETUP.md](ARDUINO_SETUP.md) for wiring and firmware instructions.

---

## Project Structure

```text
.
├── main.py                      # Desktop application & YOLOv9-e + ByteTrack pipeline
├── arduino_bridge.py            # Non-blocking USB serial bridge for Arduino station
├── inspection_guidance.py       # Policy-based corrective action recommendations
├── bytetrack_ppe.yaml           # ByteTrack tracker configuration
├── requirements.txt             # Python dependencies
├── ARDUINO_SETUP.md             # Hardware wiring, firmware & thermal printer guide
├── arduino_ppe_station/
│   └── arduino_ppe_station.ino  # Arduino Uno sketch (LCD, LEDs, buzzer, thermal printer)
├── assets/
│   └── lego_welcome_bg.jpg      # Welcome screen background asset
├── evaluation/
│   └── evaluate.py              # Dataset evaluation script (mAP, precision, recall)
├── models/                      # Local model weights (auto-downloaded, gitignored)
└── logs/                        # Audit CSV & evidence snapshots (gitignored)
```

---

## Installation & Quick Start

1. **Clone the repository:**
   ```bash
   git clone <your-repo-url>
   cd ppe-computer-vision-main
   ```

2. **Create a virtual environment (recommended) and install dependencies:**
   ```bash
   python -m venv .venv
   # Windows PowerShell:
   .venv\Scripts\Activate.ps1
   # macOS / Linux:
   source .venv/bin/activate

   pip install -r requirements.txt
   ```

3. **Run the application:**
   ```bash
   python main.py
   ```
   > **Note:** On first launch, if `models/sh17_yolov9e.pt` (~117 MB) is not present locally, `main.py` will automatically download it from the SH17 release URL.

---

## CLI Options

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--model` | `models/sh17_yolov9e.pt` | Path to YOLO weights file |
| `--camera` | `0` | Camera device index |
| `--confidence` | `0.30` | Base detection confidence threshold |
| `--imgsz` | `384` | Inference image size (`320`/`384`/`416`/`480`/`640`) |
| `--device` | `auto` | Inference device (`cpu` or `0` for NVIDIA GPU) |
| `--ppe` | `all` | PPE items enforced at startup (`all`, `none`, or item list) |
| `--hold-seconds` | `3.0` | Seconds to persist worn items across brief detection gaps |
| `--log-dir` | `logs` | Directory for CSV audit logs and snapshots |
| `--log-theme` | `dark` | Theme for saved audit report cards (`light` or `dark`) |
| `--arduino-port` | `auto` | Arduino COM port (e.g. `COM3`, `auto`, or `none` to disable) |
| `--no-download` | `False` | Disable automatic model weight download |

Example:
```bash
python main.py --camera 0 --imgsz 480 --device 0 --arduino-port COM3
```

---

## Model Evaluation

To benchmark weights against a labeled Ultralytics dataset YAML:

```bash
python evaluation/evaluate.py --model models/sh17_yolov9e.pt --data path/to/dataset.yaml --split test
```

Results and `metrics_summary.json` are saved under `evaluation/results/`.
