#include <FastLED.h>

#define NUM_MATRICES   1
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
#define PAYLOAD_SIZE (TOTAL_LEDS * 3)

uint8_t buffer[PAYLOAD_SIZE];

// Map (matrix index, x, y) to LED index in the strip.
// Assumes matrices are chained left-to-right, each 8×32.
// WS2812B matrices are typically wired in a serpentine column pattern.
int getLEDIndex(int matrixIndex, int col, int row) {
  int base = matrixIndex * MATRIX_WIDTH * MATRIX_HEIGHT;
  // Serpentine column layout (common for 8x32 panels):
  // Even columns go top→bottom, odd columns go bottom→top
  if (col % 2 == 0) {
    return base + col * MATRIX_HEIGHT + row;
  } else {
    return base + col * MATRIX_HEIGHT + (MATRIX_HEIGHT - 1 - row);
  }
}

void setup() {
  FastLED.addLeds<WS2812B, DATA_PIN, GRB>(leds, TOTAL_LEDS);
  FastLED.setBrightness(60);  // Start at ~31% — adjust as needed
  FastLED.clear();
  FastLED.show();

  Serial.begin(500000);  // 500kbaud for speed with 1024 LEDs
}

void loop() {
  // Wait for header bytes
  if (Serial.available() < 2) return;

  uint8_t a = Serial.read();
  if (a != HEADER_A) return;

  uint8_t b = Serial.read();
  if (b != HEADER_B) return;

  // Read full payload
  int received = 0;
  unsigned long timeout = millis() + 200;  // 200ms timeout
  while (received < PAYLOAD_SIZE && millis() < timeout) {
    if (Serial.available()) {
      buffer[received++] = Serial.read();
    }
  }

  if (received < PAYLOAD_SIZE) return;  // incomplete, discard

  // Map buffer → LED array
  // Buffer layout: pixels row by row, left-to-right across all 4 matrices
  // i.e. pixel[0] = matrix1 (col0,row0), pixel[32] = matrix2 (col0,row0), etc.
  // Total canvas: 128 columns × 8 rows

  int totalCols = NUM_MATRICES * MATRIX_WIDTH;
  for (int row = 0; row < MATRIX_HEIGHT; row++) {
    for (int col = 0; col < totalCols; col++) {
      int pixelIndex = row * totalCols + col;
      int matrixIndex = col / MATRIX_WIDTH;
      int localCol = col % MATRIX_WIDTH;

      int ledIndex = getLEDIndex(matrixIndex, localCol, row);
      leds[ledIndex] = CRGB(
        buffer[pixelIndex * 3],
        buffer[pixelIndex * 3 + 1],
        buffer[pixelIndex * 3 + 2]
      );
    }
  }

  FastLED.show();
  Serial.write('K');  // ACK — tells laptop it's ready for next frame
}