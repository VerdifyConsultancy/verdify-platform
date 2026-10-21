# Exact firmware toolchain selection — #1009

The qualified builder remains ESPHome `2026.6.5`, image digest `sha256:ffe23402d169fc9b8ff29fc3c9fc13b3c47e8a53726fce4569e2280bd534c84c`. Replace the `recommended` framework alias in `firmware/greenhouse.yaml` with ESP-IDF `5.5.4` and PlatformIO platform `55.3.39`. Recovery includes the same base configuration and inherits both pins.

These are the exact existing defaults, verified against the [official ESPHome 2026.6.5 ESP32 version tables and resolver](https://github.com/esphome/esphome/blob/2026.6.5/esphome/components/esp32/__init__.py): `ESP_IDF_FRAMEWORK_VERSION_LOOKUP`, `ESP_IDF_PLATFORM_VERSION_LOOKUP` and `PLATFORM_VERSION_LOOKUP`. The default toolchain is PlatformIO; its resolver expands platform `55.3.39` to the versioned release `55.03.39/platform-espressif32.zip`. This freezes version selection; it does not assert cryptographic immutability of upstream release assets or complete hermetic dependency custody.

The supported cluster CI runs secretless full and recovery compiles using public nondeployable dummy credentials. Its images and outputs must not be flashed. No compiler or build runs on the operator Mac. The live suspended builder remains full-SHA-only; this source change starts no production Job, changes no runtime pod and contacts no device.

#1009 remains open for compile/archive versus verified-flash separation and artifact provenance, the paired gateway OTA/API controls, then the approved value-blind credential compensation and attended flash/recovery proof under #390. Device-side signed-app verification remains deferred by DR-48. A version pin does not waive those criteria.
