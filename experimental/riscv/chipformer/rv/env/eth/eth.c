#include <string.h>
#include <stdio.h>
#include <ctype.h>
#include <stdlib.h>
#include "rt.h"
#include "u256.h"
#include "keccak.h"
#include "abi.h"
#include "evm.h"

#define TEXT ((const volatile unsigned char *)0x8000)
#define ROWS 80
enum { TOOL_RESPONSE = 256, TOOL_RESPONSE_END, THINK, THINK_END };

// Non-bridge gate-1 input: the NUL-terminated cmd line in eth_in (RAM 0x100;
// the host pokes it into the snapshot), exactly like calc_in in calc.c.
#ifndef BRIDGE
char eth_in[2048] __attribute__((section(".ethin")));
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
    strncpy(tmp, arg, sizeof(tmp)-1); tmp[sizeof(tmp)-1]=0;
    char *s=tmp;
    while(isspace((unsigned char)*s)) s++;
    char *e=s+strlen(s);
    while(e>s && isspace((unsigned char)e[-1])){ e--; *e=0; }
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
    char tmp[256]; strncpy(tmp,arg,sizeof(tmp)-1); tmp[sizeof(tmp)-1]=0;
    char *s=tmp; while(isspace((unsigned char)*s)) s++;
    char *e=s+strlen(s); while(e>s && isspace((unsigned char)e[-1])){ e--; *e=0; }
    if(!*s){ put_str("error (bad sig)\n"); return; }
    uint32_t sel=abi_selector(s);
    put_str("0x");
    put_hex_byte((sel>>24)&0xFF); put_hex_byte((sel>>16)&0xFF); put_hex_byte((sel>>8)&0xFF); put_hex_byte(sel&0xFF);
    put('\n');
}

static void handle_encode(const char *arg){
    char tmp[2048]; strncpy(tmp,arg,sizeof(tmp)-1); tmp[sizeof(tmp)-1]=0;
    char *s=tmp; while(isspace((unsigned char)*s)) s++;
    char *lp=strchr(s,'(');
    if(!lp){ put_str("error (bad encode)\n"); return; }
    char *rp=strchr(lp,')');
    if(!rp){ put_str("error (bad encode)\n"); return; }
    size_t siglen = rp - s +1;
    char sig[512]; memcpy(sig,s,siglen); sig[siglen]=0;
    char *rest = rp+1;
    while(isspace((unsigned char)*rest)) rest++;
    char name[128]; char types[16][64]; int ntypes=0;
    char errb[128];
    if(abi_parse_sig(sig, name,sizeof(name), types, &ntypes, errb,sizeof(errb))!=0){ put_str("error (bad sig)\n"); return; }
    const char *arg_ptrs[16];
    char arg_bufs[16][256];
    if(ntypes>0){
        int idx=0;
        char *p=rest;
        while(*p && idx<ntypes){
            while(isspace((unsigned char)*p) || *p==',') p++;
            if(!*p) break;
            char buf[256]; int bi=0;
            if(*p=='"'||*p=='\''){
                char q=*p++; while(*p && *p!=q && bi<255){ buf[bi++]=*p++; } if(*p==q) p++; buf[bi]=0;
            } else {
                while(*p && !isspace((unsigned char)*p) && *p!=',' && bi<255){ buf[bi++]=*p++; } buf[bi]=0;
            }
            if(bi==0) break;
            // Byte copy, not strcpy (picolibc strcpy faults on misaligned LW/SW).
            for(int bi2=0;bi2<=bi;bi2++) arg_bufs[idx][bi2]=buf[bi2];
            arg_ptrs[idx]=arg_bufs[idx];
            idx++;
        }
        if(idx!=ntypes){ put_str("error (arg count)\n"); return; }
        for(int i=0;i<ntypes;i++) arg_ptrs[i]=arg_bufs[i];
    }
    uint8_t out[4096]; size_t out_len=0;
    if(ntypes>0){
        if(abi_encode_calldata(sig, arg_ptrs, ntypes, out, &out_len, errb, sizeof(errb))!=0){ put_str("error "); put_str(errb); put('\n'); return; }
    } else {
        uint32_t sel=abi_selector(sig);
        out[0]=(sel>>24)&0xFF; out[1]=(sel>>16)&0xFF; out[2]=(sel>>8)&0xFF; out[3]=sel&0xFF; out_len=4;
    }
    put_str("0x");
    for(size_t i=0;i<out_len;i++) put_hex_byte(out[i]);
    put('\n');
}

static void handle_decode(const char *arg){
    char tmp[4096]; strncpy(tmp,arg,sizeof(tmp)-1); tmp[sizeof(tmp)-1]=0;
    char *s=tmp; while(isspace((unsigned char)*s)) s++;
    char *end=s+strlen(s); while(end>s && isspace((unsigned char)end[-1])){ end--; *end=0; }
    char *last_space=NULL;
    for(char *c=s; *c; c++) if(isspace((unsigned char)*c)) last_space=c;
    if(!last_space){ put_str("error (bad decode)\n"); return; }
    *last_space=0;
    char *types=s;
    char *hexd=last_space+1;
    while(isspace((unsigned char)*hexd)) hexd++;
    char types_clean[512]; int p=0;
    for(int i=0; types[i] && p<511;i++) if(!isspace((unsigned char)types[i])) types_clean[p++]=types[i];
    types_clean[p]=0;
    char out_lines[4096]; char errb[128];
    if(abi_decode(types_clean, hexd, out_lines, sizeof(out_lines), errb,sizeof(errb))!=0){ put_str("error "); put_str(errb); put('\n'); return; }
    put_str(out_lines);
}

static void handle_u256(const char *arg){
    char tmp[512]; strncpy(tmp,arg,sizeof(tmp)-1); tmp[sizeof(tmp)-1]=0;
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
                    toks[ntok].kind=1; strcpy(toks[ntok].op, (*p=='-'?"-":"+")); ntok++; p++; expect_operand=1; continue;
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
            if(*p==')'){ toks[ntok].kind=2; strcpy(toks[ntok].op,")"); ntok++; p++; expect_operand=0; continue; }
            if(p[0]=='*' && p[1]=='*'){ toks[ntok].kind=1; strcpy(toks[ntok].op,"**"); ntok++; p+=2; expect_operand=1; continue; }
            if(p[0]=='<' && p[1]=='<'){ toks[ntok].kind=1; strcpy(toks[ntok].op,"<<"); ntok++; p+=2; expect_operand=1; continue; }
            if(p[0]=='>' && p[1]=='>'){ toks[ntok].kind=1; strcpy(toks[ntok].op,">>"); ntok++; p+=2; expect_operand=1; continue; }
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

static void handle_create(const char *arg){
    char tmp[512]; strncpy(tmp,arg,sizeof(tmp)-1); tmp[sizeof(tmp)-1]=0;
    char *p=tmp;
    char *tok1=NULL, *tok2=NULL;
    while(isspace((unsigned char)*p)) p++;
    tok1=p;
    while(*p && !isspace((unsigned char)*p) && *p!=',') p++;
    if(*p){ *p=0; p++; }
    while(isspace((unsigned char)*p) || *p==',') p++;
    tok2=p;
    char *e=tok2+strlen(tok2);
    while(e>tok2 && isspace((unsigned char)e[-1])){ e--; *e=0; }
    if(!*tok1 || !*tok2){ put_str("error (bad create)\n"); return; }
    uint8_t addr[20];
    const char *s=tok1;
    while(isspace((unsigned char)*s)) s++;
    if(s[0]=='0'&&(s[1]=='x'||s[1]=='X')) s+=2;
    if(strlen(s)!=40){ put_str("error (bad address)\n"); return; }
    for(int i=0;i<20;i++){
        int hi=hex_nibble(s[i*2]), lo=hex_nibble(s[i*2+1]);
        if(hi<0||lo<0){ put_str("error (bad hex)\n"); return; }
        addr[i]=(hi<<4)|lo;
    }
    unsigned long long nonce=strtoull(tok2,NULL,0);
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
        put_str("error (rlp)\n"); return;
    }
    uint8_t h[32]; keccak256(rlp, rlen, h);
    put_str("0x");
    for(int i=12;i<32;i++) put_hex_byte(h[i]);
    put('\n');
}

static void handle_create2(const char *arg){
    char tmp[512]; strncpy(tmp,arg,sizeof(tmp)-1); tmp[sizeof(tmp)-1]=0;
    char *tokens[3]; int nt=0;
    char *p=tmp;
    while(*p && nt<3){
        while(isspace((unsigned char)*p) || *p==',') p++;
        if(!*p) break;
        tokens[nt++]=p;
        while(*p && !isspace((unsigned char)*p) && *p!=',') p++;
        if(*p){ *p=0; p++; }
    }
    if(nt!=3){ put_str("error (bad create2)\n"); return; }
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
extern const size_t builtin_erc20_len;
extern const uint8_t builtin_amm_code[];
extern const size_t builtin_amm_len;

static int load_builtin(const char *name, const uint8_t **code, size_t *len){
    if(strcmp(name,"erc20")==0){ *code=builtin_erc20_code; *len=builtin_erc20_len; return 0; }
    if(strcmp(name,"amm")==0 || strcmp(name,"uniswap")==0){ *code=builtin_amm_code; *len=builtin_amm_len; return 0; }
    return -1;
}

static void handle_run(const char *arg){
    char tmp[4096]; strncpy(tmp,arg,sizeof(tmp)-1); tmp[sizeof(tmp)-1]=0;
    char *p=tmp;
    while(isspace((unsigned char)*p)) p++;
    char *name_end=p;
    while(*name_end && !isspace((unsigned char)*name_end)) name_end++;
    if(name_end==p){ put_str("error (bad run)\n"); return; }
    *name_end=0;
    char name[64]; strncpy(name,p,sizeof(name)-1); name[sizeof(name)-1]=0;
    p=name_end+1;
    while(isspace((unsigned char)*p)) p++;
    char *calldata_str=p;
    char *cd_end=calldata_str;
    while(*cd_end && !isspace((unsigned char)*cd_end)) cd_end++;
    char *rest=cd_end;
    char calldata_hex[8192];
    size_t cd_len_str = cd_end - calldata_str;
    if(cd_len_str>=sizeof(calldata_hex)) cd_len_str=sizeof(calldata_hex)-1;
    memcpy(calldata_hex, calldata_str, cd_len_str); calldata_hex[cd_len_str]=0;
    while(*rest && isspace((unsigned char)*rest)) rest++;
    char value_str[64]="0", chain_str[32]="1";
    if(*rest){
        char *tok1=rest;
        char *tok1_end=tok1;
        while(*tok1_end && !isspace((unsigned char)*tok1_end)) tok1_end++;
        *tok1_end=0;
        char *maybe_chain=tok1_end+1;
        while(*maybe_chain && isspace((unsigned char)*maybe_chain)) maybe_chain++;
        if(*maybe_chain){
            strncpy(value_str, tok1, sizeof(value_str)-1);
            char *tok2=maybe_chain;
            char *tok2_end=tok2;
            while(*tok2_end && !isspace((unsigned char)*tok2_end)) tok2_end++;
            *tok2_end=0;
            strncpy(chain_str, tok2, sizeof(chain_str)-1);
        } else {
            strncpy(value_str, tok1, sizeof(value_str)-1);
        }
    }
    uint8_t calldata[4096]; size_t calldata_len=0;
    const char *hx=calldata_hex;
    while(isspace((unsigned char)*hx)) hx++;
    if(hx[0]=='0'&&(hx[1]=='x'||hx[1]=='X')) hx+=2;
    size_t hl=strlen(hx);
    while(hl>0 && isspace((unsigned char)hx[hl-1])) hl--;
    if(hl%2!=0){ put_str("error (bad calldata hex)\n"); return; }
    calldata_len=hl/2;
    if(calldata_len>sizeof(calldata)){ put_str("error (calldata too big)\n"); return; }
    for(size_t i=0;i<calldata_len;i++){
        int hi=hex_nibble(hx[i*2]), lo=hex_nibble(hx[i*2+1]);
        if(hi<0||lo<0){ put_str("error (bad hex)\n"); return; }
        calldata[i]=(hi<<4)|lo;
    }
    u256_t callvalue; u256_zero(&callvalue);
    if(value_str[0]){
        if(strchr(value_str,'x')||strchr(value_str,'X')){
            if(u256_parse_hex(&callvalue,value_str)!=0){ put_str("error (bad value)\n"); return; }
        } else {
            if(u256_parse_dec(&callvalue,value_str)!=0){ put_str("error (bad value)\n"); return; }
        }
    }
    uint64_t chainid=strtoull(chain_str,NULL,0);
    const uint8_t *code=NULL; size_t code_len=0;
    if(load_builtin(name, &code, &code_len)!=0){
        put_str("error (unknown contract)\n"); return;
    }
    evm_t evm;
    evm_init(&evm, code, code_len, calldata, calldata_len, callvalue, chainid);
    int rc=evm_run(&evm);
    if(rc==-2){ put_str("error (invalid opcode)\n"); return; }
    if(rc==-4){ put_str("error (bad jump)\n"); return; }
    if(rc==-5){ put_str("error (mem)\n"); return; }
    if(rc==-7){ put_str("error (chain-specific precompile)\n"); return; }
    if(rc==-10){ put_str("error (out of gas)\n"); return; }
    if(rc<0){ put_str("error (evm)\n"); return; }
    if(evm.reverted){
        put_str("REVERT 0x");
        for(size_t i=0;i<evm.ret_len;i++) put_hex_byte(evm.ret_buf[i]);
        put('\n');
        char gasbuf[32]; snprintf(gasbuf,sizeof(gasbuf),"%llu", (unsigned long long)evm.gas_used);
        put_str(gasbuf); put('\n');
        return;
    }
    put_str("0x");
    for(size_t i=0;i<evm.ret_len;i++) put_hex_byte(evm.ret_buf[i]);
    put('\n');
    char gasbuf[32]; snprintf(gasbuf,sizeof(gasbuf),"%llu", (unsigned long long)evm.gas_used);
    put_str(gasbuf); put('\n');
}

static void handle_cmd(const char *line){
    char tmp[4096]; strncpy(tmp,line,sizeof(tmp)-1); tmp[sizeof(tmp)-1]=0;
    char *p=tmp;
    while(isspace((unsigned char)*p)) p++;
    char *cmd_end=p;
    while(*cmd_end && !isspace((unsigned char)*cmd_end)) cmd_end++;
    *cmd_end=0;
    char cmd[32]; strncpy(cmd,p,sizeof(cmd)-1); cmd[sizeof(cmd)-1]=0;
    char *arg = cmd_end+1;
    if(strcmp(cmd,"eth")==0){
        while(isspace((unsigned char)*arg)) arg++;
        char *sub_end=arg;
        while(*sub_end && !isspace((unsigned char)*sub_end)) sub_end++;
        *sub_end=0;
        char sub[32]; strncpy(sub,arg,sizeof(sub)-1); sub[sizeof(sub)-1]=0;
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
#else
int main(void){
    return eth_eval();
}
#endif
