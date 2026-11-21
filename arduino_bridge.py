"""
Arduino Hardware Controller Bridge for PPE Safety Detector
Controls:
  - Red LED: Violator (missing PPE / policy violation)
  - Green LED: Accept / Valid (100% compliant)
  - Buzzer: Distinct auditory feedback for Pass (victory chime) and Fail (alert alarm)
  - Mini Thermal Receipt Printer: Automatic or on-demand printing of PPE report cards
"""

import time
import queue
import threading
from typing import Optional, List, Tuple
import serial
import serial.tools.list_ports


class ArduinoBridge:
    def __init__(self, port: str = "auto", baud: int = 9600):
        self.requested_port = port
        self.baud = baud
        self.ser: Optional[serial.Serial] = None
        self.active_port: Optional[str] = None
        self._connected = False
        self.last_error = ""
        self._retry_paused = False
        self._next_connect_at = 0.0
        self._port_generation = 0
        self._lock = threading.Lock()
        self._cmd_queue: queue.Queue = queue.Queue(maxsize=100)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Debounce and alert state tracking
        self._last_alert_time = 0.0
        self._last_alert_status = None
        self._last_alert_detail = None
        self._alert_cooldown = 4.0  # seconds between buzzer triggers for unchanged state

        # Start communication worker thread
        self._start_worker()

    @staticmethod
    def list_ports() -> List[Tuple[str, str]]:
        """List available serial COM ports as [(device, description), ...]."""
        ports = list(serial.tools.list_ports.comports())
        return [(p.device, p.description) for p in ports]

    def _find_arduino_port(self) -> Optional[str]:
        """Auto-detect likely Arduino Uno or USB serial ports."""
        ports = list(serial.tools.list_ports.comports())
        if not ports:
            return None

        # Priority 1: Match 'Arduino' in description or manufacturer
        for p in ports:
            desc = (p.description or "").lower()
            mfg = (p.manufacturer or "").lower()
            if "arduino" in desc or "arduino" in mfg:
                return p.device

        # Priority 2: Match common USB-Serial chips (CH340, CP210x, FTDI)
        for p in ports:
            desc = (p.description or "").lower()
            if any(k in desc for k in ("ch340", "cp210", "ftdi", "usb serial", "usb-serial")):
                return p.device

        return None

    def _start_worker(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker_loop, daemon=True, name="arduino_worker")
        self._thread.start()

    def _connect(self) -> bool:
        if self._retry_paused or time.monotonic() < self._next_connect_at:
            return False
        self._disconnect()
        generation = self._port_generation
        port_to_try = self.requested_port
        if not port_to_try or port_to_try.lower() == "auto":
            port_to_try = self._find_arduino_port()

        if not port_to_try:
            return False

        ser = None
        try:
            ser = serial.Serial(port=port_to_try, baudrate=self.baud, timeout=1.0, write_timeout=1.0)
            # Allow Arduino Uno reset cycle to complete (typically ~1.5s - 2s on DTR assertion)
            if self._stop_event.wait(3.5):
                ser.close()
                return False
            # Flush startup banner
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            ser.write(b"PING\n")
            deadline = time.monotonic() + 8
            next_ping = time.monotonic() + 2
            verified = False
            while True:
                if self._stop_event.is_set() or generation != self._port_generation:
                    ser.close()
                    return False
                if time.monotonic() >= deadline:
                    if verified:
                        raise ValueError("Old sketch: upload updated .ino")
                    self.last_error = f"Waiting for station reply on {port_to_try}"
                    deadline = time.monotonic() + 8
                if time.monotonic() >= next_ping and not verified:
                    ser.write(b"PING\n")
                    next_ping = time.monotonic() + 2
                reply = ser.readline().decode("ascii", errors="replace").strip()
                if reply == "PONG:PPE_STATION_OK":
                    verified = True
                if reply == "DONE" and verified:
                    break
            with self._lock:
                self.ser = ser
                self.active_port = port_to_try
                self._connected = True
                self.last_error = ""

            print(f"[INFO] Arduino connected successfully on {port_to_try} at {self.baud} baud.")
            # Send initial ping to check handshake
            return True
        except Exception as exc:
            if generation != self._port_generation:
                if ser is not None:
                    ser.close()
                return False
            self.last_error = f"{port_to_try}: {exc}"
            self._retry_paused = isinstance(exc, ValueError)
            self._next_connect_at = time.monotonic() + 5
            print(f"[WARN] {self.last_error}")
            if ser is not None:
                ser.close()
            with self._lock:
                self.ser = None
                self._connected = False
            return False

    def _disconnect(self):
        while True:
            try:
                self._cmd_queue.get_nowait()
                self._cmd_queue.task_done()
            except queue.Empty:
                break
        with self._lock:
            self._connected = False
            self._last_alert_status = None
            self._last_alert_detail = None
            if self.ser:
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None

    def _write_raw(self, line: str):
        with self._lock:
            if self.ser and self._connected:
                try:
                    self.ser.write(line.encode("utf-8", errors="ignore"))
                    self.ser.flush()
                except Exception as exc:
                    print(f"[WARN] Arduino write error: {exc}")
                    self._connected = False

    def _worker_loop(self):
        while not self._stop_event.is_set():
            if not self.is_connected():
                # Attempt background connection / auto-reconnect
                if not self._connect():
                    self._stop_event.wait(0.5)
                    continue

            # Process commands from queue
            try:
                cmd = self._cmd_queue.get(timeout=0.2)
                self._write_raw(cmd)
                deadline = time.monotonic() + (90 if cmd.startswith("PRINT:") else 30)
                while not self._stop_event.is_set() and time.monotonic() < deadline:
                    with self._lock:
                        active = self.ser
                    if active is None:
                        break
                    try:
                        reply = active.readline().decode("ascii", errors="replace").strip()
                    except Exception as exc:
                        self.last_error = str(exc)
                        self._next_connect_at = time.monotonic() + 5
                        self._disconnect()
                        break
                    if reply == "DONE":
                        break
                    if reply:
                        print(f"[ARDUINO] {reply}")
                else:
                    self.last_error = "Station did not finish command; reconnect manually"
                    self._retry_paused = True
                    self._disconnect()
                self._cmd_queue.task_done()
            except queue.Empty:
                pass

            # Read any responses from Arduino (non-blocking)
            try:
                with self._lock:
                    ser = self.ser
                if ser and ser.in_waiting > 0:
                    resp = ser.readline().decode("utf-8", errors="ignore").strip()
                    if resp:
                        print(f"[ARDUINO] {resp}")
            except Exception:
                self._disconnect()

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected and self.ser is not None and self.ser.is_open

    def get_status_label(self) -> str:
        if self.is_connected():
            return f"[ON] Arduino: {self.active_port}"
        if self._retry_paused:
            return "Arduino: Check setup"
        return "Arduino: Connecting..."

    def set_port(self, port: str):
        """Switch to a specific COM port or 'auto'."""
        self._port_generation += 1
        self.requested_port = port
        self._disconnect()
        self.last_error = ""
        self._retry_paused = False
        self._next_connect_at = 0.0

    def send_alert(self, status: str, detail: str = "", force: bool = False):
        """
        Send pass/fail status alert to Arduino.
        status: 'PASS', 'FAIL', 'IDLE'
        """
        if not self.is_connected():
            return
        now = time.monotonic()
        detail_clean = self._clean_field(detail)[:80]
        # Debounce: avoid firing buzzer repeatedly on identical state and detail within cooldown
        if not force:
            if (status == self._last_alert_status and
                    detail_clean == self._last_alert_detail and
                    (now - self._last_alert_time) < self._alert_cooldown):
                return

        self._last_alert_time = now
        self._last_alert_status = status
        self._last_alert_detail = detail_clean

        cmd = f"ALERT:{status}:{detail_clean}\n"
        try:
            self._cmd_queue.put_nowait(cmd)
        except queue.Full:
            pass

    def send_print_report(self, track_id: int, status: str, score_pct: int,
                          logged_at: str, rows: List[Tuple[str, str]]):
        """
        Send a full formatted audit report card to the Mini Thermal Printer via Arduino.
        rows is a list of (gear_label, state_text), e.g. [('Work Gloves', '1 HAND HAS NO GLOVE'), ...]
        """
        # Format payload:
        # PRINT:ID=1|STATUS=VIOLATION|SCORE=83%|TIME=2026-10-27 15:50|ITEM:Hard Hat Helmet=EQUIPPED|ITEM:Work Gloves=1 HAND HAS NO GLOVE
        time_str = self._clean_field(logged_at or time.strftime("%Y-%m-%d %H:%M:%S"))[:24]
        status_label = "NOT ASSESSED" if status == "NOT_ASSESSED" else "PASSED" if status == "PASS" else "VIOLATION"
        score_label = "N/A" if status == "NOT_ASSESSED" else f"{score_pct}%"
        parts = [
            f"PRINT:ID={track_id:02d}",
            f"STATUS={status_label}",
            f"SCORE={score_label}",
            f"TIME={time_str}"
        ]
        for label, state in rows[:6]:
            clean_label = self._clean_field(label)[:24]
            clean_state = self._clean_field({"worn": "EQUIPPED", "missing": "MISSING", "pending": "CHECKING"}.get(state, state))[:32]
            parts.append(f"ITEM:{clean_label}={clean_state}")

        cmd = "|".join(parts) + "\n"
        try:
            self._cmd_queue.put_nowait(cmd)

        except queue.Full:
            pass

    @staticmethod
    def _clean_field(value):
        return "".join(c if 32 <= ord(c) < 127 and c not in "|=" else " " for c in str(value)).strip()

    def set_printer_baud(self, baud):
        if baud != 9600:
            raise ValueError("This printer uses 9600 baud")
        self._cmd_queue.put_nowait(f"PRINTER_BAUD:{baud}\n")

    def test_print(self):
        """Send hardware test print to thermal receipt printer."""
        try:
            self._cmd_queue.put_nowait("TEST_PRINT\n")
        except queue.Full:
            pass

    def stop(self):
        self._stop_event.set()
        self._disconnect()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
