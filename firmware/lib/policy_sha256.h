// policy_sha256.h — self-contained SHA-256 for the policy-vector codec
// (Lane A, #582). Plain C++17, no OpenSSL/mbedTLS/ESPHome dependencies, so
// the same bytes hash identically on the host test runner, the twin, and the
// ESP32 build. FIPS 180-4 compliant; see the "abc" self-test in
// firmware/test/test_policy_vector.cpp.
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace verdify_policy {

class Sha256 {
 public:
  Sha256() { reset(); }

  void reset() {
    state_[0] = 0x6a09e667u;
    state_[1] = 0xbb67ae85u;
    state_[2] = 0x3c6ef372u;
    state_[3] = 0xa54ff53au;
    state_[4] = 0x510e527fu;
    state_[5] = 0x9b05688cu;
    state_[6] = 0x1f83d9abu;
    state_[7] = 0x5be0cd19u;
    total_len_ = 0;
    buffer_len_ = 0;
  }

  void update(const uint8_t* data, size_t len) {
    total_len_ += len;
    while (len > 0) {
      size_t take = 64 - buffer_len_;
      if (take > len) take = len;
      std::memcpy(buffer_ + buffer_len_, data, take);
      buffer_len_ += take;
      data += take;
      len -= take;
      if (buffer_len_ == 64) {
        compress(buffer_);
        buffer_len_ = 0;
      }
    }
  }

  void update(const void* data, size_t len) { update(static_cast<const uint8_t*>(data), len); }

  void finish(uint8_t out[32]) {
    const uint64_t bit_len = total_len_ * 8;
    const uint8_t pad = 0x80;
    update(&pad, 1);
    const uint8_t zero = 0x00;
    while (buffer_len_ != 56) update(&zero, 1);
    uint8_t len_be[8];
    for (int i = 0; i < 8; ++i) len_be[i] = static_cast<uint8_t>(bit_len >> (56 - 8 * i));
    // Bypass total_len_ bookkeeping for the trailer (already folded into bit_len).
    std::memcpy(buffer_ + buffer_len_, len_be, 8);
    compress(buffer_);
    buffer_len_ = 0;
    for (int i = 0; i < 8; ++i) {
      out[4 * i + 0] = static_cast<uint8_t>(state_[i] >> 24);
      out[4 * i + 1] = static_cast<uint8_t>(state_[i] >> 16);
      out[4 * i + 2] = static_cast<uint8_t>(state_[i] >> 8);
      out[4 * i + 3] = static_cast<uint8_t>(state_[i]);
    }
  }

  static void hash(const uint8_t* data, size_t len, uint8_t out[32]) {
    Sha256 ctx;
    ctx.update(data, len);
    ctx.finish(out);
  }

 private:
  static uint32_t rotr(uint32_t x, uint32_t n) { return (x >> n) | (x << (32 - n)); }

  void compress(const uint8_t block[64]) {
    static const uint32_t k[64] = {
        0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
        0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
        0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
        0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
        0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
        0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
        0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
        0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u, 0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u};
    uint32_t w[64];
    for (int i = 0; i < 16; ++i) {
      w[i] = (static_cast<uint32_t>(block[4 * i]) << 24) | (static_cast<uint32_t>(block[4 * i + 1]) << 16) |
             (static_cast<uint32_t>(block[4 * i + 2]) << 8) | static_cast<uint32_t>(block[4 * i + 3]);
    }
    for (int i = 16; i < 64; ++i) {
      const uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
      const uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
      w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = state_[0], b = state_[1], c = state_[2], d = state_[3];
    uint32_t e = state_[4], f = state_[5], g = state_[6], h = state_[7];
    for (int i = 0; i < 64; ++i) {
      const uint32_t s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const uint32_t ch = (e & f) ^ (~e & g);
      const uint32_t t1 = h + s1 + ch + k[i] + w[i];
      const uint32_t s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
      const uint32_t t2 = s0 + maj;
      h = g;
      g = f;
      f = e;
      e = d + t1;
      d = c;
      c = b;
      b = a;
      a = t1 + t2;
    }
    state_[0] += a;
    state_[1] += b;
    state_[2] += c;
    state_[3] += d;
    state_[4] += e;
    state_[5] += f;
    state_[6] += g;
    state_[7] += h;
  }

  uint32_t state_[8];
  uint64_t total_len_;
  uint8_t buffer_[64];
  size_t buffer_len_;
};

}  // namespace verdify_policy
