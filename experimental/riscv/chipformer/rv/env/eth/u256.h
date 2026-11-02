#pragma once
#include <stdint.h>
#include <stddef.h>

typedef struct { uint32_t d[8]; } u256_t; // d0 = LSW
typedef struct { uint32_t d[16]; } u512_t;

void u256_zero(u256_t *a);
void u256_one(u256_t *a);
int  u256_is_zero(const u256_t *a);
int  u256_eq(const u256_t *a, const u256_t *b);
int  u256_cmp(const u256_t *a, const u256_t *b); // -1 0 1 unsigned
int  u256_scmp(const u256_t *a, const u256_t *b); // signed (two's complement)
int  u256_is_negative(const u256_t *a);
void u256_neg(u256_t *r, const u256_t *a);
void u256_not(u256_t *r, const u256_t *a);
void u256_and(u256_t *r, const u256_t *a, const u256_t *b);
void u256_or (u256_t *r, const u256_t *a, const u256_t *b);
void u256_xor(u256_t *r, const u256_t *a, const u256_t *b);
void u256_add(u256_t *r, const u256_t *a, const u256_t *b); // wraps mod 2^256
int  u256_add_carry(u256_t *r, const u256_t *a, const u256_t *b); // returns carry out (overflow)
void u256_sub(u256_t *r, const u256_t *a, const u256_t *b); // wraps
int  u256_sub_borrow(u256_t *r, const u256_t *a, const u256_t *b); // returns borrow
void u256_mul(u256_t *r, const u256_t *a, const u256_t *b); // low 256 bits
void u256_mul512(u512_t *r, const u256_t *a, const u256_t *b);
void u256_divmod(u256_t *q, u256_t *rem, const u256_t *num, const u256_t *den); // unsigned; if den==0 q=0 rem=num
void u256_sdivmod(u256_t *q, u256_t *rem, const u256_t *a, const u256_t *b); // signed EVM style
void u256_addmod(u256_t *r, const u256_t *a, const u256_t *b, const u256_t *m);
void u256_mulmod(u256_t *r, const u256_t *a, const u256_t *b, const u256_t *m);
void u256_exp(u256_t *r, const u256_t *base, const u256_t *exp);
void u256_shl(u256_t *r, const u256_t *a, uint32_t shift); // shift in [0,255], >255 -> zero
void u256_shr(u256_t *r, const u256_t *a, uint32_t shift);
void u256_sar(u256_t *r, const u256_t *a, uint32_t shift);
void u256_signextend(u256_t *r, const u256_t *a, uint32_t byte_index); // 0..31
uint32_t u256_byte(const u256_t *a, uint32_t i); // byte i from LSB 0..31
int u256_bitlen(const u256_t *a);
int u256_parse_hex(u256_t *r, const char *s); // s hex with optional 0x, 0..64 nibbles
int u256_dec_fits(const char *s); // a decimal literal's magnitude is at most 2**256-1
int u256_parse_dec(u256_t *r, const char *s); // decimal, optional leading -, handles signed? returns 0 ok
int u256_try_parse_auto(u256_t *r, const char *s); // hex if 0x else dec; handles negative decimal for signed context
void u256_to_hex(const u256_t *a, char out[65]); // 64 hex chars no 0x, lowercase, no leading trim
void u256_to_hex_trim(const u256_t *a, char *out); // 0x + trimmed or 0x0
void u256_to_dec(const u256_t *a, char *out); // decimal unsigned
void u256_to_dec_signed(const u256_t *a, char *out); // signed two's complement decimal
// helper for printing with buffer size
int  u512_is_zero(const u512_t *a);
int  u512_cmp(const u512_t *a, const u512_t *b);
void u512_zero(u512_t *a);
void u256_from_u64(u256_t *a, uint64_t v);
uint32_t u256_divmod_small(u256_t *q, const u256_t *a, uint32_t divisor); // returns remainder, q may be NULL
void u256_macc_small(u256_t *r, uint32_t mul, uint32_t add); // r = r*mul + add (mod 2^256)
