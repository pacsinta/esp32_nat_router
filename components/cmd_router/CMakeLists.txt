idf_component_register(SRCS "cmd_router.c"
                    INCLUDE_DIRS . "${CMAKE_SOURCE_DIR}/include"
                    REQUIRES console nvs_flash esp_wifi driver esp_driver_gpio spi_flash pcap_capture acl remote_console syslog oled_display mbedtls app_update)
target_compile_options(${COMPONENT_LIB} PRIVATE -Os)
