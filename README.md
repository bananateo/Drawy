# Drawy

An embedded project that lets you draw (and animate) on a desktop app and displays the result in real time on a wall of WS2812B LED matrices.

A Python painting application sends pixel data to an ESP32 - over **Wi-Fi or a USB cable** - and the ESP32 drives four chained 8×32 LED panels.

![A picture of a cloud on an LED matrix](https://github.com/bananateo/Drawy/blob/main/imgs/cloud.jpg "A picture of a cloud on an LED matrix")

## How it works

```
Python app (tkinter)  ---Wi-Fi (TCP) or USB serial--->  ESP32 (FastLED)  ---data--->  4× WS2812B 8×32 panels
   draw / erase / fill / animate                        listens on both              (1024 LEDs, 32×32 grid)
```

You draw on a 32×32 grid in the app, optionally across multiple animation frames. Each frame, the app sends only the pixels that changed to the ESP32, which maps them to the physical LED layout and updates the matrices.

The ESP32 runs as its own **Wi-Fi access point** (it creates a network called `Drawy`), so it works on any laptop anywhere with no router, no phone hotspot, and no per-network reconfiguration. A USB cable can be used instead, or as a fallback.

## Hardware

- **4× WS2812B 8×32 LED panels** chained in series (DIN -> DOUT), forming one 32×32 grid (1024 LEDs total).
- **ESP32** microcontroller (data on pin **13**).
- **5V power supply** powering both the LED matrices and the ESP32 (via its 5V pin).
- No logic level shifter — the data line is driven directly from the ESP32's output.

Inside each panel the LEDs are wired in a **serpentine** pattern, and every second panel is mounted **rotated 180 degrees**. The firmware's `getLEDIndex()` handles this mapping, so the app can send simple (column, row) coordinates.

## Software

| File | Role |
|------|------|
| `drawing.py` | Desktop painting and animation app (Python, `tkinter` + `Pillow`) — entry point |
| `link.py` | Communication layer: Wi-Fi/cable connection management, wire protocol, background sender thread |
| `colorutils.py` | Pure color-conversion helpers (HSL, RGB, hex) used by the color wheel and eyedropper |
| `config.py` | Shared constants (panel geometry, network defaults, protocol headers) |
| `matrixESP32.ino` | ESP32 firmware (`FastLED` + `WiFi`) |

The UI never talks to the serial port or socket directly - it goes through a single `LedLink` object from `link.py`. A single app and a single firmware handle both Wi-Fi and cable transports; there are no separate wired/wireless versions.

### Communication protocol

The app sends only changed pixels (delta updates) for speed (around 30 fps). Two packet types share the same `0xFF` lead byte:

**Pixel frame**

- `0xFF 0xFE` - header
- 2 bytes - number of changed pixels
- per pixel: 2 bytes index + 1 byte each R, G, B

**Brightness command** (sets the LED panel brightness live)

- `0xFF 0xFD` - header
- 1 byte - brightness, `0`-`255`

The ESP32 replies with a single byte `'K'` (ACK) after processing each packet, then the app sends the next one.

Large frames are sent in small chunks (each a self-contained pixel frame). The firmware accumulates them into its LED buffer and refreshes the panels at a rate-limited 30 fps, always displaying the final state of a burst.

## Setup

### ESP32 (firmware)

1. Install the [FastLED](https://github.com/FastLED/FastLED) library in the Arduino IDE.
2. *(Optional)* Change the access-point name/password near the top of the sketch:
   ```cpp
   const char* AP_SSID = "Drawy";
   const char* AP_PASS = "drawy1234";   // must be at least 8 characters
   ```
3. Flash the ESP32.
4. *(Optional)* Open the Serial Monitor at **500000 baud** to see the access-point address (defaults to `192.168.4.1`).

### Python app

1. Install dependencies:
   ```bash
   pip install pillow pyserial
   ```
   (`tkinter` ships with Python.)
2. Run it:
   ```bash
   python drawing.py
   ```

### Building a standalone executable (optional)

If you'd rather double-click an app than run a script from an editor, you can package `drawing.py` into a single executable with [PyInstaller](https://pyinstaller.org/):

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name Drawy drawing.py
```

- `--onefile` bundles everything (including `config.py`, `colorutils.py`, and `link.py`, which PyInstaller detects automatically) into one file.
- `--windowed` suppresses the console window, since this is a GUI app.

The result lands in `dist/Drawy.exe` (Windows) or the equivalent for your OS — copy that file out and run it directly. **PyInstaller builds for whichever OS you run it on**; it doesn't cross-compile, so build on Windows for a `.exe`, on macOS for a `.app`, and so on.

### Connecting

**Wi-Fi (default):**

1. On the laptop, join the **`Drawy`** Wi-Fi network (password `drawy1234`). The laptop loses internet while joined, since it's on the ESP32's private network.
2. In the app, leave the transport on **Wi-Fi** (IP defaults to `192.168.4.1`) and click **Connect**.

**Cable:**

1. Plug the ESP32 in over USB.
2. In the app, switch the transport to **Cable**, pick the COM port, and click **Connect**.

The status indicator turns green when connected.

## Usage

### Tools

| Tool | How to use |
|------|------------|
| **Draw** | Click or drag to paint pixels with the current brush color. |
| **Erase** | Click or drag to set pixels back to black. |
| **Fill** | Click a region to flood-fill it with the current brush color. |
| **Pick** | Click any pixel to sample its color. The color wheel and shade slider jump to match, and the tool switches back to Draw automatically. |
| **Clear** | Wipes the current frame to black. |

### Color

Pick a hue and saturation from the color wheel. The **Color shade** slider controls the lightness of the brush color. The **Current color** swatch always shows what you'll paint with. The **LED brightness** slider controls the physical panel brightness live (sent to the ESP32) - this is separate from the brush color.

### Animation

The app supports multi-frame animation. An animation bar sits below the drawing canvas:

- **Add** - inserts a new blank frame after the current one.
- **Duplicate** - copies the current frame.
- **Delete** - removes the current frame (the last frame is cleared instead of deleted).
- **Play / Stop** - plays the animation in a loop at the chosen FPS. The LED wall plays along in real time.
- **FPS** - set the playback speed (1-30 fps).
- **Frame strip** - a scrollable row of thumbnails below the controls. Click any thumbnail to jump to that frame. The current frame is highlighted.
- **<- / ->** arrow keys also step through frames when a text field is not focused.

### File menu

| Item | Action |
|------|--------|
| New | Clears the animation back to a single blank frame. |
| Open (PNG/GIF) | A still PNG loads into the **current frame** only. An animated GIF replaces the **entire animation**. |
| Save frame (PNG) | Saves the current frame as a 32×32 PNG. |
| Export GIF | Saves the whole animation as an animated GIF (upscaled ×8 for readability) at the current FPS. |

### Resizing

The window is freely resizable. The drawing grid scales to fill the available space and stays centered. The sidebar shows a scrollbar when the window is too short to display all controls at once; scroll or resize to reach anything that's off-screen.

## Configuration

These must match between the app and the firmware, or the image will map incorrectly:

| Setting | `drawing.py` / `config.py` | `matrixESP32.ino` |
|---------|--------------------|-------------------|
| Number of panels | `NUM_MATRICES` | `NUM_MATRICES` |
| Panel height | `MATRIX_HEIGHT` | `MATRIX_HEIGHT` |
| Panel width | `GRID_COLS` (32) | `MATRIX_WIDTH` |
| Wi-Fi address | `ESP32_IP` / `ESP32_PORT` | `AP_SSID` / `AP_PASS` / `TCP_PORT` |

All of the settings above live in `config.py`, shared by `drawing.py` and `link.py`.

The starting LED brightness is set in the firmware via `FastLED.setBrightness(50)` and can then be changed live from the app.
