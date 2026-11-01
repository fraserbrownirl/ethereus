#pragma once
#include <stddef.h>

// Bounded string copy WITHOUT strncpy's zero-padding: strncpy pads the whole
// destination (a 4 KB tmp costs ~16k instructions of memset per call on this
// machine), while nothing reads past the NUL. Always NUL-terminates.
static inline void cpy_cap(char *dst, const char *src, size_t cap){
    if(cap == 0) return;
    size_t i = 0;
    while(i + 1 < cap && src[i]){ dst[i] = src[i]; i++; }
    dst[i] = 0;
}
