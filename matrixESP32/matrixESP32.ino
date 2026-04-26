#include <FastLED.h>

#define NUM_MATRICES   4
#define MATRIX_WIDTH   32
#define MATRIX_HEIGHT  8
#define TOTAL_LEDS     (NUM_MATRICES * MATRIX_WIDTH * MATRIX_HEIGHT)
#define DATA_PIN       13

CRGB leds[TOTAL_LEDS];

#define HEADER_A 0xFF
#define HEADER_B 0xFE

bool needsShow = false;
unsigned long lastShowTime = 0;
#define SHOW_INTERVAL_MS 33

int getLEDIndex(int matrixIndex, int col, int row) {
  int base = matrixIndex * MATRIX_WIDTH * MATRIX_HEIGHT;
  bool flipped = (matrixIndex % 2 != 0);
  int effectiveCol = flipped ? (MATRIX_WIDTH - 1 - col) : col;
  int effectiveRow = flipped ? (MATRIX_HEIGHT - 1 - row) : row;
  if (effectiveCol % 2 == 0) {
    return base + effectiveCol * MATRIX_HEIGHT + effectiveRow;
  } else {
    return base + effectiveCol * MATRIX_HEIGHT + (MATRIX_HEIGHT - 1 - effectiveRow);
  }
}

bool readBytes(uint8_t* dst, int n) {
  unsigned long timeout = millis() + 2000;
  int received = 0;
  while (received < n) {
    if (millis() > timeout) return false;
    if (Serial.available()) {
      dst[received++] = Serial.read();
    } else {
      yield();
    }
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
  if (needsShow && millis() - lastShowTime >= SHOW_INTERVAL_MS) {
    FastLED.show();
    needsShow = false;
    lastShowTime = millis();
  }

  if (Serial.available() < 2) return;

  uint8_t a = Serial.read();
  if (a != HEADER_A) return;
  uint8_t b = Serial.read();
  if (b != HEADER_B) return;

  uint8_t count_bytes[2];
  if (!readBytes(count_bytes, 2)) return;
  uint16_t count = ((uint16_t)count_bytes[0] << 8) | count_bytes[1];
  if (count > TOTAL_LEDS) return;

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

  needsShow = true;
  Serial.write('K');
}