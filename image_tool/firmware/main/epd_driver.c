// E2213JS0C1/C2, ESP-IDF 5.4, ESP32-C3, Board2 / Schematic2 PCB.
// Sequence reference: PervasiveDisplays PDLS_EXT3_Basic_Global,
// COG_SmallCJ functions. Standalone implementation, no Arduino dependency.
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdio.h>
#include "epd_driver.h"

#define BUSY 3
#define RST 1
#define DC 5
#define CLK 4
#define MOSI 6
#define CS 7
#define WIDTH 104
#define HEIGHT 212
#define FRAME_BYTES (WIDTH * HEIGHT / 8)
#define TEMPERATURE_C 25 // Change to actual ambient temperature, supported 0..40 C.
#ifndef EPD_SOFTWARE_SPI
#define EPD_SOFTWARE_SPI 0
#endif
#define SPI_HALF_PERIOD_US 5 // Software SPI: at most 100 kHz, mode 0.
#define READY_STABLE_US 200000LL // Reject isolated high samples; not a wiring test.
static spi_device_handle_t spi;
static uint8_t black[FRAME_BYTES], red[FRAME_BYTES];
static const char *TAG = "epd";
static epd_progress_fn report;
static void pause_ms(int ms) {
    // vTaskDelay(1) can return at the next tick, almost immediately.
    // Guarantee the panel's short reset delays independently of tick phase.
    if (ms <= 20) {
        esp_rom_delay_us((uint32_t)ms * 1000U);
    } else {
        vTaskDelay(pdMS_TO_TICKS(ms) + 1);
    }
}
static void transfer(const void *data, size_t bytes) {
    if (EPD_SOFTWARE_SPI) {
        const uint8_t *p = data;
        for (size_t i = 0; i < bytes; ++i) {
            for (int bit = 7; bit >= 0; --bit) {
                gpio_set_level(CLK, 0);
                gpio_set_level(MOSI, (p[i] >> bit) & 1U);
                esp_rom_delay_us(SPI_HALF_PERIOD_US);
                gpio_set_level(CLK, 1);
                esp_rom_delay_us(SPI_HALF_PERIOD_US);
                gpio_set_level(CLK, 0);
            }
            // Yield between bytes with SCLK low; avoid watchdog starvation.
            if ((i & 127U) == 127U) vTaskDelay(1);
        }
        return;
    }
    spi_transaction_t t = {.length = bytes * 8, .tx_buffer = data};
    esp_err_t err = spi_device_polling_transmit(spi, &t);
    if (err != ESP_OK) ESP_LOGE(TAG, "SPI transfer failed (%u bytes): %s", (unsigned)bytes, esp_err_to_name(err));
    ESP_ERROR_CHECK(err);
}
static void select_panel(int dc) {
    gpio_set_level(DC, dc);
    gpio_set_level(CS, 0);
    esp_rom_delay_us(50);
}
static void deselect_panel(void) {
    esp_rom_delay_us(50);
    gpio_set_level(CS, 1);
    esp_rom_delay_us(50);
}
static void command(uint8_t c) {
    ESP_LOGI(TAG, "CMD 0x%02X, BUSY=%d", c, gpio_get_level(BUSY));
    select_panel(0); transfer(&c, 1); deselect_panel();
}
static void register_byte(uint8_t c, uint8_t value) {
    ESP_LOGI(TAG, "REG 0x%02X = 0x%02X", c, value);
    select_panel(0); transfer(&c, 1);
    gpio_set_level(DC, 1); transfer(&value, 1); deselect_panel();
}
static void register_data(uint8_t c, const void *data, size_t bytes) {
    ESP_LOGI(TAG, "DATA begin: register=0x%02X bytes=%u", c, (unsigned)bytes);
    command(c);
    select_panel(1); transfer(data, bytes); deselect_panel();
    ESP_LOGI(TAG, "DATA complete: register=0x%02X BUSY=%d", c, gpio_get_level(BUSY));
}
static bool wait_ready(const char *stage) {
    int64_t start = esp_timer_get_time(), next_log = start + 1000000;
    int64_t high_since = -1;
    uint32_t samples[2] = {0, 0}, transitions = 0;
    int previous = -1;
    int initial = gpio_get_level(BUSY);
    ESP_LOGI(TAG, "WAIT [%s]: GPIO%d=%d (0=busy, 1=ready); require HIGH samples spanning 200 ms",
             stage, BUSY, initial);
    for (;;) {
        int64_t now = esp_timer_get_time();
        int level = gpio_get_level(BUSY);
        ++samples[level];
        if (previous >= 0 && level != previous) ++transitions;
        previous = level;
        if (level) {
            if (high_since < 0) high_since = now;
            if (now - high_since >= READY_STABLE_US) {
                ESP_LOGI(TAG, "READY [%s]: HIGH samples span 200 ms, elapsed=%lld ms, HIGH=%lu LOW=%lu edges=%lu",
                         stage, (long long)((now-start)/1000),
                         (unsigned long)samples[1], (unsigned long)samples[0],
                         (unsigned long)transitions);
                return true;
            }
        } else {
            high_since = -1;
        }
        if (now >= next_log) {
            ESP_LOGW(TAG, "WAIT [%s]: BUSY=%d elapsed=%lld ms, HIGH=%lu LOW=%lu edges=%lu; waiting for stable HIGH",
                     stage, level, (long long)((now-start)/1000),
                     (unsigned long)samples[1], (unsigned long)samples[0],
                     (unsigned long)transitions);
            if (report) {
                char message[160];
                snprintf(message, sizeof(message), "Waiting %s: BUSY=%d, elapsed=%lld s", stage, level, (long long)((now-start)/1000000));
                report("wait", message);
            }
            next_log = now + 2000000;
        }
        if (now - start > 120000000LL) {
            ESP_LOGE(TAG, "FAIL [%s]: no stable HIGH within 120 s. Refresh will not be forced.", stage);
            ESP_LOGE(TAG, "Check Board2 H3: pin1 BUSY->GPIO%d, pin2 RST->GPIO%d, pin7 3V3, pin8 GND.", BUSY, RST);
            ESP_LOGE(TAG, "Software cannot measure panel VDD or confirm SPI reception; use a meter/analyser.");
            gpio_set_level(CS, 1);
            gpio_set_level(RST, 0);
            gpio_set_level(DC, 0);
            ESP_LOGE(TAG, "Panel held in reset after timeout. Power-cycle before the next test.");
            return false;
        }
        // Polling must yield to FreeRTOS; precise busy-wait delays are only
        // for the short reset/command timing, never a 120-second wait loop.
        vTaskDelay(1);
    }
}
static void reset_panel(void) {
    ESP_LOGI(TAG, "RESET begin: BUSY=%d", gpio_get_level(BUSY));
    pause_ms(5); gpio_set_level(RST, 1);
    ESP_LOGI(TAG, "RST commanded HIGH; BUSY=%d", gpio_get_level(BUSY));
    pause_ms(5); gpio_set_level(RST, 0);
    ESP_LOGI(TAG, "RST commanded LOW; BUSY=%d", gpio_get_level(BUSY));
    pause_ms(10); gpio_set_level(RST, 1);
    ESP_LOGI(TAG, "RST commanded HIGH; BUSY=%d", gpio_get_level(BUSY));
    pause_ms(5); gpio_set_level(CS, 1); pause_ms(5);
    ESP_LOGI(TAG, "RESET complete: BUSY=%d", gpio_get_level(BUSY));
}
static void stage(const char *key, const char *message) {
    if (report) report(key, message);
}

esp_err_t epd_io_init(void) {
    gpio_config_t out = {
        .pin_bit_mask = (1ULL<<RST)|(1ULL<<DC)|(1ULL<<CS),
        .mode = GPIO_MODE_OUTPUT,
    };
    ESP_ERROR_CHECK(gpio_config(&out));
    gpio_set_level(CS, 1); gpio_set_level(RST, 1); gpio_set_level(DC, 1);
    gpio_config_t in = {.pin_bit_mask=1ULL<<BUSY, .mode=GPIO_MODE_INPUT};
    ESP_ERROR_CHECK(gpio_config(&in));
    ESP_LOGI(TAG, "GPIO configured: BUSY=%d", gpio_get_level(BUSY));
    ESP_LOGI(TAG, "STEP 2: SPI init, mode0, MSB first, manual CS");
    if (EPD_SOFTWARE_SPI) {
        gpio_config_t soft = {
            .pin_bit_mask = (1ULL<<CLK)|(1ULL<<MOSI), .mode = GPIO_MODE_OUTPUT,
        };
        ESP_ERROR_CHECK(gpio_config(&soft));
        gpio_set_level(CLK, 0); gpio_set_level(MOSI, 0);
    } else {
    spi_bus_config_t bus = {
        .mosi_io_num=MOSI, .miso_io_num=-1, .sclk_io_num=CLK,
        .quadwp_io_num=-1, .quadhd_io_num=-1, .max_transfer_sz=FRAME_BYTES,
    };
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO));
    spi_device_interface_config_t dev = {
        .clock_speed_hz=1000000, .mode=0, .spics_io_num=-1, .queue_size=1,
    };
    ESP_ERROR_CHECK(spi_bus_add_device(SPI2_HOST, &dev, &spi));
    }
    ESP_LOGI(TAG, "SPI initialized successfully");
    pause_ms(100);
    gpio_set_level(RST, 0); gpio_set_level(DC, 0);
    return ESP_OK;
}

bool epd_show_image(const uint8_t *frame, epd_progress_fn progress) {
    report = progress;
    stage("reset", "正在复位并初始化屏幕");
    ESP_LOGI(TAG, "STEP 3: hardware reset");
    reset_panel();
    // Official COG_SmallCJ_initial sends the soft reset immediately after
    // the timed hardware reset, without a BUSY gate at either reset stage.
    ESP_LOGI(TAG, "Official SmallCJ sequence: continue initialization after timed reset; BUSY=%d", gpio_get_level(BUSY));
    ESP_LOGI(TAG, "STEP 4: software reset, temperature and PSR");
    register_byte(0x00, 0x0e); // Software reset
    pause_ms(5);
    register_byte(0xe5, TEMPERATURE_C);
    register_byte(0xe0, 0x02);
    const uint8_t psr[] = {0xcf, 0x8d};
    ESP_LOGI(TAG, "PSR=CF 8D (official small C/J film default for 2.13 inch)");
    register_data(0x00, psr, sizeof(psr));
    // Keep SPI buffers in RAM, including when DMA is enabled.
    memcpy(black, frame, sizeof(black));
    memcpy(red, frame + EPD_PLANE_BYTES, sizeof(red));
    ESP_LOGI(TAG, "Image: USB frame: validated black/red planes loaded");
    stage("upload", "正在写入黑色和红色色层");
    ESP_LOGI(TAG, "STEP 5: upload black/red planes");
    register_data(0x10, black, sizeof(black));
    register_data(0x13, red, sizeof(red));
    ESP_LOGI(TAG, "Initialization and SPI transmission finished; reception is unverified. First BUSY gate follows; 0x04 has NOT been sent.");
    if (!wait_ready("before power on")) return false;
    stage("power_on", "正在开启屏幕升压");
    ESP_LOGI(TAG, "STEP 6: start DC/DC");
    int64_t power_start = esp_timer_get_time();
    command(0x04); pause_ms(5);
    if (!wait_ready("power on")) return false;
    ESP_LOGI(TAG, "DC/DC power-on wait finished in %lld ms", (long long)((esp_timer_get_time() - power_start) / 1000));
    stage("refresh", "正在全屏刷新，请保持供电");
    ESP_LOGI(TAG, "STEP 7: refreshing embedded image; allow tens of seconds");
    int64_t refresh_start = esp_timer_get_time();
    command(0x12); pause_ms(5);
    if (!wait_ready("refresh")) return false;
    ESP_LOGI(TAG, "Refresh wait finished in %lld ms", (long long)((esp_timer_get_time() - refresh_start) / 1000));
    bool plausible_refresh = esp_timer_get_time() - refresh_start >= 1000000LL;
    if (!plausible_refresh)
        ESP_LOGW(TAG, "Refresh reported ready in under 1 s, including the 200 ms input check. This is suspicious for this panel; do not treat it as a successful visible refresh.");
    stage("power_off", "刷新结束，正在关闭升压");
    ESP_LOGI(TAG, "STEP 8: stop DC/DC");
    command(0x02); pause_ms(5);
    if (!wait_ready("power off")) return false;
    gpio_set_level(RST, 0); gpio_set_level(DC, 0);
    ESP_LOGI(TAG, "STEP 9: commands finished. Verify visible image; SPI has no acknowledgement. Image remains after power is removed.");
    if (!plausible_refresh) {
        stage("error", "刷新在 1 秒内结束，BUSY 行为异常，不能视为正常刷新");
        return false;
    }
    return true;
}
