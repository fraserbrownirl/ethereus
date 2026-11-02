#include "u256.h"
#include <string.h>
#include <ctype.h>

void u256_zero(u256_t *a) { for(int i=0;i<8;i++) a->d[i]=0; }
void u256_one(u256_t *a) { u256_zero(a); a->d[0]=1; }
int u256_is_zero(const u256_t *a){ for(int i=0;i<8;i++) if(a->d[i]) return 0; return 1; }
int u256_eq(const u256_t *a, const u256_t *b){ for(int i=0;i<8;i++) if(a->d[i]!=b->d[i]) return 0; return 1; }
int u256_cmp(const u256_t *a, const u256_t *b){ for(int i=7;i>=0;i--){ if(a->d[i]<b->d[i]) return -1; if(a->d[i]>b->d[i]) return 1; } return 0; }
int u256_is_negative(const u256_t *a){ return (int)(a->d[7]>>31); }
int u256_scmp(const u256_t *a, const u256_t *b){
    int an=a->d[7]>>31, bn=b->d[7]>>31;
    if(an!=bn) return an? -1:1;
    return u256_cmp(a,b);
}
void u256_neg(u256_t *r, const u256_t *a){ uint64_t carry=1; for(int i=0;i<8;i++){ uint64_t v=(uint64_t)(~a->d[i])+carry; r->d[i]=(uint32_t)v; carry=v>>32; } }
void u256_not(u256_t *r, const u256_t *a){ for(int i=0;i<8;i++) r->d[i]=~a->d[i]; }
void u256_and(u256_t *r, const u256_t *a, const u256_t *b){ for(int i=0;i<8;i++) r->d[i]=a->d[i]&b->d[i]; }
void u256_or(u256_t *r, const u256_t *a, const u256_t *b){ for(int i=0;i<8;i++) r->d[i]=a->d[i]|b->d[i]; }
void u256_xor(u256_t *r, const u256_t *a, const u256_t *b){ for(int i=0;i<8;i++) r->d[i]=a->d[i]^b->d[i]; }

void u256_add(u256_t *r, const u256_t *a, const u256_t *b){
    uint64_t c=0;
    for(int i=0;i<8;i++){ uint64_t s=(uint64_t)a->d[i]+b->d[i]+c; r->d[i]=(uint32_t)s; c=s>>32; }
}
int u256_add_carry(u256_t *r, const u256_t *a, const u256_t *b){
    uint64_t c=0;
    for(int i=0;i<8;i++){ uint64_t s=(uint64_t)a->d[i]+b->d[i]+c; r->d[i]=(uint32_t)s; c=s>>32; }
    return (int)c;
}
void u256_sub(u256_t *r, const u256_t *a, const u256_t *b){
    uint64_t c=0;
    for(int i=0;i<8;i++){ uint64_t bi=(uint64_t)b->d[i]+c; uint64_t s=(uint64_t)a->d[i]-bi; r->d[i]=(uint32_t)s; c=(a->d[i] < bi)?1:0; }
}
int u256_sub_borrow(u256_t *r, const u256_t *a, const u256_t *b){
    uint64_t c=0;
    for(int i=0;i<8;i++){ uint64_t bi=(uint64_t)b->d[i]+c; uint64_t s=(uint64_t)a->d[i]-bi; r->d[i]=(uint32_t)s; c=(a->d[i] < bi)?1:0; }
    return (int)c;
}

void u256_from_u64(u256_t *a, uint64_t v){ a->d[0]=(uint32_t)v; a->d[1]=(uint32_t)(v>>32); for(int i=2;i<8;i++) a->d[i]=0; }

// schoolbook mul -> low 256 bits
void u256_mul(u256_t *r, const u256_t *a, const u256_t *b){
    uint64_t acc[8]={0};
    for(int i=0;i<8;i++){
        uint64_t carry=0;
        for(int j=0;j<8;j++){
            if(i+j>=8) break;
            uint64_t prod=(uint64_t)a->d[i]*b->d[j];
            uint64_t sum=acc[i+j]+(prod & 0xFFFFFFFFULL)+carry;
            acc[i+j]=sum & 0xFFFFFFFFULL;
            carry = (sum>>32) + (prod>>32);
            int k=i+j+1;
            while(carry && k<8){
                uint64_t s=acc[k]+(carry & 0xFFFFFFFFULL);
                uint64_t carry_next = (s>>32) + (carry>>32);
                acc[k]=s & 0xFFFFFFFFULL;
                carry=carry_next;
                k++;
            }
        }
    }
    for(int i=0;i<8;i++) r->d[i]=(uint32_t)acc[i];
}

void u512_zero(u512_t *a){ for(int i=0;i<16;i++) a->d[i]=0; }
int u512_is_zero(const u512_t *a){ for(int i=0;i<16;i++) if(a->d[i]) return 0; return 1; }
int u512_cmp(const u512_t *a, const u512_t *b){ for(int i=15;i>=0;i--){ if(a->d[i]<b->d[i]) return -1; if(a->d[i]>b->d[i]) return 1; } return 0; }

void u256_mul512(u512_t *r, const u256_t *a, const u256_t *b){
    uint64_t acc[16]={0};
    for(int i=0;i<8;i++){
        uint64_t carry=0;
        for(int j=0;j<8;j++){
            int k=i+j;
            uint64_t prod=(uint64_t)a->d[i]*b->d[j];
            uint64_t sum=acc[k]+ (prod & 0xFFFFFFFFULL) + carry;
            acc[k]=sum & 0xFFFFFFFFULL;
            carry = (sum>>32) + (prod>>32);
            int kk=k+1;
            while(carry){
                if(kk>=16) break;
                uint64_t s=acc[kk]+ (carry & 0xFFFFFFFFULL);
                uint64_t carry_next = (s>>32) + (carry>>32);
                acc[kk]=s & 0xFFFFFFFFULL;
                carry=carry_next;
                kk++;
                if(carry==0) break;
            }
        }
    }
    for(int i=0;i<16;i++) r->d[i]=(uint32_t)acc[i];
}

// unsigned divmod: binary long division
void u256_divmod(u256_t *q, u256_t *rem, const u256_t *num, const u256_t *den){
    if(u256_is_zero(den)){
        if(q) u256_zero(q);
        if(rem) *rem=*num;
        return;
    }
    if(q) u256_zero(q);
    u256_t r; u256_zero(&r);
    int nbits = u256_bitlen(num);
    int dbits = u256_bitlen(den);
    if(nbits < dbits){
        if(rem) *rem=*num;
        return;
    }
    for(int i=nbits-1; i>=0; --i){
        uint32_t c=0;
        for(int k=0;k<8;k++){ uint32_t nc = r.d[k]>>31; r.d[k]=(r.d[k]<<1)|c; c=nc; }
        int word = i/32, bit = i%32;
        uint32_t nb = (num->d[word]>>bit)&1;
        r.d[0] |= nb;
        if(u256_cmp(&r, den) >=0){
            u256_t tmp;
            u256_sub(&tmp, &r, den);
            r=tmp;
            if(q){
                q->d[word] |= (1u<<bit);
            }
        }
    }
    if(rem) *rem=r;
}

void u256_sdivmod(u256_t *q, u256_t *rem, const u256_t *a, const u256_t *b){
    int an=u256_is_negative(a);
    int bn=u256_is_negative(b);
    if(u256_is_zero(b)){
        if(q) u256_zero(q);
        if(rem) u256_zero(rem);
        return;
    }
    u256_t ua, ub;
    if(an){ u256_neg(&ua,a);} else ua=*a;
    if(bn){ u256_neg(&ub,b);} else ub=*b;
    u256_t qtmp, rtmp;
    u256_divmod(&qtmp,&rtmp,&ua,&ub);
    if(an != bn){
        u256_t nq;
        u256_neg(&nq,&qtmp);
        qtmp=nq;
    }
    if(an){
        u256_t nr;
        u256_neg(&nr,&rtmp);
        rtmp=nr;
    }
    if(q) *q=qtmp;
    if(rem) *rem=rtmp;
}

void u256_addmod(u256_t *r, const u256_t *a, const u256_t *b, const u256_t *m){
    if(u256_is_zero(m)){ u256_zero(r); return; }
    u256_t sum; int carry=u256_add_carry(&sum,a,b);
    if(carry){
        u512_t big; u512_zero(&big);
        for(int i=0;i<8;i++) big.d[i]=sum.d[i];
        big.d[8]=1;
        int bbits=0;
        for(int i=15;i>=0;i--) if(big.d[i]){ bbits=i*32+32 - __builtin_clz(big.d[i]); break; }
        int mbits=u256_bitlen(m);
        if(bbits>=mbits){
            u512_t rem; u512_zero(&rem);
            for(int i=bbits-1;i>=0;--i){
                uint32_t c=0;
                for(int k=0;k<16;k++){ uint32_t nc=rem.d[k]>>31; rem.d[k]=(rem.d[k]<<1)|c; c=nc; }
                int w=i/32,b=i%32;
                uint32_t nb=(big.d[w]>>b)&1;
                rem.d[0] |= nb;
                int high_nonzero=0; for(int k=8;k<16;k++) if(rem.d[k]) high_nonzero=1;
                int ge;
                if(high_nonzero) ge=1;
                else { u256_t rem_low; for(int k=0;k<8;k++) rem_low.d[k]=rem.d[k]; ge=(u256_cmp(&rem_low,m)>=0); }
                if(ge){
                    uint64_t borrow=0;
                    for(int k=0;k<8;k++){ uint64_t sub=(uint64_t)m->d[k]+borrow; uint64_t cur=rem.d[k]; rem.d[k]=(uint32_t)(cur - sub); borrow=(cur<sub)?1:0; }
                    for(int k=8;k<16;k++){ uint64_t sub=borrow; uint64_t cur=rem.d[k]; rem.d[k]=(uint32_t)(cur - sub); borrow=(cur<sub)?1:0; }
                }
            }
            for(int i=0;i<8;i++) r->d[i]=rem.d[i];
            return;
        } else {
            for(int i=0;i<8;i++) r->d[i]=big.d[i];
            return;
        }
    }
    if(u256_cmp(&sum,m)>=0){
        u256_sub(r,&sum,m);
    } else {
        *r=sum;
    }
}

void u256_mulmod(u256_t *r, const u256_t *a, const u256_t *b, const u256_t *m){
    if(u256_is_zero(m)){ u256_zero(r); return; }
    u512_t pr; u256_mul512(&pr,a,b);
    if(u512_is_zero(&pr)){ u256_zero(r); return; }
    int pbits=0;
    for(int i=15;i>=0;i--) if(pr.d[i]){ pbits=i*32+32 - __builtin_clz(pr.d[i]); break; }
    int mbits=u256_bitlen(m);
    if(pbits < mbits){
        for(int i=0;i<8;i++) r->d[i]=pr.d[i];
        return;
    }
    u512_t rem; u512_zero(&rem);
    for(int i=pbits-1;i>=0;--i){
        uint32_t c=0;
        for(int k=0;k<16;k++){ uint32_t nc=rem.d[k]>>31; rem.d[k]=(rem.d[k]<<1)|c; c=nc; }
        int w=i/32,bb=i%32;
        uint32_t nb=(pr.d[w]>>bb)&1;
        rem.d[0] |= nb;
        int high_nonzero=0; for(int k=8;k<16;k++) if(rem.d[k]) high_nonzero=1;
        int ge;
        if(high_nonzero) ge=1;
        else { u256_t rl; for(int k=0;k<8;k++) rl.d[k]=rem.d[k]; ge=(u256_cmp(&rl,m)>=0); }
        if(ge){
            uint64_t borrow=0;
            for(int k=0;k<8;k++){ uint64_t sub=(uint64_t)m->d[k]+borrow; uint64_t cur=rem.d[k]; rem.d[k]=(uint32_t)(cur-sub); borrow=(cur<sub)?1:0; }
            for(int k=8;k<16;k++){ uint64_t sub=borrow; uint64_t cur=rem.d[k]; rem.d[k]=(uint32_t)(cur-sub); borrow=(cur<sub)?1:0; }
        }
    }
    for(int i=0;i<8;i++) r->d[i]=rem.d[i];
}

void u256_exp(u256_t *r, const u256_t *base, const u256_t *exp){
    u256_t res; u256_one(&res);
    u256_t b=*base;
    u256_t e=*exp;
    while(!u256_is_zero(&e)){
        if(e.d[0]&1){
            u256_t tmp; u256_mul(&tmp,&res,&b); res=tmp;
        }
        u256_t tmp2; u256_mul(&tmp2,&b,&b); b=tmp2;
        uint32_t c=0;
        for(int i=7;i>=0;i--){ uint32_t nc=e.d[i]&1; e.d[i]=(e.d[i]>>1)|(c<<31); c=nc; }
    }
    *r=res;
}

void u256_shl(u256_t *r, const u256_t *a, uint32_t shift){
    u256_zero(r);
    if(shift>=256) return;
    uint32_t ws=shift/32, bs=shift%32;
    for(int i=7;i>=0;i--){
        int src = i - (int)ws;
        if(src<0) continue;
        uint32_t v = a->d[src] << bs;
        if(bs && src>0) v |= a->d[src-1] >> (32-bs);
        r->d[i]=v;
    }
}
void u256_shr(u256_t *r, const u256_t *a, uint32_t shift){
    u256_zero(r);
    if(shift>=256) return;
    uint32_t ws=shift/32, bs=shift%32;
    for(int i=0;i<8;i++){
        int src = i + (int)ws;
        if(src>=8) continue;
        uint32_t v = a->d[src] >> bs;
        if(bs && src<7) v |= a->d[src+1] << (32-bs);
        r->d[i]=v;
    }
}
void u256_sar(u256_t *r, const u256_t *a, uint32_t shift){
    u256_shr(r,a,shift);
    if(u256_is_negative(a) && shift>=1 && shift<256){
        for(uint32_t i=0;i<shift;i++){
            int word=(255 - i)/32;
            int bit=(255 - i)%32;
            r->d[word] |= (1u<<bit);
        }
    } else if(u256_is_negative(a) && shift>=256){
        for(int i=0;i<8;i++) r->d[i]=0xFFFFFFFFu;
    }
}
void u256_signextend(u256_t *r, const u256_t *a, uint32_t b){
    *r=*a;
    if(b>=31) return;
    int bit = (b+1)*8 -1;
    int word = bit/32;
    int pos = bit%32;
    uint32_t mask = 1u<<pos;
    int is_neg = (a->d[word] & mask) !=0;
    if(is_neg){
        for(int i=word;i<8;i++){
            int start = (i==word)? pos+1 : 0;
            for(int j=start;j<32;j++) r->d[i] |= (1u<<j);
        }
    } else {
        for(int i=word;i<8;i++){
            int start = (i==word)? pos+1 : 0;
            for(int j=start;j<32;j++) r->d[i] &= ~(1u<<j);
        }
    }
}
uint32_t u256_byte(const u256_t *a, uint32_t i){
    if(i>=32) return 0;
    uint32_t w = i/4;
    uint32_t sh = (i%4)*8;
    return (a->d[w]>>sh)&0xFF;
}
int u256_bitlen(const u256_t *a){
    for(int i=7;i>=0;i--) if(a->d[i]) return i*32 + 32 - __builtin_clz(a->d[i]);
    return 0;
}

// parsing
static int hexval(char c){
    if(c>='0'&&c<='9') return c-'0';
    if(c>='a'&&c<='f') return c-'a'+10;
    if(c>='A'&&c<='F') return c-'A'+10;
    return -1;
}
int u256_parse_hex(u256_t *r, const char *s){
    while(isspace((unsigned char)*s)) s++;
    if(s[0]=='0'&&(s[1]=='x'||s[1]=='X')) s+=2;
    size_t len=0;
    const char *p=s;
    while(hexval(*p)>=0) { len++; p++; }
    while(isspace((unsigned char)*p)) p++;
    if(*p) return -1; // trailing garbage (the u256 tokenizer pre-validates)
    if(len==0){ u256_zero(r); return 0; }
    if(len>64) return -1;
    u256_zero(r);
    // 8 nibbles per step: accumulate one limb, then one whole-value shift.
    // (Per-nibble shifts cost ~22k for 64 nibbles; this is ~2k.)
    size_t pos=0, head=len%8;
    if(head==0) head=8;
    for(;;){
        uint32_t w=0;
        for(size_t k=0;k<head;k++) w=(w<<4)|(uint32_t)hexval(s[pos++]);
        u256_t tmp; u256_shl(&tmp, r, (uint32_t)(head*4)); *r=tmp;
        r->d[0] |= w;
        if(pos>=len) break;
        head=8;
    }
    return 0;
}
// r = r*mul + add (mod 2^256) with single-limb operands: 8 MULs, no divmod.
void u256_macc_small(u256_t *r, uint32_t mul, uint32_t add){
    uint64_t carry=add;
    for(int i=0;i<8;i++){ uint64_t t=(uint64_t)r->d[i]*mul+carry; r->d[i]=(uint32_t)t; carry=t>>32; }
}
int u256_dec_fits(const char *s){
    while(isspace((unsigned char)*s)) s++;
    if(*s=='-'||*s=='+') s++;
    while(*s=='0') s++;
    size_t n=0; const char *p=s;
    while(isdigit((unsigned char)*p)){ n++; p++; }
    if(n==0) return 1;
    if(n>78) return 0;
    if(n<78) return 1;
    return strncmp(s, "115792089237316195423570985008687907853269984665640564039457584007913129639935", 78) <= 0;
}

int u256_parse_dec(u256_t *r, const char *s){
    static const uint32_t POW10[10]={1,10,100,1000,10000,100000,1000000,10000000,100000000,1000000000};
    while(isspace((unsigned char)*s)) s++;
    int neg=0;
    if(*s=='-'){ neg=1; s++; }
    else if(*s=='+') s++;
    if(!isdigit((unsigned char)*s)) return -1;
    u256_zero(r);
    // 9 digits per step: one single-limb multiply-accumulate, not one u256 mul per digit.
    uint32_t chunk=0; int n=0;
    while(isdigit((unsigned char)*s)){
        chunk = chunk*10 + (uint32_t)(*s - '0'); n++; s++;
        if(n==9){ u256_macc_small(r, 1000000000u, chunk); chunk=0; n=0; }
    }
    if(n) u256_macc_small(r, POW10[n], chunk);
    while(isspace((unsigned char)*s)) s++;
    if(*s) return -1;
    if(neg){
        u256_t nr; u256_neg(&nr,r); *r=nr;
    }
    return 0;
}
int u256_try_parse_auto(u256_t *r, const char *s){
    while(isspace((unsigned char)*s)) s++;
    if(s[0]=='0'&&(s[1]=='x'||s[1]=='X')) return u256_parse_hex(r,s);
    return u256_parse_dec(r,s);
}
void u256_to_hex(const u256_t *a, char out[65]){
    const char *hexd="0123456789abcdef";
    for(int i=7;i>=0;i--){
        uint32_t w=a->d[i];
        for(int j=3;j>=0;j--){
            uint32_t b=(w>>(j*8))&0xFF;
            int idx = (7-i)*8 + (3-j)*2;
            out[idx]=hexd[b>>4];
            out[idx+1]=hexd[b&0xF];
        }
    }
    out[64]=0;
}
void u256_to_hex_trim(const u256_t *a, char *out){
    char tmp[65]; u256_to_hex(a,tmp);
    int first_nonzero=0;
    while(first_nonzero<64 && tmp[first_nonzero]=='0') first_nonzero++;
    out[0]='0'; out[1]='x';
    // Byte loop, not strcpy: picolibc's strcpy uses word loads/stores and the
    // machine faults on misaligned LW/SW; out+2 is rarely word-aligned.
    int p=2;
    if(first_nonzero==64){ out[p++]='0'; }
    else { for(int i=first_nonzero;i<64;i++) out[p++]=tmp[i]; }
    out[p]=0;
}
void u256_to_dec(const u256_t *a, char *out){
    if(u256_is_zero(a)){ out[0]='0'; out[1]=0; return; }
    // 9 digits per step: divide by 10^9 (single-limb divisor: 8 DIVU/REMU) at most
    // 9 times (2^256 < 10^78), then expand groups. ~1-2k instructions, not ~230k.
    u256_t v=*a;
    uint32_t groups[9]; int ng=0;
    while(!u256_is_zero(&v)){
        u256_t q; groups[ng++] = u256_divmod_small(&q, &v, 1000000000u);
        v=q;
    }
    char *p=out;
    uint32_t g=groups[ng-1]; // most significant group: no padding
    char tmp[10]; int n=0;
    do { tmp[n++]=(char)('0'+g%10); g/=10; } while(g);
    while(n) *p++=tmp[--n];
    for(int i=ng-2;i>=0;i--){
        uint32_t gg=groups[i];
        for(int k=8;k>=0;k--){ p[k]=(char)('0'+gg%10); gg/=10; }
        p+=9;
    }
    *p=0;
}
void u256_to_dec_signed(const u256_t *a, char *out){
    if(!u256_is_negative(a)){ u256_to_dec(a,out); return; }
    u256_t n; u256_neg(&n,a);
    out[0]='-';
    u256_to_dec(&n,out+1);
}
uint32_t u256_divmod_small(u256_t *q, const u256_t *a, uint32_t divisor){
    uint64_t rem=0;
    u256_t qq; u256_zero(&qq);
    for(int i=7;i>=0;i--){
        uint64_t cur = (rem<<32) | a->d[i];
        uint32_t qd = (uint32_t)(cur / divisor);
        rem = cur % divisor;
        qq.d[i]=qd;
    }
    if(q) *q=qq;
    return (uint32_t)rem;
}
