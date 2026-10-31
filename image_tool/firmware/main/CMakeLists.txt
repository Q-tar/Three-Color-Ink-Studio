idf_component_register(SRCS "receiver.c" "epd_driver.c" INCLUDE_DIRS "."
                       REQUIRES driver esp_timer esp_driver_usb_serial_jtag json mbedtls nvs_flash)
