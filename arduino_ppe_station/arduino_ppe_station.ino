/*
 ===============================================================================
   PPE CHECKER — ARDUINO SAFETY STATION CONTROLLER
   Board: Arduino Uno (ATmega328P)
 ===============================================================================
   HARDWARE WIRING DIAGRAM:
   -----------------------------------------------------------------------------
   [COMPONENT]          [ARDUINO UNO PIN]     [NOTES]
   Red LED (Anode +)   -> Pin 7                Use 220Ω - 330Ω resistor to Pin 7
   Red LED (Cathode -) -> GND
   
   Green LED (Anode +) -> Pin 8                Use 220Ω - 330Ω resistor to Pin 8
   Green LED (Cathode -)-> GND
   
   Buzzer (+)          -> Pin 9                Passive or Active Buzzer
   Buzzer (-)          -> GND
   
   I2C LCD 16x2:
     - GND             -> Arduino GND
     - VCC             -> Arduino 5V
     - SDA             -> Arduino A4 (or dedicated SDA pin near AREF)
     - SCL             -> Arduino A5 (or dedicated SCL pin near AREF)
     (Address typically 0x27 or 0x3F)

   Mini Thermal Receipt Printer:
     - Printer RX      <- A2 (Arduino TX)   **CRITICAL: Arduino A2 goes to Printer RX!**
     - Printer TX      -> A3 (Arduino RX)   SoftwareSerial RX
     - Printer GND     -> Arduino GND (Common Ground with external power)
     - Printer VH/VCC  -> External supply matching the printer voltage/current rating
                          (NOTE: Thermal printers need 1.5A - 2A peak current,
                           do NOT power the printer from Arduino's 5V pin!)
 ===============================================================================
*/

#include <Wire.h>
#include <Adafruit_Thermal.h>
#include <LiquidCrystal_I2C.h>
#include <SoftwareSerial.h>

// --- PIN DEFINITIONS ---
const int PIN_RED_LED   = 7;   // Violator / Non-compliant alert
const int PIN_GREEN_LED = 8;   // Valid / Accepted / 100% equipped
const int PIN_BUZZER    = 9;   // Sound alert buzzer
const int PIN_PRINTER_RX = A3;  // SoftwareSerial RX (from Printer TX)
const int PIN_PRINTER_TX = A2;  // SoftwareSerial TX (to Printer RX)

// --- I2C LCD (16 columns, 2 rows) ---
LiquidCrystal_I2C lcd(0x27, 16, 2);

// Mini Thermal Printer Serial (Fixed 9600 baud)
const long PRINTER_BAUD = 9600; // Match the printer self-test page; independent of USB.
const unsigned int MAX_COMMAND = 600;
bool inputOverflow = false;
SoftwareSerial printerSerial(PIN_PRINTER_RX, PIN_PRINTER_TX);
Adafruit_Thermal printer(&printerSerial);
const byte PAPER_WIDTH = 32;

// Buffer for incoming serial commands from Python PC
String inputBuffer = "";

// Timer for non-blocking LED/Alert timeout
unsigned long alertStartTime = 0;
const unsigned long ALERT_DURATION_MS = 3500;
bool isAlertActive = false;

// Helpers to display text on 16x2 LCD using Flash strings (saves dynamic SRAM)
void updateLcd(const __FlashStringHelper* line1, const __FlashStringHelper* line2) {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(line1);
  lcd.setCursor(0, 1);
  lcd.print(line2);
}

void updateLcd(const __FlashStringHelper* line1, const String& line2) {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(line1);
  lcd.setCursor(0, 1);
  if (line2.length() > 16) {
    lcd.print(line2.substring(0, 16));
  } else {
    lcd.print(line2);
  }
}

// ---------------------------------------------------------------------------
// SETUP
// ---------------------------------------------------------------------------
void setup() {
  pinMode(PIN_RED_LED, OUTPUT);
  pinMode(PIN_GREEN_LED, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);

  // Initial state: LEDs OFF
  digitalWrite(PIN_RED_LED, LOW);
  digitalWrite(PIN_GREEN_LED, LOW);

  // Reserve input buffer to prevent heap memory fragmentation
  inputBuffer.reserve(MAX_COMMAND);

  // Initialize I2C LCD
  lcd.init();
  lcd.backlight();
  updateLcd(F("  PPE CHECKER  "), F(" INITIALIZING..."));

  // USB Serial communication with Python application
  Serial.begin(9600);
  while (!Serial) { ; } // Wait for serial port to connect

  // Thermal printer serial communication (Fixed 9600 baud)
  printerSerial.begin(PRINTER_BAUD);
  delay(1000); // Same power-up settling time as the verified standalone test.
  printer.begin();
  printer.setDefault();
  printer.justify('L');
  printer.sleep();

  // Startup melody & LED self-test
  playStartupChime();

  updateLcd(F("  PPE CHECKER  "), F(" STATION READY  "));
  Serial.println(F("[ARDUINO_READY] PPE CHECKER PPE-CHECKER-2 TX=A2 RX=A3 BAUD=9600"));
}

// ---------------------------------------------------------------------------
// MAIN LOOP
// ---------------------------------------------------------------------------
void loop() {
  // Read commands from Python over USB Serial
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (inputBuffer.length() > 0) {
        if (!inputOverflow) processCommand(inputBuffer);
        else Serial.println(F("ERROR:COMMAND_TOO_LONG"));
        Serial.println(F("DONE"));
        inputOverflow = false;
        inputBuffer = "";
      }
    } else {
      if (inputBuffer.length() < MAX_COMMAND) inputBuffer += c;
      else inputOverflow = true;
    }
  }

  // Timeout back to idle if alert finished
  if (isAlertActive && (millis() - alertStartTime > ALERT_DURATION_MS)) {
    isAlertActive = false;
  }
}

// ---------------------------------------------------------------------------
// COMMAND PROCESSOR
// ---------------------------------------------------------------------------
void processCommand(const String& cmd) {


  if (cmd == "PING") {
    Serial.println(F("PONG:PPE_STATION_OK"));
    Serial.println(F("[FIRMWARE] PPE-CHECKER-2 TX=A2 RX=A3 BAUD=9600"));
  }
  else if (cmd.startsWith("PRINTER_BAUD:")) {
    if (cmd == "PRINTER_BAUD:9600") Serial.println(F("ACK:PRINTER_BAUD:9600"));
    else Serial.println(F("ERROR:PRINTER_FIXED_AT_9600"));
  }
  else if (cmd.startsWith("ALERT:PASS")) {
    handlePassAlert();
  }
  else if (cmd.startsWith("ALERT:FAIL")) {
    String detail = "";
    int secondColon = cmd.indexOf(':', 6);
    if (secondColon != -1) {
      detail = cmd.substring(secondColon + 1);
    }
    handleFailAlert(detail);
  }
  else if (cmd.startsWith("ALERT:IDLE")) {
    handleIdleAlert();
  }
  else if (cmd.startsWith("PRINT:TEST") || cmd == "TEST_PRINT") {
    testPrintThermalPrinter();
  }
  else if (cmd.startsWith("PRINT:")) {
    handlePrintCommand(cmd);
  }
}

// ---------------------------------------------------------------------------
// ALERT HANDLERS
// ---------------------------------------------------------------------------
void handlePassAlert() {
  digitalWrite(PIN_GREEN_LED, HIGH);
  digitalWrite(PIN_RED_LED, LOW);
  isAlertActive = true;
  alertStartTime = millis();

  updateLcd(F("STATUS: COMPLIANT"), F("ACCESS GRANTED  "));

  tone(PIN_BUZZER, 523, 90);
  delay(110);
  tone(PIN_BUZZER, 659, 90);
  delay(110);
  tone(PIN_BUZZER, 784, 220);

  Serial.println(F("[ACK] ALERT:PASS EXECUTED"));
}

void handleFailAlert(String detail) {
  digitalWrite(PIN_RED_LED, HIGH);
  digitalWrite(PIN_GREEN_LED, LOW);
  isAlertActive = true;
  alertStartTime = millis();

  if (detail.length() == 0) {
    updateLcd(F("PPE VIOLATION!  "), F("CHECK PPE GEAR! "));
  } else {
    updateLcd(F("PPE VIOLATION!  "), detail);
  }

  tone(PIN_BUZZER, 440, 140);
  delay(170);
  tone(PIN_BUZZER, 310, 260);

  Serial.print(F("[ACK] ALERT:FAIL EXECUTED: "));
  Serial.println(detail);
}

void handleIdleAlert() {
  digitalWrite(PIN_RED_LED, LOW);
  digitalWrite(PIN_GREEN_LED, LOW);
  isAlertActive = false;

  updateLcd(F("  PPE CHECKER  "), F(" SCANNING AREA  "));
  Serial.println(F("[ACK] ALERT:IDLE"));
}

// ---------------------------------------------------------------------------
// THERMAL RECEIPT PRINTER — pacing verified by the standalone printer test.
// ---------------------------------------------------------------------------
void initPrinter() {
  noTone(PIN_BUZZER);
  printer.wake();
  delay(50);
  printer.setDefault();
  printer.justify('L');
  printer.println();
  delay(60);
}

// Keep each physical line below 32 columns, including centered padding.
// Long checklist descriptions wrap explicitly instead of overflowing.
void printReceiptLine(const String& text, bool center = false) {
  unsigned int offset = 0;
  do {
    unsigned int remaining = text.length() - offset;
    byte count = remaining > PAPER_WIDTH - 1 ? PAPER_WIDTH - 1 : remaining;
    if (center && text.length() < PAPER_WIDTH) {
      byte pad = (PAPER_WIDTH - count) / 2;
      if (pad + count >= PAPER_WIDTH) pad = PAPER_WIDTH - 1 - count;
      for (byte i = 0; i < pad; ++i) printer.write((uint8_t)' ');
    }
    for (byte i = 0; i < count; ++i) {
      char c = text[offset + i];
      printer.write((uint8_t)((c >= 32 && c <= 126) ? c : ' '));
    }
    printer.println();
    delay(100);
    offset += count;
  } while (offset < text.length());
}

void finishReceipt() {
  for (byte i = 0; i < 3; ++i) {
    printer.println();
    delay(100);
  }
  printer.sleep();
}

void testPrintThermalPrinter() {
  updateLcd(F("TESTING PRINTER"), F("CHECK PAPER"));
  initPrinter();
  // Keep this test identical to the user-verified standalone output.
  printer.println(F("PRINTER TEST"));
  delay(100);
  printer.println(F("Hello! Printer is working."));
  delay(100);
  printer.println(F("ABCDEFGHIJKLMNOPQRSTUVWXYZ"));
  delay(100);
  printer.println(F("abcdefghijklmnopqrstuvwxyz"));
  delay(100);
  printer.println(F("0123456789"));
  delay(100);
  finishReceipt();
  updateLcd(F("TEST SENT"), F("CHECK PAPER"));
  Serial.println(F("[PRINTER] Test sent; verify the paper."));
}

void printMissingAction(const String& name) {
  if (name == "Hard Hat Helmet") printReceiptLine(F("Obtain and wear a hard hat."));
  if (name == "Safety Vest") printReceiptLine(F("Obtain and wear a safety vest."));
  if (name == "Protective Mask") printReceiptLine(F("Obtain the task-required mask."));
  if (name == "Safety Goggles") printReceiptLine(F("Obtain and wear safety goggles."));
  if (name == "Work Gloves") printReceiptLine(F("Wear required gloves on both hands."));
  if (name == "Sound Earmuffs") printReceiptLine(F("Obtain and wear hearing protection."));
}

void handlePrintCommand(const String& payload) {
  updateLcd(F("PRINTING REPORT"), F("PLEASE WAIT"));
  initPrinter();
  printReceiptLine(F("=============================="));
  printReceiptLine(F("PPE CHECKER"));
  printReceiptLine(F("SAFETY INSPECTION"));
  printReceiptLine(F("=============================="));
  // Parse one token at a time to keep Uno RAM usage bounded.
  byte checked = 0;
  byte missing = 0;
  bool incomplete = false;
  int start = 6;
  while (start < (int)payload.length()) {
    int end = payload.indexOf('|', start);
    if (end < 0) end = payload.length();
    String token = payload.substring(start, end);
    start = end + 1;
    if (token.startsWith("ID=")) {
      printReceiptLine(String(F("Worker: #")) + token.substring(3));
    } else if (token.startsWith("STATUS=")) {
      printReceiptLine(String(F("Result: ")) + token.substring(7));
    } else if (token.startsWith("SCORE=")) {
      printReceiptLine(String(F("Compliance: ")) + token.substring(6));
    } else if (token.startsWith("TIME=")) {
      printReceiptLine(token.substring(5));
      printReceiptLine(F("------------------------------"));
    } else if (token.startsWith("ITEM:")) {
      int eq = token.indexOf('=', 5);
      if (eq > 5) {
        ++checked;
        String state = token.substring(eq + 1);
        if (state == "MISSING" || state.indexOf("NO GLOVE") >= 0) ++missing;
        else if (state != "EQUIPPED") incomplete = true;
        printReceiptLine(token.substring(5, eq));
        printReceiptLine(String(F("  ")) + token.substring(eq + 1));
      }
    }
  }
  printReceiptLine(F("=============================="));
  printReceiptLine(F("INSPECTION COMMENTS"));
  if (checked == 0 || incomplete) {
    printReceiptLine(F("Inspection incomplete. Recheck PPE."));
    printReceiptLine(F("Contact: Safety Officer / Site Supervisor."));
  } else if (missing == 0) {
    printReceiptLine(F("All checked PPE detected."));
    printReceiptLine(F("Follow site rules and supervisor instructions."));
    printReceiptLine(F("This check is not a work permit."));
  } else {
    printReceiptLine(F("HOLD: Do not start work."));
    if (missing == checked) printReceiptLine(F("No required PPE detected."));
    else printReceiptLine(F("Required PPE is incomplete."));
    start = 6;
    while (start < (int)payload.length()) {
      int end = payload.indexOf('|', start);
      if (end < 0) end = payload.length();
      String token = payload.substring(start, end);
      start = end + 1;
      if (token.startsWith("ITEM:")) {
        int eq = token.indexOf('=', 5);
        if (eq > 5) {
          String state = token.substring(eq + 1);
          if (state == "MISSING" || state.indexOf("NO GLOVE") >= 0)
            printMissingAction(token.substring(5, eq));
        }
      }
    }
    printReceiptLine(F("Contact: Safety Officer / Site Supervisor."));
    printReceiptLine(F("Equipment: PPE Custodian / Storekeeper."));
    printReceiptLine(F("Correct missing PPE and repeat inspection."));
  }
  finishReceipt();
  updateLcd(F("REPORT SENT"), F("TAKE RECEIPT"));
  Serial.println(F("[PRINTER] Report sent; verify the paper."));
}

// ---------------------------------------------------------------------------
// STARTUP MELODY & LED TEST
// ---------------------------------------------------------------------------
void playStartupChime() {
  digitalWrite(PIN_RED_LED, HIGH);
  digitalWrite(PIN_GREEN_LED, HIGH);
  tone(PIN_BUZZER, 440, 80);
  delay(100);
  digitalWrite(PIN_RED_LED, LOW);
  tone(PIN_BUZZER, 554, 80);
  delay(100);
  digitalWrite(PIN_GREEN_LED, LOW);
  tone(PIN_BUZZER, 659, 140);
  delay(160);
}
