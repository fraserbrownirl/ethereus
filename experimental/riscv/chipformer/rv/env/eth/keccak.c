// keccak256 over XKCP's 32-bit bit-interleaved Keccak-p[1600] (CC0).
// No uint64_t lanes: on RV32 those cost ~90k instructions per permutation
// through libgcc's software shifts/rotates. The interleaved form keeps every
// rotate a 32-bit shift+or (no Zbb rotate available).
#include "keccak.h"
#include <string.h>
#include "xkcp/KeccakP-1600-SnP.h"

#define KECCAK_RATE 136

void keccak256(const uint8_t *in, size_t inlen, uint8_t out[32]){
    KeccakP1600_state state;
    KeccakP1600_Initialize(&state);
    size_t off = 0;
    while(inlen - off >= KECCAK_RATE){
        KeccakP1600_AddBytes(&state, in+off, 0, KECCAK_RATE);
        KeccakP1600_Permute_24rounds(&state);
        off += KECCAK_RATE;
    }
    uint8_t block[KECCAK_RATE];
    memset(block, 0, sizeof(block));
    size_t rem = inlen - off;
    if(rem) memcpy(block, in+off, rem);
    block[rem] = 0x01;
    block[KECCAK_RATE-1] |= 0x80;
    KeccakP1600_AddBytes(&state, block, 0, KECCAK_RATE);
    KeccakP1600_Permute_24rounds(&state);
    KeccakP1600_ExtractBytes(&state, out, 0, 32);
}
