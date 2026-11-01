#include <string.h>
#include <stdio.h>
#include <ctype.h>
#include <stdlib.h>
#include "rt.h"
#include "u256.h"
#include "util.h"
#include "keccak.h"
#include "abi.h"
#include "evm.h"

#define TEXT ((const volatile unsigned char *)0x8000)
#define ROWS 80
enum { TOOL_RESPONSE = 256, TOOL_RESPONSE_END, THINK, THINK_END };

// Non-bridge gate-1 input: the NUL-terminated cmd line in eth_in (RAM 0x100;
// the host pokes it into the snapshot), exactly like calc_in in calc.c.
#ifndef BRIDGE
#ifdef HOST_FUZZ
char eth_in[2048];
#else
char eth_in[2048] __attribute__((section(".ethin")));
#endif
// Bridge-resolved @file args: the host writes name\0hex\0 pairs, double-NUL
// terminated, into file_buf (RAM 0x900, 4 KB, outside the 80-token capture).
// Large arguments never go through the token window on any path; the bridge
// image has no buffer and answers error (no file). Max ~4K hex chars per
// command (~2 KB of data, ~16x the window).
#define FILE_BUF_SIZE 4096
#ifdef HOST_FUZZ
char file_buf[FILE_BUF_SIZE];
#else
char file_buf[FILE_BUF_SIZE] __attribute__((section(".filebuf")));
#endif
#endif

static void put(int c){ rv_ecall2(1,c,0,0); }
static void put_str(const char *s){ while(*s) put(*s++); }
static void put_hex_byte(uint8_t b){ const char *h="0123456789abcdef"; put(h[b>>4]); put(h[b&0xF]); }

static char call_buf[1280];
static int gather_call(void){
    int n=0, i;
    for(i=1;i<=ROWS;i++){
        const volatile unsigned char *r=TEXT+16*i;
        int c=r[0];
        if(!c) break;
        for(int k=0;k<16 && c; c= ++k<16 ? r[k]:0){
            if(n < (int)sizeof(call_buf)-1) call_buf[n++]=(char)c;
        }
    }
    call_buf[n]=0;
    if(i>ROWS){
        // Inner content only is ever captured (no </tool_call>); a full window
        // without the cmd value's closing tag means the value was truncated.
        if(!strstr(call_buf,"</parameter>")) return -2;
    }
    if(n==0) return -1;
    return n;
}

static int extract_param(const char *src, const char *param, char *out, size_t cap){
    char open[64]; snprintf(open,sizeof(open),"<parameter=%s>",param);
    char open2[64]; snprintf(open2,sizeof(open2),"<parameter name=\"%s\">",param);
    const char *p=strstr(src, open);
    if(!p) p=strstr(src, open2);
    if(!p) return -1;
    const char *v = strchr(p, '>');
    if(!v) return -1;
    v++;
    if(*v=='\n') v++;
    const char *e = strstr(v, "</parameter>");
    if(!e) return -1;
    size_t len = e - v;
    while(len>0 && (v[len-1]=='\n' || v[len-1]=='\r')) len--;
    if(len >= cap) len=cap-1;
    memcpy(out, v, len); out[len]=0;
    return 0;
}

static int hex_nibble(char c){
    if(c>='0'&&c<='9') return c-'0';
    if(c>='a'&&c<='f') return c-'a'+10;
    if(c>='A'&&c<='F') return c-'A'+10;
    return -1;
}

// Resolve an @file token to its hex content, or NULL if absent/malformed.
// tok must be NUL-terminated (all call sites pass tmp copies). Bounded scan.
static const char *file_arg(const char *tok){
    if(!tok || tok[0]!='@' || !tok[1]) return NULL;
#ifdef BRIDGE
    (void)tok;
    return NULL;
#else
    const char *want=tok+1;
    size_t off=0;
    while(off<FILE_BUF_SIZE && file_buf[off]){
        size_t nl=0;
        while(off+nl<FILE_BUF_SIZE && file_buf[off+nl]) nl++;
        if(off+nl>=FILE_BUF_SIZE) return NULL; // unterminated name
        size_t ho=off+nl+1;
        if(ho>=FILE_BUF_SIZE) return NULL;
        size_t hl=0;
        while(ho+hl<FILE_BUF_SIZE && file_buf[ho+hl]) hl++;
        if(ho+hl>=FILE_BUF_SIZE) return NULL; // unterminated content
        if(strcmp(file_buf+off,want)==0) return file_buf+ho;
        off=ho+hl+1;
    }
    return NULL;
#endif
}
static int parse_hex_fixed(const char *s, uint8_t *out, int out_len){
    while(isspace((unsigned char)*s)) s++;
    if(s[0]=='0'&&(s[1]=='x'||s[1]=='X')) s+=2;
    size_t len=strlen(s);
    while(len>0 && isspace((unsigned char)s[len-1])) len--;
    if((int)len != out_len*2) return -1;
    for(int i=0;i<out_len;i++){
        int hi=hex_nibble(s[i*2]), lo=hex_nibble(s[i*2+1]);
        if(hi<0||lo<0) return -1;
        out[i]=(hi<<4)|lo;
    }
    return 0;
}

static int prec_of(const char *op){
    if(strcmp(op,"**")==0) return 6;
    if(strcmp(op,"*")==0||strcmp(op,"/")==0||strcmp(op,"%")==0) return 5;
    if(strcmp(op,"+")==0||strcmp(op,"-")==0) return 4;
    if(strcmp(op,"<<")==0||strcmp(op,">>")==0) return 3;
    if(strcmp(op,"&")==0) return 2;
    if(strcmp(op,"^")==0) return 1;
    if(strcmp(op,"|")==0) return 0;
    return -1;
}
static int is_right_assoc(const char *op){ return strcmp(op,"**")==0; }

// command handlers
static void handle_keccak(const char *arg){
    char tmp[2048];
    cpy_cap(tmp, arg, sizeof(tmp));
    char *s=tmp;
    while(isspace((unsigned char)*s)) s++;
    char *e=s+strlen(s);
    while(e>s && isspace((unsigned char)e[-1])){ e--; *e=0; }
    if(s[0]=='@'){ const char *f=file_arg(s); if(!f){ put_str("error (no file)\n"); return; } s=(char*)f; }
    uint8_t data[2048];
    size_t dlen=0;
    if(s[0]=='0' && (s[1]=='x'||s[1]=='X')){
        const char *h=s+2;
        size_t hl=strlen(h);
        if(hl%2!=0){ put_str("error (bad hex)\n"); return; }
        for(size_t i=0;i<hl/2;i++){
            int hi=hex_nibble(h[i*2]), lo=hex_nibble(h[i*2+1]);
            if(hi<0||lo<0){ put_str("error (bad hex)\n"); return; }
            data[dlen++]=(hi<<4)|lo;
        }
    } else if( strlen(s)>=2 && ((s[0]=='\'' && s[strlen(s)-1]=='\'') || (s[0]=='"' && s[strlen(s)-1]=='"')) ){
        size_t l=strlen(s);
        memcpy(data, s+1, l-2); dlen=l-2;
    } else {
        dlen=strlen(s);
        memcpy(data,s,dlen);
    }
    if(dlen>1100){ put_str("error (too long)\n"); return; }
    uint8_t out[32]; keccak256(data,dlen,out);
    put_str("0x");
    for(int i=0;i<32;i++) put_hex_byte(out[i]);
    put('\n');
}

static void handle_selector(const char *arg){
    char tmp[256]; cpy_cap(tmp, arg, sizeof(tmp));
    char *s=tmp; while(isspace((unsigned char)*s)) s++;
    char *e=s+strlen(s); while(e>s && isspace((unsigned char)e[-1])){ e--; *e=0; }
    if(!*s){ put_str("error (bad sig)\n"); return; }
    uint32_t sel=abi_selector(s);
    put_str("0x");
    put_hex_byte((sel>>24)&0xFF); put_hex_byte((sel>>16)&0xFF); put_hex_byte((sel>>8)&0xFF); put_hex_byte(sel&0xFF);
    put('\n');
}

static void handle_encode(const char *arg){
    char tmp[2048]; cpy_cap(tmp, arg, sizeof(tmp));
    char *s=tmp; while(isspace((unsigned char)*s)) s++;
    char *lp=strchr(s,'(');
    if(!lp){ put_str("error (bad encode)\n"); return; }
    char *rp=strchr(lp,')');
    if(!rp){ put_str("error (bad encode)\n"); return; }
    size_t siglen = rp - s +1;
    if(siglen >= 512){ put_str("error (bad sig)\n"); return; }
    char sig[512]; memcpy(sig,s,siglen); sig[siglen]=0;
    char *rest = rp+1;
    while(isspace((unsigned char)*rest)) rest++;
    char name[128]; char types[16][64]; int ntypes=0;
    char errb[128];
    if(abi_parse_sig(sig, name,sizeof(name), types, &ntypes, errb,sizeof(errb))!=0){ put_str("error (bad sig)\n"); return; }
    const char *arg_ptrs[16];
    char arg_bufs[16][256];
    int arg_quoted[16]={0};
    if(ntypes>0){
        int idx=0;
        char *p=rest;
        while(*p && idx<ntypes){
            while(isspace((unsigned char)*p) || *p==',') p++;
            if(!*p) break;
            char buf[256]; int bi=0;
            if(*p=='"'||*p=='\''){
                arg_quoted[idx]=1;
                char q=*p++; while(*p && *p!=q && bi<255){ buf[bi++]=*p++; }
                if(bi==255 && *p!=q){ put_str("error (arg too long)\n"); return; }
                if(*p==q) p++; buf[bi]=0;
            } else {
                // Bracket-aware: array args "[1,2,3]" survive unquoted.
                int depth=0;
                while(*p && bi<255){
                    if(*p=='[') depth++;
                    else if(*p==']' && depth>0) depth--;
                    else if(depth==0 && (isspace((unsigned char)*p) || *p==',')) break;
                    buf[bi++]=*p++;
                }
                if(bi==255 && *p && (depth>0 || (!isspace((unsigned char)*p) && *p!=','))){ put_str("error (arg too long)\n"); return; }
                buf[bi]=0;
            }
            // NOTE: no bi==0 break — quoted "" is a real empty arg (only ""
            // can yield bi==0 here), so empty strings are encodable.
            // Byte copy, not strcpy (picolibc strcpy faults on misaligned LW/SW).
            for(int bi2=0;bi2<=bi;bi2++) arg_bufs[idx][bi2]=buf[bi2];
            arg_ptrs[idx]=arg_bufs[idx];
            idx++;
        }
        if(idx!=ntypes){ put_str("error (arg count)\n"); return; }
        // Strict: anything after the last arg is an error, not ignored.
        while(isspace((unsigned char)*p) || *p==',') p++;
        if(*p){ put_str("error (arg count)\n"); return; }
        for(int i=0;i<ntypes;i++) arg_ptrs[i]=arg_bufs[i];
        for(int i=0;i<ntypes;i++){
            if(arg_quoted[i] || arg_ptrs[i][0]!='@') continue;
            const char *f=file_arg(arg_ptrs[i]);
            if(!f){ put_str("error (no file)\n"); return; }
            arg_ptrs[i]=f;
        }
    } else {
        // Zero-arg call: the line must end after the sig.
        char *q=rest;
        while(isspace((unsigned char)*q) || *q==',') q++;
        if(*q){ put_str("error (arg count)\n"); return; }
    }
    uint8_t out[4096]; size_t out_len=0;
    if(ntypes>0){
        if(abi_encode_calldata(sig, arg_ptrs, ntypes, out, sizeof(out), &out_len, errb, sizeof(errb))!=0){ put_str("error ("); put_str(errb); put_str(")\n"); return; }
    } else {
        uint32_t sel=abi_selector(sig);
        out[0]=(sel>>24)&0xFF; out[1]=(sel>>16)&0xFF; out[2]=(sel>>8)&0xFF; out[3]=sel&0xFF; out_len=4;
    }
    put_str("0x");
    for(size_t i=0;i<out_len;i++) put_hex_byte(out[i]);
    put('\n');
}

static void handle_decode(const char *arg){
    char tmp[4096]; cpy_cap(tmp, arg, sizeof(tmp));
    char *s=tmp; while(isspace((unsigned char)*s)) s++;
    char *end=s+strlen(s); while(end>s && isspace((unsigned char)end[-1])){ end--; *end=0; }
    char *last_space=NULL;
    for(char *c=s; *c; c++) if(isspace((unsigned char)*c)) last_space=c;
    if(!last_space){ put_str("error (bad decode)\n"); return; }
    *last_space=0;
    char *types=s;
    char *hexd=last_space+1;
    while(isspace((unsigned char)*hexd)) hexd++;
    if(hexd[0]=='@'){ const char *f=file_arg(hexd); if(!f){ put_str("error (no file)\n"); return; } hexd=(char*)f; }
    char out_lines[4096]; char errb[128];
    if(abi_decode(types, hexd, out_lines, sizeof(out_lines), errb,sizeof(errb))!=0){ put_str("error ("); put_str(errb); put_str(")\n"); return; }
    put_str(out_lines);
}

static void handle_u256(const char *arg){
    char tmp[512]; cpy_cap(tmp, arg, sizeof(tmp));
    char *s=tmp; while(isspace((unsigned char)*s)) s++;
    char *e=s+strlen(s); while(e>s && isspace((unsigned char)e[-1])){ e--; *e=0; }
    int check_overflow=0, print_hex=0;
    char *str=s;
    size_t slen=strlen(str);
    // Trailing suffixes in any order: '!' (error on overflow), 'hex' (0x output).
    // 'hex' must be a separate word so hex digits like "0x..he.." never strip.
    for(;;){
        while(slen>0 && isspace((unsigned char)str[slen-1])){ str[--slen]=0; }
        if(slen>0 && str[slen-1]=='!'){ check_overflow=1; str[--slen]=0; continue; }
        if(slen>3 && strncmp(str+slen-3,"hex",3)==0 && isspace((unsigned char)str[slen-4])){ print_hex=1; slen-=3; str[slen]=0; continue; }
        break;
    }

#define MAX_TOK 64
    struct Tok { int kind; //0 num,1 op,2 paren
        u256_t num;
        char op[3];
    } toks[MAX_TOK];
    int ntok=0;
    char *p=str;
    int expect_operand=1;
    while(*p){
        while(isspace((unsigned char)*p)) p++;
        if(!*p) break;
        if(expect_operand){
            if(*p=='('){ toks[ntok].kind=2; toks[ntok].op[0]='('; toks[ntok].op[1]=0; ntok++; p++; expect_operand=1; continue; }
            if(*p=='-' || *p=='+'){
                char *np=p+1; while(isspace((unsigned char)*np)) np++;
                if(*np=='('){
                    u256_t z; u256_zero(&z);
                    toks[ntok].kind=0; toks[ntok].num=z; ntok++;
                    toks[ntok].kind=1; toks[ntok].op[0]=*p; toks[ntok].op[1]=0; ntok++; p++; expect_operand=1; continue;
                }
            }
            char *start=p;
            if(p[0]=='0' && (p[1]=='x'||p[1]=='X')){
                p+=2;
                while(isxdigit((unsigned char)*p)) p++;
            } else {
                while(isdigit((unsigned char)*p)) p++;
            }
            size_t nlen=p-start;
            if(nlen==0){ put_str("error (parse)\n"); return; }
            char numbuf[128]; if(nlen>=sizeof(numbuf)) nlen=sizeof(numbuf)-1; memcpy(numbuf,start,nlen); numbuf[nlen]=0;
            u256_t v;
            if(numbuf[0]=='0'&&(numbuf[1]=='x'||numbuf[1]=='X')){
                if(u256_parse_hex(&v,numbuf)!=0){ put_str("error (bad hex)\n"); return; }
            } else {
                if(u256_parse_dec(&v,numbuf)!=0){ put_str("error (bad dec)\n"); return; }
            }
            toks[ntok].kind=0; toks[ntok].num=v; ntok++;
            expect_operand=0;
        } else {
            if(*p==')'){ toks[ntok].kind=2; toks[ntok].op[0]=')'; toks[ntok].op[1]=0; ntok++; p++; expect_operand=0; continue; }
            if(p[0]=='*' && p[1]=='*'){ toks[ntok].kind=1; toks[ntok].op[0]='*'; toks[ntok].op[1]='*'; toks[ntok].op[2]=0; ntok++; p+=2; expect_operand=1; continue; }
            if(p[0]=='<' && p[1]=='<'){ toks[ntok].kind=1; toks[ntok].op[0]='<'; toks[ntok].op[1]='<'; toks[ntok].op[2]=0; ntok++; p+=2; expect_operand=1; continue; }
            if(p[0]=='>' && p[1]=='>'){ toks[ntok].kind=1; toks[ntok].op[0]='>'; toks[ntok].op[1]='>'; toks[ntok].op[2]=0; ntok++; p+=2; expect_operand=1; continue; }
            if(strchr("+-*/%&|^", *p)){ toks[ntok].kind=1; toks[ntok].op[0]=*p; toks[ntok].op[1]=0; ntok++; p++; expect_operand=1; continue; }
            put_str("error (bad op)\n"); return;
        }
        if(ntok>=MAX_TOK){ put_str("error (too complex)\n"); return; }
    }
    if(ntok==0){ put_str("error (parse)\n"); return; }
    struct Tok output[MAX_TOK]; int out_n=0;
    struct Tok opstack[MAX_TOK]; int op_n=0;
    for(int i=0;i<ntok;i++){
        if(toks[i].kind==0){ output[out_n++]=toks[i]; }
        else if(toks[i].kind==2){
            if(toks[i].op[0]=='('){ opstack[op_n++]=toks[i]; }
            else {
                while(op_n>0 && opstack[op_n-1].op[0]!='('){ output[out_n++]=opstack[--op_n]; }
                if(op_n==0){ put_str("error (mismatch)\n"); return; }
                op_n--;
            }
        } else {
            while(op_n>0 && opstack[op_n-1].kind==1){
                int p1=prec_of(toks[i].op), p2=prec_of(opstack[op_n-1].op);
                if( (is_right_assoc(toks[i].op) && p1 < p2) || (!is_right_assoc(toks[i].op) && p1 <= p2) ){
                    output[out_n++]=opstack[--op_n];
                } else break;
            }
            opstack[op_n++]=toks[i];
        }
    }
    while(op_n>0){ if(opstack[op_n-1].op[0]=='('){ put_str("error (mismatch)\n"); return; } output[out_n++]=opstack[--op_n]; }
    u256_t stack[64]; int sp=-1;
    int overflow_flag=0;
    for(int i=0;i<out_n;i++){
        if(output[i].kind==0){ stack[++sp]=output[i].num; }
        else {
            if(sp<1){ put_str("error (stack)\n"); return; }
            u256_t b=stack[sp--], a=stack[sp--], r;
            const char *op=output[i].op;
            if(strcmp(op,"+")==0){
                int carry=u256_add_carry(&r,&a,&b); if(carry) overflow_flag=1;
            } else if(strcmp(op,"-")==0){
                u256_sub(&r,&a,&b);
            } else if(strcmp(op,"*")==0){
                u512_t pr; u256_mul512(&pr,&a,&b);
                int overflow=0; for(int k=8;k<16;k++) if(pr.d[k]) overflow=1;
                if(overflow) overflow_flag=1;
                for(int k=0;k<8;k++) r.d[k]=pr.d[k];
            } else if(strcmp(op,"/")==0){
                if(u256_is_zero(&b)){ put_str("error (div by zero)\n"); return; }
                u256_t q, rem; u256_divmod(&q,&rem,&a,&b); r=q;
            } else if(strcmp(op,"%")==0){
                if(u256_is_zero(&b)){ put_str("error (div by zero)\n"); return; }
                u256_t q, rem; u256_divmod(&q,&rem,&a,&b); r=rem;
            } else if(strcmp(op,"**")==0){
                u256_t base=a, exp=b, res; u256_one(&res);
                u256_t cur_base=base;
                u256_t cur_exp=exp;
                int ov=0;
                while(!u256_is_zero(&cur_exp)){
                    if(cur_exp.d[0]&1){
                        u512_t pr2; u256_mul512(&pr2,&res,&cur_base); for(int k=8;k<16;k++) if(pr2.d[k]) ov=1; for(int k=0;k<8;k++) res.d[k]=pr2.d[k];
                    }
                    u512_t pr3; u256_mul512(&pr3,&cur_base,&cur_base); for(int k=8;k<16;k++) if(pr3.d[k]) ov=1;
                    for(int k=0;k<8;k++) cur_base.d[k]=pr3.d[k];
                    uint32_t c=0; for(int k=7;k>=0;k--){ uint32_t nc=cur_exp.d[k]&1; cur_exp.d[k]=(cur_exp.d[k]>>1)|(c<<31); c=nc; }
                }
                r=res;
                if(ov) overflow_flag=1;
            } else if(strcmp(op,"&")==0){ u256_and(&r,&a,&b); }
            else if(strcmp(op,"|")==0){ u256_or(&r,&a,&b); }
            else if(strcmp(op,"^")==0){ u256_xor(&r,&a,&b); }
            else if(strcmp(op,"<<")==0){
                uint32_t sh=b.d[0]; if(b.d[1]||b.d[2]||b.d[3]||b.d[4]||b.d[5]||b.d[6]||b.d[7]) sh=256;
                if(sh>=256) u256_zero(&r); else u256_shl(&r,&a,sh);
                if(sh>=1 && !u256_is_zero(&a)){
                    if(u256_bitlen(&a) + (int)sh > 256) overflow_flag=1;
                }
            } else if(strcmp(op,">>")==0){
                uint32_t sh=b.d[0]; if(b.d[1]||b.d[2]||b.d[3]||b.d[4]||b.d[5]||b.d[6]||b.d[7]) sh=256;
                if(sh>=256) u256_zero(&r); else u256_shr(&r,&a,sh);
            } else { put_str("error (op)\n"); return; }
            stack[++sp]=r;
        }
    }
    if(sp!=0){ put_str("error (expr)\n"); return; }
    if(check_overflow && overflow_flag){ put_str("error (overflow)\n"); return; }
    u256_t res=stack[0];
    if(print_hex){ char hexb[68]; u256_to_hex_trim(&res, hexb); put_str(hexb); put('\n'); }
    else { char dec[80]; u256_to_dec(&res, dec); put_str(dec); put('\n'); }
#undef MAX_TOK
}

// Strict uint64: decimal or 0x-hex, full match, no sign, no octal.
static int parse_nonce(const char *s, unsigned long long *out){
    while(isspace((unsigned char)*s)) s++;
    int hex=0;
    if(s[0]=='0'&&(s[1]=='x'||s[1]=='X')){ hex=1; s+=2; }
    if(!*s) return -1;
    unsigned long long v=0;
    if(hex){
        if(!isxdigit((unsigned char)*s)) return -1;
        while(isxdigit((unsigned char)*s)){
            int d=hex_nibble(*s++);
            if(v > 0xFFFFFFFFFFFFFFFULL/16) return -1;
            v=v*16+(unsigned)d;
        }
    } else {
        if(!isdigit((unsigned char)*s)) return -1;
        while(isdigit((unsigned char)*s)){
            int d=*s++-'0';
            if(v > 0xFFFFFFFFFFFFFFFFULL/10) return -1;
            unsigned long long nv=v*10+(unsigned)d;
            if(nv < v) return -1;
            v=nv;
        }
    }
    while(isspace((unsigned char)*s)) s++;
    if(*s) return -1;
    *out=v;
    return 0;
}

static void handle_create(const char *arg){
    char tmp[512]; cpy_cap(tmp, arg, sizeof(tmp));
    // Strict: exactly two tokens (address, nonce).
    char *toks[2]; int nt=0;
    char *p=tmp;
    while(*p && nt<2){
        while(isspace((unsigned char)*p) || *p==',') p++;
        if(!*p) break;
        toks[nt++]=p;
        while(*p && !isspace((unsigned char)*p) && *p!=',') p++;
        if(*p){ *p=0; p++; }
    }
    while(isspace((unsigned char)*p) || *p==',') p++;
    if(nt!=2 || *p){ put_str("error (bad create)\n"); return; }
    uint8_t addr[20];
    if(parse_hex_fixed(toks[0], addr, 20)!=0){ put_str("error (bad address)\n"); return; }
    unsigned long long nonce;
    if(parse_nonce(toks[1], &nonce)!=0){ put_str("error (bad nonce)\n"); return; }
    uint8_t rlp[64]; int rlen=0;
    uint8_t payload[64]; int plen=0;
    payload[plen++]=0x94; memcpy(payload+plen, addr,20); plen+=20;
    if(nonce==0){
        payload[plen++]=0x80;
    } else if(nonce < 0x80){
        payload[plen++]=(uint8_t)nonce;
    } else {
        uint8_t nb[8]; int nlen=0;
        unsigned long long t=nonce;
        while(t){ nb[nlen++]=(uint8_t)(t&0xFF); t>>=8; }
        payload[plen++]=0x80 + nlen;
        for(int i=nlen-1;i>=0;i--) payload[plen++]=nb[i];
    }
    if(plen <56){
        rlp[rlen++]=0xC0 + plen;
        memcpy(rlp+rlen, payload, plen); rlen+=plen;
    } else {
        put_str("error (bad rlp)\n"); return; // unreachable: plen <= 31
    }
    uint8_t h[32]; keccak256(rlp, rlen, h);
    put_str("0x");
    for(int i=12;i<32;i++) put_hex_byte(h[i]);
    put('\n');
}

static void handle_create2(const char *arg){
    char tmp[512]; cpy_cap(tmp, arg, sizeof(tmp));
    char *tokens[3]; int nt=0;
    char *p=tmp;
    while(*p && nt<3){
        while(isspace((unsigned char)*p) || *p==',') p++;
        if(!*p) break;
        tokens[nt++]=p;
        while(*p && !isspace((unsigned char)*p) && *p!=',') p++;
        if(*p){ *p=0; p++; }
    }
    while(isspace((unsigned char)*p) || *p==',') p++;
    if(nt!=3 || *p){ put_str("error (bad create2)\n"); return; }
    for(int i=0;i<3;i++){
        if(tokens[i][0]!='@') continue;
        const char *f=file_arg(tokens[i]);
        if(!f){ put_str("error (no file)\n"); return; }
        tokens[i]=(char*)f;
    }
    uint8_t deployer[20], salt[32], initcodehash[32];
    if(parse_hex_fixed(tokens[0], deployer,20)!=0 || parse_hex_fixed(tokens[1], salt,32)!=0 || parse_hex_fixed(tokens[2], initcodehash,32)!=0){
        put_str("error (bad hex)\n"); return;
    }
    uint8_t data[85]; data[0]=0xFF; memcpy(data+1,deployer,20); memcpy(data+21,salt,32); memcpy(data+53,initcodehash,32);
    uint8_t h[32]; keccak256(data,85,h);
    put_str("0x");
    for(int i=12;i<32;i++) put_hex_byte(h[i]);
    put('\n');
}

extern const uint8_t builtin_erc20_code[];
extern const size_t builtin_erc20_code_len;
extern const uint8_t builtin_amm_code[];
extern const size_t builtin_amm_code_len;
// Layout-identical to builtin_slot_t (asserted in builtins.c).
extern const evm_fixture_slot_t builtin_erc20_fixture[];
extern const size_t builtin_erc20_fixture_len;
extern const uint8_t builtin_alice[20];
extern const uint8_t builtin_bob[20];
extern const uint8_t builtin_spender[20];

static int load_builtin(const char *name, const uint8_t **code, size_t *len){
    if(strcmp(name,"erc20")==0){ *code=builtin_erc20_code; *len=builtin_erc20_code_len; return 0; }
    if(strcmp(name,"amm")==0 || strcmp(name,"uniswap")==0){ *code=builtin_amm_code; *len=builtin_amm_code_len; return 0; }
    return -1;
}

static void put_u256_hex(const u256_t *v){
    for(int i=7;i>=0;i--){
        uint32_t w=v->d[i];
        put_hex_byte((w>>24)&0xFF); put_hex_byte((w>>16)&0xFF);
        put_hex_byte((w>>8)&0xFF); put_hex_byte(w&0xFF);
    }
}

// run <name> <calldata-hex> [value] [from] [chainid]
// from: alice (default), bob, or 0x + 40 hex. value: decimal or 0x hex.
// Success output: 0x<retdata>\n<gas>\n then LOG/SLOT lines.
// Revert: REVERT 0x<data>\n<gas>\n (logs/storage discarded, like the chain).
static void handle_run(const char *arg){
    // Tokenize in place: at most 5 tokens, no copies (stack budget).
    char tmp[2048]; cpy_cap(tmp, arg, sizeof(tmp));
    const char *tok[5]; int ntok=0;
    char *p=tmp;
    while(ntok<5){
        while(isspace((unsigned char)*p)) p++;
        if(!*p) break;
        tok[ntok++]=p;
        while(*p && !isspace((unsigned char)*p)) p++;
        if(*p) *p++=0;
    }
    while(isspace((unsigned char)*p)) p++;
    if(ntok<2 || *p){ put_str("error (bad run)\n"); return; }
    const char *name=tok[0], *cdstr=tok[1];
    const char *valuestr=ntok>2?tok[2]:"0";
    const char *fromstr=ntok>3?tok[3]:"alice";
    const char *chainstr=ntok>4?tok[4]:"1";

    const uint8_t *code=NULL; size_t code_len=0;
    const evm_fixture_slot_t *fixture=NULL; size_t fixture_len=0;
    if(load_builtin(name, &code, &code_len)!=0){
        put_str("error (unknown contract)\n"); return;
    }
    if(strcmp(name,"erc20")==0){
        fixture=builtin_erc20_fixture; fixture_len=builtin_erc20_fixture_len;
    }

    if(cdstr[0]=='@'){ const char *f=file_arg(cdstr); if(!f){ put_str("error (no file)\n"); return; } cdstr=f; }
    const char *hx=cdstr;
    if(hx[0]=='0'&&(hx[1]=='x'||hx[1]=='X')) hx+=2;
    size_t hl=strlen(hx);
    if(hl%2!=0 || hl/2>EVM_CALLDATA_MAX){ put_str("error (bad calldata)\n"); return; }
    uint8_t calldata[EVM_CALLDATA_MAX]; size_t calldata_len=hl/2;
    for(size_t i=0;i<calldata_len;i++){
        int hi=hex_nibble(hx[i*2]), lo=hex_nibble(hx[i*2+1]);
        if(hi<0||lo<0){ put_str("error (bad calldata)\n"); return; }
        calldata[i]=(uint8_t)((hi<<4)|lo);
    }

    u256_t callvalue; u256_zero(&callvalue);
    if(strchr(valuestr,'x')||strchr(valuestr,'X')){
        if(u256_parse_hex(&callvalue,valuestr)!=0){ put_str("error (bad value)\n"); return; }
    } else {
        if(u256_parse_dec(&callvalue,valuestr)!=0){ put_str("error (bad value)\n"); return; }
    }

    u256_t caller; u256_zero(&caller);
    if(strcmp(fromstr,"alice")==0 || strcmp(fromstr,"bob")==0 || strcmp(fromstr,"spender")==0){
        const uint8_t *ab=strcmp(fromstr,"bob")==0?builtin_bob:
            (strcmp(fromstr,"spender")==0?builtin_spender:builtin_alice);
        for(int i=0;i<5;i++)
            caller.d[i]=((uint32_t)ab[19-i*4-3]<<24)|((uint32_t)ab[19-i*4-2]<<16)|
                        ((uint32_t)ab[19-i*4-1]<<8)|ab[19-i*4];
    } else {
        const char *ax=fromstr;
        if(ax[0]=='0'&&(ax[1]=='x'||ax[1]=='X')) ax+=2;
        if(strlen(ax)!=40){ put_str("error (bad from)\n"); return; }
        uint8_t ab[20];
        for(int i=0;i<20;i++){
            int hi=hex_nibble(ax[i*2]), lo=hex_nibble(ax[i*2+1]);
            if(hi<0||lo<0){ put_str("error (bad from)\n"); return; }
            ab[i]=(uint8_t)((hi<<4)|lo);
        }
        for(int i=0;i<5;i++)
            caller.d[i]=((uint32_t)ab[19-i*4-3]<<24)|((uint32_t)ab[19-i*4-2]<<16)|
                        ((uint32_t)ab[19-i*4-1]<<8)|ab[19-i*4];
    }

    char *end=NULL;
    unsigned long long chainid=strtoull(chainstr,&end,0);
    if(!end || *end){ put_str("error (bad chainid)\n"); return; }

    static evm_t evm; // BSS, not stack (~13 KB)
    if(evm_init(&evm, code, code_len, calldata, calldata_len,
             &callvalue, &caller, (uint64_t)chainid, fixture, fixture_len)!=0){
        put_str("error (too big)\n"); return;
    }
    int rc=evm_run(&evm);
    if(rc==-2){ put_str("error (invalid opcode)\n"); return; }
    if(rc==-3){ put_str("error (stack)\n"); return; }
    if(rc==-4){ put_str("error (bad jump)\n"); return; }
    if(rc==-5){ put_str("error (mem)\n"); return; }
    if(rc==-6){ put_str("error (storage full)\n"); return; }
    if(rc==-7){ put_str("error (unsupported op)\n"); return; }
    if(rc==-8){ put_str("error (invalid)\n"); return; }
    if(rc==-10){ put_str("error (out of gas)\n"); return; }
    if(rc==-11){ put_str("error (log overflow)\n"); return; }
    if(rc==-12){ put_str("error (chain precompile)\n"); return; }
    if(rc<0){ put_str("error (evm)\n"); return; }
    const uint8_t *ret=evm.mem+evm.ret_off;
    char gasbuf[32]; snprintf(gasbuf,sizeof(gasbuf),"%llu", (unsigned long long)evm.gas_used);
    if(evm.reverted){
        put_str("REVERT 0x");
        for(size_t i=0;i<evm.ret_len;i++) put_hex_byte(ret[i]);
        put('\n'); put_str(gasbuf); put('\n');
        return;
    }
    put_str("0x");
    for(size_t i=0;i<evm.ret_len;i++) put_hex_byte(ret[i]);
    put('\n'); put_str(gasbuf); put('\n');
    for(int i=0;i<evm.nlogs;i++){
        char nb[16]; snprintf(nb,sizeof(nb),"LOG %d",evm.logs[i].ntopics);
        put_str(nb);
        for(int t=0;t<evm.logs[i].ntopics;t++){
            put(' ');
            for(int b=0;b<32;b++) put_hex_byte(evm.logs[i].topics[t][b]);
        }
        put(' ');
        for(size_t b=0;b<evm.logs[i].data_len;b++) put_hex_byte(evm.logs[i].data[b]);
        put('\n');
    }
    for(int i=0;i<evm.nslots;i++){
        u256_t orig; evm_slot_orig(&evm,&evm.storage[i].key,&orig);
        if(u256_eq(&orig,&evm.storage[i].val)) continue; // not dirty
        put_str("SLOT ");
        put_u256_hex(&evm.storage[i].key); put(' ');
        put_u256_hex(&evm.storage[i].val); put('\n');
    }
}

static void handle_cmd(const char *line){
    char tmp[4096]; cpy_cap(tmp, line, sizeof(tmp));
    char *p=tmp;
    while(isspace((unsigned char)*p)) p++;
    char *cmd_end=p;
    while(*cmd_end && !isspace((unsigned char)*cmd_end)) cmd_end++;
    *cmd_end=0;
    char cmd[32]; cpy_cap(cmd, p, sizeof(cmd));
    char *arg = cmd_end+1;
    if(strcmp(cmd,"eth")==0){
        while(isspace((unsigned char)*arg)) arg++;
        char *sub_end=arg;
        while(*sub_end && !isspace((unsigned char)*sub_end)) sub_end++;
        *sub_end=0;
        char sub[32]; cpy_cap(sub, arg, sizeof(sub));
        arg=sub_end+1;
        if(strcmp(sub,"keccak")==0) handle_keccak(arg);
        else if(strcmp(sub,"selector")==0) handle_selector(arg);
        else if(strcmp(sub,"encode")==0) handle_encode(arg);
        else if(strcmp(sub,"decode")==0) handle_decode(arg);
        else if(strcmp(sub,"u256")==0) handle_u256(arg);
        else if(strcmp(sub,"create")==0) handle_create(arg);
        else if(strcmp(sub,"create2")==0) handle_create2(arg);
        else if(strcmp(sub,"run")==0) handle_run(arg);
        else { put_str("error (unknown cmd)\n"); }
        return;
    }
    if(strcmp(cmd,"keccak")==0) handle_keccak(arg);
    else if(strcmp(cmd,"selector")==0) handle_selector(arg);
    else if(strcmp(cmd,"encode")==0) handle_encode(arg);
    else if(strcmp(cmd,"decode")==0) handle_decode(arg);
    else if(strcmp(cmd,"u256")==0) handle_u256(arg);
    else if(strcmp(cmd,"create")==0) handle_create(arg);
    else if(strcmp(cmd,"create2")==0) handle_create2(arg);
    else if(strcmp(cmd,"run")==0) handle_run(arg);
    else { put_str("error (unknown cmd)\n"); }
}

#ifndef BRIDGE
static int eth_eval(void){
    handle_cmd(eth_in);
    return 0;
}
#endif

#ifdef BRIDGE
int main(void){
    for(;;){
        rv_ecall2(93,0,0,0);
        int n=gather_call();
        put('\n');
        put(TOOL_RESPONSE);
        put('\n');
        if(n<0){
            if(n==-2) put_str("error (too long)\n");
            else put_str("error\n");
        } else {
            char cmdline[2048];
            if(extract_param(call_buf,"cmd",cmdline,sizeof(cmdline))==0){
                handle_cmd(cmdline);
            } else {
                char first[2048];
                const char *pp=strstr(call_buf,"<parameter");
                if(pp){
                    const char *gt=strchr(pp,'>');
                    if(gt){
                        gt++;
                        if(*gt=='\n') gt++;
                        const char *ep=strstr(gt,"</parameter>");
                        if(ep){
                            size_t l=ep-gt;
                            if(l>=sizeof(first)) l=sizeof(first)-1;
                            memcpy(first,gt,l); first[l]=0;
                            char *ee=first+l;
                            while(ee>first && (ee[-1]=='\n'||ee[-1]=='\r'||isspace((unsigned char)ee[-1]))){ ee--; *ee=0; }
                            handle_cmd(first);
                        } else put_str("error (bad param)\n");
                    } else put_str("error (bad param)\n");
                } else {
                    put_str("error (no cmd)\n");
                }
            }
        }
        put(TOOL_RESPONSE_END);
        put('\n');
        put(THINK);
        put('\n');
        put('\n');
        put(THINK_END);
        put('\n');
    }
}
#elif !defined(HOST_FUZZ)
int main(void){
    return eth_eval();
}
#endif

#ifdef HOST_FUZZ
// Test-only entry point for the host ASan harness (never in firmware images).
int host_cmd(const char *cmd){ handle_cmd(cmd); return 0; }
// Raw-bytecode entry: run <hex> with empty calldata/caller/fixture, print
// 0x<retdata>\n<gas>\n (or REVERT/error). For opcode-level unit tests.
int host_raw(const char *hex){
    while(*hex==' '||*hex=='\t') hex++;
    if(hex[0]=='0'&&(hex[1]=='x'||hex[1]=='X')) hex+=2;
    size_t hl=strlen(hex);
    static uint8_t code[4096];
    if(hl%2!=0 || hl/2>sizeof(code)){ put_str("error (bad hex)\n"); return 0; }
    for(size_t i=0;i<hl/2;i++){
        int hi=hex_nibble(hex[i*2]), lo=hex_nibble(hex[i*2+1]);
        if(hi<0||lo<0){ put_str("error (bad hex)\n"); return 0; }
        code[i]=(uint8_t)((hi<<4)|lo);
    }
    static evm_t evm;
    u256_t zero; u256_zero(&zero);
    if(evm_init(&evm, code, hl/2, NULL, 0, &zero, &zero, 1, NULL, 0)!=0){
        put_str("error (too big)\n"); return 0;
    }
    int rc=evm_run(&evm);
    if(rc!=0){ put_str("error (evm)\n"); return 0; }
    const uint8_t *ret=evm.mem+evm.ret_off;
    char gasbuf[32]; snprintf(gasbuf,sizeof(gasbuf),"%llu",(unsigned long long)evm.gas_used);
    if(evm.reverted) put_str("REVERT ");
    put_str("0x");
    for(size_t i=0;i<evm.ret_len;i++) put_hex_byte(ret[i]);
    put('\n'); put_str(gasbuf); put('\n');
    return 0;
}
#endif
