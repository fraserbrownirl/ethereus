#pragma once
#include <stdint.h>
#include <stddef.h>
void keccak256(const uint8_t *in, size_t inlen, uint8_t out[32]);
