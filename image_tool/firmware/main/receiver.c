// Native USB Serial/JTAG transport. Console logs remain on UART0 to avoid
// interleaving with framed replies. No display activity until a host requests it.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "cJSON.h"
#include "mbedtls/base64.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "epd_driver.h"

#define FW_VERSION "EPD_BOARD2_RECEIVER_1.1"
#define PREFIX "@EPD1 "
#define MAX_LINE 10000
#define SAVED_MAGIC 0x31445045U

typedef struct {
    uint32_t magic, version, length, crc;
    uint8_t data[EPD_FRAME_BYTES];
} stored_image_t;

typedef struct {
    int id;
    bool save;
    uint32_t crc;
    uint8_t data[EPD_FRAME_BYTES];
} image_job_t;

static SemaphoreHandle_t tx_lock, state_lock;
static QueueHandle_t jobs;
static nvs_handle_t storage;
static bool busy, saved_valid;
static bool storage_available;
static esp_err_t storage_error;
static uint32_t saved_crc;
static int current_id;
static char line_buffer[MAX_LINE + 1];

static uint32_t image_crc(const uint8_t *data, size_t size)
{
    // CRC-32/ISO-HDLC, identical to Python zlib.crc32.
    uint32_t value = 0xffffffffU;
    for (size_t i = 0; i < size; ++i) {
        value ^= data[i];
        for (int bit = 0; bit < 8; ++bit)
            value = (value >> 1) ^ ((value & 1U) ? 0xedb88320U : 0U);
    }
    return value ^ 0xffffffffU;
}

static bool valid_planes(const uint8_t *data)
{
    for (size_t i = 0; i < EPD_PLANE_BYTES; ++i)
        if (data[i] & data[i + EPD_PLANE_BYTES]) return false;
    return true;
}

static bool is_busy(void)
{
    xSemaphoreTake(state_lock, portMAX_DELAY);
    bool value = busy;
    xSemaphoreGive(state_lock);
    return value;
}

static void set_busy(bool value)
{
    xSemaphoreTake(state_lock, portMAX_DELAY);
    busy = value;
    xSemaphoreGive(state_lock);
}

static cJSON *reply(int id, const char *event)
{
    cJSON *obj = cJSON_CreateObject();
    if (obj) {
        cJSON_AddNumberToObject(obj, "id", id);
        cJSON_AddStringToObject(obj, "event", event);
    }
    return obj;
}

static void send_reply(cJSON *obj)
{
    if (!obj) return;
    char *json = cJSON_PrintUnformatted(obj);
    cJSON_Delete(obj);
    if (!json) return;
    size_t length = strlen(json) + strlen(PREFIX) + 2;
    char *line = malloc(length);
    if (line) {
        snprintf(line, length, PREFIX "%s\n", json);
        xSemaphoreTake(tx_lock, portMAX_DELAY);
        int sent = usb_serial_jtag_write_bytes(line, strlen(line), pdMS_TO_TICKS(200));
        if (sent != (int)strlen(line))
            usb_serial_jtag_write_bytes("\n", 1, pdMS_TO_TICKS(20));
        xSemaphoreGive(tx_lock);
        free(line);
    }
    free(json);
}

static void error_reply(int id, const char *message)
{
    cJSON *obj = reply(id, "error");
    if (obj) cJSON_AddStringToObject(obj, "message", message);
    send_reply(obj);
}

static void add_crc(cJSON *obj, uint32_t crc)
{
    char text[9];
    snprintf(text, sizeof(text), "%08lx", (unsigned long)crc);
    if (obj) cJSON_AddStringToObject(obj, "crc", text);
}

static bool read_saved(uint8_t *data, uint32_t *crc)
{
    if (!storage_available) return false;
    stored_image_t *record = malloc(sizeof(*record));
    if (!record) return false;
    size_t size = sizeof(*record);
    bool valid = nvs_get_blob(storage, "image", record, &size) == ESP_OK &&
                 size == sizeof(*record) && record->magic == SAVED_MAGIC &&
                 record->version == 1 && record->length == EPD_FRAME_BYTES &&
                 image_crc(record->data, EPD_FRAME_BYTES) == record->crc &&
                 valid_planes(record->data);
    if (valid) {
        if (data) memcpy(data, record->data, EPD_FRAME_BYTES);
        if (crc) *crc = record->crc;
    }
    free(record);
    return valid;
}

static esp_err_t save_image(const image_job_t *job)
{
    if (!storage_available) return storage_error;
    // NVS replaces a blob with power-loss recovery; do not erase old data first.
    if (saved_valid && saved_crc == job->crc) return ESP_OK;
    stored_image_t *record = malloc(sizeof(*record));
    if (!record) return ESP_ERR_NO_MEM;
    record->magic = SAVED_MAGIC;
    record->version = 1;
    record->length = EPD_FRAME_BYTES;
    record->crc = job->crc;
    memcpy(record->data, job->data, EPD_FRAME_BYTES);
    esp_err_t err = nvs_set_blob(storage, "image", record, sizeof(*record));
    if (err == ESP_OK) err = nvs_commit(storage);
    free(record);
    if (err == ESP_OK) {
        xSemaphoreTake(state_lock, portMAX_DELAY);
        saved_valid = true;
        saved_crc = job->crc;
        xSemaphoreGive(state_lock);
    }
    return err;
}

static void progress(const char *stage, const char *message)
{
    cJSON *obj = reply(current_id, "stage");
    if (obj) {
        cJSON_AddStringToObject(obj, "stage", stage);
        cJSON_AddStringToObject(obj, "message", message);
    }
    send_reply(obj);
}

static void display_task(void *arg)
{
    image_job_t *job;
    while (true) {
        if (xQueueReceive(jobs, &job, portMAX_DELAY) != pdTRUE) continue;
        current_id = job->id;
        int64_t start = esp_timer_get_time();
        bool ok = epd_show_image(job->data, progress);
        cJSON *result = NULL;
        if (!ok) {
            result = reply(job->id, "error");
            if (result) cJSON_AddStringToObject(result, "message", "屏幕刷新未完成：检查 BUSY、供电及日志；已停止后续操作。请复位后重试。");
        } else {
            esp_err_t stored = ESP_OK;
            if (job->save) {
                progress("save", "刷新结束，正在保存图像到 Flash");
                stored = save_image(job);
            }
            if (stored != ESP_OK) {
                char text[150];
                snprintf(text, sizeof(text), "刷新流程已完成，但保存失败：%s", esp_err_to_name(stored));
                result = reply(job->id, "error");
                if (result) cJSON_AddStringToObject(result, "message", text);
            } else {
                result = reply(job->id, "done");
                add_crc(result, job->crc);
                if (result) {
                    cJSON_AddBoolToObject(result, "saved", job->save);
                    cJSON_AddNumberToObject(result, "elapsed_ms", (double)((esp_timer_get_time()-start)/1000));
                }
            }
        }
        free(job);
        set_busy(false);
        // Publish completion only after a subsequent upload can be accepted.
        send_reply(result);
    }
}

static void hello(int id)
{
    cJSON *obj = reply(id, "hello");
    if (obj) {
        cJSON_AddNumberToObject(obj, "protocol", 1);
        cJSON_AddStringToObject(obj, "firmware", FW_VERSION);
        cJSON_AddNumberToObject(obj, "width", EPD_WIDTH);
        cJSON_AddNumberToObject(obj, "height", EPD_HEIGHT);
        cJSON_AddBoolToObject(obj, "storage_available", storage_available);
        if (!storage_available)
            cJSON_AddStringToObject(obj, "storage_error", esp_err_to_name(storage_error));
        cJSON_AddBoolToObject(obj, "busy", is_busy());
        xSemaphoreTake(state_lock, portMAX_DELAY);
        cJSON_AddBoolToObject(obj, "saved", saved_valid);
        xSemaphoreGive(state_lock);
    }
    send_reply(obj);
}

static bool number_is(cJSON *obj, const char *key, int expected)
{
    cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, key);
    return cJSON_IsNumber(item) && item->valuedouble == expected;
}

static void handle_line(const char *line)
{
    if (strncmp(line, PREFIX, strlen(PREFIX))) return;
    cJSON *obj = cJSON_Parse(line + strlen(PREFIX));
    if (!cJSON_IsObject(obj)) {
        cJSON_Delete(obj);
        error_reply(0, "无效 JSON");
        return;
    }
    cJSON *id_item = cJSON_GetObjectItemCaseSensitive(obj, "id");
    cJSON *cmd_item = cJSON_GetObjectItemCaseSensitive(obj, "cmd");
    if (!cJSON_IsNumber(id_item) || !isfinite(id_item->valuedouble) ||
        id_item->valuedouble < 1 || id_item->valuedouble > 2147483647.0 ||
        floor(id_item->valuedouble) != id_item->valuedouble || !cJSON_IsString(cmd_item)) {
        error_reply(0, "无效请求 ID 或命令");
        cJSON_Delete(obj);
        return;
    }
    int id = id_item->valueint;
    const char *cmd = cmd_item->valuestring;
    if (!strcmp(cmd, "hello") || !strcmp(cmd, "status")) {
        hello(id);
        cJSON_Delete(obj);
        return;
    }
    if (strcmp(cmd, "image") && strcmp(cmd, "restore")) {
        error_reply(id, "不支持的命令");
        cJSON_Delete(obj);
        return;
    }
    if (is_busy()) {
        error_reply(id, "设备正在刷新，请等待完成");
        cJSON_Delete(obj);
        return;
    }
    if (!storage_available && (!strcmp(cmd, "restore") ||
        cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(obj, "save")))) {
        error_reply(id, "存储初始化失败：可取消保存后传图显示，已有 NVS 数据未被擦除");
        cJSON_Delete(obj);
        return;
    }
    image_job_t *job = calloc(1, sizeof(*job));
    if (!job) {
        error_reply(id, "内存不足");
        cJSON_Delete(obj);
        return;
    }
    job->id = id;
    bool valid = false;
    if (!strcmp(cmd, "restore")) {
        valid = read_saved(job->data, &job->crc);
        if (!valid) error_reply(id, "没有通过校验的已保存图片");
    } else {
        cJSON *data = cJSON_GetObjectItemCaseSensitive(obj, "data");
        cJSON *crc = cJSON_GetObjectItemCaseSensitive(obj, "crc");
        cJSON *save = cJSON_GetObjectItemCaseSensitive(obj, "save");
        size_t decoded = 0;
        if (!number_is(obj, "width", EPD_WIDTH) || !number_is(obj, "height", EPD_HEIGHT) ||
            !cJSON_IsString(data) || !cJSON_IsString(crc) || strlen(crc->valuestring) != 8 ||
            !cJSON_IsBool(save)) {
            error_reply(id, "图像尺寸或字段类型错误");
        } else if (mbedtls_base64_decode(job->data, sizeof(job->data), &decoded,
                                        (const unsigned char *)data->valuestring, strlen(data->valuestring)) != 0 ||
                   decoded != EPD_FRAME_BYTES) {
            error_reply(id, "图像解码或长度错误，必须为 5512 字节");
        } else {
            char expected[9];
            job->crc = image_crc(job->data, EPD_FRAME_BYTES);
            snprintf(expected, sizeof(expected), "%08lx", (unsigned long)job->crc);
            if (strcmp(expected, crc->valuestring)) error_reply(id, "CRC 校验失败");
            else if (!valid_planes(job->data)) error_reply(id, "黑色与红色色层发生重叠");
            else {
                valid = true;
                job->save = cJSON_IsTrue(save);
            }
        }
    }
    cJSON_Delete(obj);
    if (!valid) {
        free(job);
        return;
    }
    set_busy(true);
    cJSON *ack = reply(id, "accepted");
    add_crc(ack, job->crc);
    if (ack) cJSON_AddNumberToObject(ack, "bytes", EPD_FRAME_BYTES);
    send_reply(ack);
    if (xQueueSend(jobs, &job, 0) != pdTRUE) {
        set_busy(false);
        free(job);
        error_reply(id, "图像队列已满");
    }
}

void app_main(void)
{
    tx_lock = xSemaphoreCreateMutex();
    state_lock = xSemaphoreCreateMutex();
    jobs = xQueueCreate(1, sizeof(image_job_t *));
    configASSERT(tx_lock && state_lock && jobs);
    // Start USB first. Storage errors must not turn into a silent reboot loop
    // that looks like a disconnected board. Never erase previous NVS data.
    usb_serial_jtag_driver_config_t config = {.tx_buffer_size=4096, .rx_buffer_size=16384};
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&config));
    storage_error = nvs_flash_init();
    if (storage_error == ESP_OK)
        storage_error = nvs_open("inkstudio", NVS_READWRITE, &storage);
    storage_available = storage_error == ESP_OK;
    if (!storage_available)
        ESP_LOGW("receiver", "Storage disabled: %s; USB/display remain available", esp_err_to_name(storage_error));
    saved_valid = read_saved(NULL, &saved_crc);
    ESP_ERROR_CHECK(epd_io_init());
    configASSERT(xTaskCreate(display_task, "epd_display", 8192, NULL, 4, NULL) == pdPASS);
    send_reply(reply(0, "ready"));
    size_t length = 0;
    bool overflow = false;
    int64_t last_byte = 0;
    uint8_t chunk[256];
    while (true) {
        int count = usb_serial_jtag_read_bytes(chunk, sizeof(chunk), pdMS_TO_TICKS(100));
        if (count <= 0) {
            if (length && esp_timer_get_time()-last_byte > 10000000LL) {
                length = 0;
                overflow = false;
                error_reply(0, "接收中断超过 10 秒，已丢弃未完成的图像");
            }
            continue;
        }
        last_byte = esp_timer_get_time();
        for (int i = 0; i < count; ++i) {
            uint8_t value = chunk[i];
            if (value == '\n') {
                if (!overflow) {
                    line_buffer[length] = 0;
                    if (length && line_buffer[length-1] == '\r') line_buffer[length-1] = 0;
                    handle_line(line_buffer);
                } else error_reply(0, "接收行过长，已丢弃");
                length = 0;
                overflow = false;
            } else if (length < MAX_LINE && !overflow) {
                if (value == 0) overflow = true;
                else line_buffer[length++] = (char)value;
            } else overflow = true;
        }
    }
}
