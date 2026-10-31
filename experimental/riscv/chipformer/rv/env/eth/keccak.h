#pragma once
#include <stdint.h>
#include <stddef.h>
void keccak256(const uint8_t *in, size_t inlen, uint8_t out[32]);
void keccak256_hex(const char *hex_or_text, uint8_t out[32]); // helper not needed in firmware but for tests
// low-level
void keccak_f1600(uint64_t st[25]);
