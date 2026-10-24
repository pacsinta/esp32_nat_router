"""Compact ESP32 Wi-Fi configurator with a live serial monitor.

Requirements: Python 3.9+, customtkinter, pyserial
Run: python esp32_wifi_configurator.py
"""

from __future__ import annotations

import queue
import threading
import time
from datetime import datetime
import tkinter.messagebox as messagebox

import customtkinter as ctk
import serial
from serial.tools import list_ports


APP_NAME = "ESP32 Studio"
BAUD_RATE = 115200
SERIAL_TIMEOUT = 0.1
MAX_MONITOR_LINES = 5000
PRUNE_MONITOR_LINES = 1000

# Dark UI palette
BG = "#0B1020"
PANEL = "#121D32"
PANEL_ALT = "#17243B"
INPUT_BG = "#0D1729"
BORDER = "#273752"
TEXT = "#F2F6FC"
MUTED = "#8FA2BE"
ACCENT = "#4F8CFF"
ACCENT_HOVER = "#3D78E8"
SUCCESS = "#35D49A"
WARNING = "#F6BD60"
ERROR = "#FF6B78"


class ESP32WiFiConfigurator(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(f"{APP_NAME} · Wi-Fi & Serial Monitor")
        self.geometry("900x720")
        self.minsize(760, 600)
        self.configure(fg_color=BG)

        self.port_var = ctk.StringVar(value="")
        self.ssid_var = ctk.StringVar(value="")
        self.password_var = ctk.StringVar(value="")
        self.show_password_var = ctk.BooleanVar(value=False)
        self.auto_scroll_var = ctk.BooleanVar(value=True)

        self.port_lookup: dict[str, str] = {}
        self.connection: serial.Serial | None = None
        self.reader_thread: threading.Thread | None = None
        self.reader_stop = threading.Event()
        self.reader_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.monitor_line_count = 0
        self.rx_line_count = 0
        self.tx_count = 0
        self._closing = False
        self._queue_after_id: str | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._build_ui()
        self.ssid_var.trace_add("write", self._update_command_preview)
        self.password_entry.bind("<Return>", lambda _event: self.send_settings())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.refresh_ports()
        self._poll_serial_queue()

    def _build_ui(self) -> None:
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 10))
        header.grid_columnconfigure(0, weight=1)

        title_row = ctk.CTkFrame(header, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            title_row, text="⌁", width=34, height=34, corner_radius=10,
            fg_color=ACCENT, text_color="white",
            font=ctk.CTkFont(size=23, weight="bold"),
        ).pack(side="left", padx=(0, 10))
        title_text = ctk.CTkFrame(title_row, fg_color="transparent")
        title_text.pack(side="left")
        ctk.CTkLabel(
            title_text, text="ESP32 Studio",
            text_color=TEXT, font=ctk.CTkFont(size=21, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_text, text="Wi-Fi provisioning  ·  Serial monitor",
            text_color=MUTED, font=ctk.CTkFont(size=11),
        ).pack(anchor="w", pady=(1, 0))

        self.top_status = ctk.CTkLabel(
            header, text="●  NO DEVICE", width=126, height=30, corner_radius=8,
            fg_color=PANEL_ALT, text_color=WARNING,
            font=ctk.CTkFont(size=10, weight="bold"),
        )
        self.top_status.grid(row=0, column=1, sticky="e")

        # Serial connection toolbar
        connection_card = self._card(self, row=1, padx=20, pady=(0, 10))
        connection_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            connection_card, text="SERIAL CONNECTION",
            text_color=MUTED, font=ctk.CTkFont(size=10, weight="bold"),
        ).grid(row=0, column=0, columnspan=5, sticky="w", padx=14, pady=(10, 6))

        self.port_combo = ctk.CTkComboBox(
            connection_card, variable=self.port_var, values=[], state="readonly",
            height=36, corner_radius=8, border_color=BORDER, fg_color=INPUT_BG,
            button_color=PANEL_ALT, button_hover_color="#263955",
            dropdown_fg_color=PANEL, dropdown_text_color=TEXT,
            dropdown_hover_color="#263955", text_color=TEXT,
            font=ctk.CTkFont(size=12),
        )
        self.port_combo.grid(row=1, column=0, sticky="ew", padx=(14, 8), pady=(0, 12))

        self.refresh_button = ctk.CTkButton(
            connection_card, text="Refresh", width=76, height=36, corner_radius=8,
            fg_color=PANEL_ALT, hover_color="#263955", text_color=TEXT,
            border_width=1, border_color=BORDER,
            font=ctk.CTkFont(size=11, weight="bold"), command=self.refresh_ports,
        )
        self.refresh_button.grid(row=1, column=1, padx=(0, 8), pady=(0, 12))

        self.connect_button = ctk.CTkButton(
            connection_card, text="Connect", width=94, height=36, corner_radius=8,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white",
            font=ctk.CTkFont(size=11, weight="bold"), command=self.toggle_connection,
        )
        self.connect_button.grid(row=1, column=2, padx=(0, 12), pady=(0, 12))

        ctk.CTkLabel(
            connection_card, text="115200 baud · 8-N-1",
            text_color="#A7C5FF", fg_color="#1A2B47", corner_radius=7,
            height=28, font=ctk.CTkFont(size=10, weight="bold"),
        ).grid(row=1, column=3, padx=(0, 14), pady=(0, 12))

        # Compact credentials form
        credentials = self._card(self, row=2, padx=20, pady=(0, 10))
        credentials.grid_columnconfigure(0, weight=1)
        credentials.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            credentials, text="WI-FI CREDENTIALS",
            text_color=MUTED, font=ctk.CTkFont(size=10, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(10, 7))

        ctk.CTkLabel(
            credentials, text="Network name (SSID)", text_color=TEXT,
            font=ctk.CTkFont(size=11, weight="bold"),
        ).grid(row=1, column=0, sticky="w", padx=(14, 8), pady=(0, 5))
        ctk.CTkLabel(
            credentials, text="Password", text_color=TEXT,
            font=ctk.CTkFont(size=11, weight="bold"),
        ).grid(row=1, column=1, sticky="w", padx=(8, 14), pady=(0, 5))

        self.ssid_entry = ctk.CTkEntry(
            credentials, textvariable=self.ssid_var, height=36, corner_radius=8,
            border_color=BORDER, fg_color=INPUT_BG, text_color=TEXT,
            placeholder_text="e.g. HomeNetwork", placeholder_text_color="#536781",
            font=ctk.CTkFont(size=12),
        )
        self.ssid_entry.grid(row=2, column=0, sticky="ew", padx=(14, 8), pady=(0, 12))

        password_row = ctk.CTkFrame(credentials, fg_color="transparent")
        password_row.grid(row=2, column=1, sticky="ew", padx=(8, 14), pady=(0, 12))
        password_row.grid_columnconfigure(0, weight=1)
        self.password_entry = ctk.CTkEntry(
            password_row, textvariable=self.password_var, height=36, corner_radius=8,
            border_color=BORDER, fg_color=INPUT_BG, text_color=TEXT, show="●",
            placeholder_text="Wi-Fi password", placeholder_text_color="#536781",
            font=ctk.CTkFont(size=12),
        )
        self.password_entry.grid(row=0, column=0, sticky="ew", padx=(0, 7))
        self.show_password_button = ctk.CTkButton(
            password_row, text="Show", width=55, height=36, corner_radius=8,
            fg_color=PANEL_ALT, hover_color="#263955", text_color=TEXT,
            border_width=1, border_color=BORDER, font=ctk.CTkFont(size=10),
            command=self.toggle_password,
        )
        self.show_password_button.grid(row=0, column=1, sticky="e")

        # Command preview and send action
        action_row = ctk.CTkFrame(credentials, fg_color="transparent")
        action_row.grid(row=3, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 12))
        action_row.grid_columnconfigure(0, weight=1)

        preview = ctk.CTkFrame(action_row, fg_color=INPUT_BG, corner_radius=8,
                               border_width=1, border_color=BORDER)
        preview.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        ctk.CTkLabel(
            preview, text="COMMAND", text_color=MUTED,
            font=ctk.CTkFont(size=9, weight="bold"),
        ).pack(anchor="w", padx=10, pady=(5, 0))
        self.command_preview = ctk.CTkLabel(
            preview, text='set_sta "SSID" "••••••••"', text_color="#9DC0FF",
            font=ctk.CTkFont(family="Consolas", size=11), anchor="w",
        )
        self.command_preview.pack(fill="x", padx=10, pady=(0, 6))

        self.send_button = ctk.CTkButton(
            action_row, text="Send Wi-Fi settings  →", width=180, height=45,
            corner_radius=9, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color="white", font=ctk.CTkFont(size=12, weight="bold"),
            command=self.send_settings,
        )
        self.send_button.grid(row=0, column=1, sticky="e", padx=(0, 8))

        self.restart_button = ctk.CTkButton(
            action_row, text="Restart", width=100, height=45,
            corner_radius=9, fg_color="#8C3543", hover_color="#702A36",
            text_color="white", font=ctk.CTkFont(size=12, weight="bold"),
            command=self.send_restart,
        )
        self.restart_button.grid(row=0, column=2, sticky="e")

        # Serial monitor expands into remaining space
        monitor_card = self._card(self, row=3, padx=20, pady=(0, 12))
        monitor_card.grid_columnconfigure(0, weight=1)
        monitor_card.grid_rowconfigure(1, weight=1)

        monitor_header = ctk.CTkFrame(monitor_card, fg_color="transparent")
        monitor_header.grid(row=0, column=0, sticky="ew", padx=12, pady=(9, 8))
        monitor_header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            monitor_header, text="SERIAL MONITOR", text_color=TEXT,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        self.monitor_state = ctk.CTkLabel(
            monitor_header, text="● Disconnected", text_color=WARNING,
            font=ctk.CTkFont(size=10),
        )
        self.monitor_state.grid(row=0, column=1, sticky="w", padx=(12, 0))

        ctk.CTkCheckBox(
            monitor_header, text="Auto-scroll", variable=self.auto_scroll_var,
            width=92, checkbox_width=14, checkbox_height=14, corner_radius=4,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=MUTED,
            font=ctk.CTkFont(size=10),
        ).grid(row=0, column=2, padx=(0, 12))
        self.clear_button = ctk.CTkButton(
            monitor_header, text="Clear", width=54, height=27, corner_radius=7,
            fg_color=PANEL_ALT, hover_color="#263955", text_color=TEXT,
            border_width=1, border_color=BORDER, font=ctk.CTkFont(size=10),
            command=self.clear_monitor,
        )
        self.clear_button.grid(row=0, column=3)

        self.monitor = ctk.CTkTextbox(
            monitor_card, wrap="word", corner_radius=8, border_width=1,
            border_color=BORDER, fg_color="#080E1A", text_color="#C9D7EA",
            font=ctk.CTkFont(family="Consolas", size=11),
            activate_scrollbars=True,
        )
        self.monitor.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))
        # CTkTextbox wraps a Tk Text widget; use Tk tags for color-coded terminal lines.
        self.monitor._textbox.tag_configure("RX", foreground="#C9D7EA")
        self.monitor._textbox.tag_configure("TX", foreground="#8DB4FF")
        self.monitor._textbox.tag_configure("SYS", foreground=SUCCESS)
        self.monitor._textbox.tag_configure("ERR", foreground=ERROR)
        self.monitor.configure(state="disabled")

        bottom = ctk.CTkFrame(monitor_card, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
        bottom.grid_columnconfigure(0, weight=1)
        self.monitor_hint = ctk.CTkLabel(
            bottom, text="Connect to the ESP32 to view incoming serial messages.",
            text_color=MUTED, font=ctk.CTkFont(size=10), anchor="w",
        )
        self.monitor_hint.grid(row=0, column=0, sticky="w")
        self.rx_counter = ctk.CTkLabel(
            bottom, text="RX 0  ·  TX 0", text_color=MUTED,
            font=ctk.CTkFont(size=10),
        )
        self.rx_counter.grid(row=0, column=1, sticky="e")

        # Status strip
        self.status_label = ctk.CTkLabel(
            self, text="Connect your ESP32, then send its Wi-Fi settings.",
            fg_color=PANEL_ALT, corner_radius=7, text_color=MUTED,
            anchor="w", height=28, font=ctk.CTkFont(size=10),
        )
        self.status_label.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 12))

    @staticmethod
    def _card(parent, row: int, padx: int, pady) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent, fg_color=PANEL, corner_radius=12,
            border_width=1, border_color=BORDER,
        )
        card.grid(row=row, column=0, sticky="nsew", padx=padx, pady=pady)
        return card

    def _set_status(self, message: str, state: str = "neutral") -> None:
        colors = {
            "neutral": MUTED, "success": SUCCESS, "warning": WARNING, "error": ERROR
        }
        self.status_label.configure(text=message, text_color=colors.get(state, MUTED))

    def _update_command_preview(self, *_args) -> None:
        ssid = self.ssid_var.get().strip() or "SSID"
        self.command_preview.configure(text=f'set_sta "{ssid}" "••••••••"')

    def toggle_password(self) -> None:
        visible = not self.show_password_var.get()
        self.show_password_var.set(visible)
        self.password_entry.configure(show="" if visible else "●")
        self.show_password_button.configure(text="Hide" if visible else "Show")

    def refresh_ports(self) -> None:
        if self.connection and self.connection.is_open:
            self._set_status("Disconnect before changing the serial port.", "warning")
            return

        previous = self.port_var.get()
        try:
            ports = sorted(list(list_ports.comports()), key=lambda port: port.device)
        except Exception as exc:
            self.port_lookup = {}
            self.port_combo.configure(values=[], state="disabled")
            self.port_var.set("")
            self.top_status.configure(text="●  SCAN FAILED", text_color=ERROR)
            self._set_status(f"Could not enumerate serial ports: {exc}", "error")
            self._append_monitor(f"Port scan failed: {exc}", "ERR")
            return

        self.port_lookup = {}
        for port in ports:
            description = (port.description or "Serial device").strip()
            self.port_lookup[f"{port.device} · {description}"] = port.device

        values = list(self.port_lookup)
        self.port_combo.configure(values=values)
        retained = next(
            (label for label, device in self.port_lookup.items()
             if device == previous or label == previous),
            "",
        )
        if values:
            self.port_var.set(retained or values[0])
            self.port_combo.configure(state="readonly")
            self.connect_button.configure(state="normal")
            self.top_status.configure(text="●  PORT AVAILABLE", text_color=SUCCESS)
            self._set_status(f"{len(values)} serial port(s) available. Select the ESP32 port.", "success")
            self.monitor_hint.configure(text="Select a port and click Connect to start receiving messages.")
        else:
            self.port_var.set("")
            self.port_combo.configure(state="disabled")
            self.connect_button.configure(state="disabled")
            self.top_status.configure(text="●  NO DEVICE", text_color=WARNING)
            self._set_status("No serial ports found. Connect the ESP32 and click Refresh.", "warning")
            self.monitor_hint.configure(text="No serial port detected. Connect the ESP32 and refresh.")
        self._append_monitor(f"Port scan complete: {len(values)} serial port(s) found.", "SYS")

    def toggle_connection(self) -> None:
        if self.connection and self.connection.is_open:
            self.disconnect_serial()
        else:
            self.connect_serial()

    def connect_serial(self) -> bool:
        selected = self.port_var.get()
        if not selected or selected not in self.port_lookup:
            self._set_status("Select a valid COM port first.", "error")
            return False

        device = self.port_lookup[selected]
        try:
            conn = serial.Serial(
                port=device,
                baudrate=BAUD_RATE,
                timeout=SERIAL_TIMEOUT,
                write_timeout=2,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
        except (serial.SerialException, OSError) as exc:
            self.top_status.configure(text="●  CONNECT FAILED", text_color=ERROR)
            self.monitor_state.configure(text="● Connection failed", text_color=ERROR)
            self._set_status(f"Could not open {device}: {exc}", "error")
            self._append_monitor(f"Could not open {device}: {exc}", "ERR")
            messagebox.showerror(
                "Serial connection failed",
                f"Could not open {device} at {BAUD_RATE} baud.\n\n{exc}",
                parent=self,
            )
            return False

        self.connection = conn
        self.reader_stop.clear()
        self.reader_thread = threading.Thread(
            target=self._serial_reader, args=(conn,), daemon=True,
            name="ESP32SerialReader",
        )
        self.reader_thread.start()

        self.port_combo.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.connect_button.configure(
            text="Disconnect", fg_color="#8C3543", hover_color="#702A36", state="normal"
        )
        self.top_status.configure(text="●  CONNECTED", text_color=SUCCESS)
        self.monitor_state.configure(text=f"● {device} connected", text_color=SUCCESS)
        self.monitor_hint.configure(text=f"Listening on {device} at {BAUD_RATE} baud.")
        self._set_status(f"Serial connection open on {device} at {BAUD_RATE} baud.", "success")
        self._append_monitor(f"Connected to {device} at {BAUD_RATE} baud (8-N-1).", "SYS")
        return True

    def disconnect_serial(self) -> None:
        conn = self.connection
        self.reader_stop.set()
        self.connection = None
        if conn:
            try:
                if conn.is_open:
                    conn.close()
            except (serial.SerialException, OSError):
                pass

        self.port_combo.configure(state="readonly" if self.port_lookup else "disabled")
        self.refresh_button.configure(state="normal")
        self.connect_button.configure(
            text="Connect", fg_color=ACCENT, hover_color=ACCENT_HOVER,
            state="normal" if self.port_lookup else "disabled",
        )
        self.top_status.configure(text="●  DISCONNECTED", text_color=WARNING)
        self.monitor_state.configure(text="● Disconnected", text_color=WARNING)
        self.monitor_hint.configure(text="Connect to the ESP32 to view incoming serial messages.")
        self._set_status("Serial connection closed.", "neutral")
        self._append_monitor("Serial connection closed.", "SYS")

    def _serial_reader(self, conn: serial.Serial) -> None:
        """Read serial bytes off the UI thread and queue decoded messages."""
        buffer = bytearray()
        last_data_at = time.monotonic()
        try:
            while not self.reader_stop.is_set() and conn.is_open:
                chunk = conn.read(512)
                now = time.monotonic()
                if chunk:
                    buffer.extend(chunk)
                    last_data_at = now
                    while b"\n" in buffer:
                        raw, _, remainder = buffer.partition(b"\n")
                        buffer = bytearray(remainder)
                        line = raw.decode("utf-8", errors="replace").rstrip("\r")
                        self.reader_queue.put(("rx", line))
                elif buffer and now - last_data_at >= 0.35:
                    # Flush devices that print progress without a trailing newline.
                    line = bytes(buffer).decode("utf-8", errors="replace").rstrip("\r")
                    buffer.clear()
                    self.reader_queue.put(("rx", line))
        except (serial.SerialException, OSError) as exc:
            if not self.reader_stop.is_set():
                self.reader_queue.put(("reader_error", str(exc)))
        finally:
            if buffer and not self.reader_stop.is_set():
                self.reader_queue.put(
                    ("rx", bytes(buffer).decode("utf-8", errors="replace").rstrip("\r"))
                )

    def send_settings(self) -> None:
        ssid = self.ssid_var.get()
        password = self.password_var.get()
        if not ssid.strip():
            self._set_status("Enter a Wi-Fi network name (SSID).", "error")
            self.ssid_entry.focus_set()
            return
        if any(ch in ssid or ch in password for ch in ("\r", "\n")):
            self._set_status("SSID and password cannot contain line breaks.", "error")
            return

        # Connect on demand so a send also starts the live monitor.
        if not self.connection or not self.connection.is_open:
            if not self.connect_serial():
                return

        assert self.connection is not None
        command = f'set_sta "{self.quote_argument(ssid)}" "{self.quote_argument(password)}"\r\n'
        try:
            self.connection.write(command.encode("utf-8"))
            self.connection.flush()
            self.tx_count += 1
            self._update_counters()
            # Never echo the Wi-Fi password to the monitor.
            self._append_monitor(f'set_sta "{self._safe_display(ssid)}" "<password hidden>"', "TX")
            self._set_status(
                "Wi-Fi settings command sent. Watch the serial monitor for the ESP32 response.",
                "success",
            )
            self.top_status.configure(text="●  COMMAND SENT", text_color=SUCCESS)
        except (serial.SerialException, OSError, UnicodeEncodeError) as exc:
            self._append_monitor(f"Send failed: {exc}", "ERR")
            self._set_status(f"Could not send command: {exc}", "error")
            self.disconnect_serial()

    def send_restart(self) -> None:
        # Connect on demand so a restart also starts the live monitor.
        if not self.connection or not self.connection.is_open:
            if not self.connect_serial():
                return

        assert self.connection is not None
        try:
            self.connection.write(b"restart\r\n")
            self.connection.flush()
            self.tx_count += 1
            self._update_counters()
            self._append_monitor("restart", "TX")
            self._set_status(
                "Restart command sent. The ESP32 should reboot shortly.",
                "success",
            )
            self.top_status.configure(text="●  RESTART SENT", text_color=SUCCESS)
        except (serial.SerialException, OSError, UnicodeEncodeError) as exc:
            self._append_monitor(f"Send failed: {exc}", "ERR")
            self._set_status(f"Could not send restart command: {exc}", "error")
            self.disconnect_serial()

    @staticmethod
    def quote_argument(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _safe_display(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _poll_serial_queue(self) -> None:
        if self._closing:
            return
        try:
            while True:
                event, message = self.reader_queue.get_nowait()
                if event == "rx":
                    self.rx_line_count += 1
                    self._append_monitor(message, "RX")
                    self._update_counters()
                elif event == "reader_error":
                    self._append_monitor(f"Serial read error: {message}", "ERR")
                    self._set_status(f"Serial read error: {message}", "error")
                    self.disconnect_serial()
        except queue.Empty:
            pass
        self._queue_after_id = self.after(50, self._poll_serial_queue)

    def _append_monitor(self, message: str, tag: str = "SYS") -> None:
        if not hasattr(self, "monitor"):
            return
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.monitor.configure(state="normal")
        self.monitor.insert("end", f"{timestamp}  {tag:>3}  {message}\n", tag)
        self.monitor_line_count += 1

        if self.monitor_line_count > MAX_MONITOR_LINES:
            self.monitor.delete("1.0", f"{PRUNE_MONITOR_LINES + 1}.0")
            self.monitor_line_count -= PRUNE_MONITOR_LINES

        if self.auto_scroll_var.get():
            self.monitor.see("end")
        self.monitor.configure(state="disabled")

    def _update_counters(self) -> None:
        self.rx_counter.configure(text=f"RX {self.rx_line_count}  ·  TX {self.tx_count}")

    def clear_monitor(self) -> None:
        self.monitor.configure(state="normal")
        self.monitor.delete("1.0", "end")
        self.monitor.configure(state="disabled")
        self.monitor_line_count = 0
        self.rx_line_count = 0
        self.tx_count = 0
        self._update_counters()
        self._append_monitor("Monitor cleared.", "SYS")

    def _on_close(self) -> None:
        self._closing = True
        self.reader_stop.set()
        conn = self.connection
        self.connection = None
        if conn:
            try:
                if conn.is_open:
                    conn.close()
            except (serial.SerialException, OSError):
                pass
        if self._queue_after_id:
            try:
                self.after_cancel(self._queue_after_id)
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    app = ESP32WiFiConfigurator()
    app.mainloop()
