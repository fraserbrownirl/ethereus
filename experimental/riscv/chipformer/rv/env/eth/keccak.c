#include "keccak.h"
#include <string.h>

static const uint64_t RC[24] = {
    0x0000000000000001ULL, 0x0000000000008082ULL, 0x800000000000808aULL,
    0x8000000080008000ULL, 0x000000000000808bULL, 0x0000000080000001ULL,
    0x8000000080008081ULL, 0x8000000000008009ULL, 0x000000000000008aULL,
    0x0000000000000088ULL, 0x0000000080008009ULL, 0x000000008000000aULL,
    0x000000008000808bULL, 0x800000000000008bULL, 0x8000000000008089ULL,
    0x8000000000008003ULL, 0x8000000000008002ULL, 0x8000000000000080ULL,
    0x000000000000800aULL, 0x800000008000000aULL, 0x8000000080008081ULL,
    0x8000000000008080ULL, 0x0000000080000001ULL, 0x8000000080008008ULL
};

static inline uint64_t ROTL64(uint64_t x, int n){ return n==0 ? x : (x<<n)|(x>>(64-n)); }

void keccak_f1600(uint64_t st[25]){
    static const int RHO_BY_INDEX[25]={
        0, 1, 62, 28, 27,
        36,44, 6, 55, 20,
        3,10,43,25,39,
        41,45,15,21, 8,
        18, 2,61,56,14
    };
    for(int round=0; round<24; round++){
        uint64_t C[5], D[5];
        for(int x=0;x<5;x++) C[x]= st[x] ^ st[x+5] ^ st[x+10] ^ st[x+15] ^ st[x+20];
        for(int x=0;x<5;x++) D[x]= C[(x+4)%5] ^ ROTL64(C[(x+1)%5],1);
        for(int x=0;x<5;x++) for(int y=0;y<5;y++) st[x+5*y] ^= D[x];
        uint64_t B[25];
        uint64_t T[25];
        for(int i=0;i<25;i++) T[i]=st[i];
        for(int x=0;x<5;x++) for(int y=0;y<5;y++){
            int idx = x+5*y;
            int rho = RHO_BY_INDEX[idx];
            int nx = y;
            int ny = (2*x+3*y)%5;
            int nidx = nx+5*ny;
            B[nidx] = ROTL64(T[idx], rho);
        }
        for(int x=0;x<5;x++) for(int y=0;y<5;y++){
            st[x+5*y] = B[x+5*y] ^ ((~B[(x+1)%5+5*y]) & B[(x+2)%5+5*y]);
        }
        st[0] ^= RC[round];
    }
}

void keccak256(const uint8_t *in, size_t inlen, uint8_t out[32]){
    uint64_t st[25]={0};
    const size_t rate = 136;
    size_t offset=0;
    while(inlen - offset >= rate){
        for(int i=0;i< rate/8;i++){
            uint64_t t=0;
            for(int j=0;j<8;j++) t |= (uint64_t)in[offset + i*8 + j] << (8*j);
            st[i] ^= t;
        }
        keccak_f1600(st);
        offset += rate;
    }
    uint8_t block[136]={0};
    size_t rem = inlen - offset;
    if(rem) memcpy(block, in+offset, rem);
    block[rem] = 0x01;
    block[rate-1] |= 0x80;
    for(int i=0;i< rate/8;i++){
        uint64_t t=0;
        for(int j=0;j<8;j++) t |= (uint64_t)block[i*8+j] << (8*j);
        st[i] ^= t;
    }
    keccak_f1600(st);
    for(int i=0;i<4;i++){
        uint64_t t=st[i];
        for(int j=0;j<8;j++) out[i*8+j]= (t >> (8*j)) & 0xFF;
    }
}
