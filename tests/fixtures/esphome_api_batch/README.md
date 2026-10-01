# ESPHome2026.6.5 upstream API test fixture

`api_connection.h` and `.cpp` are the exact unmodified upstream ESPHome2026.6.5
files from the compiled831 and969 build trees (identical in both). They are test
fixtures only, not an external component or another deployed API implementation.
Upstream: https://github.com/esphome/esphome/tree/2026.6.5/esphome/components/api

The build-time correction validates full-file hashes before patching generated
files. These fixtures let offline tests validate those same hashes and compile
actual patched caller methods with an instrumented transport sink. Protocol serialization and unrelated API methods remain upstream. The overflow
queue correction changes only allocation/deallocation and publishes a new entry
after both allocations succeed. It preserves the configured queue capacity.
`api_frame_helper.cpp` is the exact selected generated caller, compiled with
dependency stubs to verify its existing failed-connection path; it is not patched.

SHA256:
- api_connection.h:43e5f3718d37a0aa153f49ece2f96e96de60ef839363fc9c9c97b6d1525cfdd3
- api_connection.cpp:dc923e04b1e18e43661ce02e1af98d959cb9414c51587c872478fc0b8d524517

- api_overflow_buffer.h:d0692231e9f63d237836c3a784f092a2abeeacef45fa04856768d0336cce8b94
- api_overflow_buffer.cpp:81f7be6e141f043660ca87b7a1a81918caab4105d8968782880e3d02664e7cd6
- api_frame_helper.cpp:9d594b8e620e8ab4958471d5e2b1daa57973a66a3205017cd67104a6a4079a7d
