#include <FastLED.h>
#include <WiFi.h>

//  Drawy firmware — unified transport (Wi-Fi SoftAP + USB serial fallback)
//  and runtime LED-brightness control for the 4-matrix RGB LED panel

//  These MUST match drawing.py:
#define NUM_MATRICES   4
#define MATRIX_WIDTH   32
#define MATRIX_HEIGHT  8
#define TOTAL_LEDS     (NUM_MATRICES * MATRIX_WIDTH * MATRIX_HEIGHT)
#define DATA_PIN       13

// Protocol
#define HEADER_A        0xFF
#define HEADER_B        0xFE   // pixel frame:  FF FE [count hi][count lo] [idx idx R G B]...
#define HEADER_BRIGHT   0xFD   // brightness :  FF FD [value 0-255]

//  Access point (the ESP32 creates its OWN network)
//  Join this network from the laptop, then point the app at 192.168.4.1.
//  AP password must be at least 8 characters for WPA2.
const char*    AP_SSID  = "Drawy";
const char*    AP_PASS  = "drawy1234";
const uint16_t TCP_PORT = 1234;

WiFiServer server(TCP_PORT);
WiFiClient client;

CRGB leds[TOTAL_LEDS];

unsigned long lastShowTime = 0;
#define SHOW_INTERVAL_MS 33

// Serpentine + every-other-panel-rotated-180 mapping
int getLEDIndex(int matrixIndex, int col, int row) {
  int base = matrixIndex * MATRIX_WIDTH * MATRIX_HEIGHT;
  bool flipped = (matrixIndex % 2 != 0);
  int effectiveCol = flipped ? (MATRIX_WIDTH  - 1 - col) : col;
  int effectiveRow = flipped ? (MATRIX_HEIGHT - 1 - row) : row;
  if (effectiveCol % 2 == 0)
    return base + effectiveCol * MATRIX_HEIGHT + effectiveRow;
  else
    return base + effectiveCol * MATRIX_HEIGHT + (MATRIX_HEIGHT - 1 - effectiveRow);
}

// Read exactly n bytes from whichever transport we were handed (Serial or TCP).
// Both derive from Stream, so the same code works for cable and Wi-Fi.
bool readBytes(Stream* s, uint8_t* dst, int n) {
  unsigned long timeout = millis() + 3000;
  int received = 0;
  while (received < n) {
    int avail = s->available();
    if (avail > 0) {
      int toRead = min(avail, n - received);
      received += s->readBytes(dst + received, toRead);
    } else if (millis() > timeout) {
      return false;
    } else {
      yield();
    }
  }
  return true;
}

void setup() {
  FastLED.addLeds<WS2812B, DATA_PIN, GRB>(leds, TOTAL_LEDS);
  FastLED.setBrightness(50);        // starting value; app can change it live
  FastLED.clear();
  FastLED.show();

  Serial.begin(500000);             // doubles as the cable transport

  WiFi.mode(WIFI_AP);
  WiFi.setSleep(false);             // steadier link, lower latency
  WiFi.softAP(AP_SSID, AP_PASS);

  // Setup-only debug. Avoid printing in loop() so cable transport stays clean.
  Serial.print("AP \"");
  Serial.print(AP_SSID);
  Serial.print("\" up. Join it, then point the app at ");
  Serial.println(WiFi.softAPIP());  // 192.168.4.1 by default

  server.begin();
}

void loop() {
  // Keep a single TCP client.
  if (!client || !client.connected()) {
    WiFiClient incoming = server.accept();
    if (incoming) {
      client = incoming;
      client.setNoDelay(true);
    }
  }

  // Choose a transport that has a header waiting: TCP first, cable as fallback.
  Stream* src = nullptr;
  if (client && client.connected() && client.available() >= 2)
    src = &client;
  else if (Serial.available() >= 2)
    src = &Serial;

  if (!src) return;

  if (src->read() != HEADER_A) return;
  uint8_t b = src->read();

  // Brightness command
  if (b == HEADER_BRIGHT) {
    uint8_t val;
    if (!readBytes(src, &val, 1)) return;
    FastLED.setBrightness(val);
    FastLED.show();
    src->write('K');
    return;
  }

  if (b != HEADER_B) return;

  // Pixel frame
  uint8_t cb[2];
  if (!readBytes(src, cb, 2)) return;
  uint16_t count = ((uint16_t)cb[0] << 8) | cb[1];
  if (count > TOTAL_LEDS) return;

  int totalCols = NUM_MATRICES * MATRIX_WIDTH;
  uint8_t chunk[5];

  for (uint16_t i = 0; i < count; i++) {
    if (!readBytes(src, chunk, 5)) return;

    uint16_t pixelIndex = ((uint16_t)chunk[0] << 8) | chunk[1];
    if (pixelIndex >= TOTAL_LEDS) continue;

    int row         = pixelIndex / totalCols;
    int col         = pixelIndex % totalCols;
    int matrixIndex = col / MATRIX_WIDTH;
    int localCol    = col % MATRIX_WIDTH;

    leds[getLEDIndex(matrixIndex, localCol, row)] = CRGB(chunk[2], chunk[3], chunk[4]);
  }

  src->write('K');   // ACK first so the app can queue the next frame

  if (millis() - lastShowTime >= SHOW_INTERVAL_MS) {
    FastLED.show();
    lastShowTime = millis();
  }
}
