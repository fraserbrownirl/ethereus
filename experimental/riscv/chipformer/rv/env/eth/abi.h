#pragma once
#include <stdint.h>
#include <stddef.h>
#include "u256.h"

// selector: 4-byte big-endian
uint32_t abi_selector(const char *sig); // parse "transfer(address,uint256)" -> selector

// Encode: uint<M>, int<M>, address, bool, bytes<M>, bytes, string, fixed and
// dynamic arrays (incl. multi-dimensional), and nested tuples.
// encode(sig, args) -> calldata bytes.
// args: one C string per type; scalars are decimal or 0x hex (bool also
// true/false), arrays [v1,..,vn], tuples (v1,..,vn); nested elements may be
// quoted with \" \' \\ \n \r \t \xNN escapes.

int abi_encode_calldata(const char *sig, const char **args, int nargs, uint8_t *out, size_t out_cap, size_t *out_len, char *errbuf, size_t errlen);
// decode: types is a comma list like "uint256,(address,bool)" ; hex is 0x...
// One line per top-level value (a top-level fixed array of static scalars
// prints one line per element); nested composites print bracketed.
int abi_decode(const char *types, const char *hexdata, char *out_lines, size_t out_size, char *errbuf, size_t errlen);

// helpers
int abi_parse_sig(const char *sig, char *name_out, size_t name_cap, char types[][64], int *ntypes, char *errbuf, size_t errlen);
int abi_type_kind(const char *t); // 0 uint,1 int,2 address,3 bool,4 bytesN,5 array,6 tuple
