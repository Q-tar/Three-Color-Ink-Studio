// A bounded event journal isolates HTTP clients from USB replies. HTTP handlers
// only validate/queue work; panel refresh always runs in the display task.
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <ctype.h>
#include <errno.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_http_server.h"
#include "esp_random.h"
#include "esp_log.h"
#include "esp_check.h"
#include "wifi_transport.h"

#define JOURNAL_SIZE 32
#define HTTP_BODY_LIMIT 10000
typedef struct { uint32_t seq; char client[33]; char *json; } journal_entry_t;
static journal_entry_t journal[JOURNAL_SIZE];
static uint32_t sequence;
static char boot_id[17];
static SemaphoreHandle_t journal_lock;
static wifi_command_fn command_handler;

void wifi_publish(const cJSON *event, const char *client)
{
    char *json = cJSON_PrintUnformatted(event);
    xSemaphoreTake(journal_lock, portMAX_DELAY);
    uint32_t next = ++sequence;
    journal_entry_t *slot = &journal[(next - 1) % JOURNAL_SIZE];
    free(slot->json);
    slot->seq = next;
    memcpy(slot->client, client, 33);
    slot->json = json; // A failed allocation becomes an explicit lost-event error.
    xSemaphoreGive(journal_lock);
}

static esp_err_t send_json(httpd_req_t *req, cJSON *obj)
{
    char *text = obj ? cJSON_PrintUnformatted(obj) : NULL;
    cJSON_Delete(obj);
    if (!text) return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Out of memory");
    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    esp_err_t err = httpd_resp_sendstr(req, text);
    free(text);
    return err;
}

static bool query_params(httpd_req_t *req, char client[33], char query[128])
{
    if (httpd_req_get_url_query_str(req, query, 128) != ESP_OK ||
        httpd_query_key_value(query, "client", client, 33) != ESP_OK || strlen(client) != 32)
        return false;
    for (size_t i = 0; i < 32; ++i)
        if (!isxdigit((unsigned char)client[i])) return false;
    return true;
}

static esp_err_t events_get(httpd_req_t *req)
{
    char client[33], query[128], after_text[16];
    if (!query_params(req, client, query))
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Expected client=32 hex digits");
    bool has_after = httpd_query_key_value(query, "after", after_text, sizeof(after_text)) == ESP_OK;
    uint32_t after = 0;
    if (has_after) {
        char *end;
        errno = 0;
        unsigned long value = strtoul(after_text, &end, 10);
        if (errno || !after_text[0] || *end || after_text[0] == '-' || value > UINT32_MAX)
            return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid cursor");
        after = (uint32_t)value;
    }
    cJSON *obj = cJSON_CreateObject();
    if (!obj) return send_json(req, NULL);
    cJSON *events = cJSON_AddArrayToObject(obj, "events");
    if (!events) { cJSON_Delete(obj); return send_json(req, NULL); }
    xSemaphoreTake(journal_lock, portMAX_DELAY);
    uint32_t latest = sequence;
    bool lost = has_after && (after > latest || latest - after > JOURNAL_SIZE);
    if (has_after && !lost) {
        for (uint32_t n = after; n < latest; ++n) {
            journal_entry_t *slot = &journal[n % JOURNAL_SIZE];
            if (!strcmp(slot->client, client)) {
                cJSON *event = slot->json ? cJSON_Parse(slot->json) : NULL;
                if (!event || !cJSON_AddItemToArray(events, event)) {
                    cJSON_Delete(event);
                    lost = true;
                    break;
                }
            }
        }
    }
    xSemaphoreGive(journal_lock);
    cJSON_AddNumberToObject(obj, "cursor", latest);
    cJSON_AddStringToObject(obj, "boot", boot_id);
    cJSON_AddBoolToObject(obj, "lost", lost);
    return send_json(req, obj);
}

static esp_err_t command_post(httpd_req_t *req)
{
    char client[33], query[128];
    if (!query_params(req, client, query) || req->content_len <= 0 || req->content_len > HTTP_BODY_LIMIT)
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid client or body length");
    char *body = malloc(req->content_len + 1);
    if (!body) return send_json(req, NULL);
    size_t received = 0;
    while (received < req->content_len) {
        int count = httpd_req_recv(req, body + received, req->content_len - received);
        if (count <= 0) {
            free(body);
            return httpd_resp_send_err(req, HTTPD_408_REQ_TIMEOUT, "Incomplete body; not queued");
        }
        received += count;
    }
    body[received] = 0;
    if (memchr(body, 0, received) || strncmp(body, "@EPD1 ", 6)) {
        free(body);
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Expected @EPD1 JSON");
    }
    command_handler(body, client);
    free(body);
    cJSON *obj = cJSON_CreateObject();
    if (obj) cJSON_AddBoolToObject(obj, "ok", true);
    return send_json(req, obj);
}

static esp_err_t index_get(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/plain; charset=utf-8");
    return httpd_resp_sendstr(req, "Ink Studio Wi-Fi receiver 1.2\nOpen Ink Studio, choose Wi-Fi, connect to 192.168.4.1.\nUSB remains available.\n");
}

esp_err_t wifi_transport_start(wifi_command_fn handler)
{
    journal_lock = xSemaphoreCreateMutex();
    if (!journal_lock) return ESP_ERR_NO_MEM;
    command_handler = handler;
    snprintf(boot_id, sizeof(boot_id), "%08lx%08lx", (unsigned long)esp_random(), (unsigned long)esp_random());
    const char *ssid = CONFIG_INK_WIFI_SSID;
    const char *password = CONFIG_INK_WIFI_PASSWORD;
    if (!strlen(ssid) || strlen(ssid) > 32 || strlen(password) < 8 || strlen(password) > 63)
        return ESP_ERR_INVALID_ARG;
    ESP_RETURN_ON_ERROR(esp_netif_init(), "wifi", "netif init");
    ESP_RETURN_ON_ERROR(esp_event_loop_create_default(), "wifi", "event loop");
    if (!esp_netif_create_default_wifi_ap()) return ESP_ERR_NO_MEM;
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    init.nvs_enable = false; // Image NVS failure must not disable network access.
    ESP_RETURN_ON_ERROR(esp_wifi_init(&init), "wifi", "wifi init");
    ESP_RETURN_ON_ERROR(esp_wifi_set_storage(WIFI_STORAGE_RAM), "wifi", "wifi storage");
    wifi_config_t config = { .ap = { .channel = 1, .max_connection = 2, .authmode = WIFI_AUTH_WPA2_PSK } };
    memcpy(config.ap.ssid, ssid, strlen(ssid));
    config.ap.ssid_len = strlen(ssid);
    memcpy(config.ap.password, password, strlen(password));
    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_AP), "wifi", "AP mode");
    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_AP, &config), "wifi", "AP config");
    ESP_RETURN_ON_ERROR(esp_wifi_start(), "wifi", "AP start");
    httpd_config_t http_config = HTTPD_DEFAULT_CONFIG();
    http_config.stack_size = 8192;
    http_config.max_open_sockets = 3;
    http_config.lru_purge_enable = true;
    http_config.recv_wait_timeout = 3;
    http_config.send_wait_timeout = 3;
    httpd_handle_t server;
    ESP_RETURN_ON_ERROR(httpd_start(&server, &http_config), "wifi", "HTTP start");
    const httpd_uri_t events = { .uri="/api/events", .method=HTTP_GET, .handler=events_get };
    const httpd_uri_t command = { .uri="/api/command", .method=HTTP_POST, .handler=command_post };
    const httpd_uri_t index = { .uri="/", .method=HTTP_GET, .handler=index_get };
    ESP_RETURN_ON_ERROR(httpd_register_uri_handler(server, &events), "wifi", "events handler");
    ESP_RETURN_ON_ERROR(httpd_register_uri_handler(server, &command), "wifi", "command handler");
    ESP_RETURN_ON_ERROR(httpd_register_uri_handler(server, &index), "wifi", "index handler");
    ESP_LOGI("wifi", "Hotspot: %s; HTTP: http://192.168.4.1", ssid);
    return ESP_OK;
}
