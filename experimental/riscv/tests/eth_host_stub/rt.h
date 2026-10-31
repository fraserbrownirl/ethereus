#pragma once
// Host-test stub for env/rt.h: capture putchar (a7=1) into a buffer so the
// ASan harness can check firmware output. Exit-park (a7=93) is a nop.
#include <stddef.h>

extern char host_out[1 << 20];
extern size_t host_out_len;

static inline long rv_ecall2(long n, long a0, long a1, long a2) {
    (void)a1; (void)a2;
    if (n == 1 && host_out_len + 1 < sizeof(host_out))
        host_out[host_out_len++] = (char)a0;
    return 0;
}

static inline void rv_putchar(char c) { rv_ecall2(1, (unsigned char)c, 0, 0); }
static inline void rv_mark(int id) { (void)id; }
