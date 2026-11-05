#include "abi.h"
#include "util.h"
#include "keccak.h"
#include "selectors.h"
#include <string.h>
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>

// Strict ABI codec (v2 scope).
//
// Accepted type grammar (canonical only, matching eth_abi's registry):
//   address | bool | bytes | string | uint | int |
//   uint<B> | int<B>   (B in 8,16,..,256, canonical digits) |
//   bytes<N>           (N in 1..32, canonical digits) |
//   T[N] | T[]         (N in 1..64, canonical digits; T any accepted type) |
//   (T1,..,Tn)         (n >= 1; nested)
// No whitespace inside a type. Nesting deeper than 16 levels is "too deep".
// fixed/ufixed (anywhere, including nested) is well-formed but out of scope:
// "unsupported type". Everything else is malformed: "bad type".
//
// Value syntax (encode args): scalars as before; arrays [v1,..,vn] (empty
// [] only for dynamic); tuples (v1,..,vn). Element splitting is
// bracket/paren/quote aware with backslash escapes. Inside nested values a
// ONE layer of matching quotes is stripped and these escapes processed:
// \" \' \\ \n \r \t \xNN (a backslash before anything else, or trailing,
// stays literal). Top-level scalar args are raw exactly as before.
//
// Decode output: top-level scalars print as before; a top-level fixed array
// of static scalars prints one line per element (as before); every other
// composite prints one bracketed line ([..] for arrays, (..) for tuples).
// Nested strings print double-quoted with the escapes above (plus \xNN for
// other control bytes); top-level strings print raw as before.
//
// Error precedence (encode): types in order, head capacity, then static
// top-level args in order (depth-first inside each), then dynamic top-level
// args in order (depth-first inside each, capacity checked per append).
// Decode validates everything depth-first in order, then formats; "out too
// small" therefore always loses to a value error.

static void trim(char *s){ char *a=s; while(isspace((unsigned char)*a)) a++; if(a!=s) memmove(s,a,strlen(a)+1); char *e=s+strlen(s); while(e>s && isspace((unsigned char)e[-1])){ e[-1]=0; e--; } }

static void set_err(char *errbuf, size_t errlen, const char *reason){
    if(errbuf && errlen) snprintf(errbuf,errlen,"%s",reason);
}

static int is_id_start(char c){ return (c>='A'&&c<='Z')||(c>='a'&&c<='z')||c=='_'; }
static int is_id_char(char c){ return is_id_start(c)||(c>='0'&&c<='9'); }

// Canonical non-negative integer: nonempty all-digits, no leading zero.
// Saturates huge values so overlong digit runs fail range checks, not parsing.
static int canon_num(const char *s, long *out){
    if(!s || !*s) return 0;
    if(s[0]=='0') return 0; // covers "0", "00", "08"
    long v=0;
    for(const char *p=s; *p; p++){
        if(*p<'0' || *p>'9') return 0;
        if(v > 1000000000L) v = 1000000000L;
        else v = v*10 + (*p-'0');
    }
    *out=v;
    return 1;
}

// Decimal magnitude check: skip spaces, one sign, zeros; the digit run must
// be <= 2^256-1. No digits at all (hex, empty, garbage) counts as fits;
// the parse step rejects those.
// Strict UTF-8 (matches Python's decoder: rejects overlongs, surrogates,
// >U+10FFFF). 0 valid, -1 invalid.
static int utf8_valid(const uint8_t *s, size_t n){
    size_t i=0;
    while(i<n){
        uint8_t c=s[i];
        size_t need;
        if(c<0x80){ i++; continue; }
        else if((c&0xE0)==0xC0){ need=1; if(c<0xC2) return -1; }
        else if((c&0xF0)==0xE0){ need=2; }
        else if((c&0xF8)==0xF0){ need=3; if(c>0xF4) return -1; }
        else return -1;
        if(i+need>=n) return -1;
        for(size_t k=1;k<=need;k++) if((s[i+k]&0xC0)!=0x80) return -1;
        uint32_t cp;
        if(need==1) cp=((uint32_t)(c&0x1F)<<6)|(s[i+1]&0x3F);
        else if(need==2) cp=((uint32_t)(c&0x0F)<<12)|((uint32_t)(s[i+1]&0x3F)<<6)|(s[i+2]&0x3F);
        else cp=((uint32_t)(c&0x07)<<18)|((uint32_t)(s[i+1]&0x3F)<<12)|((uint32_t)(s[i+2]&0x3F)<<6)|(s[i+3]&0x3F);
        if(cp>0x10FFFF || (cp>=0xD800&&cp<=0xDFFF)) return -1;
        if(need==1 && cp<0x80) return -1;
        if(need==2 && cp<0x800) return -1;
        if(need==3 && cp<0x10000) return -1;
        i+=1+need;
    }
    return 0;
}

// --- Type classification -----------------------------------------------
// Node pool: one type token is <= 63 chars, so a handful of nodes; the pool
// is per-parse (callers re-parse per use, keeping only head_slots/dyn).

typedef enum { TY_UINT, TY_INT, TY_ADDR, TY_BOOL, TY_BYTESN, TY_BYTES, TY_STR, TY_ARR, TY_TUP } tyc_t;
typedef struct {
    tyc_t c;
    int num;     // uint/int bits | bytesN | array length (-1 dynamic)
    int dyn;     // 1 if dynamic
    int elem;    // TY_ARR: child node
    int cfirst;  // TY_TUP: first slot in the kid pool
    int nchild;  // TY_TUP: child count
} tyn_t;
#define ABI_POOL 96
#define ABI_MAX_DEPTH 16
#define ABI_ARR_CAP 64
typedef struct { tyn_t n[ABI_POOL]; int nn; int kids[ABI_POOL]; int nk; } tpool_t;

static int bits_ok(long b){ return b>=8 && b<=256 && b%8==0; }

// Any fixed-point token anywhere (letter-word exactly u?fixed with optional
// canonical MxN). Checked after the whitespace scan, before parsing.
static int has_fixed(const char *t){
    for(const char *p=t; *p; ){
        if((*p>='A'&&*p<='Z')||(*p>='a'&&*p<='z')){
            const char *s=p;
            while((*p>='A'&&*p<='Z')||(*p>='a'&&*p<='z')) p++;
            const char *q=s;
            if(*q=='u') q++;
            if((size_t)(p-q)>=5 && memcmp(q,"fixed",5)==0){
                const char *r=q+5;
                if(r==p) return 1;
                if(*r>='1'&&*r<='9'){
                    while(*r>='0'&&*r<='9') r++;
                    if(*r=='x'){
                        r++;
                        if(*r>='1'&&*r<='9'){
                            while(*r>='0'&&*r<='9') r++;
                            if(r==p) return 1;
                        }
                    }
                }
            }
        } else p++;
    }
    return 0;
}

static int classify_scalar(const char *t, tyc_t *c, int *num){
    if(strcmp(t,"address")==0){ *c=TY_ADDR; *num=0; return 0; }
    if(strcmp(t,"bool")==0){ *c=TY_BOOL; *num=0; return 0; }
    if(strcmp(t,"bytes")==0){ *c=TY_BYTES; *num=0; return 0; }
    if(strcmp(t,"string")==0){ *c=TY_STR; *num=0; return 0; }
    if(strcmp(t,"uint")==0){ *c=TY_UINT; *num=256; return 0; }
    if(strcmp(t,"int")==0){ *c=TY_INT; *num=256; return 0; }
    long v;
    if(strncmp(t,"uint",4)==0 && canon_num(t+4,&v) && bits_ok(v)){ *c=TY_UINT; *num=(int)v; return 0; }
    if(strncmp(t,"int",3)==0 && canon_num(t+3,&v) && bits_ok(v)){ *c=TY_INT; *num=(int)v; return 0; }
    if(strncmp(t,"bytes",5)==0 && canon_num(t+5,&v) && v>=1 && v<=32){ *c=TY_BYTESN; *num=(int)v; return 0; }
    return -1;
}

static int pool_alloc(tpool_t *pl){
    if(pl->nn>=ABI_POOL) return -1;
    return pl->nn++;
}

// Parse one type span. Returns the node index, or -1 bad / -3 too deep /
// -4 array too long. (Unsupported fixed-point is pre-scanned by the caller.)
static int parse_inner(const char *t, size_t len, tpool_t *pl, int depth){
    if(depth>ABI_MAX_DEPTH) return -3;
    if(len==0 || len>=128) return -1;
    int base=-1; // base node
    size_t rest=0; // dim suffix starts here
    if(t[0]=='('){
        int d=0; size_t mc=len; int ok=0;
        for(size_t i=0;i<len;i++){
            if(t[i]=='(') d++;
            else if(t[i]==')'){ d--; if(d<0) return -1; if(d==0){ mc=i; ok=1; break; } }
        }
        if(!ok) return -1;
        // tuple components: split (1..mc) on top-level commas
        int cstart[32]; int clen[32]; int nc=0;
        size_t cs=1; int dd=0;
        for(size_t i=1;i<mc;i++){
            char c=t[i];
            if(c=='('||c=='[') dd++;
            else if(c==')'||c==']'){ dd--; if(dd<0) return -1; }
            else if(c==','&&dd==0){
                if(nc>=32) return -1;
                cstart[nc]=(int)cs; clen[nc]=(int)(i-cs); nc++; cs=i+1;
            }
        }
        if(dd!=0) return -1;
        if(nc>=32) return -1;
        cstart[nc]=(int)cs; clen[nc]=(int)(mc-cs); nc++;
        int ni=pool_alloc(pl);
        if(ni<0) return -1;
        if(pl->nk+nc>ABI_POOL) return -1;
        // Reserve the kid slots up front: nested tuples parse into the same
        // pool and would otherwise consume our children's slots.
        pl->n[ni].c=TY_TUP; pl->n[ni].num=0;
        pl->n[ni].cfirst=pl->nk; pl->n[ni].nchild=nc;
        pl->nk+=nc;
        int dyn=0;
        for(int k=0;k<nc;k++){
            if(clen[k]<=0) return -1;
            int ci=parse_inner(t+cstart[k],(size_t)clen[k],pl,depth+1);
            if(ci<0) return ci;
            pl->kids[pl->n[ni].cfirst+k]=ci;
            if(pl->n[ci].dyn) dyn=1;
        }
        pl->n[ni].dyn=dyn; pl->n[ni].elem=-1;
        base=ni; rest=mc+1;
    } else {
        size_t be=0;
        while(be<len && t[be]!='[' && t[be]!=']' && t[be]!='(' && t[be]!=')') be++;
        char sb[128];
        if(be==0 || be>=sizeof(sb)) return -1;
        memcpy(sb,t,be); sb[be]=0;
        tyc_t c; int num=0;
        if(classify_scalar(sb,&c,&num)!=0) return -1;
        int ni=pool_alloc(pl);
        if(ni<0) return -1;
        pl->n[ni].c=c; pl->n[ni].num=num;
        pl->n[ni].dyn=(c==TY_BYTES||c==TY_STR)?1:0;
        pl->n[ni].elem=-1; pl->n[ni].cfirst=-1; pl->n[ni].nchild=0;
        base=ni; rest=be;
    }
    // dimension suffixes, outer to inner; each is one depth level
    int lvl=depth;
    size_t p=rest;
    while(p<len){
        if(t[p]!='[') return -1;
        size_t q=p+1;
        while(q<len && t[q]!=']') q++;
        if(q>=len) return -1;
        int alen=-1; // -1 dynamic
        if(q>p+1){
            char nb[32];
            if(q-p-1>=sizeof(nb)) return -1;
            memcpy(nb,t+p+1,q-p-1); nb[q-p-1]=0;
            long nv=0;
            if(!canon_num(nb,&nv)) return -1;
            if(nv>ABI_ARR_CAP) return -4;
            alen=(int)nv;
        }
        lvl++;
        if(lvl>ABI_MAX_DEPTH) return -3;
        int ni=pool_alloc(pl);
        if(ni<0) return -1;
        pl->n[ni].c=TY_ARR; pl->n[ni].num=alen; pl->n[ni].elem=base;
        pl->n[ni].cfirst=-1; pl->n[ni].nchild=0;
        pl->n[ni].dyn=(alen<0)?1:pl->n[base].dyn;
        base=ni; p=q+1;
    }
    return base;
}

// Judge + parse one type token. Returns the node index, or -1 bad type /
// -2 unsupported type / -3 too deep / -4 array too long.
static int parse_top_type(const char *t, tpool_t *pl){
    for(const char *p=t;*p;p++) if(isspace((unsigned char)*p)) return -1;
    if(has_fixed(t)) return -2;
    pl->nn=0; pl->nk=0;
    return parse_inner(t,strlen(t),pl,0);
}

static const char *parse_reason(int r){
    if(r==-2) return "unsupported type";
    if(r==-3) return "too deep";
    if(r==-4) return "array too long";
    return "bad type";
}

// Compat wrapper: 0 uint,1 int,2 address,3 bool,4 bytesN,5 array,
// 6 tuple, -2 bytes/string, -1 otherwise.
int abi_type_kind(const char *t){
    tpool_t pl;
    int ni=parse_top_type(t,&pl);
    if(ni<0) return -1;
    switch(pl.n[ni].c){
        case TY_UINT: return 0;
        case TY_INT: return 1;
        case TY_ADDR: return 2;
        case TY_BOOL: return 3;
        case TY_BYTESN: return 4;
        case TY_ARR: return 5;
        case TY_TUP: return 6;
        default: return -2;
    }
}

static int head_slots(tpool_t *pl, int ni){
    tyn_t *n=&pl->n[ni];
    if(n->dyn) return 1;
    if(n->c==TY_TUP){
        int s=0;
        for(int k=0;k<n->nchild;k++) s+=head_slots(pl,pl->kids[n->cfirst+k]);
        return s;
    }
    if(n->c==TY_ARR) return n->num*head_slots(pl,n->elem);
    return 1;
}

// --- Selector cache (unchanged) ------------------------------------------

static int selector_cache_lookup(const char *canon, uint8_t out[32]){
    int lo=0, hi=(int)SELECTOR_COUNT-1;
    while(lo<=hi){
        int mid=lo+(hi-lo)/2;
        int c=strcmp(canon, selector_table[mid].sig);
        if(c==0){ memcpy(out, selector_table[mid].hash, 32); return 0; }
        if(c<0) hi=mid-1; else lo=mid+1;
    }
    return -1;
}

uint32_t abi_selector(const char *sig){
    // canonicalize: remove whitespace
    char canon[512];
    int p=0;
    for(int i=0; sig[i] && p<511; i++) if(!isspace((unsigned char)sig[i])) canon[p++]=sig[i];
    canon[p]=0;
    uint8_t h[32];
    if(selector_cache_lookup(canon, h)!=0){
        keccak256((uint8_t*)canon, p, h);
    }
    return ((uint32_t)h[0]<<24)|((uint32_t)h[1]<<16)|((uint32_t)h[2]<<8)|h[3];
}

// --- Signature parsing (strict, depth-aware: unchanged) --------------------

int abi_parse_sig(const char *sig, char *name_out, size_t name_cap, char types[][64], int *ntypes, char *errbuf, size_t errlen){
    const char *lp=strchr(sig,'(');
    const char *rp=strrchr(sig,')');
    if(!lp || !rp || rp<lp){ set_err(errbuf,errlen,"bad sig"); return -1; }
    // name: trimmed, identifier, fits
    const char *nb=sig, *ne=lp;
    while(nb<ne && isspace((unsigned char)*nb)) nb++;
    while(ne>nb && isspace((unsigned char)ne[-1])) ne--;
    if(nb==ne || !is_id_start(*nb)){ set_err(errbuf,errlen,"bad sig"); return -1; }
    for(const char *c=nb+1;c<ne;c++) if(!is_id_char(*c)){ set_err(errbuf,errlen,"bad sig"); return -1; }
    if((size_t)(ne-nb)>=name_cap){ set_err(errbuf,errlen,"bad sig"); return -1; }
    if(name_out){ size_t nl=(size_t)(ne-nb); memcpy(name_out,nb,nl); name_out[nl]=0; }
    // inside: empty (after trim) means zero types
    const char *ib=lp+1, *ie=rp;
    while(ib<ie && isspace((unsigned char)*ib)) ib++;
    while(ie>ib && isspace((unsigned char)ie[-1])) ie--;
    if(ib==ie){ *ntypes=0; return 0; }
    // split on top-level commas; empty segments are malformed
    int cnt=0, depth=0;
    const char *seg=lp+1;
    for(const char *c=lp+1; c<=rp; c++){
        char ch=(c==rp)?0:*c;
        if(ch=='('||ch=='[') depth++;
        else if(ch==')'||ch==']') depth--;
        if((ch==','&&depth==0)||ch==0){
            const char *tb=seg, *te=c;
            while(tb<te && isspace((unsigned char)*tb)) tb++;
            while(te>tb && isspace((unsigned char)te[-1])) te--;
            if(tb==te){ set_err(errbuf,errlen,"bad sig"); return -1; }
            if(cnt>=16){ set_err(errbuf,errlen,"too many types"); return -1; }
            if((size_t)(te-tb)>=64){ set_err(errbuf,errlen,"bad type"); return -1; }
            size_t tl=(size_t)(te-tb);
            memcpy(types[cnt],tb,tl); types[cnt][tl]=0;
            cnt++;
            seg=c+1;
        }
    }
    *ntypes=cnt;
    return 0;
}

// --- Value parsing --------------------------------------------------------

static int parse_address_bytes(const char *s, uint8_t out[20]){
    while(isspace((unsigned char)*s)) s++;
    if(s[0]=='0'&&(s[1]=='x'||s[1]=='X')) s+=2;
    size_t len=strlen(s);
    while(len>0 && isspace((unsigned char)s[len-1])) len--;
    if(len!=40) return -1;
    for(int i=0;i<20;i++){
        int hi, lo;
        char a=s[i*2], b=s[i*2+1];
        if(a>='0'&&a<='9') hi=a-'0'; else if(a>='a'&&a<='f') hi=a-'a'+10; else if(a>='A'&&a<='F') hi=a-'A'+10; else return -1;
        if(b>='0'&&b<='9') lo=b-'0'; else if(b>='a'&&b<='f') lo=b-'a'+10; else if(b>='A'&&b<='F') lo=b-'A'+10; else return -1;
        out[i]=(hi<<4)|lo;
    }
    return 0;
}

// Hex body check shared by bytesN/bytes values: all-hex (empty ok), then
// odd-length, then caller's length cap. Order: validity, oddness, length.
static int check_hex_body(const char *s, size_t *len_out, char *errbuf, size_t errlen){
    const char *p=s;
    while(isspace((unsigned char)*p)) p++;
    if(p[0]=='0'&&(p[1]=='x'||p[1]=='X')) p+=2;
    size_t len=strlen(p);
    while(len>0 && isspace((unsigned char)p[len-1])) len--;
    for(size_t i=0;i<len;i++){
        char c=p[i];
        int ok=(c>='0'&&c<='9')||(c>='a'&&c<='f')||(c>='A'&&c<='F');
        if(!ok){ set_err(errbuf,errlen,"bad hex"); return -1; }
    }
    if(len%2!=0){ set_err(errbuf,errlen,"odd hex"); return -1; }
    *len_out=len;
    return 0;
}

static int hex_nib(char c){
    if(c>='0'&&c<='9') return c-'0';
    if(c>='a'&&c<='f') return c-'a'+10;
    if(c>='A'&&c<='F') return c-'A'+10;
    return -1;
}

// Encode one static scalar. The type is already judged valid.
static int encode_scalar(tyc_t c, int num, const char *arg, uint8_t *slot32, char *errbuf, size_t errlen){
    memset(slot32,0,32);
    if(c==TY_UINT){
        int bits=num;
        const char *t=arg; while(isspace((unsigned char)*t)) t++;
        if(*t=='-'){ set_err(errbuf,errlen,"uint overflow"); return -1; }
        if(t[0]=='0'&&(t[1]=='x'||t[1]=='X') && !isxdigit((unsigned char)t[2])){
            set_err(errbuf,errlen,"bad uint"); return -1; // bare "0x"
        }
        u256_t v;
        if(u256_try_parse_auto(&v, arg)!=0){ set_err(errbuf,errlen,"bad uint"); return -1; }
        if(!u256_dec_fits(arg)){ set_err(errbuf,errlen,"uint overflow"); return -1; }
        if(u256_bitlen(&v) > bits){ set_err(errbuf,errlen,"uint overflow"); return -1; }
        for(int i=0;i<8;i++){
            uint32_t w=v.d[7-i];
            slot32[i*4+0]=(w>>24)&0xFF;
            slot32[i*4+1]=(w>>16)&0xFF;
            slot32[i*4+2]=(w>>8)&0xFF;
            slot32[i*4+3]=w&0xFF;
        }
        return 0;
    } else if(c==TY_INT){
        int bits=num;
        const char *t=arg; while(isspace((unsigned char)*t)) t++;
        if(t[0]=='0'&&(t[1]=='x'||t[1]=='X') && !isxdigit((unsigned char)t[2])){
            set_err(errbuf,errlen,"bad int"); return -1; // bare "0x"
        }
        u256_t v;
        if(u256_try_parse_auto(&v, arg)!=0){ set_err(errbuf,errlen,"bad int"); return -1; }
        if(!u256_dec_fits(arg)){ set_err(errbuf,errlen,"int overflow"); return -1; }
        u256_t one, lim; u256_one(&one); u256_shl(&lim, &one, (uint32_t)(bits-1));
        const char *ts=arg; while(isspace((unsigned char)*ts)) ts++;
        if(*ts=='-'){
            u256_t mag; u256_neg(&mag, &v);
            if(u256_cmp(&mag, &lim) > 0){ set_err(errbuf,errlen,"int overflow"); return -1; }
        } else if(!(ts[0]=='0'&&(ts[1]=='x'||ts[1]=='X'))){
            // A positive decimal is a value, never two's complement: 2**255 is not int256's minimum.
            if(u256_cmp(&v, &lim) >= 0){ set_err(errbuf,errlen,"int overflow"); return -1; }
        } else {
            if(u256_cmp(&v, &lim) >= 0){
                u256_t lo; u256_neg(&lo, &lim);
                if(u256_cmp(&v, &lo) < 0){ set_err(errbuf,errlen,"int overflow"); return -1; }
            }
        }
        for(int i=0;i<8;i++){ uint32_t w=v.d[7-i]; slot32[i*4+0]=(w>>24)&0xFF; slot32[i*4+1]=(w>>16)&0xFF; slot32[i*4+2]=(w>>8)&0xFF; slot32[i*4+3]=w&0xFF; }
        return 0;
    } else if(c==TY_ADDR){
        uint8_t addr[20];
        if(parse_address_bytes(arg, addr)!=0){ set_err(errbuf,errlen,"bad address"); return -1; }
        memcpy(slot32+12, addr,20);
        return 0;
    } else if(c==TY_BOOL){
        const char *b=arg;
        while(isspace((unsigned char)*b)) b++;
        size_t bl=strlen(b);
        while(bl>0 && isspace((unsigned char)b[bl-1])) bl--;
        uint8_t v=0;
        if(bl==4 && memcmp(b,"true",4)==0) v=1;
        else if(bl==1 && b[0]=='1') v=1;
        else if(bl==5 && memcmp(b,"false",5)==0) v=0;
        else if(bl==1 && b[0]=='0') v=0;
        else { set_err(errbuf,errlen,"bad bool"); return -1; }
        slot32[31]=v;
        return 0;
    } else if(c==TY_BYTESN){
        int n=num;
        size_t len=0;
        // re-derive the body start for the copy loop
        const char *s=arg;
        while(isspace((unsigned char)*s)) s++;
        if(s[0]=='0'&&(s[1]=='x'||s[1]=='X')) s+=2;
        if(check_hex_body(arg,&len,errbuf,errlen)!=0) return -1;
        if(len != (size_t)n*2){ set_err(errbuf,errlen,"bad bytes"); return -1; }
        // exactly N bytes: a short value is an error, not a guess at its alignment
        for(size_t i=0;i<len/2;i++) slot32[i]=(hex_nib(s[i*2])<<4)|hex_nib(s[i*2+1]);
        return 0;
    }
    set_err(errbuf,errlen,"bad type");
    return -1;
}

// --- Nested value splitting ------------------------------------------------
// Top-level comma split with bracket/paren depth and quote+escape awareness.
// A backslash quotes the next byte for splitting purposes. Lenient:
// unterminated quotes/brackets run to the end; depth never goes negative.
static int tsp_next(const char *s, size_t len, size_t *pos, const char **sp, size_t *sn){
    if(*pos>=len) return 0;
    size_t start=*pos, i=start;
    int depth=0; char quote=0;
    for(;i<len;i++){
        char c=s[i];
        if(quote){
            if(c=='\\'){ i++; continue; }
            if(c==quote) quote=0;
            continue;
        }
        if(c=='\\'){ i++; continue; }
        if(c=='"'||c=='\''){ quote=c; continue; }
        if(c=='['||c=='('){ depth++; continue; }
        if(c==']'||c==')'){ if(depth>0) depth--; continue; }
        if(c==','&&depth==0){ *sp=s+start; *sn=i-start; *pos=i+1; return 1; }
    }
    *sp=s+start; *sn=len-start; *pos=len; return 1;
}

#define ABI_SPAN_OVER (ABI_ARR_CAP+2) // counts past the cap: "too many"

// Count spans in a pre-trimmed interior; empty interior is zero spans.
static int count_spans(const char *s, size_t len){
    if(len==0) return 0;
    size_t pos=0; int n=0;
    const char *sp; size_t sn;
    while(tsp_next(s,len,&pos,&sp,&sn)){ n++; if(n>ABI_SPAN_OVER) return ABI_SPAN_OVER; }
    return n;
}

static void trim_span(const char **p, size_t *n){
    while(*n>0 && isspace((unsigned char)(*p)[0])){ (*p)++; (*n)--; }
    while(*n>0 && isspace((unsigned char)(*p)[*n-1])) (*n)--;
}

static void strip1_span(const char **p, size_t *n){
    if(*n>=2 && ((*p)[0]=='"'||(*p)[0]=='\'') && (*p)[*n-1]==(*p)[0]){ (*p)++; *n-=2; }
}

// Unescape into out (caller guarantees stripped length < 512; every escape
// consumes >= 2 input chars for <= 2 output bytes, so out[513] always fits).
// \xNN denotes code point U+00NN, UTF-8 encoded like Python's '\xNN'.
// Total: never fails. Reports the output byte length (NULs preserved).
static void unescape_span(const char *p, size_t n, char *out, size_t *olen){
    size_t o=0;
    for(size_t i=0;i<n;i++){
        char c=p[i];
        if(c=='\\' && i+1<n){
            char d=p[i+1];
            if(d=='n'){ out[o++]='\n'; i++; }
            else if(d=='r'){ out[o++]='\r'; i++; }
            else if(d=='t'){ out[o++]='\t'; i++; }
            else if(d=='x' && i+3<n && hex_nib(p[i+2])>=0 && hex_nib(p[i+3])>=0){
                unsigned v=(unsigned)((hex_nib(p[i+2])<<4)|hex_nib(p[i+3])); i+=3;
                if(v<0x80) out[o++]=(char)v;
                else { out[o++]=(char)(0xC0|(v>>6)); out[o++]=(char)(0x80|(v&0x3F)); }
            }
            else if(d=='"'||d=='\''||d=='\\'){ out[o++]=d; i++; }
            else { out[o++]='\\'; }
        } else { out[o++]=c; }
    }
    out[o]=0; *olen=o;
}

// Nested scalar span -> text. nul_reason: lexical error when the output
// contains a NUL (scalar kinds); NULL allows NULs (bytes/string carry the
// explicit length). 0 ok, -1 elem too long or NUL-lexical.
static int proc_scalar_span(const char *p, size_t n, int parent_is_tup, const char *nul_reason,
                            char *obuf, size_t *olen, char *errbuf, size_t errlen){
    trim_span(&p,&n);
    strip1_span(&p,&n);
    if(n>=512){ set_err(errbuf,errlen,parent_is_tup?"tuple elem too long":"array elem too long"); return -1; }
    unescape_span(p,n,obuf,olen);
    if(nul_reason){
        for(size_t k=0;k<*olen;k++) if(obuf[k]==0){ set_err(errbuf,errlen,nul_reason); return -1; }
    }
    return 0;
}

static const char *nul_lexical(tyc_t c){
    if(c==TY_UINT) return "bad uint";
    if(c==TY_INT) return "bad int";
    if(c==TY_ADDR) return "bad address";
    if(c==TY_BOOL) return "bad bool";
    if(c==TY_BYTESN) return "bad hex";
    return NULL; // bytes/string: NULs are content
}

// --- Encode calldata -------------------------------------------------------
// Heads-then-tails per level; offsets relative to the enclosing level's
// start (frame_base). Static top-level args encode in order first, so a bad
// static later in order still wins over a bad dynamic earlier in order.

static int enc_static(tpool_t *pl, int ni, const char *t, size_t n, int parent_is_tup, int nostrip,
                      uint8_t *out, size_t cap, size_t *pos, char *errbuf, size_t errlen);
static int enc_level(tpool_t *pl, int ni, const char *in, size_t ilen, int count, size_t frame_base,
                     uint8_t *out, size_t cap, size_t *pos, size_t *tail, char *errbuf, size_t errlen);
static int enc_tail(tpool_t *pl, int ni, const char *t, size_t n, int top, int parent_is_tup, size_t tc,
                    uint8_t *out, size_t cap, size_t *tail, char *errbuf, size_t errlen);
static int enc_composite_tail(tpool_t *pl, int ni, const char *t, size_t n, size_t tc,
                              uint8_t *out, size_t cap, size_t *tail, char *errbuf, size_t errlen);

static void write_u32_slot(uint8_t *dst, uint32_t v){
    memset(dst,0,32);
    dst[28]=(v>>24)&0xFF; dst[29]=(v>>16)&0xFF; dst[30]=(v>>8)&0xFF; dst[31]=v&0xFF;
}

static int enc_static_scalar(tyn_t *nd, const char *t, size_t n, int parent_is_tup,
                             uint8_t *out, size_t cap, size_t *pos, char *errbuf, size_t errlen){
    char tbuf[513]; size_t tlen=0;
    if(proc_scalar_span(t,n,parent_is_tup,nul_lexical(nd->c),tbuf,&tlen,errbuf,errlen)!=0) return -1;
    uint8_t slot[32];
    if(encode_scalar(nd->c,nd->num,tbuf,slot,errbuf,errlen)!=0) return -1;
    if(*pos+32>cap){ set_err(errbuf,errlen,"out too small"); return -1; }
    memcpy(out+*pos,slot,32); *pos+=32; return 0;
}

static int enc_static(tpool_t *pl, int ni, const char *t, size_t n, int parent_is_tup, int nostrip,
                      uint8_t *out, size_t cap, size_t *pos, char *errbuf, size_t errlen){
    tyn_t *nd=&pl->n[ni];
    if(nd->c!=TY_ARR && nd->c!=TY_TUP)
        return enc_static_scalar(nd,t,n,parent_is_tup,out,cap,pos,errbuf,errlen);
    trim_span(&t,&n);
    if(!nostrip) strip1_span(&t,&n);
    int is_arr=(nd->c==TY_ARR);
    if(n<2 || t[0]!=(is_arr?'[':'(') || t[n-1]!=(is_arr?']':')')){
        set_err(errbuf,errlen,is_arr?"bad array":"bad tuple"); return -1;
    }
    const char *in=t+1; size_t ilen=n-2;
    trim_span(&in,&ilen);
    int count=(ilen==0)?0:count_spans(in,ilen);
    if(is_arr){
        if(count>ABI_ARR_CAP){ set_err(errbuf,errlen,"array too long"); return -1; }
        if(count!=nd->num){ set_err(errbuf,errlen,"array len"); return -1; }
    } else {
        if(count!=nd->nchild){ set_err(errbuf,errlen,"tuple len"); return -1; }
    }
    size_t dummy=*pos;
    return enc_level(pl,ni,in,ilen,count,*pos,out,cap,pos,&dummy,errbuf,errlen);
}

static int enc_level(tpool_t *pl, int ni, const char *in, size_t ilen, int count, size_t frame_base,
                     uint8_t *out, size_t cap, size_t *pos, size_t *tail, char *errbuf, size_t errlen){
    tyn_t *nd=&pl->n[ni];
    int is_tup=(nd->c==TY_TUP);
    size_t head_start=*pos;
    size_t ppos=0; const char *sp; size_t sn;
    for(int k=0;k<count;k++){
        tsp_next(in,ilen,&ppos,&sp,&sn);
        int ci=is_tup?pl->kids[nd->cfirst+k]:nd->elem;
        if(!pl->n[ci].dyn){
            if(enc_static(pl,ci,sp,sn,is_tup,0,out,cap,pos,errbuf,errlen)!=0) return -1;
        } else {
            if(*pos+32>cap){ set_err(errbuf,errlen,"out too small"); return -1; }
            memset(out+*pos,0,32); *pos+=32;
        }
    }
    *tail=*pos;
    ppos=0;
    for(int k=0;k<count;k++){
        tsp_next(in,ilen,&ppos,&sp,&sn);
        int ci=is_tup?pl->kids[nd->cfirst+k]:nd->elem;
        if(!pl->n[ci].dyn) continue;
        size_t h=0;
        for(int j=0;j<k;j++){
            int cj=is_tup?pl->kids[nd->cfirst+j]:nd->elem;
            h+=(size_t)head_slots(pl,cj);
        }
        size_t slot=head_start+h*32;
        size_t tc=*tail;
        uint32_t off=(uint32_t)(tc-frame_base);
        out[slot+28]=(off>>24)&0xFF; out[slot+29]=(off>>16)&0xFF;
        out[slot+30]=(off>>8)&0xFF; out[slot+31]=off&0xFF;
        if(enc_tail(pl,ci,sp,sn,0,is_tup,tc,out,cap,tail,errbuf,errlen)!=0) return -1;
    }
    return 0;
}

static int enc_bytes_tail(const char *text, size_t tlen, int is_str,
                          uint8_t *out, size_t cap, size_t *tail, char *errbuf, size_t errlen); // fwd

// Split out so recursive composite frames never carry the 513-byte scalar
// scratch (same wrap fix as the 8KB hex scratch below): composite levels
// recurse through enc_tail -> enc_composite_tail only. noinline: a single
// caller would otherwise inline the scratch back into the recursive frame.
__attribute__((noinline))
static int enc_scalar_tail(tpool_t *pl, int ni, const char *t, size_t n, int top, int parent_is_tup,
                           uint8_t *out, size_t cap, size_t *tail, char *errbuf, size_t errlen){
    tyn_t *nd=&pl->n[ni];
    const char *text=t; size_t tlen=n; char tbuf[513];
    if(!top){
        if(proc_scalar_span(t,n,parent_is_tup,NULL,tbuf,&tlen,errbuf,errlen)!=0) return -1;
        text=tbuf;
    } else {
        tlen=strlen(text);
    }
    return enc_bytes_tail(text, tlen, nd->c==TY_STR, out, cap, tail, errbuf, errlen);
}

static int enc_tail(tpool_t *pl, int ni, const char *t, size_t n, int top, int parent_is_tup, size_t tc,
                    uint8_t *out, size_t cap, size_t *tail, char *errbuf, size_t errlen){
    tyn_t *nd=&pl->n[ni];
    if(nd->c==TY_BYTES || nd->c==TY_STR)
        return enc_scalar_tail(pl,ni,t,n,top,parent_is_tup,out,cap,tail,errbuf,errlen);
    trim_span(&t,&n);
    if(!top) strip1_span(&t,&n);
    return enc_composite_tail(pl, ni, t, n, tc, out, cap, tail, errbuf, errlen);
}

// Split out so recursive composite frames never carry the 8KB hex scratch.
// Explicit length throughout: nested text may hold NULs (content for
// strings, "bad hex" for bytes).
static int enc_bytes_tail(const char *text, size_t tlen, int is_str,
                          uint8_t *out, size_t cap, size_t *tail, char *errbuf, size_t errlen){
        size_t data_len=0;
        const uint8_t *data_bytes=NULL;
        // 2048, not 8192: the longest hex that can arrive is ~4093 chars
        // (file_buf 4K, one pair), i.e. 2046 bytes. The old 8KB frame plus
        // handle_encode's ~12KB wrapped the 32KB stack on nested composites
        // in the bridge image (store-out-of-range fault). "bytes too long"
        // stays unreachable on every path.
        uint8_t tmp[2048];
        if(is_str){
            data_len=tlen;
            data_bytes=(const uint8_t*)text;
        } else {
            const char *s=text; size_t slen=tlen;
            while(slen>0 && isspace((unsigned char)*s)){ s++; slen--; }
            if(slen>=2 && s[0]=='0'&&(s[1]=='x'||s[1]=='X')){ s+=2; slen-=2; }
            while(slen>0 && isspace((unsigned char)s[slen-1])) slen--;
            for(size_t k=0;k<slen;k++){
                char c=s[k];
                int ok=(c>='0'&&c<='9')||(c>='a'&&c<='f')||(c>='A'&&c<='F');
                if(!ok){ set_err(errbuf,errlen,"bad hex"); return -1; }
            }
            if(slen%2!=0){ set_err(errbuf,errlen,"odd hex"); return -1; }
            data_len=slen/2;
            if(data_len>sizeof(tmp)){ set_err(errbuf,errlen,"bytes too long"); return -1; }
            for(size_t k=0;k<data_len;k++) tmp[k]=(hex_nib(s[k*2])<<4)|hex_nib(s[k*2+1]);
            data_bytes=tmp;
        }
        size_t padded=(data_len+31)&~((size_t)31);
        if(*tail+32+padded>cap){ set_err(errbuf,errlen,"out too small"); return -1; }
        write_u32_slot(out+*tail,(uint32_t)data_len);
        *tail+=32;
        memcpy(out+*tail, data_bytes, data_len);
        memset(out+*tail+data_len,0,padded-data_len);
        *tail+=padded;
        return 0;
}

static int enc_composite_tail(tpool_t *pl, int ni, const char *t, size_t n, size_t tc,
                              uint8_t *out, size_t cap, size_t *tail, char *errbuf, size_t errlen){
    tyn_t *nd=&pl->n[ni];
    int is_arr=(nd->c==TY_ARR);
    if(n<2 || t[0]!=(is_arr?'[':'(') || t[n-1]!=(is_arr?']':')')){
        set_err(errbuf,errlen,is_arr?"bad array":"bad tuple"); return -1;
    }
    const char *in=t+1; size_t ilen=n-2;
    trim_span(&in,&ilen);
    int count=(ilen==0)?0:count_spans(in,ilen);
    size_t cbase=tc;
    if(is_arr){
        if(count>ABI_ARR_CAP){ set_err(errbuf,errlen,"array too long"); return -1; }
        if(nd->num>=0 && count!=nd->num){ set_err(errbuf,errlen,"array len"); return -1; }
        if(nd->num<0){
            // Only dynamic arrays carry a length slot; element offsets are
            // then relative to the element sequence (after the slot).
            if(*tail+32>cap){ set_err(errbuf,errlen,"out too small"); return -1; }
            write_u32_slot(out+*tail,(uint32_t)count);
            *tail+=32;
            cbase=tc+32;
        }
    } else {
        if(count!=nd->nchild){ set_err(errbuf,errlen,"tuple len"); return -1; }
    }
    return enc_level(pl,ni,in,ilen,count,cbase,out,cap,tail,tail,errbuf,errlen);
}

int abi_encode_calldata(const char *sig, const char **args, int nargs, uint8_t *out, size_t out_cap, size_t *out_len, char *errbuf, size_t errlen){
    char name[128]; char types[16][64]; int ntypes=0;
    if(abi_parse_sig(sig, name, sizeof(name), types, &ntypes, errbuf, errlen)!=0) return -1;
    if(ntypes != nargs){ set_err(errbuf,errlen,"arg count"); return -1; }
    if(out_cap < 4){ set_err(errbuf,errlen,"out too small"); return -1; }
    // Gate all types first, in order; keep only head size + dynamic flag.
    int thy[16]; int tdyn[16];
    for(int i=0;i<ntypes;i++){
        tpool_t pl;
        int ni=parse_top_type(types[i],&pl);
        if(ni<0){ set_err(errbuf,errlen,parse_reason(ni)); return -1; }
        thy[i]=head_slots(&pl,ni); tdyn[i]=pl.n[ni].dyn;
    }
    uint32_t sel=abi_selector(sig);
    out[0]=(sel>>24)&0xFF; out[1]=(sel>>16)&0xFF; out[2]=(sel>>8)&0xFF; out[3]=sel&0xFF;
    size_t head=0;
    for(int i=0;i<ntypes;i++) head+=(size_t)thy[i];
    size_t head_end = 4 + head*32;
    if(head_end > out_cap){ set_err(errbuf,errlen,"out too small"); return -1; }
    size_t off=4, tail_off=head_end;
    // First pass: static args in order (dynamic values deferred to the tail
    // pass, so a bad static later in order wins).
    for(int i=0;i<ntypes;i++){
        if(tdyn[i]){
            memset(out+off,0,32);
            off+=32;
            continue;
        }
        tpool_t pl;
        int ni=parse_top_type(types[i],&pl);
        tyn_t *nd=&pl.n[ni];
        if(nd->c!=TY_ARR && nd->c!=TY_TUP){
            uint8_t slot[32];
            if(encode_scalar(nd->c,nd->num,args[i],slot,errbuf,errlen)!=0) return -1;
            memcpy(out+off,slot,32);
            off+=32;
        } else {
            size_t alen=strlen(args[i]);
            if(enc_static(&pl,ni,args[i],alen,0,1,out,out_cap,&off,errbuf,errlen)!=0) return -1;
        }
    }
    // Tail pass: dynamic args in order.
    for(int i=0;i<ntypes;i++){
        if(!tdyn[i]) continue;
        size_t h=0;
        for(int j=0;j<i;j++) h+=(size_t)thy[j];
        size_t slot=4+h*32;
        uint32_t oval=(uint32_t)(tail_off-4);
        out[slot+28]=(oval>>24)&0xFF; out[slot+29]=(oval>>16)&0xFF;
        out[slot+30]=(oval>>8)&0xFF; out[slot+31]=oval&0xFF;
        tpool_t pl;
        int ni=parse_top_type(types[i],&pl);
        size_t alen=strlen(args[i]);
        if(enc_tail(&pl,ni,args[i],alen,1,0,tail_off,out,out_cap,&tail_off,errbuf,errlen)!=0) return -1;
    }
    *out_len=tail_off;
    return 0;
}

// --- Decode ---------------------------------------------------------------
// Two passes: validate everything depth-first in order, then format. Value
// errors therefore always precede "out too small".

static int hex_to_bytes(const char *hex, uint8_t *out, size_t *out_len, char *errbuf, size_t errlen){
    while(isspace((unsigned char)*hex)) hex++;
    if(hex[0]=='0'&&(hex[1]=='x'||hex[1]=='X')) hex+=2;
    size_t len=strlen(hex);
    while(len>0 && isspace((unsigned char)hex[len-1])) len--;
    if(len > 4096){ set_err(errbuf,errlen,"hex too long"); return -1; }
    if(len%2!=0){ set_err(errbuf,errlen,"odd hex"); return -1; }
    for(size_t i=0;i<len/2;i++){
        int hi=hex_nib(hex[i*2]), lo=hex_nib(hex[i*2+1]);
        if(hi<0||lo<0){ set_err(errbuf,errlen,"bad hex"); return -1; }
        out[i]=(hi<<4)|lo;
    }
    *out_len=len/2;
    return 0;
}

// Padding validation (mirrors eth_abi's NonEmptyPaddingBytes). 0 ok, -1 bad.
static int validate_slot(const tyn_t *nd, const uint8_t slot[32]){
    if(nd->c==TY_ADDR){
        for(int i=0;i<12;i++) if(slot[i]) return -1;
        return 0;
    }
    if(nd->c==TY_BOOL){
        for(int i=0;i<31;i++) if(slot[i]) return -1;
        return slot[31]<=1 ? 0 : -1;
    }
    if(nd->c==TY_UINT){
        int n=nd->num/8;
        for(int i=0;i<32-n;i++) if(slot[i]) return -1;
        return 0;
    }
    if(nd->c==TY_INT){
        int n=nd->num/8;
        uint8_t fill=(slot[32-n]&0x80)?0xFF:0x00;
        for(int i=0;i<32-n;i++) if(slot[i]!=fill) return -1;
        return 0;
    }
    if(nd->c==TY_BYTESN){
        for(int i=nd->num;i<32;i++) if(slot[i]) return -1;
        return 0;
    }
    return -1;
}

// Full-width u32 slot read: nonzero high bytes mean the value exceeds 32
// bits. 0 ok, -1 high bytes set.
static int read_u32_full(const uint8_t slot[32], uint32_t *v){
    for(int i=0;i<28;i++) if(slot[i]) return -1;
    *v=((uint32_t)slot[28]<<24)|((uint32_t)slot[29]<<16)|((uint32_t)slot[30]<<8)|slot[31];
    return 0;
}

// Read a dynamic offset slot at *pos (consumes it). All arithmetic is in
// subtraction form: off+base never wraps. Errors: "bad offset".
static int read_offset(const uint8_t *data, size_t dlen, size_t frame_base, size_t *pos,
                       size_t *target, char *errbuf, size_t errlen){
    if(*pos>dlen || dlen-*pos<32){ set_err(errbuf,errlen,"bad offset"); return -1; }
    uint32_t off=0;
    if(read_u32_full(data+*pos,&off)!=0){ set_err(errbuf,errlen,"bad offset"); return -1; }
    *pos+=32;
    if(frame_base>dlen || off>dlen-frame_base){ set_err(errbuf,errlen,"bad offset"); return -1; }
    *target=frame_base+off;
    if(dlen-*target<32){ set_err(errbuf,errlen,"bad offset"); return -1; }
    return 0;
}

static int dec_validate(tpool_t *pl, int ni, const uint8_t *data, size_t dlen,
                        size_t frame_base, size_t *pos, char *errbuf, size_t errlen){
    tyn_t *nd=&pl->n[ni];
    if(nd->c!=TY_ARR && nd->c!=TY_TUP && nd->c!=TY_BYTES && nd->c!=TY_STR){
        if(*pos>dlen || dlen-*pos<32){ set_err(errbuf,errlen,"bad data"); return -1; }
        if(validate_slot(nd, data+*pos)!=0){ set_err(errbuf,errlen,"bad padding"); return -1; }
        *pos+=32; return 0;
    }
    if(nd->c==TY_BYTES || nd->c==TY_STR){
        size_t target=0;
        if(read_offset(data,dlen,frame_base,pos,&target,errbuf,errlen)!=0) return -1;
        uint32_t blen=0;
        if(read_u32_full(data+target,&blen)!=0){ set_err(errbuf,errlen,"bad data"); return -1; }
        if(blen>dlen-target-32){ set_err(errbuf,errlen,"bad data"); return -1; }
        size_t padded=((size_t)blen+31)&~((size_t)31);
        if(padded>dlen-target-32){ set_err(errbuf,errlen,"bad data"); return -1; }
        for(size_t k=blen;k<padded;k++) if(data[target+32+k]){ set_err(errbuf,errlen,"bad padding"); return -1; }
        if(nd->c==TY_STR && utf8_valid(data+target+32,blen)!=0){ set_err(errbuf,errlen,"bad string"); return -1; }
        return 0;
    }
    if(nd->c==TY_ARR){
        if(!nd->dyn){
            for(int k=0;k<nd->num;k++)
                if(dec_validate(pl,nd->elem,data,dlen,frame_base,pos,errbuf,errlen)!=0) return -1;
            return 0;
        }
        size_t target=0;
        if(read_offset(data,dlen,frame_base,pos,&target,errbuf,errlen)!=0) return -1;
        // Only dynamic arrays carry a length slot; fixed arrays start their
        // elements at the target. Element offsets are relative to the
        // element sequence in both cases.
        size_t cbase=target;
        uint32_t count=0;
        if(nd->num<0){
            if(read_u32_full(data+target,&count)!=0){ set_err(errbuf,errlen,"bad data"); return -1; }
            if(count>(uint32_t)ABI_ARR_CAP){ set_err(errbuf,errlen,"array too long"); return -1; }
            cbase=target+32;
        } else {
            count=(uint32_t)nd->num;
        }
        int ei=nd->elem;
        if(!pl->n[ei].dyn){
            size_t esz=(size_t)head_slots(pl,ei)*32;
            if(count>(dlen-cbase)/esz){ set_err(errbuf,errlen,"bad data"); return -1; }
            size_t ep=cbase;
            for(uint32_t k=0;k<count;k++)
                if(dec_validate(pl,ei,data,dlen,cbase,&ep,errbuf,errlen)!=0) return -1;
            return 0;
        }
        if(count>(dlen-cbase)/32){ set_err(errbuf,errlen,"bad data"); return -1; }
        size_t ep=cbase;
        for(uint32_t k=0;k<count;k++)
            if(dec_validate(pl,ei,data,dlen,cbase,&ep,errbuf,errlen)!=0) return -1;
        return 0;
    }
    // TY_TUP
    if(!nd->dyn){
        for(int k=0;k<nd->nchild;k++){
            int ci=pl->kids[nd->cfirst+k];
            if(dec_validate(pl,ci,data,dlen,frame_base,pos,errbuf,errlen)!=0) return -1;
        }
        return 0;
    }
    size_t target=0;
    if(read_offset(data,dlen,frame_base,pos,&target,errbuf,errlen)!=0) return -1;
    size_t ep=target;
    for(int k=0;k<nd->nchild;k++){
        int ci=pl->kids[nd->cfirst+k];
        if(dec_validate(pl,ci,data,dlen,target,&ep,errbuf,errlen)!=0) return -1;
    }
    return 0;
}

// --- Decode formatting ------------------------------------------------------
// Runs only after validation passed; guards repeat cheaply (unreachable).

static int emit_byte(char c, char *out_lines, size_t out_size, size_t *opos, char *errbuf, size_t errlen){
    if(*opos+2>=out_size){ set_err(errbuf,errlen,"out too small"); return -1; }
    out_lines[(*opos)++]=c;
    return 0;
}

static int emit_str(const char *s, char *out_lines, size_t out_size, size_t *opos, char *errbuf, size_t errlen){
    size_t ll=strlen(s);
    if(*opos+ll+2>=out_size){ set_err(errbuf,errlen,"out too small"); return -1; }
    memcpy(out_lines+*opos,s,ll); *opos+=ll;
    return 0;
}

static int emit_eol(char *out_lines, size_t out_size, size_t *opos, char *errbuf, size_t errlen){
    return emit_byte('\n',out_lines,out_size,opos,errbuf,errlen);
}

static void fmt_scalar_text(const tyn_t *nd, const uint8_t slot[32], char *line, size_t cap){
    const char *hexd="0123456789abcdef";
    if(nd->c==TY_ADDR){
        int pos=0; pos+=snprintf(line+pos,cap-pos,"0x");
        for(int k=12;k<32;k++){ line[pos++]=hexd[slot[k]>>4]; line[pos++]=hexd[slot[k]&0xF]; }
        line[pos]=0;
    } else if(nd->c==TY_BOOL){
        snprintf(line,cap,"%s", slot[31]?"true":"false");
    } else if(nd->c==TY_UINT){
        u256_t v; for(int k=0;k<8;k++){ uint32_t w = ((uint32_t)slot[k*4]<<24)|((uint32_t)slot[k*4+1]<<16)|((uint32_t)slot[k*4+2]<<8)|slot[k*4+3]; v.d[7-k]=w; }
        char dec[80]; u256_to_dec(&v, dec); snprintf(line,cap,"%s",dec);
    } else if(nd->c==TY_INT){
        u256_t v; for(int k=0;k<8;k++){ uint32_t w = ((uint32_t)slot[k*4]<<24)|((uint32_t)slot[k*4+1]<<16)|((uint32_t)slot[k*4+2]<<8)|slot[k*4+3]; v.d[7-k]=w; }
        char dec[80]; u256_to_dec_signed(&v, dec); snprintf(line,cap,"%s",dec);
    } else if(nd->c==TY_BYTESN){
        int n=nd->num;
        if(n<0) n=0; if(n>32) n=32;
        int pos=0; pos+=snprintf(line+pos,cap-pos,"0x");
        for(int k=0;k<n;k++){ line[pos++]=hexd[slot[k]>>4]; line[pos++]=hexd[slot[k]&0xF]; }
        line[pos]=0;
    } else {
        snprintf(line,cap,"?");
    }
}

static int emit_hex(const uint8_t *s, size_t n, char *out_lines, size_t out_size, size_t *opos, char *errbuf, size_t errlen){
    const char *hexd="0123456789abcdef";
    for(size_t k=0;k<n;k++){
        if(emit_byte(hexd[s[k]>>4],out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
        if(emit_byte(hexd[s[k]&0xF],out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
    }
    return 0;
}

// Nested strings print quoted with escapes: \" \\ \n \r \t and \xNN.
static int emit_quoted_str(const uint8_t *s, size_t n, char *out_lines, size_t out_size, size_t *opos, char *errbuf, size_t errlen){
    const char *hexd="0123456789abcdef";
    if(emit_byte('"',out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
    for(size_t k=0;k<n;k++){
        uint8_t c=s[k];
        if(c=='"'){ if(emit_str("\\\"",out_lines,out_size,opos,errbuf,errlen)!=0) return -1; }
        else if(c=='\\'){ if(emit_str("\\\\",out_lines,out_size,opos,errbuf,errlen)!=0) return -1; }
        else if(c=='\n'){ if(emit_str("\\n",out_lines,out_size,opos,errbuf,errlen)!=0) return -1; }
        else if(c=='\r'){ if(emit_str("\\r",out_lines,out_size,opos,errbuf,errlen)!=0) return -1; }
        else if(c=='\t'){ if(emit_str("\\t",out_lines,out_size,opos,errbuf,errlen)!=0) return -1; }
        else if(c<0x20||c==0x7F){
            char e[5]; e[0]='\\'; e[1]='x'; e[2]=hexd[c>>4]; e[3]=hexd[c&0xF]; e[4]=0;
            if(emit_str(e,out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
        }
        else if(emit_byte((char)c,out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
    }
    return emit_byte('"',out_lines,out_size,opos,errbuf,errlen);
}

static int dec_format(tpool_t *pl, int ni, const uint8_t *data, size_t dlen,
                      size_t frame_base, size_t *pos, int is_top,
                      char *out_lines, size_t out_size, size_t *opos, char *errbuf, size_t errlen){
    tyn_t *nd=&pl->n[ni];
    if(nd->c!=TY_ARR && nd->c!=TY_TUP && nd->c!=TY_BYTES && nd->c!=TY_STR){
        if(*pos>dlen || dlen-*pos<32){ set_err(errbuf,errlen,"bad data"); return -1; }
        char line[96]; fmt_scalar_text(nd,data+*pos,line,sizeof(line));
        *pos+=32;
        return emit_str(line,out_lines,out_size,opos,errbuf,errlen);
    }
    if(nd->c==TY_BYTES || nd->c==TY_STR){
        size_t target=0;
        if(read_offset(data,dlen,frame_base,pos,&target,errbuf,errlen)!=0) return -1;
        uint32_t blen=0;
        if(read_u32_full(data+target,&blen)!=0){ set_err(errbuf,errlen,"bad data"); return -1; }
        if(blen>dlen-target-32){ set_err(errbuf,errlen,"bad data"); return -1; }
        if(nd->c==TY_BYTES){
            if(emit_str("0x",out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
            return emit_hex(data+target+32,blen,out_lines,out_size,opos,errbuf,errlen);
        }
        if(is_top){
            // Raw, unless a control byte would break the line protocol (a
            // NUL would even truncate the C string): then quoted+escaped.
            int raw=1;
            for(uint32_t k=0;k<blen;k++){
                uint8_t c=data[target+32+k];
                if(c<0x20||c==0x7F){ raw=0; break; }
            }
            if(raw){
                for(uint32_t k=0;k<blen;k++)
                    if(emit_byte((char)data[target+32+k],out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
                return 0;
            }
        }
        return emit_quoted_str(data+target+32,blen,out_lines,out_size,opos,errbuf,errlen);
    }
    if(nd->c==TY_ARR){
        // Top-level fixed array of static scalars keeps the flat one-line-
        // per-element format; every other array prints one bracketed line.
        int ei=nd->elem;
        int flat=is_top && !nd->dyn && pl->n[ei].c!=TY_ARR && pl->n[ei].c!=TY_TUP &&
                 pl->n[ei].c!=TY_BYTES && pl->n[ei].c!=TY_STR;
        if(!nd->dyn){
            if(!flat && emit_byte('[',out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
            for(int k=0;k<nd->num;k++){
                if(k>0 && !flat && emit_byte(',',out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
                if(dec_format(pl,ei,data,dlen,frame_base,pos,0,out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
                if(flat && emit_eol(out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
            }
            if(!flat && emit_byte(']',out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
            return 0;
        }
        size_t target=0;
        if(read_offset(data,dlen,frame_base,pos,&target,errbuf,errlen)!=0) return -1;
        size_t cbase=target;
        uint32_t count=0;
        if(nd->num<0){
            if(read_u32_full(data+target,&count)!=0){ set_err(errbuf,errlen,"bad data"); return -1; }
            cbase=target+32;
        } else {
            count=(uint32_t)nd->num;
        }
        if(emit_byte('[',out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
        if(!pl->n[ei].dyn){
            size_t esz=(size_t)head_slots(pl,ei)*32;
            if(count>(dlen-cbase)/esz){ set_err(errbuf,errlen,"bad data"); return -1; }
            size_t ep=cbase;
            for(uint32_t k=0;k<count;k++){
                if(k>0 && emit_byte(',',out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
                if(dec_format(pl,ei,data,dlen,cbase,&ep,0,out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
            }
        } else {
            if(count>(dlen-cbase)/32){ set_err(errbuf,errlen,"bad data"); return -1; }
            size_t ep=cbase;
            for(uint32_t k=0;k<count;k++){
                if(k>0 && emit_byte(',',out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
                if(dec_format(pl,ei,data,dlen,cbase,&ep,0,out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
            }
        }
        return emit_byte(']',out_lines,out_size,opos,errbuf,errlen);
    }
    // TY_TUP: always one parenthesized line.
    size_t target=0, ep=0;
    if(nd->dyn){
        if(read_offset(data,dlen,frame_base,pos,&target,errbuf,errlen)!=0) return -1;
        ep=target;
    }
    if(emit_byte('(',out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
    for(int k=0;k<nd->nchild;k++){
        if(k>0 && emit_byte(',',out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
        int ci=pl->kids[nd->cfirst+k];
        size_t base=nd->dyn?target:frame_base;
        size_t *pp=nd->dyn?&ep:pos;
        if(dec_format(pl,ci,data,dlen,base,pp,0,out_lines,out_size,opos,errbuf,errlen)!=0) return -1;
    }
    return emit_byte(')',out_lines,out_size,opos,errbuf,errlen);
}

int abi_decode(const char *types, const char *hexdata, char *out_lines, size_t out_size, char *errbuf, size_t errlen){
    // Split the type list on depth-0 commas (no strtok: it silently drops
    // empty segments). Types never contain quotes.
    char type_list[32][64]; int ntypes=0;
    {
        const char *seg=types;
        const char *end=types+strlen(types);
        const char *b=types;
        while(b<end && isspace((unsigned char)*b)) b++;
        if(b==end){ ntypes=0; }
        else {
            int depth=0;
            for(const char *c=types; ; c++){
                char ch=(c==end)?0:*c;
                if(ch=='('||ch=='[') depth++;
                else if(ch==')'||ch==']'){ if(depth>0) depth--; }
                if((ch==','&&depth==0)||ch==0){
                    const char *tb=seg, *te=c;
                    while(tb<te && isspace((unsigned char)*tb)) tb++;
                    while(te>tb && isspace((unsigned char)te[-1])) te--;
                    if(tb==te){ set_err(errbuf,errlen,"bad type"); return -1; }
                    if(ntypes>=32){ set_err(errbuf,errlen,"too many types"); return -1; }
                    if((size_t)(te-tb)>=64){ set_err(errbuf,errlen,"bad type"); return -1; }
                    size_t tl=(size_t)(te-tb);
                    memcpy(type_list[ntypes],tb,tl); type_list[ntypes][tl]=0;
                    ntypes++;
                    if(ch==0) break;
                    seg=c+1;
                }
            }
        }
    }
    int thy[32];
    for(int i=0;i<ntypes;i++){
        tpool_t pl;
        int ni=parse_top_type(type_list[i],&pl);
        if(ni<0){ set_err(errbuf,errlen,parse_reason(ni)); return -1; }
        thy[i]=head_slots(&pl,ni);
    }
    // 2048, not 8192: hex_to_bytes caps input at 4096 chars (2048 bytes);
    // longest reachable input is ~4093 file chars. Same stack-wrap fix as
    // enc_bytes_tail's tmp. "hex too long" stays unreachable on every path.
    uint8_t data[2048]; size_t dlen=0;
    if(hex_to_bytes(hexdata, data, &dlen, errbuf, errlen)!=0) return -1;
    if(dlen%32!=0){ set_err(errbuf,errlen,"bad data"); return -1; }
    size_t head_slots_total=0;
    for(int i=0;i<ntypes;i++) head_slots_total+=(size_t)thy[i];
    if(dlen < head_slots_total*32){ set_err(errbuf,errlen,"data too short"); return -1; }
    // Validate everything first, in order.
    {
        size_t cursor=0;
        for(int i=0;i<ntypes;i++){
            tpool_t pl;
            int ni=parse_top_type(type_list[i],&pl);
            if(dec_validate(&pl,ni,data,dlen,0,&cursor,errbuf,errlen)!=0) return -1;
        }
    }
    // Then format.
    size_t opos=0, cursor=0;
    for(int i=0;i<ntypes;i++){
        tpool_t pl;
        int ni=parse_top_type(type_list[i],&pl);
        tyn_t *nd=&pl.n[ni];
        int flat=0;
        if(nd->c==TY_ARR && !nd->dyn){
            tyn_t *e=&pl.n[nd->elem];
            flat=(e->c!=TY_ARR && e->c!=TY_TUP && e->c!=TY_BYTES && e->c!=TY_STR);
        }
        if(dec_format(&pl,ni,data,dlen,0,&cursor,1,out_lines,out_size,&opos,errbuf,errlen)!=0) return -1;
        if(!flat && emit_eol(out_lines,out_size,&opos,errbuf,errlen)!=0) return -1;
    }
    out_lines[opos]=0;
    return 0;
}

