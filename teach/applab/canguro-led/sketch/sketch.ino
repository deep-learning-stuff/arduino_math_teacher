// Canguro Coach on the LED matrix. The sketch only draws: Python decides every frame.
// Same pattern as the official example core-and-foundational/02-led-matrix/02-led-matrix-frame.
#include <Arduino_RouterBridge.h>
#include <Arduino_LED_Matrix.h>
#include <vector>

Arduino_LED_Matrix matrix;

const uint8_t FRAME_SIZE = 8 * 13;
uint8_t frame[FRAME_SIZE] = {0};

void draw(std::vector<uint8_t> newFrame) {
  size_t len = min(newFrame.size(), (size_t)FRAME_SIZE);
  memcpy(frame, newFrame.data(), len);
}

void setup() {
  matrix.begin();
  matrix.setGrayscaleBits(3);  // brightness 0 to 7
  matrix.clear();
  Bridge.begin();
  Bridge.provide("draw", draw);
}

void loop() {
  matrix.draw(frame);
  delay(10);
}
