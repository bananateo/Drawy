# Drawy
<<<<<<< Updated upstream
Drawy is an embedded project that provides a platform for pixel drawing in a 32x32 grid. You can draw, select a color using a color wheel, and adjust the brightness of the colors. The program also allows you to save and load images. Via Wi-Fi, image information is transmitted in real time to an ESP32 that controls 4 8x32 LED matrices.
![alt text](https://github.com/bananateo/Drawy/blob/main/imgs/cloud.jpg "A picture of a cloud on an LED matrix")
=======

An embedded project that lets you draw on a desktop app and displays the result in real time on a wall of WS2812B LED matrices.

A Python painting application sends pixel data over Wi-Fi to an ESP32, which drives four chained 8×32 LED panels.

## How it works

```
Python app (tkinter)  ──Wi-Fi / TCP──>  ESP32 (FastLED)  ──data──>  4× WS2812B 8×32 panels
   draw / erase / fill                  TCP server :1234            (1024 LEDs, 32×32 grid)
```

You draw on a 32×32 grid in the app. Each frame, the app sends only the pixels that changed to the ESP32, which maps them to the physical LED layout and updates the matrices.

## Hardware

- **4× WS2812B 8×32 LED panels** chained in series (DIN → DOUT), forming one 32×32 grid (1024 LEDs total).
- **ESP32** microcontroller (data on pin **13**), connected over Wi-Fi.
- **5V power supply** (purchased) powering both the LED matrices and the ESP32 (via its 5V pin).
- No logic level shifter — the data line is driven directly from the ESP32's 3.3V output.

Inside each panel the LEDs are wired in a **serpentine** pattern, and every second panel is mounted **rotated 180°**. The firmware's `getLEDIndex()` handles this mapping, so the app can send simple (column, row) coordinates.

## Software

| File | Role |
|------|------|
| `drawing.py` | Desktop painting app (Python, `tkinter` + `Pillow`). Draws, sends frames over Wi-Fi. |
| `matrixESP32.ino` | ESP32 firmware (`FastLED` + `WiFi`). TCP server that receives frames and drives the LEDs. |

### Communication protocol

The app sends only changed pixels (delta updates) for speed (~30 fps). Each packet:

- `0xFF 0xFE` — header
- 2 bytes — number of changed pixels
- per pixel: 2 bytes index + 1 byte each R, G, B

The ESP32 replies with a single byte `'K'` (ACK) after processing, then the app sends the next frame. The app auto-reconnects if the connection drops.

## Setup

### ESP32 (firmware)

1. Install the [FastLED](https://github.com/FastLED/FastLED) library in the Arduino IDE.
2. Create a `secrets.h` file (one directory above the sketch) with your Wi-Fi credentials:
   ```cpp
   #define WIFI_SSID     "your-network"
   #define WIFI_PASSWORD "your-password"
   ```
3. Adjust the static IP in `setup()` if needed, then flash the ESP32.
4. Open the Serial Monitor (115200 baud) to confirm the assigned IP address.

### Python app

1. Install dependencies:
   ```bash
   pip install pillow pyserial
   ```
   (`tkinter` ships with Python.)
2. In `drawing.py`, set `ESP32_IP` to the IP shown in the Serial Monitor.
3. Run it:
   ```bash
   python drawing.py
   ```
4. Click **Connect**. The status indicator turns green when connected.

## Usage

- **Tools:** Draw, Erase, Fill (flood fill), Clear.
- **Color:** pick a hue/saturation from the color wheel; adjust the Brightness slider.
- **File menu:** New, Open (load a PNG onto the grid), Save (export the grid as a PNG).

## Configuration

These must match between the app and the firmware, or the image will map incorrectly:

| Setting | `drawing.py` | `matrixESP32.ino` |
|---------|--------------|-------------------|
| Number of panels | `NUM_MATRICES` | `NUM_MATRICES` |
| Panel height | `MATRIX_HEIGHT` | `MATRIX_HEIGHT` |
| Panel width | `GRID_COLS` (32) | `MATRIX_WIDTH` |
| ESP32 address | `ESP32_IP` / `ESP32_PORT` | static IP / `TCP_PORT` |

LED brightness is set in the firmware via `FastLED.setBrightness(50)`.
>>>>>>> Stashed changes
