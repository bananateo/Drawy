"""Communication layer for Drawy.

Everything that talks to the ESP32 lives here: the Wi-Fi socket wrapper,
the wire protocol (delta pixel frames + brightness commands), connection
management for both transports, and the background sender thread.

The UI never touches the serial port directly. It interacts with one
LedLink object:

    link = LedLink(get_image=lambda: current_pil_image,
                   on_status=status_callback,
                   on_conn_change=conn_callback)
    link.start_sender()
    ...
    link.mark_dirty()          # after any pixel change
    link.set_brightness(128)   # panel brightness command
    link.reset_history()       # force a full resend (e.g. File > New)
"""

import socket
import threading
import time

import serial
import serial.tools.list_ports

from config import (
    ESP32_IP, ESP32_PORT, BAUD_RATE,
    NUM_MATRICES, MATRIX_HEIGHT, GRID_COLS, GRID_ROWS,
    CHUNK_SIZE, MAX_RETRIES, RETRY_DELAY,
    HDR_A, HDR_PIXELS, HDR_BRIGHT,
)


def list_serial_ports():
    """Names of the serial ports currently present on the system."""
    return [p.device for p in serial.tools.list_ports.comports()]


class WifiSerial:
    """Wi-Fi socket wrapper that mimics the bits of serial.Serial we use."""

    def __init__(self, ip, port, timeout=2):
        self._sock = socket.create_connection((ip, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self.is_open = True

    def write(self, data):
        self._sock.sendall(data)

    def read(self, n):
        buf = b''
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("ESP32 disconnected")
            buf += chunk
        return buf

    def reset_input_buffer(self):  pass
    def reset_output_buffer(self): pass

    def close(self):
        self._sock.close()
        self.is_open = False


class LedLink:
    """Owns the connection to the ESP32 and the frame-sending loop."""

    def __init__(self, get_image, on_status=None, on_conn_change=None):
        """
        get_image      -- zero-arg callable returning the current PIL image
                          (native grid resolution) to mirror on the LEDs.
        on_status      -- callable(state, text); state is 'ok' | 'error' | 'off'.
        on_conn_change -- callable(connected: bool), fired when the link
                          opens or closes (drives the Connect button label).
        """
        self._get_image      = get_image
        self._on_status      = on_status or (lambda state, text: None)
        self._on_conn_change = on_conn_change or (lambda connected: None)

        self._ser   = None
        self._lock  = threading.Lock()
        self._dirty = False
        self._prev_leds = {}   # pixel_index -> (R, G, B)

    # Connection state

    def is_connected(self):
        return bool(self._ser) and getattr(self._ser, 'is_open', False)

    def connect_wifi(self, ip=None, port=None, retries=MAX_RETRIES):
        """Blocking; run on a worker thread. Retries with status updates."""
        ip   = (ip or '').strip() or ESP32_IP
        port = int(port or ESP32_PORT)
        for attempt in range(1, retries + 1):
            try:
                self._on_status('off', f'Connecting... ({attempt}/{retries})')
                self._ser = WifiSerial(ip, port, timeout=5)
                self._on_status('ok', f'Connected  {ip}:{port}')
                self._on_conn_change(True)
                return True
            except Exception:
                self._ser = None
                if attempt < retries:
                    time.sleep(RETRY_DELAY)
        self._on_status('error', f'Failed after {retries} attempts')
        self._on_conn_change(False)
        return False

    def connect_cable(self, port):
        if not port:
            self._on_status('error', 'No port selected')
            return False
        try:
            self._ser = serial.Serial(port, BAUD_RATE, timeout=2)
            time.sleep(3)               # wait for ESP32 reset on serial open
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()
            self._on_status('ok', f'Connected  {port}')
            self._on_conn_change(True)
            return True
        except Exception as e:
            self._ser = None
            self._on_status('error', str(e))
            return False

    def disconnect(self):
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None
        self._on_status('off', 'Disconnected')
        self._on_conn_change(False)

    # Frame state

    def mark_dirty(self):
        """Call after any pixel change; the sender thread picks it up."""
        self._dirty = True

    def reset_history(self):
        """Forget the last-sent pixels so the next frame is sent in full."""
        self._prev_leds.clear()

    # Commands

    def set_brightness(self, val):
        """Send a live panel-brightness command (0-255)."""
        if not self.is_connected():
            return
        with self._lock:
            try:
                self._ser.write(bytearray([HDR_A, HDR_BRIGHT, int(val) & 0xFF]))
                if self._ser.read(1) != b'K':
                    self._on_status('error', 'Brightness: no ACK')
            except Exception as e:
                self._on_status('error', f'Brightness failed: {e}')

    def send_blackout(self):
        """Force every physical LED off, regardless of delta history."""
        if not self.is_connected():
            return
        physical_cols = NUM_MATRICES * GRID_COLS
        total = physical_cols * MATRIX_HEIGHT
        with self._lock:
            try:
                for chunk_start in range(0, total, CHUNK_SIZE):
                    chunk_end = min(chunk_start + CHUNK_SIZE, total)
                    count = chunk_end - chunk_start
                    pkt = bytearray([HDR_A, HDR_PIXELS,
                                     (count >> 8) & 0xFF, count & 0xFF])
                    for pixel_index in range(chunk_start, chunk_end):
                        pkt.extend([(pixel_index >> 8) & 0xFF,
                                    pixel_index & 0xFF, 0, 0, 0])
                    self._ser.write(pkt)
                    if self._ser.read(1) != b'K':
                        self._on_status('error', 'Blackout: no ACK')
                        return
                self._on_status('ok', 'Cleared')
            except Exception as e:
                self._on_status('error', f'Blackout failed: {e}')

    # Frame building / sending (works for both Wi-Fi and cable)

    def _build_frame(self):
        image   = self._get_image()
        changed = []

        physical_cols = NUM_MATRICES * GRID_COLS   # 128 - full strip width
        physical_rows = MATRIX_HEIGHT              # 8

        for canvas_row in range(GRID_ROWS):        # 0-31 in the UI
            for canvas_col in range(GRID_COLS):    # 0-31 in the UI
                # Convert canvas (col, row) to physical strip coordinates
                matrix_index = canvas_row // physical_rows  # which matrix (0-3)
                local_row    = canvas_row %  physical_rows  # row within it (0-7)
                physical_col = matrix_index * GRID_COLS + canvas_col
                pixel_index  = local_row * physical_cols + physical_col

                px  = image.getpixel((canvas_col, canvas_row))
                rgb = (px[0], px[1], px[2])
                if self._prev_leds.get(pixel_index) != rgb:
                    changed.append((pixel_index, rgb))

        if not changed:
            return None
        for pixel_index, rgb in changed:
            self._prev_leds[pixel_index] = rgb

        count = len(changed)
        pkt = bytearray([HDR_A, HDR_PIXELS, (count >> 8) & 0xFF, count & 0xFF])
        for pixel_index, (r, g, b) in changed:
            pkt.extend([(pixel_index >> 8) & 0xFF, pixel_index & 0xFF, r, g, b])
        return pkt

    def _send_frame_chunked(self, frame_pkt):
        total_pixels = (frame_pkt[2] << 8) | frame_pkt[3]
        pixel_data   = frame_pkt[4:]
        with self._lock:
            for i in range(0, total_pixels, CHUNK_SIZE):
                chunk_pixels = pixel_data[i*5 : (i+CHUNK_SIZE)*5]
                count = len(chunk_pixels) // 5
                pkt = bytearray([HDR_A, HDR_PIXELS,
                                 (count >> 8) & 0xFF, count & 0xFF])
                pkt.extend(chunk_pixels)
                self._ser.write(pkt)
                if self._ser.read(1) != b'K':
                    raise Exception(f'No ACK at chunk offset {i}')

    def _sender_loop(self):
        while True:
            time.sleep(1 / 30)
            if not self.is_connected():
                continue
            if self._dirty:
                self._dirty = False
                frame = self._build_frame()
                if frame is None:
                    continue
                try:
                    self._send_frame_chunked(frame)
                except Exception as e:
                    print(f'Send error: {e}')
                    self._ser = None
                    self._on_status('error', f'Lost connection: {e}')
                    self._on_conn_change(False)

    def start_sender(self):
        threading.Thread(target=self._sender_loop, daemon=True).start()
