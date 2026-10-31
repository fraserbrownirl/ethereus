#pragma once
#include <stdint.h>
#include <stddef.h>
#include "u256.h"

// selector: 4-byte big-endian
uint32_t abi_selector(const char *sig); // parse "transfer(address,uint256)" -> selector

// Encode static types: uint<M>, int<M>, address, bool, bytes<M>, uint[N], etc.
// For v1: static-only encode, dynamic bytes/string encode only.
// encode(sig, args) -> calldata hex string (0x...)
// args: array of strings, each decimal or 0x hex for uint/address/bool/bytes32 ; for arrays comma-separated? We'll parse per spec.

int abi_encode_calldata(const char *sig, const char **args, int nargs, uint8_t *out, size_t *out_len, char *errbuf, size_t errlen);
// decode: types is comma list like "uint256,address" ; hex is 0x...
int abi_decode(const char *types, const char *hexdata, char *out_lines, size_t out_size, char *errbuf, size_t errlen);

// helpers
int abi_parse_sig(const char *sig, char *name_out, size_t name_cap, char types[][64], int *ntypes, char *errbuf, size_t errlen);
int abi_type_kind(const char *t); // 0 uint,1 int,2 address,3 bool,4 bytesN,5 array
