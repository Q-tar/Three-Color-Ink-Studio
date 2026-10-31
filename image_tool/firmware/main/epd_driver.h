#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"

#define EPD_WIDTH 104
#define EPD_HEIGHT 212
#define EPD_PLANE_BYTES 2756
#define EPD_FRAME_BYTES 5512
typedef void (*epd_progress_fn)(const char *stage, const char *message);

esp_err_t epd_io_init(void);
bool epd_show_image(const uint8_t *frame, epd_progress_fn progress);
