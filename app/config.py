"""Shared configuration for Drawy.

Values marked MUST MATCH FIRMWARE have to agree with matrixESP32.ino,
or the image will map incorrectly onto the panels.
"""

# Network / serial defaults
# With the SoftAP firmware the ESP32 is ALWAYS at this address: join its
# "Drawy" Wi-Fi network on the laptop, then just hit Connect.
ESP32_IP   = "192.168.4.1"
ESP32_PORT = 1234
BAUD_RATE  = 500000

# Panel geometry
NUM_MATRICES  = 4    # chained 8x32 panels - MUST MATCH FIRMWARE
MATRIX_HEIGHT = 8    # rows per panel      - MUST MATCH FIRMWARE

GRID_COLS = 32
GRID_ROWS = MATRIX_HEIGHT * NUM_MATRICES

# Transport tuning
CHUNK_SIZE  = 20     # pixels per packet when sending large frames
MAX_RETRIES = 5      # Wi-Fi connect attempts
RETRY_DELAY = 3      # seconds between attempts

# Protocol headers (shared with the firmware)
HDR_A      = 0xFF
HDR_PIXELS = 0xFE    # FF FE [count hi][count lo] [idx idx R G B]...
HDR_BRIGHT = 0xFD    # FF FD [value 0-255]
