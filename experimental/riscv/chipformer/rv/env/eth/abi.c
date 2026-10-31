#include "abi.h"
#include "keccak.h"
#include <string.h>
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>

static void trim(char *s){ char *a=s; while(isspace((unsigned char)*a)) a++; if(a!=s) memmove(s,a,strlen(a)+1); char *e=s+strlen(s); while(e>s && isspace((unsigned char)e[-1])){ e[-1]=0; e--; } }

uint32_t abi_selector(const char *sig){
    // canonicalize: remove spaces
    char canon[512];
    int p=0;
    for(int i=0; sig[i] && p<511; i++) if(!isspace((unsigned char)sig[i])) canon[p++]=sig[i];
    canon[p]=0;
    uint8_t h[32];
    keccak256((uint8_t*)canon, p, h);
    return ((uint32_t)h[0]<<24)|((uint32_t)h[1]<<16)|((uint32_t)h[2]<<8)|h[3];
}

int abi_parse_sig(const char *sig, char *name_out, size_t name_cap, char types[][64], int *ntypes, char *errbuf, size_t errlen){
    const char *lp=strchr(sig,'(');
    const char *rp=strrchr(sig,')');
    if(!lp || !rp || rp<lp){ if(errbuf) snprintf(errbuf,errlen,"bad sig"); return -1; }
    size_t nlen=lp - sig;
    // trim name
    char name[128]; if(nlen>=sizeof(name)) nlen=sizeof(name)-1; memcpy(name,sig,nlen); name[nlen]=0; trim(name);
    if(name_out){ strncpy(name_out,name,name_cap-1); name_out[name_cap-1]=0; }
    // inside
    char inside[512];
    size_t ilen = rp - lp -1;
    if(ilen>=sizeof(inside)) ilen=sizeof(inside)-1;
    memcpy(inside, lp+1, ilen); inside[ilen]=0;
    // split by comma handling nested brackets not needed for static types except fixed arrays like uint256[3]
    // For now dynamic types not in decode: we handle nested tuple? v1 static only so split by comma top-level
    int cnt=0;
    if(ilen==0 || (ilen==1 && inside[0]==0)) { *ntypes=0; return 0; }
    // trim inside
    // split
    char *tok=inside;
    char *cur=tok;
    int depth=0;
    for(char *c=inside; ; c++){
        char ch=*c;
        if(ch=='(' || ch=='[') depth++;
        else if(ch==')' || ch==']') depth--;
        if(ch==',' && depth==0){
            *c=0;
            trim(cur);
            if(cnt>=16){ if(errbuf) snprintf(errbuf,errlen,"too many types"); return -1; }
            strncpy(types[cnt],cur,63); types[cnt][63]=0;
            cnt++;
            cur=c+1;
        } else if(ch==0){
            trim(cur);
            if(cur[0] || cnt>0){
                if(cnt>=16){ if(errbuf) snprintf(errbuf,errlen,"too many types"); return -1; }
                strncpy(types[cnt],cur,63); types[cnt][63]=0;
                cnt++;
            }
            break;
        }
    }
    *ntypes=cnt;
    return 0;
}

int abi_type_kind(const char *t){
    if(strcmp(t,"address")==0) return 2;
    if(strcmp(t,"bool")==0) return 3;
    if(strncmp(t,"uint",4)==0) return 0;
    if(strncmp(t,"int",3)==0) return 1;
    if(strncmp(t,"bytes",5)==0){
        if(t[5]==0) return -2; // dynamic bytes
        return 4;
    }
    if(strcmp(t,"string")==0) return -2;
    // array check last char ]
    size_t l=strlen(t);
    if(l>2 && t[l-1]==']') return 5;
    return -1;
}

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

static int encode_one(const char *type, const char *arg, uint8_t *slot32, char *errbuf, size_t errlen){
    memset(slot32,0,32);
    int k=abi_type_kind(type);
    if(k==0){ // uint
        // bitsize
        int bits=256;
        if(type[4]) bits=atoi(type+4);
        u256_t v;
        if(u256_try_parse_auto(&v, arg)!=0){ if(errbuf) snprintf(errbuf,errlen,"bad uint %s",arg); return -1; }
        if(bits<256){
            // check fits
            // if high bits beyond bits not zero -> error? For now allow truncation check
            int maxwords = (bits+31)/32;
            // Actually check that value < 2^bits
            for(int i=maxwords; i<8; i++) if(v.d[i/ (32/32) ]){} // simplified: if bits<256, high limbs must be zero
            // Proper check: bitlen <= bits
            if(u256_bitlen(&v) > bits){ if(errbuf) snprintf(errbuf,errlen,"uint overflow"); return -1; }
        }
        // big-endian 32
        for(int i=0;i<8;i++){
            uint32_t w=v.d[7-i];
            slot32[i*4+0]=(w>>24)&0xFF;
            slot32[i*4+1]=(w>>16)&0xFF;
            slot32[i*4+2]=(w>>8)&0xFF;
            slot32[i*4+3]=w&0xFF;
        }
        return 0;
    } else if(k==1){ // int
        int bits=256;
        if(type[3]) bits=atoi(type+3);
        u256_t v;
        if(u256_try_parse_auto(&v, arg)!=0){ if(errbuf) snprintf(errbuf,errlen,"bad int %s",arg); return -1; }
        // range check for smaller bits not strict now
        for(int i=0;i<8;i++){ uint32_t w=v.d[7-i]; slot32[i*4+0]=(w>>24)&0xFF; slot32[i*4+1]=(w>>16)&0xFF; slot32[i*4+2]=(w>>8)&0xFF; slot32[i*4+3]=w&0xFF; }
        return 0;
    } else if(k==2){ // address
        uint8_t addr[20];
        if(parse_address_bytes(arg, addr)!=0){ if(errbuf) snprintf(errbuf,errlen,"bad address %s",arg); return -1; }
        memcpy(slot32+12, addr,20);
        return 0;
    } else if(k==3){ // bool
        char low[32]; int p=0; for(int i=0; arg[i] && p<31; i++) low[p++]=tolower((unsigned char)arg[i]); low[p]=0;
        // trim
        trim(low);
        uint8_t v=0;
        if(strcmp(low,"true")==0 || strcmp(low,"1")==0) v=1;
        else if(strcmp(low,"false")==0 || strcmp(low,"0")==0) v=0;
        else { if(errbuf) snprintf(errbuf,errlen,"bad bool"); return -1; }
        slot32[31]=v;
        return 0;
    } else if(k==4){ // bytesN
        int n=atoi(type+5);
        if(n<1||n>32){ if(errbuf) snprintf(errbuf,errlen,"bad bytesN"); return -1; }
        // arg is 0x hex
        const char *s=arg;
        while(isspace((unsigned char)*s)) s++;
        if(s[0]=='0'&&(s[1]=='x'||s[1]=='X')) s+=2;
        size_t len=strlen(s);
        while(len>0 && isspace((unsigned char)s[len-1])) len--;
        if(len != (size_t)n*2){ if(errbuf) snprintf(errbuf,errlen,"bytes%d len mismatch",n); return -1; }
        for(int i=0;i<n;i++){
            int hi, lo; char a=s[i*2], b=s[i*2+1];
            if(a>='0'&&a<='9') hi=a-'0'; else if(a>='a'&&a<='f') hi=a-'a'+10; else if(a>='A'&&a<='F') hi=a-'A'+10; else return -1;
            if(b>='0'&&b<='9') lo=b-'0'; else if(b>='a'&&b<='f') lo=b-'a'+10; else if(b>='A'&&b<='F') lo=b-'A'+10; else return -1;
            slot32[i]=(hi<<4)|lo;
        }
        return 0;
    } else if(k==5){ // fixed array like uint256[3] or address[2]
        // unsupported in v1 static encode except we can handle if type ends with [N]
        if(errbuf) snprintf(errbuf,errlen,"array type not supported in v1");
        return -1;
    } else if(k==-2){ // dynamic bytes/string - encode as slot handled by outer function
        if(errbuf) snprintf(errbuf,errlen,"dynamic type not in encode_one");
        return -1;
    } else {
        if(errbuf) snprintf(errbuf,errlen,"unknown type %s",type);
        return -1;
    }
}

static int is_dynamic_type(const char *t){
    if(strcmp(t,"bytes")==0 || strcmp(t,"string")==0) return 1;
    size_t l=strlen(t);
    if(l>2 && t[l-1]==']' && t[l-2]=='[') return 1; // [] dynamic array (not in v1)
    // bytes[] etc not needed
    return 0;
}

int abi_encode_calldata(const char *sig, const char **args, int nargs, uint8_t *out, size_t *out_len, char *errbuf, size_t errlen){
    char name[128]; char types[16][64]; int ntypes=0;
    if(abi_parse_sig(sig, name, sizeof(name), types, &ntypes, errbuf, errlen)!=0) return -1;
    if(ntypes != nargs){ if(errbuf) snprintf(errbuf,errlen,"arg count mismatch %d vs %d", nargs,ntypes); return -1; }
    // selector
    char canon[512]; int p=0;
    for(int i=0; sig[i] && p<511;i++) if(!isspace((unsigned char)sig[i])) canon[p++]=sig[i];
    canon[p]=0;
    uint8_t h[32]; keccak256((uint8_t*)canon,p,h);
    out[0]=h[0]; out[1]=h[1]; out[2]=h[2]; out[3]=h[3];
    size_t off=4;
    // For dynamic types: head slots contain offsets, tail contains data.
    int dyn_index[16]; int ndyn=0;
    for(int i=0;i<ntypes;i++) if(is_dynamic_type(types[i])) dyn_index[ndyn++]=i;
    // head size = ntypes*32, tail starts after head
    size_t head_end = 4 + ntypes*32;
    size_t tail_off = head_end;
    // First pass: write static slots and placeholder for dynamic
    for(int i=0;i<ntypes;i++){
        if(is_dynamic_type(types[i])){
            // offset = tail_off - 4 (selector not counted? Actually calldata offset is from start of calldata after selector? EVM ABI: offset from start of calldata excluding selector? Let's check: ABI offsets are relative to start of parameters block (after selector). Our out includes selector at 0..3, params at 4.. So offset value should be tail_off -4
            uint32_t offset_val = (uint32_t)(tail_off - 4);
            // write big-endian 32-byte offset
            memset(out+off,0,32);
            out[off+28]=(offset_val>>24)&0xFF;
            out[off+29]=(offset_val>>16)&0xFF;
            out[off+30]=(offset_val>>8)&0xFF;
            out[off+31]=offset_val&0xFF;
            // prepare tail data length to compute next tail_off
            const char *arg=args[i];
            // arg for bytes/string: expect 0x hex or plain string?
            // We'll support 0x hex for bytes, raw utf8 for string (we treat same as bytes)
            // Compute tail size: 32 len + padded data
            size_t data_len=0;
            if(strcmp(types[i],"string")==0){
                data_len=strlen(arg); // raw bytes (caller should pass without quotes)
            } else { // bytes
                const char *s=arg;
                while(isspace((unsigned char)*s)) s++;
                if(s[0]=='0'&&(s[1]=='x'||s[1]=='X')) s+=2;
                size_t hexlen=strlen(s);
                while(hexlen>0 && isspace((unsigned char)s[hexlen-1])) hexlen--;
                data_len=hexlen/2;
            }
            size_t padded = (data_len+31)&~31;
            tail_off += 32 + padded;
        } else {
            uint8_t slot[32];
            if(encode_one(types[i], args[i], slot, errbuf, errlen)!=0) return -1;
            memcpy(out+off, slot,32);
        }
        off+=32;
    }
    // Now write tails
    off = head_end;
    for(int di=0; di<ndyn; di++){
        int i=dyn_index[di];
        const char *arg=args[i];
        size_t data_len=0;
        uint8_t *data_bytes=NULL;
        uint8_t tmp[8192];
        if(strcmp(types[i],"string")==0){
            data_len=strlen(arg);
            data_bytes=(uint8_t*)arg;
        } else {
            const char *s=arg;
            while(isspace((unsigned char)*s)) s++;
            if(s[0]=='0'&&(s[1]=='x'||s[1]=='X')) s+=2;
            size_t hexlen=strlen(s);
            while(hexlen>0 && isspace((unsigned char)s[hexlen-1])) hexlen--;
            if(hexlen%2!=0){ if(errbuf) snprintf(errbuf,errlen,"odd hex len"); return -1; }
            data_len=hexlen/2;
            if(data_len>sizeof(tmp)){ if(errbuf) snprintf(errbuf,errlen,"bytes too long"); return -1; }
            for(size_t k=0;k<data_len;k++){
                int hi, lo; char a=s[k*2], b=s[k*2+1];
                if(a>='0'&&a<='9') hi=a-'0'; else if(a>='a'&&a<='f') hi=a-'a'+10; else if(a>='A'&&a<='F') hi=a-'A'+10; else return -1;
                if(b>='0'&&b<='9') lo=b-'0'; else if(b>='a'&&b<='f') lo=b-'a'+10; else if(b>='A'&&b<='F') lo=b-'A'+10; else return -1;
                tmp[k]=(hi<<4)|lo;
            }
            data_bytes=tmp;
        }
        // len slot
        memset(out+off,0,32);
        out[off+28]=(data_len>>24)&0xFF;
        out[off+29]=(data_len>>16)&0xFF;
        out[off+30]=(data_len>>8)&0xFF;
        out[off+31]=data_len&0xFF;
        off+=32;
        memcpy(out+off, data_bytes, data_len);
        size_t padded=(data_len+31)&~31;
        memset(out+off+data_len,0,padded-data_len);
        off+=padded;
    }
    *out_len=off;
    return 0;
}

static int hex_to_bytes(const char *hex, uint8_t *out, size_t *out_len, char *errbuf, size_t errlen){
    while(isspace((unsigned char)*hex)) hex++;
    if(hex[0]=='0'&&(hex[1]=='x'||hex[1]=='X')) hex+=2;
    size_t len=strlen(hex);
    while(len>0 && isspace((unsigned char)hex[len-1])) len--;
    if(len%2!=0){ if(errbuf) snprintf(errbuf,errlen,"odd hex"); return -1; }
    for(size_t i=0;i<len/2;i++){
        int hi, lo; char a=hex[i*2], b=hex[i*2+1];
        if(a>='0'&&a<='9') hi=a-'0'; else if(a>='a'&&a<='f') hi=a-'a'+10; else if(a>='A'&&a<='F') hi=a-'A'+10; else { if(errbuf) snprintf(errbuf,errlen,"bad hex"); return -1; }
        if(b>='0'&&b<='9') lo=b-'0'; else if(b>='a'&&b<='f') lo=b-'a'+10; else if(b>='A'&&b<='F') lo=b-'A'+10; else { if(errbuf) snprintf(errbuf,errlen,"bad hex"); return -1; }
        out[i]=(hi<<4)|lo;
    }
    *out_len=len/2;
    return 0;
}

int abi_decode(const char *types, const char *hexdata, char *out_lines, size_t out_size, char *errbuf, size_t errlen){
    // types comma-separated list
    char tcopy[512]; strncpy(tcopy, types, sizeof(tcopy)-1); tcopy[sizeof(tcopy)-1]=0;
    char *type_list[32]; int ntypes=0;
    char *tok=strtok(tcopy, ",");
    while(tok && ntypes<32){
        trim(tok);
        // remove spaces inside type? keep
        char *p=tok;
        char tmp[64]; int q=0;
        for(int i=0;p[i]&&q<63;i++) if(!isspace((unsigned char)p[i])) tmp[q++]=p[i];
        tmp[q]=0;
        type_list[ntypes++]=strdup(tmp);
        tok=strtok(NULL, ",");
    }
    uint8_t data[8192]; size_t dlen=0;
    if(hex_to_bytes(hexdata, data, &dlen, errbuf, errlen)!=0) { for(int i=0;i<ntypes;i++) free(type_list[i]); return -1; }
    if(dlen %32 !=0){ if(errbuf) snprintf(errbuf,errlen,"data len not multiple of 32"); for(int i=0;i<ntypes;i++) free(type_list[i]); return -1; }
    if(dlen < (size_t)ntypes*32){ if(errbuf) snprintf(errbuf,errlen,"data too short"); for(int i=0;i<ntypes;i++) free(type_list[i]); return -1; }
    size_t out_pos=0;
    for(int i=0;i<ntypes;i++){
        const char *tp=type_list[i];
        uint8_t slot[32];
        int is_dyn = is_dynamic_type(tp);
        size_t slot_off = i*32;
        if(!is_dyn){
            if(slot_off+32 > dlen){ if(errbuf) snprintf(errbuf,errlen,"oob"); for(int k=0;k<ntypes;k++) free(type_list[k]); return -1; }
            memcpy(slot, data+slot_off,32);
        } else {
            // offset
            if(slot_off+32 > dlen){ if(errbuf) snprintf(errbuf,errlen,"oob offset"); for(int k=0;k<ntypes;k++) free(type_list[k]); return -1; }
            uint32_t off = ((uint32_t)data[slot_off+28]<<24)|((uint32_t)data[slot_off+29]<<16)|((uint32_t)data[slot_off+30]<<8)|data[slot_off+31];
            if(off+32 > dlen){ if(errbuf) snprintf(errbuf,errlen,"dyn offset oob"); for(int k=0;k<ntypes;k++) free(type_list[k]); return -1; }
            uint32_t blen = ((uint32_t)data[off+28]<<24)|((uint32_t)data[off+29]<<16)|((uint32_t)data[off+30]<<8)|data[off+31];
            // for output we will handle separately
            if(strcmp(tp,"string")==0){
                if(off+32+blen > dlen){ if(errbuf) snprintf(errbuf,errlen,"dyn data oob"); for(int k=0;k<ntypes;k++) free(type_list[k]); return -1; }
                // ensure not overflow out_lines
                if(out_pos + blen + 2 >= out_size){ if(errbuf) snprintf(errbuf,errlen,"out too small"); for(int k=0;k<ntypes;k++) free(type_list[k]); return -1; }
                memcpy(out_lines+out_pos, data+off+32, blen);
                out_pos+=blen;
                out_lines[out_pos++]='\n';
                continue;
            } else if(strcmp(tp,"bytes")==0){
                if(off+32+blen > dlen){ if(errbuf) snprintf(errbuf,errlen,"dyn data oob"); for(int k=0;k<ntypes;k++) free(type_list[k]); return -1; }
                if(out_pos + 2 + blen*2 +2 >= out_size){ if(errbuf) snprintf(errbuf,errlen,"out too small"); for(int k=0;k<ntypes;k++) free(type_list[k]); return -1; }
                out_lines[out_pos++]='0'; out_lines[out_pos++]='x';
                const char *hexd="0123456789abcdef";
                for(uint32_t k=0;k<blen;k++){ out_lines[out_pos++]=hexd[data[off+32+k]>>4]; out_lines[out_pos++]=hexd[data[off+32+k]&0xF]; }
                out_lines[out_pos++]='\n';
                continue;
            } else {
                if(errbuf) snprintf(errbuf,errlen,"unsupported dyn type %s",tp); for(int k=0;k<ntypes;k++) free(type_list[k]); return -1;
            }
        }
        // static types printing
        char line[256];
        if(strcmp(tp,"address")==0){
            // last 20 bytes
            int pos=0; pos+=snprintf(line+pos,sizeof(line)-pos,"0x");
            const char *hexd="0123456789abcdef";
            for(int k=12;k<32;k++){ line[pos++]=hexd[slot[k]>>4]; line[pos++]=hexd[slot[k]&0xF]; }
            line[pos]=0;
        } else if(strcmp(tp,"bool")==0){
            snprintf(line,sizeof(line),"%s", slot[31]?"true":"false");
        } else if(strncmp(tp,"uint",4)==0){
            u256_t v; for(int k=0;k<8;k++){ uint32_t w = ((uint32_t)slot[k*4]<<24)|((uint32_t)slot[k*4+1]<<16)|((uint32_t)slot[k*4+2]<<8)|slot[k*4+3]; v.d[7-k]=w; }
            char dec[80]; u256_to_dec(&v, dec); snprintf(line,sizeof(line),"%s",dec);
        } else if(strncmp(tp,"int",3)==0){
            u256_t v; for(int k=0;k<8;k++){ uint32_t w = ((uint32_t)slot[k*4]<<24)|((uint32_t)slot[k*4+1]<<16)|((uint32_t)slot[k*4+2]<<8)|slot[k*4+3]; v.d[7-k]=w; }
            char dec[80]; u256_to_dec_signed(&v, dec); snprintf(line,sizeof(line),"%s",dec);
        } else if(strncmp(tp,"bytes",5)==0){
            int n=atoi(tp+5);
            int pos=0; pos+=snprintf(line+pos,sizeof(line)-pos,"0x"); const char *hexd="0123456789abcdef";
            for(int k=0;k<n;k++){ line[pos++]=hexd[slot[k]>>4]; line[pos++]=hexd[slot[k]&0xF]; }
            line[pos]=0;
        } else {
            snprintf(line,sizeof(line),"unsupported %s",tp);
        }
        size_t ll=strlen(line);
        if(out_pos+ll+2 >= out_size){ if(errbuf) snprintf(errbuf,errlen,"out too small"); for(int k=0;k<ntypes;k++) free(type_list[k]); return -1; }
        memcpy(out_lines+out_pos, line, ll); out_pos+=ll; out_lines[out_pos++]='\n';
    }
    out_lines[out_pos]=0;
    for(int i=0;i<ntypes;i++) free(type_list[i]);
    return 0;
}
