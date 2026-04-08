#include <FastLED.h>

#define NUM_MATRICES   4
#define MATRIX_WIDTH   32
#define MATRIX_HEIGHT  8
#define TOTAL_LEDS     (NUM_MATRICES * MATRIX_WIDTH * MATRIX_HEIGHT)
#define DATA_PIN       6

CRGB leds[TOTAL_LEDS];

// Serial protocol:
// Laptop sends packets of exactly TOTAL_LEDS * 3 bytes (R,G,B per pixel)
// preceded by a 2-byte header: 0xFF 0xFE
// Total packet size: 2 + 1024*3 = 3074 bytes

#define HEADER_A 0xFF
#define HEADER_B 0xFE

// Map (matrix index, x, y) to LED index in the strip.
// Assumes matrices are chained left-to-right, each 8×32.
// WS2812B matrices are typically wired in a serpentine column pattern.
int getLEDIndex(int matrixIndex, int col, int row) {
  int base = matrixIndex * MATRIX_WIDTH * MATRIX_HEIGHT;
  // Serpentine column layout
  // Even columns go top→bottom, odd columns go bottom→top
  if (col % 2 == 0) {
    return base + col * MATRIX_HEIGHT + row;
  } else {
    return base + col * MATRIX_HEIGHT + (MATRIX_HEIGHT - 1 - row);
  }
}

// Read exactly n bytes into dst with a timeout, returns false on timeout
bool readBytes(uint8_t* dst, int n) {
  unsigned long timeout = millis() + 200;
  int received = 0;
  while (received < n) {
    if (millis() > timeout) return false;
    if (Serial.available()) dst[received++] = Serial.read();
  }
  return true;
}

void setup() {
  FastLED.addLeds<WS2812B, DATA_PIN, GRB>(leds, TOTAL_LEDS);
  FastLED.setBrightness(60);
  FastLED.clear();
  FastLED.show();

  Serial.begin(500000);
}

void loop() {
  // Wait for header bytes
  if (Serial.available() < 2) return;

  if (Serial.read() != HEADER_A) return;
  if (Serial.read() != HEADER_B) return;

  // Read 2-byte pixel count
  uint8_t count_bytes[2];
  if (!readBytes(count_bytes, 2)) return;
  uint16_t count = ((uint16_t)count_bytes[0] << 8) | count_bytes[1];

  if (count > TOTAL_LEDS) return;  // sanity check

  int totalCols = NUM_MATRICES * MATRIX_WIDTH;

  uint8_t chunk[5];
  for (uint16_t i = 0; i < count; i++) {
    if (!readBytes(chunk, 5)) return;

    uint16_t pixelIndex = ((uint16_t)chunk[0] << 8) | chunk[1];
    if (pixelIndex >= TOTAL_LEDS) continue;

    int row         = pixelIndex / totalCols;
    int col         = pixelIndex % totalCols;
    int matrixIndex = col / MATRIX_WIDTH;
    int localCol    = col % MATRIX_WIDTH;

    leds[getLEDIndex(matrixIndex, localCol, row)] = CRGB(chunk[2], chunk[3], chunk[4]);
  }

  FastLED.show();
  Serial.write('K');  // ACK — tells laptop it's ready for next frame
}