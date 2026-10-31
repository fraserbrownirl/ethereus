#include "evm.h"
#include <string.h>
#include <stdio.h>

static int push_u256(evm_t *e, u256_t v){
    if(e->sp+1 >= EVM_STACK_MAX) return -1;
    e->stack[++e->sp]=v;
    return 0;
}
static int pop_u256(evm_t *e, u256_t *out){
    if(e->sp<0) return -1;
    *out=e->stack[e->sp--];
    return 0;
}
static u256_t* peek_u256(evm_t *e, int depth){
    // depth 0 = top
    if(e->sp - depth <0) return NULL;
    return &e->stack[e->sp - depth];
}
void evm_init(evm_t *evm, const uint8_t *code, size_t code_len, const uint8_t *calldata, size_t calldata_len, u256_t callvalue, uint64_t chainid){
    memset(evm,0,sizeof(*evm));
    if(code_len > EVM_CODE_MAX) code_len=EVM_CODE_MAX;
    memcpy(evm->code, code, code_len); evm->code_len=code_len;
    if(calldata_len>sizeof(evm->calldata)) calldata_len=sizeof(evm->calldata);
    if(calldata) memcpy(evm->calldata, calldata, calldata_len);
    evm->calldata_len=calldata_len;
    evm->callvalue=callvalue;
    evm->chainid=chainid;
    evm->sp=-1;
    evm->gas_limit=EVM_GAS_LIMIT;
    evm->ret_data=evm->ret_buf;
}
int evm_storage_load(evm_t *evm, const u256_t *key, u256_t *out){
    for(int i=0;i<EVM_STORAGE_SLOTS;i++) if(evm->storage[i].used && u256_eq(&evm->storage[i].key,key)){ *out=evm->storage[i].val; return 0; }
    u256_zero(out); return 0;
}
int evm_storage_store(evm_t *evm, const u256_t *key, const u256_t *val){
    for(int i=0;i<EVM_STORAGE_SLOTS;i++) if(evm->storage[i].used && u256_eq(&evm->storage[i].key,key)){ evm->storage[i].val=*val; if(u256_is_zero(val)) evm->storage[i].used=0; return 0; }
    if(u256_is_zero(val)) return 0;
    for(int i=0;i<EVM_STORAGE_SLOTS;i++) if(!evm->storage[i].used){ evm->storage[i].key=*key; evm->storage[i].val=*val; evm->storage[i].used=1; return 0; }
    return -1; // out of slots
}
int evm_mem_expand(evm_t *evm, size_t offset, size_t len){
    if(len==0) return 0;
    size_t need = offset + len;
    if(need > EVM_MEM_MAX) return -1;
    if(need > evm->mem_size){
        size_t words = (need+31)/32;
        size_t new_size = words*32;
        if(new_size > EVM_MEM_MAX) return -1;
        memset(evm->mem + evm->mem_size, 0, new_size - evm->mem_size);
        // gas for expansion: 3*words + words^2/512 simplified? We'll charge 3 per word
        // This is approximate; we tally gas elsewhere
        evm->mem_size = new_size;
    }
    return 0;
}
static uint64_t mem_gas_cost(size_t new_words, size_t old_words){
    if(new_words<=old_words) return 0;
    uint64_t c1 = 3*(new_words - old_words);
    uint64_t c2 = (new_words*new_words)/512 - (old_words*old_words)/512;
    return c1 + c2;
}

static int check_gas(evm_t *e, uint64_t cost){
    e->gas_used += cost;
    if(e->gas_used > e->gas_limit) return -1;
    return 0;
}

int evm_run(evm_t *evm){
    while(evm->pc < evm->code_len && !evm->halted){
        uint8_t op = evm->code[evm->pc];
        // gas table (simplified, yellow paper values)
        uint64_t base_gas=0;
        // We'll handle per-opcode
        switch(op){
            case 0x00: // STOP
                evm->halted=1; evm->pc++; return 0;
            case 0x01: base_gas=3; break; // ADD
            case 0x02: base_gas=5; break; // MUL
            case 0x03: base_gas=5; break; // SUB
            case 0x04: base_gas=5; break; // DIV
            case 0x05: base_gas=5; break; // SDIV
            case 0x06: base_gas=5; break; // MOD
            case 0x07: base_gas=5; break; // SMOD
            case 0x08: base_gas=8; break; // ADDMOD
            case 0x09: base_gas=8; break; // MULMOD
            case 0x0A: base_gas=10; break; // EXP (plus per byte)
            case 0x0B: base_gas=5; break; // SIGNEXTEND
            case 0x10: base_gas=3; break;
            case 0x11: base_gas=3; break;
            case 0x12: base_gas=3; break;
            case 0x13: base_gas=3; break;
            case 0x14: base_gas=3; break;
            case 0x15: base_gas=3; break;
            case 0x16: base_gas=3; break;
            case 0x17: base_gas=3; break;
            case 0x18: base_gas=3; break;
            case 0x19: base_gas=3; break;
            case 0x1A: base_gas=3; break; // BYTE
            case 0x1B: base_gas=3; break; // SHL
            case 0x1C: base_gas=3; break; // SHR
            case 0x1D: base_gas=3; break; // SAR
            case 0x20: base_gas=30; break; // KECCAK256 - we will handle 30+6*words
            case 0x30: base_gas=2; break; // ADDRESS
            case 0x31: base_gas=2; break; // BALANCE - stub 0
            case 0x32: base_gas=2; break; // ORIGIN
            case 0x33: base_gas=2; break; // CALLER
            case 0x34: base_gas=2; break; // CALLVALUE
            case 0x35: base_gas=3; break; // CALLDATALOAD
            case 0x36: base_gas=2; break; // CALLDATASIZE
            case 0x37: base_gas=3; break; // CALLDATACOPY
            case 0x38: base_gas=2; break; // CODESIZE
            case 0x39: base_gas=3; break; // CODECOPY
            case 0x3A: base_gas=2; break; // GASPRICE
            case 0x3B: base_gas=2; break; // EXTCODESIZE
            case 0x3C: base_gas=2; break; // EXTCODECOPY
            case 0x3D: base_gas=2; break; // RETURNDATASIZE
            case 0x3E: base_gas=3; break; // RETURNDATACOPY
            case 0x3F: base_gas=2; break; // EXTCODEHASH
            case 0x40: base_gas=2; break; // BLOCKHASH -> 0
            case 0x41: base_gas=2; break; // COINBASE
            case 0x42: base_gas=2; break; // TIMESTAMP
            case 0x43: base_gas=2; break; // NUMBER
            case 0x44: base_gas=2; break; // PREVRANDAO
            case 0x45: base_gas=2; break; // GASLIMIT
            case 0x46: base_gas=2; break; // CHAINID
            case 0x47: base_gas=2; break; // SELFBALANCE
            case 0x48: base_gas=2; break; // BASEFEE
            case 0x50: base_gas=2; break; // POP
            case 0x51: base_gas=3; break; // MLOAD
            case 0x52: base_gas=3; break; // MSTORE
            case 0x53: base_gas=3; break; // MSTORE8
            case 0x54: base_gas=100; break; // SLOAD (warm)
            case 0x55: base_gas=100; break; // SSTORE
            case 0x56: base_gas=8; break; // JUMP
            case 0x57: base_gas=10; break; // JUMPI
            case 0x58: base_gas=2; break; // PC
            case 0x59: base_gas=2; break; // MSIZE
            case 0x5A: base_gas=2; break; // GAS
            case 0x5B: base_gas=1; break; // JUMPDEST
            case 0x5F: base_gas=2; break; // PUSH0
            case 0x60: case 0x61: case 0x62: case 0x63: case 0x64: case 0x65: case 0x66: case 0x67:
            case 0x68: case 0x69: case 0x6A: case 0x6B: case 0x6C: case 0x6D: case 0x6E: case 0x6F:
            case 0x70: case 0x71: case 0x72: case 0x73: case 0x74: case 0x75: case 0x76: case 0x77:
            case 0x78: case 0x79: case 0x7A: case 0x7B: case 0x7C: case 0x7D: case 0x7E: case 0x7F:
                base_gas=3; break;
            case 0x80: case 0x81: case 0x82: case 0x83: case 0x84: case 0x85: case 0x86: case 0x87:
            case 0x88: case 0x89: case 0x8A: case 0x8B: case 0x8C: case 0x8D: case 0x8E: case 0x8F:
                base_gas=3; break; // DUP
            case 0x90: case 0x91: case 0x92: case 0x93: case 0x94: case 0x95: case 0x96: case 0x97:
            case 0x98: case 0x99: case 0x9A: case 0x9B: case 0x9C: case 0x9D: case 0x9E: case 0x9F:
                base_gas=3; break; // SWAP
            case 0xA0: case 0xA1: case 0xA2: case 0xA3: case 0xA4: base_gas=375; break; // LOG
            case 0xF0: case 0xF1: case 0xF2: case 0xF3: case 0xF4: case 0xF5: case 0xFA: case 0xFD: case 0xFE: case 0xFF:
                base_gas=0; break; // handled specially
            default: return -2; // invalid opcode
        }
        // EXP extra gas
        if(op==0x0A){
            u256_t exp;
            if(evm->sp<1) return -3;
            exp=evm->stack[evm->sp]; // top
            int bl = u256_bitlen(&exp);
            int bytes = (bl+7)/8;
            base_gas += 50 * (bytes ? bytes : 0);
        }
        if(check_gas(evm, base_gas)!=0) return -10; // out of gas

        // Execute
        switch(op){
            case 0x01: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_add(&r,&a,&b); push_u256(evm,r); evm->pc++; break; }
            case 0x02: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_mul(&r,&a,&b); push_u256(evm,r); evm->pc++; break; }
            case 0x03: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_sub(&r,&a,&b); push_u256(evm,r); evm->pc++; break; }
            case 0x04: { u256_t a,b,q,rem; pop_u256(evm,&a); pop_u256(evm,&b); u256_divmod(&q,&rem,&a,&b); push_u256(evm,q); evm->pc++; break; }
            case 0x05: { u256_t a,b,q,rem; pop_u256(evm,&a); pop_u256(evm,&b); u256_sdivmod(&q,&rem,&a,&b); push_u256(evm,q); evm->pc++; break; }
            case 0x06: { u256_t a,b,q,rem; pop_u256(evm,&a); pop_u256(evm,&b); u256_divmod(&q,&rem,&a,&b); push_u256(evm,rem); evm->pc++; break; }
            case 0x07: { u256_t a,b,q,rem; pop_u256(evm,&a); pop_u256(evm,&b); u256_sdivmod(&q,&rem,&a,&b); push_u256(evm,rem); evm->pc++; break; }
            case 0x08: { u256_t a,b,m,r; pop_u256(evm,&a); pop_u256(evm,&b); pop_u256(evm,&m); u256_addmod(&r,&a,&b,&m); push_u256(evm,r); evm->pc++; break; }
            case 0x09: { u256_t a,b,m,r; pop_u256(evm,&a); pop_u256(evm,&b); pop_u256(evm,&m); u256_mulmod(&r,&a,&b,&m); push_u256(evm,r); evm->pc++; break; }
            case 0x0A: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_exp(&r,&a,&b); push_u256(evm,r); evm->pc++; break; }
            case 0x0B: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); uint32_t idx = a.d[0]; if(idx>31){ r=b; } else { u256_signextend(&r,&b,idx); } push_u256(evm,r); evm->pc++; break; }
            case 0x10: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); int c=u256_cmp(&a,&b); u256_zero(&r); if(c<0) r.d[0]=1; push_u256(evm,r); evm->pc++; break; }
            case 0x11: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); int c=u256_cmp(&a,&b); u256_zero(&r); if(c>0) r.d[0]=1; push_u256(evm,r); evm->pc++; break; }
            case 0x12: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); int c=u256_scmp(&a,&b); u256_zero(&r); if(c<0) r.d[0]=1; push_u256(evm,r); evm->pc++; break; }
            case 0x13: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); int c=u256_scmp(&a,&b); u256_zero(&r); if(c>0) r.d[0]=1; push_u256(evm,r); evm->pc++; break; }
            case 0x14: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_zero(&r); if(u256_eq(&a,&b)) r.d[0]=1; push_u256(evm,r); evm->pc++; break; }
            case 0x15: { u256_t a,r; pop_u256(evm,&a); u256_zero(&r); if(u256_is_zero(&a)) r.d[0]=1; push_u256(evm,r); evm->pc++; break; }
            case 0x16: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_and(&r,&a,&b); push_u256(evm,r); evm->pc++; break; }
            case 0x17: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_or(&r,&a,&b); push_u256(evm,r); evm->pc++; break; }
            case 0x18: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_xor(&r,&a,&b); push_u256(evm,r); evm->pc++; break; }
            case 0x19: { u256_t a,r; pop_u256(evm,&a); u256_not(&r,&a); push_u256(evm,r); evm->pc++; break; }
            case 0x1A: { u256_t i,v,r; pop_u256(evm,&i); pop_u256(evm,&v); uint32_t idx=i.d[0]; if(idx>=32) u256_zero(&r); else { u256_zero(&r); uint32_t b = u256_byte(&v, 31-idx); // Actually BYTE: byte 0 is MSB, need to map correctly
                    // Let's implement EVM BYTE: operand a is byte index (0 = most significant), b is value
                    // Our u256_byte currently is messy; instead directly extract from big-endian representation
                    // Build big-endian bytes of v
                    uint8_t be[32]; for(int k=0;k<8;k++){ uint32_t w=v.d[7-k]; be[k*4+0]=(w>>24)&0xFF; be[k*4+1]=(w>>16)&0xFF; be[k*4+2]=(w>>8)&0xFF; be[k*4+3]=w&0xFF; }
                    r.d[0]= be[idx];
                }
                push_u256(evm,r); evm->pc++; break;
            }
            case 0x1B: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); uint32_t sh=a.d[0]; if(sh>=256) u256_zero(&r); else u256_shl(&r,&b,sh); push_u256(evm,r); evm->pc++; break; }
            case 0x1C: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); uint32_t sh=a.d[0]; if(sh>=256) u256_zero(&r); else u256_shr(&r,&b,sh); push_u256(evm,r); evm->pc++; break; }
            case 0x1D: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); uint32_t sh=a.d[0]; if(sh>=256){ if(u256_is_negative(&b)) { for(int k=0;k<8;k++) r.d[k]=0xFFFFFFFFu; } else u256_zero(&r); } else u256_sar(&r,&b,sh); push_u256(evm,r); evm->pc++; break; }
            case 0x20: {
                u256_t off, len; pop_u256(evm,&off); pop_u256(evm,&len);
                uint32_t o = off.d[0]; uint32_t l = len.d[0];
                // check overflow: if len > 2^32 or off has high bits => error
                if(!u256_is_zero(&len) && (len.d[1]||len.d[2]||len.d[3]||len.d[4]||len.d[5]||len.d[6]||len.d[7])){ evm->halted=1; return -5; }
                if(!u256_is_zero(&off) && (off.d[1]||off.d[2]||off.d[3]||off.d[4]||off.d[5]||off.d[6]||off.d[7])){ evm->halted=1; return -5; }
                if(evm_mem_expand(evm, o, l)!=0) return -5;
                if(check_gas(evm, 6 * ((l+31)/32))!=0) return -10;
                uint8_t out[32]; extern void keccak256(const uint8_t*, size_t, uint8_t*); keccak256(evm->mem+o, l, out);
                u256_t r; u256_zero(&r);
                for(int k=0;k<32;k++){ int word = (31-k)/4; int sh=( (3- (31-k)%4)*8 ); // big-endian to little limbs
                    // Simpler: build via big-endian bytes
                }
                for(int k=0;k<32;k++){
                    int be_idx=k;
                    int word_le = (31 - be_idx)/4;
                    int byte_in_word = be_idx%4; // 0 is MSB of that word's bytes? Actually big-endian bytes: be[0] is most significant byte of d[7]
                    // Map be byte to limb
                    // be[0] -> d[7] >>24
                    // We'll reconstruct directly:
                }
                // Simpler reconstruct: u256 from bytes big-endian
                u256_zero(&r);
                for(int k=0;k<32;k++){
                    // shift left 8 and add byte
                    u256_t tmp; u256_shl(&tmp,&r,8);
                    u256_t btmp; u256_zero(&btmp); btmp.d[0]=out[k];
                    u256_add(&r,&tmp,&btmp);
                }
                push_u256(evm,r); evm->pc++; break;
            }
            case 0x30: { u256_t r; u256_zero(&r); // address: low 20 bytes of address field
                // evm->address holds it in low 160 bits
                r=evm->address; // already
                push_u256(evm,r); evm->pc++; break; }
            case 0x31: { u256_t a,r; pop_u256(evm,&a); u256_zero(&r); push_u256(evm,r); evm->pc++; break; }
            case 0x32: { u256_t r; r=evm->caller; push_u256(evm,r); evm->pc++; break; }
            case 0x33: { u256_t r; r=evm->caller; push_u256(evm,r); evm->pc++; break; }
            case 0x34: { push_u256(evm, evm->callvalue); evm->pc++; break; }
            case 0x35: { u256_t off, r; pop_u256(evm,&off); u256_zero(&r);
                uint32_t o = off.d[0];
                if(off.d[1]==0 && off.d[2]==0 && off.d[3]==0 && o+32 <= evm->calldata_len){
                    for(int k=0;k<32;k++){
                        uint8_t b = (o+k < evm->calldata_len) ? evm->calldata[o+k] : 0;
                        // build r big-endian
                        u256_t tmp; u256_shl(&tmp,&r,8); u256_t bt; u256_zero(&bt); bt.d[0]=b; u256_add(&r,&tmp,&bt);
                    }
                } else {
                    // partially out of range: pad zeros
                    for(int k=0;k<32;k++){
                        uint32_t idx = o + k;
                        uint8_t b = (idx < evm->calldata_len) ? evm->calldata[idx] : 0;
                        u256_t tmp; u256_shl(&tmp,&r,8); u256_t bt; u256_zero(&bt); bt.d[0]=b; u256_add(&r,&tmp,&bt);
                    }
                    // if off beyond calldata_len, r stays zero (all pads)
                    if(o >= evm->calldata_len && !u256_is_zero(&off) && off.d[1]==0){}
                }
                push_u256(evm,r); evm->pc++; break;
            }
            case 0x36: { u256_t r; u256_zero(&r); r.d[0]=(uint32_t)evm->calldata_len; push_u256(evm,r); evm->pc++; break; }
            case 0x37: { u256_t dOff, oOff, len; pop_u256(evm,&dOff); pop_u256(evm,&oOff); pop_u256(evm,&len);
                uint32_t dd=dOff.d[0], oo=oOff.d[0], ll=len.d[0];
                if(evm_mem_expand(evm, dd, ll)!=0) return -5;
                if(check_gas(evm, 3 * ((ll+31)/32))!=0) return -10;
                for(uint32_t k=0;k<ll;k++){
                    uint8_t b = (oo+k < evm->calldata_len) ? evm->calldata[oo+k] : 0;
                    evm->mem[dd+k]=b;
                }
                evm->pc++; break; }
            case 0x38: { u256_t r; u256_zero(&r); r.d[0]=(uint32_t)evm->code_len; push_u256(evm,r); evm->pc++; break; }
            case 0x39: { u256_t dOff, oOff, len; pop_u256(evm,&dOff); pop_u256(evm,&oOff); pop_u256(evm,&len);
                uint32_t dd=dOff.d[0], oo=oOff.d[0], ll=len.d[0];
                if(evm_mem_expand(evm, dd, ll)!=0) return -5;
                if(check_gas(evm, 3 * ((ll+31)/32))!=0) return -10;
                for(uint32_t k=0;k<ll;k++){
                    uint8_t b = (oo+k < evm->code_len) ? evm->code[oo+k] : 0;
                    evm->mem[dd+k]=b;
                }
                evm->pc++; break; }
            case 0x3A: case 0x3B: case 0x3C: { u256_t r; u256_zero(&r); push_u256(evm,r); evm->pc++; break; }
            case 0x3D: { u256_t r; u256_zero(&r); r.d[0]=(uint32_t)evm->ret_len; push_u256(evm,r); evm->pc++; break; }
            case 0x3E: { u256_t dOff, oOff, len; pop_u256(evm,&dOff); pop_u256(evm,&oOff); pop_u256(evm,&len);
                uint32_t dd=dOff.d[0], oo=oOff.d[0], ll=len.d[0];
                if(evm_mem_expand(evm, dd, ll)!=0) return -5;
                if(check_gas(evm, 3 * ((ll+31)/32))!=0) return -10;
                for(uint32_t k=0;k<ll;k++){ uint8_t b = (oo+k < evm->ret_len) ? evm->ret_data[oo+k] : 0; evm->mem[dd+k]=b; }
                evm->pc++; break; }
            case 0x3F: { u256_t a,r; pop_u256(evm,&a); u256_zero(&r); push_u256(evm,r); evm->pc++; break; }
            case 0x40: case 0x41: case 0x42: case 0x43: case 0x44: case 0x45: { u256_t r; u256_zero(&r); push_u256(evm,r); evm->pc++; break; }
            case 0x46: { u256_t r; u256_zero(&r); r.d[0]=(uint32_t)evm->chainid; r.d[1]=(uint32_t)(evm->chainid>>32); push_u256(evm,r); evm->pc++; break; }
            case 0x47: case 0x48: { u256_t r; u256_zero(&r); push_u256(evm,r); evm->pc++; break; }
            case 0x50: { u256_t a; pop_u256(evm,&a); (void)a; evm->pc++; break; }
            case 0x51: { u256_t off, r; pop_u256(evm,&off); uint32_t o=off.d[0]; if(evm_mem_expand(evm,o,32)!=0) return -5; if(check_gas(evm, mem_gas_cost((evm->mem_size)/32, (evm->mem_size-32)/32))!=0) {} u256_zero(&r); for(int k=0;k<32;k++){ uint8_t b=evm->mem[o+k]; u256_t tmp; u256_shl(&tmp,&r,8); u256_t bt; u256_zero(&bt); bt.d[0]=b; u256_add(&r,&tmp,&bt); } push_u256(evm,r); evm->pc++; break; }
            case 0x52: { u256_t off, val; pop_u256(evm,&off); pop_u256(evm,&val); uint32_t o=off.d[0]; if(evm_mem_expand(evm,o,32)!=0) return -5; uint8_t be[32]; for(int k=0;k<8;k++){ uint32_t w=val.d[7-k]; be[k*4+0]=(w>>24)&0xFF; be[k*4+1]=(w>>16)&0xFF; be[k*4+2]=(w>>8)&0xFF; be[k*4+3]=w&0xFF; } memcpy(evm->mem+o, be,32); evm->pc++; break; }
            case 0x53: { u256_t off, val; pop_u256(evm,&off); pop_u256(evm,&val); uint32_t o=off.d[0]; if(evm_mem_expand(evm,o,1)!=0) return -5; evm->mem[o]= val.d[0] & 0xFF; evm->pc++; break; }
            case 0x54: { u256_t key, val; pop_u256(evm,&key); evm_storage_load(evm,&key,&val); push_u256(evm,val); evm->pc++; break; }
            case 0x55: { u256_t key, val; pop_u256(evm,&key); pop_u256(evm,&val); if(evm_storage_store(evm,&key,&val)!=0) return -6; evm->pc++; break; }
            case 0x56: { u256_t dest; pop_u256(evm,&dest); uint32_t d=dest.d[0]; if(d>=evm->code_len || evm->code[d]!=0x5B) return -4; evm->pc=d; break; }
            case 0x57: { u256_t dest, cond; pop_u256(evm,&dest); pop_u256(evm,&cond); if(!u256_is_zero(&cond)){ uint32_t d=dest.d[0]; if(d>=evm->code_len || evm->code[d]!=0x5B) return -4; evm->pc=d; } else evm->pc++; break; }
            case 0x58: { u256_t r; u256_zero(&r); r.d[0]=(uint32_t)evm->pc; push_u256(evm,r); evm->pc++; break; }
            case 0x59: { u256_t r; u256_zero(&r); r.d[0]=(uint32_t)evm->mem_size; push_u256(evm,r); evm->pc++; break; }
            case 0x5A: { u256_t r; u256_zero(&r); r.d[0]=(uint32_t)(evm->gas_limit - evm->gas_used); push_u256(evm,r); evm->pc++; break; }
            case 0x5B: { evm->pc++; break; }
            case 0x5F: { u256_t r; u256_zero(&r); push_u256(evm,r); evm->pc++; break; }
            default: {
                if(op>=0x60 && op<=0x7F){
                    int n=op-0x5F;
                    if(evm->pc+1+n > evm->code_len) return -3;
                    u256_t r; u256_zero(&r);
                    for(int k=0;k<n;k++){
                        uint8_t b=evm->code[evm->pc+1+k];
                        u256_t tmp; u256_shl(&tmp,&r,8); u256_t bt; u256_zero(&bt); bt.d[0]=b; u256_add(&r,&tmp,&bt);
                    }
                    push_u256(evm,r); evm->pc+=1+n; break;
                } else if(op>=0x80 && op<=0x8F){
                    int n=op-0x7F;
                    if(evm->sp+1-n <0) return -3;
                    u256_t v=evm->stack[evm->sp - n +1]; push_u256(evm,v); evm->pc++; break;
                } else if(op>=0x90 && op<=0x9F){
                    int n=op-0x8F;
                    if(evm->sp - n <0) return -3;
                    int top=evm->sp;
                    int other=top - n;
                    u256_t tmp=evm->stack[top]; evm->stack[top]=evm->stack[other]; evm->stack[other]=tmp;
                    evm->pc++; break;
                } else if(op>=0xA0 && op<=0xA4){
                    u256_t off, len; pop_u256(evm,&off); pop_u256(evm,&len);
                    uint32_t o=off.d[0], l=len.d[0];
                    if(evm_mem_expand(evm,o,l)!=0) return -5;
                    // LOG ignored except gas already charged
                    evm->pc++; break;
                } else if(op==0xF3){ // RETURN
                    u256_t off, len; pop_u256(evm,&off); pop_u256(evm,&len);
                    uint32_t o=off.d[0], l=len.d[0];
                    if(l>sizeof(evm->ret_buf)) return -5;
                    if(evm_mem_expand(evm,o,l)!=0) return -5;
                    memcpy(evm->ret_buf, evm->mem+o, l); evm->ret_len=l; evm->ret_data=evm->ret_buf;
                    evm->halted=1; evm->pc++; return 0;
                } else if(op==0xFD){ // REVERT
                    u256_t off, len; pop_u256(evm,&off); pop_u256(evm,&len);
                    uint32_t o=off.d[0], l=len.d[0];
                    if(l>sizeof(evm->ret_buf)) return -5;
                    if(evm_mem_expand(evm,o,l)!=0) return -5;
                    memcpy(evm->ret_buf, evm->mem+o, l); evm->ret_len=l; evm->ret_data=evm->ret_buf;
                    evm->reverted=1; evm->halted=1; evm->pc++; return 0;
                } else if(op==0xFE){ return -8; } // INVALID
                else if(op==0xFF){ evm->halted=1; return -9; } // SELFDESTRUCT forbidden
                else if(op==0xF0||op==0xF1||op==0xF2||op==0xF4||op==0xF5||op==0xFA){ return -7; } // CALL etc forbidden
                else { return -2; }
            }
        }
    }
    return 0;
}
