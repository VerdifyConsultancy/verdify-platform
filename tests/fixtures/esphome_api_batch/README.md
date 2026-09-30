# ESPHome2026.6.5 upstream API test fixture

`api_connection.h` and `.cpp` are the exact unmodified upstream ESPHome2026.6.5
files from the compiled831 and969 build trees (identical in both). They are test
fixtures only, not an external component or another deployed API implementation.
Upstream: https://github.com/esphome/esphome/tree/2026.6.5/esphome/components/api

The build-time correction validates full-file hashes before patching generated
files. These fixtures let offline tests validate those same hashes and compile
actual patched caller methods with an instrumented transport sink. All protocol
serialization, transport buffering and unrelated API methods remain upstream.

SHA256:
- api_connection.h:43e5f3718d37a0aa153f49ece2f96e96de60ef839363fc9c9c97b6d1525cfdd3
- api_connection.cpp:dc923e04b1e18e43661ce02e1af98d959cb9414c51587c872478fc0b8d524517
