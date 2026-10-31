idf_component_register(SRCS "receiver.c" "epd_driver.c" "wifi_transport.c" INCLUDE_DIRS "."
                       REQUIRES driver esp_timer esp_driver_usb_serial_jtag json mbedtls nvs_flash
                                esp_wifi esp_netif esp_event esp_http_server esp_hw_support)
