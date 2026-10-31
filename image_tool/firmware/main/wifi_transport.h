#pragma once
#include "cJSON.h"
#include "esp_err.h"

typedef void (*wifi_command_fn)(const char *line, const char *client);
esp_err_t wifi_transport_start(wifi_command_fn handler);
// Copies the event; caller retains ownership. Client is 32 hex digits.
void wifi_publish(const cJSON *event, const char *client);
