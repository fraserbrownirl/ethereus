#include "abi.h"
#include "util.h"
#include "keccak.h"
#include "selectors.h"
#include <string.h>
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>

// Strict ABI codec (v1 scope).
//
// Accepted type grammar (canonical only, as enforced by eth_abi's registry):
//   address | bool | bytes | string | uint | int |
//   uint<B> | int<B>   (B in 8,16,..,256, canonical digits) |
//   bytes<N>           (N in 1..32, canonical digits) |
//   T[N]               (T a static scalar, N in 1..64, canonical digits)
// Well-formed but out of v1 scope: tuples, fixed/ufixed, dynamic T[].
// Everything else is malformed. Nothing truncates-and-continues: oversize
// input is an error.
//
// Error reasons (errbuf, wrapped by the caller as "error (<reason>)") are
// coarse static strings: no echoes, no counts.

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
// Returns 0 ok / -1 malformed ("bad type") / -2 out of v1 scope
// ("unsupported type"). On ok, fills *o (num = bits / N / length).

typedef enum { TY_UINT, TY_INT, TY_ADDR, TY_BOOL, TY_BYTESN, TY_BYTES, TY_STR, TY_ARR } tyc_t;
typedef struct { tyc_t c; int num; char elem[16]; } ty_t;

static int bits_ok(long b){ return b>=8 && b<=256 && b%8==0; }

// Well-formed fixed-point marker: bare fixed/ufixed or u?fixed<M>x<N> with
// canonical digits. (Out of v1 scope; anything else falls through to bad.)
static int is_fixed_marker(const char *t){
    const char *p=t;
    if(strncmp(p,"ufixed",6)==0) p+=6;
    else if(strncmp(p,"fixed",5)==0) p+=5;
    else return 0;
    if(*p==0) return 1;
    long m, n;
    const char *x=strchr(p,'x');
    if(!x) return 0;
    char mb[16]; size_t ml=x-p;
    if(ml==0 || ml>=sizeof(mb)) return 0;
    memcpy(mb,p,ml); mb[ml]=0;
    if(!canon_num(mb,&m) || !canon_num(x+1,&n)) return 0;
    return 1;
}

static int classify_scalar(const char *t, ty_t *o){
    if(strcmp(t,"address")==0){ o->c=TY_ADDR; o->num=0; return 0; }
    if(strcmp(t,"bool")==0){ o->c=TY_BOOL; o->num=0; return 0; }
    if(strcmp(t,"bytes")==0){ o->c=TY_BYTES; o->num=0; return 0; }
    if(strcmp(t,"string")==0){ o->c=TY_STR; o->num=0; return 0; }
    if(strcmp(t,"uint")==0){ o->c=TY_UINT; o->num=256; return 0; }
    if(strcmp(t,"int")==0){ o->c=TY_INT; o->num=256; return 0; }
    long v;
    if(strncmp(t,"uint",4)==0 && canon_num(t+4,&v) && bits_ok(v)){ o->c=TY_UINT; o->num=(int)v; return 0; }
    if(strncmp(t,"int",3)==0 && canon_num(t+3,&v) && bits_ok(v)){ o->c=TY_INT; o->num=(int)v; return 0; }
    if(strncmp(t,"bytes",5)==0 && canon_num(t+5,&v) && v>=1 && v<=32){ o->c=TY_BYTESN; o->num=(int)v; return 0; }
    return -1;
}

// -2 (unsupported) iff the shape is well-formed-but-out-of-v1-scope:
// tuple markers, multi-dimension/nested arrays, fixed markers (including
// as an array element), dynamic T[] with a bracket-free prefix.
static int classify(const char *t, ty_t *o){
    if(strchr(t,'(') || strchr(t,')')) return -2;
    { int nb=0; for(const char *c=t;*c;c++) if(*c=='[') nb++;
      if(nb>1) return -2; }
    size_t l=strlen(t);
    if(l>2 && t[l-1]==']' && t[l-2]=='['){
        for(size_t i=0;i<l-2;i++) if(t[i]=='['||t[i]==']') return -1;
        return -2; // dynamic T[]
    }
    if(is_fixed_marker(t)) return -2;
    if(l>0 && t[l-1]==']'){
        const char *lb=strchr(t,'[');
        if(!lb || lb==t) return -1;
        size_t elen=(size_t)(lb-t);
        if(elen>=sizeof(o->elem)) return -1;
        memcpy(o->elem,t,elen); o->elem[elen]=0;
        if(is_fixed_marker(o->elem)) return -2;
        ty_t e;
        if(classify_scalar(o->elem,&e)!=0) return -1;
        if(e.c!=TY_UINT && e.c!=TY_INT && e.c!=TY_ADDR && e.c!=TY_BOOL && e.c!=TY_BYTESN) return -1;
        char nb[16]; size_t nl=(size_t)(t+l-1-(lb+1));
        if(nl==0 || nl>=sizeof(nb)) return -1;
        memcpy(nb,lb+1,nl); nb[nl]=0;
        long n;
        if(!canon_num(nb,&n)) return -1;
        o->c=TY_ARR; o->num=(int)n; // caller checks the 64 cap
        return 0;
    }
    if(strchr(t,'[') || strchr(t,']')) return -1;
    return classify_scalar(t,o);
}

// Compat wrapper: 0 uint,1 int,2 address,3 bool,4 bytesN,5 array,
// -2 bytes/string, -1 otherwise.
int abi_type_kind(const char *t){
    ty_t o;
    int r=classify(t,&o);
    if(r!=0) return -1;
    switch(o.c){
        case TY_UINT: return 0;
        case TY_INT: return 1;
        case TY_ADDR: return 2;
        case TY_BOOL: return 3;
        case TY_BYTESN: return 4;
        case TY_ARR: return 5;
        default: return -2;
    }
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

// --- Signature parsing (strict) ------------------------------------------

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

// Encode one static scalar. ty comes from classify(), so the type is valid.
static int encode_one(const ty_t *ty, const char *arg, uint8_t *slot32, char *errbuf, size_t errlen){
    memset(slot32,0,32);
    if(ty->c==TY_UINT){
        int bits=ty->num;
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
    } else if(ty->c==TY_INT){
        int bits=ty->num;
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
    } else if(ty->c==TY_ADDR){
        uint8_t addr[20];
        if(parse_address_bytes(arg, addr)!=0){ set_err(errbuf,errlen,"bad address"); return -1; }
        memcpy(slot32+12, addr,20);
        return 0;
    } else if(ty->c==TY_BOOL){
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
    } else if(ty->c==TY_BYTESN){
        int n=ty->num;
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

// --- Encode calldata ------------------------------------------------------

int abi_encode_calldata(const char *sig, const char **args, int nargs, uint8_t *out, size_t out_cap, size_t *out_len, char *errbuf, size_t errlen){
    char name[128]; char types[16][64]; int ntypes=0;
    if(abi_parse_sig(sig, name, sizeof(name), types, &ntypes, errbuf, errlen)!=0) return -1;
    if(ntypes != nargs){ set_err(errbuf,errlen,"arg count"); return -1; }
    if(out_cap < 4){ set_err(errbuf,errlen,"out too small"); return -1; }
    ty_t ty[16];
    for(int i=0;i<ntypes;i++){
        int r=classify(types[i],&ty[i]);
        if(r==-2){ set_err(errbuf,errlen,"unsupported type"); return -1; }
        if(r!=0){ set_err(errbuf,errlen,"bad type"); return -1; }
        if(ty[i].c==TY_ARR && ty[i].num>64){ set_err(errbuf,errlen,"array too long"); return -1; }
    }
    uint32_t sel=abi_selector(sig);
    out[0]=(sel>>24)&0xFF; out[1]=(sel>>16)&0xFF; out[2]=(sel>>8)&0xFF; out[3]=sel&0xFF;
    size_t off=4;
    int dyn_index[16]; int ndyn=0;
    size_t head_slots_total=0;
    for(int i=0;i<ntypes;i++){
        if(ty[i].c==TY_BYTES || ty[i].c==TY_STR){ dyn_index[ndyn++]=i; head_slots_total+=1; }
        else if(ty[i].c==TY_ARR) head_slots_total+=(size_t)ty[i].num;
        else head_slots_total+=1;
    }
    size_t head_end = 4 + head_slots_total*32;
    if(head_end > out_cap){ set_err(errbuf,errlen,"out too small"); return -1; }
    size_t tail_off = head_end;
    // First pass: static slots and dynamic placeholders (dynamic values are
    // validated in the tail pass, so a bad static later in order wins).
    for(int i=0;i<ntypes;i++){
        if(ty[i].c==TY_BYTES || ty[i].c==TY_STR){
            uint32_t offset_val = (uint32_t)(tail_off - 4);
            memset(out+off,0,32);
            out[off+28]=(offset_val>>24)&0xFF;
            out[off+29]=(offset_val>>16)&0xFF;
            out[off+30]=(offset_val>>8)&0xFF;
            out[off+31]=offset_val&0xFF;
            const char *arg=args[i];
            size_t data_len=0;
            if(ty[i].c==TY_STR){
                data_len=strlen(arg);
            } else {
                const char *s=arg;
                while(isspace((unsigned char)*s)) s++;
                if(s[0]=='0'&&(s[1]=='x'||s[1]=='X')) s+=2;
                size_t hexlen=strlen(s);
                while(hexlen>0 && isspace((unsigned char)s[hexlen-1])) hexlen--;
                data_len=hexlen/2;
            }
            size_t padded = (data_len+31)&~31;
            tail_off += 32 + padded;
            off+=32;
        } else if(ty[i].c==TY_ARR){
            int need=ty[i].num;
            ty_t e; // element scalar (validated by classify)
            classify_scalar(ty[i].elem,&e);
            const char *a=args[i];
            while(isspace((unsigned char)*a)) a++;
            size_t alen=strlen(a);
            while(alen>0 && isspace((unsigned char)a[alen-1])) alen--;
            if(alen<2||a[0]!='['||a[alen-1]!=']'){ set_err(errbuf,errlen,"bad array"); return -1; }
            const char *esp[64]; size_t eln[64]; int nen=0;
            const char *end=a+alen-1;
            for(const char *q=a+1; ; ){
                const char *c=q;
                while(c<end && *c!=',') c++;
                if(nen>=64){ set_err(errbuf,errlen,"array too long"); return -1; }
                esp[nen]=q; eln[nen]=(size_t)(c-q); nen++;
                if(c>=end) break;
                q=c+1;
            }
            if(nen!=need){ set_err(errbuf,errlen,"array len"); return -1; }
            for(int k=0;k<nen;k++){
                const char *el=esp[k]; size_t elen=eln[k];
                while(elen>0 && isspace((unsigned char)*el)){ el++; elen--; }
                while(elen>0 && isspace((unsigned char)el[elen-1])) elen--;
                if(elen>=2 && ((el[0]=='"'&&el[elen-1]=='"')||(el[0]=='\''&&el[elen-1]=='\''))){ el++; elen-=2; }
                if(elen>=512){ set_err(errbuf,errlen,"array elem too long"); return -1; }
                char ebuf[512]; memcpy(ebuf,el,elen); ebuf[elen]=0;
                uint8_t slot[32];
                if(encode_one(&e, ebuf, slot, errbuf, errlen)!=0) return -1;
                if(off+32>out_cap){ set_err(errbuf,errlen,"out too small"); return -1; }
                memcpy(out+off, slot,32);
                off+=32;
            }
        } else {
            uint8_t slot[32];
            if(encode_one(&ty[i], args[i], slot, errbuf, errlen)!=0) return -1;
            if(off+32>out_cap){ set_err(errbuf,errlen,"out too small"); return -1; }
            memcpy(out+off, slot,32);
            off+=32;
        }
    }
    // Tail pass: dynamic values in order.
    off = head_end;
    for(int di=0; di<ndyn; di++){
        int i=dyn_index[di];
        const char *arg=args[i];
        size_t data_len=0;
        const uint8_t *data_bytes=NULL;
        uint8_t tmp[8192];
        if(ty[i].c==TY_STR){
            data_len=strlen(arg);
            data_bytes=(const uint8_t*)arg;
        } else {
            const char *s=arg;
            while(isspace((unsigned char)*s)) s++;
            if(s[0]=='0'&&(s[1]=='x'||s[1]=='X')) s+=2;
            size_t hexlen=strlen(s);
            while(hexlen>0 && isspace((unsigned char)s[hexlen-1])) hexlen--;
            for(size_t k=0;k<hexlen;k++){
                char c=s[k];
                int ok=(c>='0'&&c<='9')||(c>='a'&&c<='f')||(c>='A'&&c<='F');
                if(!ok){ set_err(errbuf,errlen,"bad hex"); return -1; }
            }
            if(hexlen%2!=0){ set_err(errbuf,errlen,"odd hex"); return -1; }
            data_len=hexlen/2;
            if(data_len>sizeof(tmp)){ set_err(errbuf,errlen,"bytes too long"); return -1; }
            for(size_t k=0;k<data_len;k++) tmp[k]=(hex_nib(s[k*2])<<4)|hex_nib(s[k*2+1]);
            data_bytes=tmp;
        }
        size_t padded=(data_len+31)&~31;
        if(off+32+padded>out_cap){ set_err(errbuf,errlen,"out too small"); return -1; }
        memset(out+off,0,32);
        out[off+28]=(data_len>>24)&0xFF;
        out[off+29]=(data_len>>16)&0xFF;
        out[off+30]=(data_len>>8)&0xFF;
        out[off+31]=data_len&0xFF;
        off+=32;
        memcpy(out+off, data_bytes, data_len);
        memset(out+off+data_len,0,padded-data_len);
        off+=padded;
    }
    *out_len=off;
    return 0;
}

// --- Decode ---------------------------------------------------------------

static int hex_to_bytes(const char *hex, uint8_t *out, size_t *out_len, char *errbuf, size_t errlen){
    while(isspace((unsigned char)*hex)) hex++;
    if(hex[0]=='0'&&(hex[1]=='x'||hex[1]=='X')) hex+=2;
    size_t len=strlen(hex);
    while(len>0 && isspace((unsigned char)hex[len-1])) len--;
    if(len > 16384){ set_err(errbuf,errlen,"hex too long"); return -1; }
    if(len%2!=0){ set_err(errbuf,errlen,"odd hex"); return -1; }
    for(size_t i=0;i<len/2;i++){
        int hi=hex_nib(hex[i*2]), lo=hex_nib(hex[i*2+1]);
        if(hi<0||lo<0){ set_err(errbuf,errlen,"bad hex"); return -1; }
        out[i]=(hi<<4)|lo;
    }
    *out_len=len/2;
    return 0;
}

// One static slot -> one output line. No heap: the machine has no brk ecall.
static void format_slot(const char *tp, const uint8_t slot[32], char *line, size_t cap){
    if(strcmp(tp,"address")==0){
        int pos=0; pos+=snprintf(line+pos,cap-pos,"0x");
        const char *hexd="0123456789abcdef";
        for(int k=12;k<32;k++){ line[pos++]=hexd[slot[k]>>4]; line[pos++]=hexd[slot[k]&0xF]; }
        line[pos]=0;
    } else if(strcmp(tp,"bool")==0){
        snprintf(line,cap,"%s", slot[31]?"true":"false");
    } else if(strncmp(tp,"uint",4)==0){
        u256_t v; for(int k=0;k<8;k++){ uint32_t w = ((uint32_t)slot[k*4]<<24)|((uint32_t)slot[k*4+1]<<16)|((uint32_t)slot[k*4+2]<<8)|slot[k*4+3]; v.d[7-k]=w; }
        char dec[80]; u256_to_dec(&v, dec); snprintf(line,cap,"%s",dec);
    } else if(strncmp(tp,"int",3)==0){
        u256_t v; for(int k=0;k<8;k++){ uint32_t w = ((uint32_t)slot[k*4]<<24)|((uint32_t)slot[k*4+1]<<16)|((uint32_t)slot[k*4+2]<<8)|slot[k*4+3]; v.d[7-k]=w; }
        char dec[80]; u256_to_dec_signed(&v, dec); snprintf(line,cap,"%s",dec);
    } else if(strncmp(tp,"bytes",5)==0){
        int n=atoi(tp+5);
        if(n<0) n=0; if(n>32) n=32;
        int pos=0; pos+=snprintf(line+pos,cap-pos,"0x"); const char *hexd="0123456789abcdef";
        for(int k=0;k<n;k++){ line[pos++]=hexd[slot[k]>>4]; line[pos++]=hexd[slot[k]&0xF]; }
        line[pos]=0;
    } else {
        snprintf(line,cap,"unsupported %s",tp);
    }
}

// Padding validation (mirrors eth_abi's NonEmptyPaddingBytes). 0 ok, -1 bad.
static int validate_slot(const ty_t *ty, const uint8_t slot[32]){
    if(ty->c==TY_ADDR){
        for(int i=0;i<12;i++) if(slot[i]) return -1;
        return 0;
    }
    if(ty->c==TY_BOOL){
        for(int i=0;i<31;i++) if(slot[i]) return -1;
        return slot[31]<=1 ? 0 : -1;
    }
    if(ty->c==TY_UINT){
        int n=ty->num/8;
        for(int i=0;i<32-n;i++) if(slot[i]) return -1;
        return 0;
    }
    if(ty->c==TY_INT){
        int n=ty->num/8;
        uint8_t fill=(slot[32-n]&0x80)?0xFF:0x00;
        for(int i=0;i<32-n;i++) if(slot[i]!=fill) return -1;
        return 0;
    }
    if(ty->c==TY_BYTESN){
        for(int i=ty->num;i<32;i++) if(slot[i]) return -1;
        return 0;
    }
    return -1;
}

static int emit_line(const char *line, char *out_lines, size_t out_size, size_t *out_pos, char *errbuf, size_t errlen){
    size_t ll=strlen(line);
    if(*out_pos+ll+2 >= out_size){ set_err(errbuf,errlen,"out too small"); return -1; }
    memcpy(out_lines+*out_pos, line, ll); *out_pos+=ll; out_lines[(*out_pos)++]='\n';
    return 0;
}

int abi_decode(const char *types, const char *hexdata, char *out_lines, size_t out_size, char *errbuf, size_t errlen){
    // Split the type list on commas; empty segments are malformed.
    // (No strtok: it silently drops empty segments.)
    char type_list[32][64]; int ntypes=0;
    {
        const char *seg=types;
        const char *end=types+strlen(types);
        // all-blank list means zero types
        const char *b=types;
        while(b<end && isspace((unsigned char)*b)) b++;
        if(b==end){ ntypes=0; }
        else {
            for(const char *c=types; ; c++){
                char ch=(c==end)?0:*c;
                if(ch==','||ch==0){
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
    ty_t ty[32];
    for(int i=0;i<ntypes;i++){
        int r=classify(type_list[i],&ty[i]);
        if(r==-2){ set_err(errbuf,errlen,"unsupported type"); return -1; }
        if(r!=0){ set_err(errbuf,errlen,"bad type"); return -1; }
        if(ty[i].c==TY_ARR && ty[i].num>64){ set_err(errbuf,errlen,"array too long"); return -1; }
    }
    uint8_t data[8192]; size_t dlen=0;
    if(hex_to_bytes(hexdata, data, &dlen, errbuf, errlen)!=0) return -1;
    if(dlen%32!=0){ set_err(errbuf,errlen,"bad data"); return -1; }
    size_t head_slots_total=0;
    for(int i=0;i<ntypes;i++){
        if(ty[i].c==TY_BYTES || ty[i].c==TY_STR) head_slots_total+=1;
        else if(ty[i].c==TY_ARR) head_slots_total+=(size_t)ty[i].num;
        else head_slots_total+=1;
    }
    if(dlen < head_slots_total*32){ set_err(errbuf,errlen,"data too short"); return -1; }
    size_t out_pos=0, cursor=0;
    for(int i=0;i<ntypes;i++){
        if(ty[i].c==TY_BYTES || ty[i].c==TY_STR){
            if(cursor+32 > dlen){ set_err(errbuf,errlen,"bad offset"); return -1; }
            uint32_t off = ((uint32_t)data[cursor+28]<<24)|((uint32_t)data[cursor+29]<<16)|((uint32_t)data[cursor+30]<<8)|data[cursor+31];
            cursor+=32;
            // Subtraction form: off+32 and off+32+blen wrap in 32-bit arithmetic when off or blen is near 2^32,
            // which passed the old additive checks and read before the buffer (the Solana lane's finding).
            if(off > dlen || dlen - off < 32){ set_err(errbuf,errlen,"bad offset"); return -1; }
            uint32_t blen = ((uint32_t)data[off+28]<<24)|((uint32_t)data[off+29]<<16)|((uint32_t)data[off+30]<<8)|data[off+31];
            if(blen > dlen - off - 32){ set_err(errbuf,errlen,"bad data"); return -1; }
            if(ty[i].c==TY_STR){
                if(utf8_valid(data+off+32, blen)!=0){ set_err(errbuf,errlen,"bad string"); return -1; }
                if(out_pos + blen + 2 >= out_size){ set_err(errbuf,errlen,"out too small"); return -1; }
                memcpy(out_lines+out_pos, data+off+32, blen);
                out_pos+=blen;
                out_lines[out_pos++]='\n';
                continue;
            } else {
                if(out_pos + 2 + blen*2 +2 >= out_size){ set_err(errbuf,errlen,"out too small"); return -1; }
                out_lines[out_pos++]='0'; out_lines[out_pos++]='x';
                const char *hexd="0123456789abcdef";
                for(uint32_t k=0;k<blen;k++){ out_lines[out_pos++]=hexd[data[off+32+k]>>4]; out_lines[out_pos++]=hexd[data[off+32+k]&0xF]; }
                out_lines[out_pos++]='\n';
                continue;
            }
        }
        if(ty[i].c==TY_ARR){
            ty_t e; classify_scalar(ty[i].elem,&e);
            for(int k=0;k<ty[i].num;k++){
                if(cursor+32 > dlen){ set_err(errbuf,errlen,"bad data"); return -1; }
                if(validate_slot(&e, data+cursor)!=0){ set_err(errbuf,errlen,"bad padding"); return -1; }
                char line[256]; format_slot(ty[i].elem, data+cursor, line, sizeof(line));
                if(emit_line(line, out_lines, out_size, &out_pos, errbuf, errlen)!=0) return -1;
                cursor+=32;
            }
            continue;
        }
        if(cursor+32 > dlen){ set_err(errbuf,errlen,"bad data"); return -1; }
        if(validate_slot(&ty[i], data+cursor)!=0){ set_err(errbuf,errlen,"bad padding"); return -1; }
        char line[256]; format_slot(type_list[i], data+cursor, line, sizeof(line));
        if(emit_line(line, out_lines, out_size, &out_pos, errbuf, errlen)!=0) return -1;
        cursor+=32;
    }
    out_lines[out_pos]=0;
    return 0;
}
