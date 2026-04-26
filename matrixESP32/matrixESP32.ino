#include <FastLED.h>
#include <WiFi.h>
#include "../secrets.h"

#define NUM_MATRICES   4
#define MATRIX_WIDTH   32
#define MATRIX_HEIGHT  8
#define TOTAL_LEDS     (NUM_MATRICES * MATRIX_WIDTH * MATRIX_HEIGHT)
#define DATA_PIN       13

#define HEADER_A 0xFF
#define HEADER_B 0xFE

// Wi-Fi config
// Password and network name are in secrets.h
const uint16_t TCP_PORT = 1234;

WiFiServer server(TCP_PORT);
WiFiClient client;

CRGB leds[TOTAL_LEDS];

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
  unsigned long timeout = millis() + 5000;
  int received = 0;
  while (received < n) {
    int avail = client.available();
    if (avail > 0) {
      int toRead = min(avail, n - received);
      client.read(dst + received, toRead);
      received += toRead;
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
  FastLED.setBrightness(50);
  FastLED.clear();
  FastLED.show();
  
  Serial.begin(115200); // Now used for debug

  IPAddress staticIP(10, 180, 227, 110);
  IPAddress gateway(10, 180, 227, 1);
  IPAddress subnet(255, 255, 255, 0);
  IPAddress dns(8, 8, 8, 8);

  WiFi.config(staticIP, gateway, subnet, dns);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.print("Connecting to Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("Connected! IP: ");
  Serial.println(WiFi.localIP()); // see in Serial Monitor

  server.begin();
  Serial.print("TCP server listening on port ");
  Serial.println(TCP_PORT);
}

void loop() 
{
  // reconnection logic
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi lost, reconnecting...");
    client.stop();
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    unsigned long t = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t < 10000) {
      delay(500);
    }
    if (WiFi.status() == WL_CONNECTED) {
      Serial.println("Reconnected!");
    }
    return;
  }
  
  // Accept a new client if none connected
  if (!client || !client.connected()) 
  {
    client = server.accept();
    if (client) 
    {
      client.setNoDelay(true);
      Serial.println("Client connected");
    } else 
    {
      return; // no client yet, nothing to do
    }
  }

  if (client.available() < 2) return;

  uint8_t a = client.read();
  if (a != HEADER_A) return;
  uint8_t b = client.read();
  if (b != HEADER_B) return;

  uint8_t count_bytes[2];
  if (!readBytes(count_bytes, 2)) return;
  uint16_t count = ((uint16_t)count_bytes[0] << 8) | count_bytes[1];
  if (count > TOTAL_LEDS) return;

  int totalCols = NUM_MATRICES * MATRIX_WIDTH;
  uint8_t chunk[5];

  for (uint16_t i = 0; i < count; i++) 
  {
    if (!readBytes(chunk, 5)) return;

    uint16_t pixelIndex = ((uint16_t)chunk[0] << 8) | chunk[1];
    if (pixelIndex >= TOTAL_LEDS) continue;

    int row         = pixelIndex / totalCols;
    int col         = pixelIndex % totalCols;
    int matrixIndex = col / MATRIX_WIDTH;
    int localCol    = col % MATRIX_WIDTH;

    leds[getLEDIndex(matrixIndex, localCol, row)] = CRGB(chunk[2], chunk[3], chunk[4]);
  }

  // ACK first — Python can queue the next frame while we do show()
  client.write('K');

  // Now safe to call show(): no receive in progress, interrupts won't drop bytes
  if (millis() - lastShowTime >= SHOW_INTERVAL_MS)
  {
    FastLED.show();
    lastShowTime = millis();
  }
}