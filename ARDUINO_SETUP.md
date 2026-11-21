# 🛠️ Arduino PPE Safety Inspection Station Setup Guide

This guide walks you through connecting your **Arduino Uno**, **I2C LCD (16x2)**, **2 LEDs (Red & Green)**, **1 Buzzer**, and **1 Mini Thermal Receipt Printer** to the PPE Computer Vision system.

---

## 📌 1. Hardware Components

| Component | Quantity | Purpose |
| :--- | :---: | :--- |
| **Arduino Uno** | 1 | Microcontroller station |
| **I2C LCD 16x2 Display** | 1 | Live screen showing worker status & compliance messages |
| **Red LED** | 1 | Lights up for **Violators** (missing PPE gear / policy violation) |
| **Green LED** | 1 | Lights up for **Valid / Accepted** (100% compliant) |
| **Resistors (220Ω – 330Ω)** | 2 | Current limiters for the Red and Green LEDs |
| **Buzzer** (Passive or Active) | 1 | Warning alarm for violators & Victory chime for 100% equipped |
| **Mini Thermal Receipt Printer** | 1 | Prints formatted safety audit report cards |
| **External DC Power Supply (5V–9V 2A)** | 1 | Power for the thermal printer (do **not** power printer from Arduino 5V) |
| Jumper Wires & Breadboard | — | Connections |

---

## 🔌 2. Wiring Diagram

```text
                     +---------------------------------------+
                     |              ARDUINO UNO              |
                     |                                       |
  [Red LED Anode] <--| Pin 7 (via 220Ω resistor)             |
  [Red LED Cathode]->| GND                                   |
                     |                                       |
[Green LED Anode] <--| Pin 8 (via 220Ω resistor)             |
[Green LED Cathode]->| GND                                   |
                     |                                       |
      [Buzzer (+)]<--| Pin 9                                 |
      [Buzzer (-)]-->| GND                                   |
                     |                                       |
     [I2C LCD GND]-->| GND                                   |
     [I2C LCD VCC]-->| 5V                                    |
     [I2C LCD SDA]-->| Pin A4 (or dedicated SDA pin)         |
     [I2C LCD SCL]-->| Pin A5 (or dedicated SCL pin)         |
                     |                                       |
     [Printer TX] -->| Pin A3 (SoftwareSerial RX)            |
     [Printer RX] <--| Pin A2 (SoftwareSerial TX)            |
    [Printer GND] -->| GND (Common Ground)                   |
                     +---------------------------------------+

        [External Power Supply 5V–9V 2A]
                     |
                     +----> Printer VH / VCC Pin
                     +----> Printer GND Pin (Connects to Arduino GND)
```

> [!IMPORTANT]
> **Thermal Printer Power Warning:**
> Mini thermal receipt printers draw **1.5A to 2.0A of peak current** during printing. The Arduino's onboard 5V regulator cannot supply this current. Connect the printer's power wires (`VH` / `VCC` and `GND`) to an **external 5V to 9V 2A power adapter**, and connect the printer `GND` to the Arduino `GND` so they share a common ground reference.

---

## 💻 3. Arduino IDE Setup & Library Installation

1. Open **Arduino IDE**.
2. Install the required libraries via **Sketch > Include Library > Manage Libraries...** (`Ctrl + Shift + I`):
   - **LiquidCrystal I2C** by Frank de Brabander (or Marco Schwartz)
   - **Adafruit Thermal Printer Library** by Adafruit
3. Open the sketch located at:
   `arduino_ppe_station/arduino_ppe_station.ino`
4. Go to **Tools > Board** and select **Arduino Uno**.
5. Go to **Tools > Port** and select your Arduino's COM port (e.g., `COM3`, `COM4`, etc.).
6. Click the **Upload (➔)** button.
7. Once uploaded, the Arduino will play a short startup chime, flash both LEDs, and show on the LCD:
   ```text
   +----------------+
   |  PPE DETECTOR  |
   | STATION READY  |
   +----------------+
   ```

> [!TIP]
> **LCD Contrast Adjustment:**
> If the LCD backlight turns on but you see black boxes or no text, adjust the **small blue potentiometer (contrast trimmer)** on the back of the I2C backpack using a small flathead screwdriver until the letters are crisp and clear.

> [!TIP]
> **Thermal Printer Baud Rate (9600 Baud):**
> 1. Both USB serial and the thermal printer use a fixed **9600 baud** rate (`Adafruit_Thermal`).
> 2. Open **Printer setup > Print test receipt** in the PPE Checker window to verify. If the receipt prints `THERMAL PRINTER WORKING!`, your wiring and baud rate are correct.
> 3. To check your printer's factory baud rate: unplug printer power, hold down the printer's **FEED** button, and plug power back in while holding the button for 2 seconds to print the self-test slip.

---

## 🚀 4. Running the Vision Software

1. Connect the Arduino Uno to your PC via USB cable.
2. Launch the PPE Checker software:
   ```bash
   python main.py
   ```
3. The software will **auto-detect** the Arduino Uno and show the connection status in the top header:
   ```text
   [ON] Arduino: COM3
   ```
4. If you have multiple devices or want to force a specific port, specify:
   ```bash
   python main.py --arduino-port COM3
   ```

---

## ⚡ 5. How It Works

### A. Automatic Mode (Real-Time Vision Alerts & LCD Updates)
- **Worker 100% Compliant**:
  - **Green LED** lights up.
  - **Buzzer** plays a 3-tone victory chime (C5 $\rightarrow$ E5 $\rightarrow$ G5).
  - **LCD** displays:
    ```text
    +----------------+
    |STATUS:COMPLIANT|
    |ACCESS GRANTED  |
    +----------------+
    ```
- **Worker Missing Gear / Violation** (or `1 HAND HAS NO GLOVE`):
  - **Red LED** lights up.
  - **Buzzer** sounds an urgent 2-tone warning alarm (440Hz $\rightarrow$ 310Hz).
  - **LCD** displays:
    ```text
    +----------------+
    |PPE VIOLATION!  |
    |MISSING HELMET  |
    +----------------+
    ```
- **Standby / Empty Inspection Zone**:
  - Both LEDs turn off.
  - **LCD** displays:
    ```text
    +----------------+
    |  PPE DETECTOR  |
    | SCANNING AREA  |
    +----------------+
    ```

### B. Manual / On-Demand Mode (Thermal Report Card Printing)
- On any detected worker's card in the software, click **`🖨️ Print Pass Card`** or **`🖨️ Print Violation`**:
- The LCD instantly updates to:
  ```text
  +----------------+
  |PRINTING REPORT |
  | PLEASE WAIT... |
  +----------------+
  ```
- The printer immediately outputs an itemized receipt card:
  ```text
  ================================
            PPE DETECTOR
      SAFETY AUDIT REPORT CARD
  ================================
  Worker ID : #01
  Date/Time : 2026-10-27 15:58:30
  Compliance: 100%
  Verdict   : [!] PASSED & 100% EQUIPPED
  --------------------------------
  GEAR CHECKLIST:
   [V] Hard Hat Helmet: EQUIPPED
   [V] Safety Vest: EQUIPPED
   [V] Protective Mask: EQUIPPED
   [V] Work Gloves: EQUIPPED
   [V] Safety Goggles: EQUIPPED
   [V] Sound Earmuffs: EQUIPPED
  ================================
  Industrial Safety Compliance
  Automated Vision AI Engine
  Keep Safe On The Job!
  ```
- Once done, the LCD displays:
  ```text
  +----------------+
  | REPORT PRINTED |
  |  TAKE RECEIPT  |
  +----------------+
  ```
