#include "evm.h"
#include "keccak.h"
#include <string.h>
#include <stdio.h>

// Cancun interpreter (v1 scope): no CALL/CREATE/DELEGATECALL family, no
// SELFDESTRUCT, BLOCKHASH/PREVRANDAO stubbed 0, STATICCALL only for the
// identity precompile (0x04). Gas is gross execution gas (pre-refund),
// matching py-evm's computation.get_gas_used(). Berlin cold/warm storage
// gas (EIP-2929) with EIP-2200 dirty/clean SSTORE costs.

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
    if(e->sp - depth <0) return NULL;
    return &e->stack[e->sp - depth];
}

int evm_init(evm_t *evm, const uint8_t *code, size_t code_len,
             const uint8_t *calldata, size_t calldata_len,
             const u256_t *callvalue, const u256_t *caller, uint64_t chainid,
             const evm_fixture_slot_t *fixture, size_t fixture_len){
    if(code_len>EVM_CODE_MAX) return -1;
    // No bulk zeroing: stack is sp-guarded, mem is zeroed on expansion,
    // storage/logs/retdata are count-guarded, calldata reads are len-guarded.
    evm->sp=-1; evm->mem_size=0;
    evm->code=code; evm->code_len=code_len; // DROM, no copy
    // One linear pass marks every true JUMPDEST (skipping push-data), so
    // JUMP/JUMPI validate in O(1). Rescanning per jump cost ~3k each.
    { size_t nb=(code_len+7)/8, p=0;
      memset(evm->jumpmap,0,nb);
      while(p<code_len){ uint8_t op=code[p];
          if(op==0x5B) evm->jumpmap[p>>3]|=(uint8_t)(1<<(p&7));
          p+=(op>=0x60&&op<=0x7F)?1+(op-0x5F):1; } }
    evm->pc=0;
    evm->gas_used=0; evm->gas_limit=EVM_GAS_LIMIT;
    evm->halted=0; evm->reverted=0;
    evm->ret_off=0; evm->ret_len=0;
    if(calldata_len>sizeof(evm->calldata)) calldata_len=sizeof(evm->calldata);
    if(calldata && calldata_len) memcpy(evm->calldata, calldata, calldata_len);
    evm->calldata_len=calldata_len;
    if(callvalue) evm->callvalue=*callvalue; else u256_zero(&evm->callvalue);
    if(caller) evm->caller=*caller; else u256_zero(&evm->caller);
    u256_zero(&evm->address); // v1 runs address-less; contracts must not read it
    evm->retdata_len=0;
    evm->chainid=chainid; evm->block_number=0;
    evm->nlogs=0;
    evm->fixture=fixture; evm->fixture_len=fixture_len;
    evm->addr04_warm=0;
    evm->nslots=0;
    return 0;
}

// --- Storage (32-slot RAM map + DROM fixture for orig values) -----------

static int slot_find(evm_t *evm, const u256_t *key){
    for(int i=0;i<evm->nslots;i++)
        if(u256_eq(&evm->storage[i].key,key)) return i;
    return -1;
}

static int slot_find_or_create(evm_t *evm, const u256_t *key){
    int f=slot_find(evm,key);
    if(f>=0) return f;
    if(evm->nslots>=EVM_STORAGE_SLOTS) return -1; // full
    int i=evm->nslots++;
    evm->storage[i].key=*key;
    evm_slot_orig(evm,key,&evm->storage[i].val);
    evm->storage[i].warm=0; // cold until first access charges it
    return i;
}

// Original (fixture) value of a slot, 0 if absent. Linear scan is fine:
// the fixture has 2 entries.
void evm_slot_orig(evm_t *evm, const u256_t *key, u256_t *out){
    u256_zero(out);
    uint8_t kb[32];
    for(int i=0;i<8;i++){ uint32_t w=key->d[7-i]; kb[i*4+0]=(w>>24)&0xFF; kb[i*4+1]=(w>>16)&0xFF; kb[i*4+2]=(w>>8)&0xFF; kb[i*4+3]=w&0xFF; }
    for(size_t i=0;i<evm->fixture_len;i++)
        if(memcmp(evm->fixture[i].key,kb,32)==0){
            for(int k=0;k<8;k++) out->d[7-k]=((uint32_t)evm->fixture[i].val[k*4]<<24)|((uint32_t)evm->fixture[i].val[k*4+1]<<16)|((uint32_t)evm->fixture[i].val[k*4+2]<<8)|evm->fixture[i].val[k*4+3];
            return;
        }
}

int evm_mem_expand(evm_t *evm, size_t offset, size_t len){
    if(len==0) return 0;
    // Wrap guard: on rv32 size_t is 32-bit, so offset+len can wrap.
    if(offset > EVM_MEM_MAX || len > EVM_MEM_MAX) return -1;
    size_t need = offset + len;
    if(need < offset || need > EVM_MEM_MAX) return -1;
    if(need > evm->mem_size){
        size_t words = (need+31)/32;
        size_t new_size = words*32;
        if(new_size > EVM_MEM_MAX) return -1;
        memset(evm->mem + evm->mem_size, 0, new_size - evm->mem_size);
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

// Extract a u256 operand as u32. -1 if >= 2^32.
static int u256_to_u32(const u256_t *a, uint32_t *out){
    for(int i=1;i<8;i++) if(a->d[i]) return -1;
    *out=a->d[0];
    return 0;
}

// Charge memory-expansion gas for [offset, offset+len) and grow.
// 0 ok, -5 capacity (beyond EVM_MEM_MAX), -10 out of gas.
static int charge_mem_expand(evm_t *evm, uint32_t offset, uint32_t len){
    if(len==0) return 0;
    uint64_t need=(uint64_t)offset+(uint64_t)len;
    if(need > EVM_MEM_MAX) return -5;
    uint32_t old_words=(uint32_t)(evm->mem_size+31)/32;
    uint32_t new_words=(uint32_t)(need+31)/32;
    if(new_words > old_words){
        uint64_t add=mem_gas_cost(new_words, old_words);
        if(check_gas(evm,add)!=0) return -10;
        if(evm_mem_expand(evm,offset,len)!=0) return -5;
    }
    return 0;
}

// O(1) JUMPDEST check against the init-built bitmap.
static int jumpdest_valid(evm_t *evm, uint32_t d){
    if((size_t)d>=evm->code_len) return 0;
    return (evm->jumpmap[d>>3]>>(d&7))&1;
}

// Stack depth requirement per opcode: need items present, growth net push.
// Underflow AND overflow both fault (-3), matching py-evm.
static void stack_req(uint8_t op, int *need, int *growth){
    *need=0; *growth=0;
    if(op==0x08 || op==0x09){ *need=3; *growth=-2; } // ADDMOD MULMOD
    else if((op>=0x01 && op<=0x07)||op==0x0A||op==0x0B){ *need=2; *growth=-1; }
    else if(op==0x15 || op==0x19){ *need=1; *growth=0; }    // ISZERO NOT
    else if(op>=0x10 && op<=0x1D){ *need=2; *growth=-1; }
    else if(op==0x20){ *need=2; *growth=-1; }
    else if(op==0x50){ *need=1; *growth=-1; }
    else if(op==0x51 || op==0x54){ *need=1; *growth=0; }
    else if(op==0x52 || op==0x53 || op==0x55){ *need=2; *growth=-2; }
    else if(op==0x56){ *need=1; *growth=-1; }
    else if(op==0x57){ *need=2; *growth=-2; }
    else if(op==0x37 || op==0x39 || op==0x3E){ *need=3; *growth=-3; }
    else if(op==0x3B){ *need=1; *growth=0; } // EXTCODESIZE
    else if(op==0x58 || op==0x59 || op==0x5A || op==0x5F){ *need=0; *growth=1; }
    else if((op>=0x30&&op<=0x34)||op==0x36||op==0x38||op==0x3A||op==0x3D||op==0x42||op==0x43||op==0x44||op==0x45||op==0x46||op==0x47||op==0x48){ *need=0; *growth=1; }
    else if(op==0x5B || op==0x00 || op==0xFE){ *need=0; *growth=0; }
    else if(op>=0x60 && op<=0x7F){ *need=0; *growth=1; }
    else if(op>=0x80 && op<=0x8F){ *need=op-0x80+1; *growth=1; }
    else if(op>=0x90 && op<=0x9F){ *need=op-0x90+2; *growth=0; }
    else if(op>=0xA0 && op<=0xA4){ int n=2+op-0xA0; *need=n; *growth=-n; }
    else if(op==0xF3 || op==0xFD){ *need=2; *growth=-2; }
    else if(op==0xFF){ *need=1; *growth=-1; }
    else if(op==0x30 || op==0x32 || op==0x33 || op==0x34 || op==0x36 ||
            op==0x38 || op==0x3A || op==0x3B || op==0x3D || op==0x40 ||
            op==0x41 || op==0x42 || op==0x43 || op==0x44 || op==0x45 ||
            op==0x46 || op==0x47 || op==0x48 || op==0x4A || op==0x4B){ *need=0; *growth=1; }
    else if(op==0x31 || op==0x3F || op==0x35 || op==0x40){ *need=1; *growth=0; }
    else if(op==0x3C){ *need=4; *growth=-4; } // EXTCODECOPY
    else if(op==0x37 || op==0x39 || op==0x3E || op==0x5E){ *need=3; *growth=-3; }
    else if(op==0xF1 || op==0xF2 || op==0xF4){ *need=7; *growth=-6; }
    else if(op==0xFA){ *need=6; *growth=-5; }
    else if(op==0xF0 || op==0xF5){ *need=3; *growth=-2; }
}

// Cancun SSTORE cost (EIP-2929 + EIP-2200, no refunds: gross gas only).
// sload=100, set=20000, reset=2900; +2100 if the slot is cold.
static int sstore_cost(evm_t *evm, int idx, const u256_t *key, const u256_t *cur, const u256_t *nw, uint64_t *cost_out){
    u256_t orig;
    evm_slot_orig(evm, key, &orig);
    uint64_t cost;
    if(u256_eq(cur,nw)) cost=100;
    else if(u256_eq(&orig,cur)) cost=u256_is_zero(&orig)?20000:2900;
    else cost=100;
    if(!evm->storage[idx].warm){ cost+=2100; evm->storage[idx].warm=1; }
    *cost_out=cost;
    return 0;
}

int evm_run(evm_t *evm){
    while(evm->pc < evm->code_len && !evm->halted){
        uint8_t op = evm->code[evm->pc];
#ifdef EVM_TRACE
        { static long n=0;
          fprintf(stderr,"[%ld] pc=%zu op=0x%02x sp=%d gas=%llu",
                  n++,evm->pc,op,evm->sp+1,(unsigned long long)evm->gas_used);
          if(evm->sp>=0){ u256_t *t=&evm->stack[evm->sp];
              fprintf(stderr," top=%08x%08x%08x%08x",t->d[3],t->d[2],t->d[1],t->d[0]); }
          fprintf(stderr,"\n"); }
#endif
        { int need=0, growth=0; stack_req(op,&need,&growth);
          int depth=evm->sp+1;
          if(depth < need || depth - need + growth > EVM_STACK_MAX) return -3; }
        uint64_t base_gas=0;
        switch(op){
            case 0x00:
                evm->halted=1; evm->pc++; return 0;
            case 0x01: base_gas=3; break;
            case 0x02: base_gas=5; break;
            case 0x03: base_gas=3; break; // SUB is G_verylow
            case 0x04: base_gas=5; break;
            case 0x05: base_gas=5; break;
            case 0x06: base_gas=5; break;
            case 0x07: base_gas=5; break;
            case 0x08: base_gas=8; break;
            case 0x09: base_gas=8; break;
            case 0x0A: base_gas=10; break;
            case 0x0B: base_gas=5; break;
            case 0x10: case 0x11: case 0x12: case 0x13: case 0x14: case 0x15:
            case 0x16: case 0x17: case 0x18: case 0x19: case 0x1A: case 0x1B:
            case 0x1C: case 0x1D:
                base_gas=3; break;
            case 0x20: base_gas=30; break;
            case 0x30: case 0x32: case 0x33: case 0x34: case 0x36: case 0x38:
            case 0x3A: case 0x3B: case 0x3D: case 0x40: case 0x41: case 0x42:
            case 0x43: case 0x44: case 0x45: case 0x46: case 0x48:
                base_gas=2; break;
            case 0x31: case 0x3C: case 0x3F: base_gas=2; break; // stubs (unreachable on gate)
            case 0x35: base_gas=3; break;
            case 0x37: case 0x39: case 0x3E: case 0x5E: base_gas=3; break;
            case 0x47: base_gas=5; break; // SELFBALANCE
            case 0x50: base_gas=2; break;
            case 0x51: case 0x52: case 0x53: base_gas=3; break;
            case 0x54: case 0x55: base_gas=0; break; // Berlin, charged in execution
            case 0x56: base_gas=8; break;
            case 0x57: base_gas=10; break;
            case 0x58: case 0x59: case 0x5A: base_gas=2; break;
            case 0x5B: base_gas=1; break;
            case 0x5F: base_gas=2; break;
            case 0x60: case 0x61: case 0x62: case 0x63: case 0x64: case 0x65: case 0x66: case 0x67:
            case 0x68: case 0x69: case 0x6A: case 0x6B: case 0x6C: case 0x6D: case 0x6E: case 0x6F:
            case 0x70: case 0x71: case 0x72: case 0x73: case 0x74: case 0x75: case 0x76: case 0x77:
            case 0x78: case 0x79: case 0x7A: case 0x7B: case 0x7C: case 0x7D: case 0x7E: case 0x7F:
                base_gas=3; break;
            case 0x80: case 0x81: case 0x82: case 0x83: case 0x84: case 0x85: case 0x86: case 0x87:
            case 0x88: case 0x89: case 0x8A: case 0x8B: case 0x8C: case 0x8D: case 0x8E: case 0x8F:
                base_gas=3; break;
            case 0x90: case 0x91: case 0x92: case 0x93: case 0x94: case 0x95: case 0x96: case 0x97:
            case 0x98: case 0x99: case 0x9A: case 0x9B: case 0x9C: case 0x9D: case 0x9E: case 0x9F:
                base_gas=3; break;
            case 0xA0: case 0xA1: case 0xA2: case 0xA3: case 0xA4: base_gas=375; break; // G_log
            case 0xF0: case 0xF1: case 0xF2: case 0xF3: case 0xF4: case 0xF5: case 0xFA: case 0xFD: case 0xFE: case 0xFF:
                base_gas=0; break;
            default: return -2;
        }
        if(op==0x0A){
            u256_t exp=evm->stack[evm->sp];
            int bytes=(u256_bitlen(&exp)+7)/8;
            base_gas += 50*(uint64_t)bytes;
        }
        if(check_gas(evm, base_gas)!=0) return -10;

        switch(op){
            case 0x01: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_add(&r,&a,&b); push_u256(evm,r); evm->pc++; break; }
            case 0x02: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_mul(&r,&a,&b); push_u256(evm,r); evm->pc++; break; }
            case 0x03: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_sub(&r,&a,&b); push_u256(evm,r); evm->pc++; break; }
            case 0x04: { u256_t a,b,q,rem; pop_u256(evm,&a); pop_u256(evm,&b); u256_divmod(&q,&rem,&a,&b); push_u256(evm,q); evm->pc++; break; }
            case 0x05: { u256_t a,b,q,rem; pop_u256(evm,&a); pop_u256(evm,&b); u256_sdivmod(&q,&rem,&a,&b); push_u256(evm,q); evm->pc++; break; }
            case 0x06: { u256_t a,b,q,rem,r; pop_u256(evm,&a); pop_u256(evm,&b);
                         if(u256_is_zero(&b)) u256_zero(&r); else { u256_divmod(&q,&rem,&a,&b); r=rem; }
                         push_u256(evm,r); evm->pc++; break; }
            case 0x07: { u256_t a,b,q,rem; pop_u256(evm,&a); pop_u256(evm,&b); u256_sdivmod(&q,&rem,&a,&b); push_u256(evm,rem); evm->pc++; break; }
            case 0x08: { u256_t a,b,m,r; pop_u256(evm,&a); pop_u256(evm,&b); pop_u256(evm,&m); u256_addmod(&r,&a,&b,&m); push_u256(evm,r); evm->pc++; break; }
            case 0x09: { u256_t a,b,m,r; pop_u256(evm,&a); pop_u256(evm,&b); pop_u256(evm,&m); u256_mulmod(&r,&a,&b,&m); push_u256(evm,r); evm->pc++; break; }
            case 0x0A: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_exp(&r,&a,&b); push_u256(evm,r); evm->pc++; break; }
            case 0x0B: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b);
                         int i; for(i=1;i<8;i++) if(a.d[i]) break;
                         if(i<8 || a.d[0]>=31) r=b; else u256_signextend(&r,&b,a.d[0]);
                         push_u256(evm,r); evm->pc++; break; }
            case 0x10: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_from_u64(&r, (uint64_t)(u256_cmp(&a,&b)<0?1:0)); push_u256(evm,r); evm->pc++; break; }
            case 0x11: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_from_u64(&r, (uint64_t)(u256_cmp(&a,&b)>0?1:0)); push_u256(evm,r); evm->pc++; break; }
            case 0x12: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_from_u64(&r, (uint64_t)(u256_scmp(&a,&b)<0?1:0)); push_u256(evm,r); evm->pc++; break; }
            case 0x13: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_from_u64(&r, (uint64_t)(u256_scmp(&a,&b)>0?1:0)); push_u256(evm,r); evm->pc++; break; }
            case 0x14: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_from_u64(&r, (uint64_t)(u256_eq(&a,&b)?1:0)); push_u256(evm,r); evm->pc++; break; }
            case 0x15: { u256_t a,r; pop_u256(evm,&a); u256_from_u64(&r, (uint64_t)(u256_is_zero(&a)?1:0)); push_u256(evm,r); evm->pc++; break; }
            case 0x16: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_and(&r,&a,&b); push_u256(evm,r); evm->pc++; break; }
            case 0x17: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_or(&r,&a,&b); push_u256(evm,r); evm->pc++; break; }
            case 0x18: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b); u256_xor(&r,&a,&b); push_u256(evm,r); evm->pc++; break; }
            case 0x19: { u256_t a,r; pop_u256(evm,&a); u256_not(&r,&a); push_u256(evm,r); evm->pc++; break; }
            case 0x1A: { u256_t i,v,r; pop_u256(evm,&i); pop_u256(evm,&v); uint32_t idx=i.d[0];
                         u256_from_u64(&r, (uint64_t)(idx>=32?0:u256_byte(&v,idx))); push_u256(evm,r); evm->pc++; break; }
            case 0x1B: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b);
                         int i; for(i=1;i<8;i++) if(a.d[i]) break;
                         if(i<8 || a.d[0]>=256) u256_zero(&r); else u256_shl(&r,&b,a.d[0]);
                         push_u256(evm,r); evm->pc++; break; }
            case 0x1C: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b);
                         int i; for(i=1;i<8;i++) if(a.d[i]) break;
                         if(i<8 || a.d[0]>=256) u256_zero(&r); else u256_shr(&r,&b,a.d[0]);
                         push_u256(evm,r); evm->pc++; break; }
            case 0x1D: { u256_t a,b,r; pop_u256(evm,&a); pop_u256(evm,&b);
                         int i; for(i=1;i<8;i++) if(a.d[i]) break;
                         if(i<8 || a.d[0]>=256){ if(b.d[7]&0x80000000){ for(int k=0;k<8;k++) r.d[k]=0xFFFFFFFF; } else u256_zero(&r); }
                         else u256_sar(&r,&b,a.d[0]);
                         push_u256(evm,r); evm->pc++; break; }
            case 0x20: { u256_t o,l; pop_u256(evm,&o); pop_u256(evm,&l); uint32_t oo,ll;
                         if(u256_to_u32(&o,&oo)!=0 || u256_to_u32(&l,&ll)!=0) return -5;
                         { int r=charge_mem_expand(evm,oo,ll); if(r!=0) return r; }
                         { uint32_t w=(ll+31)/32; if(check_gas(evm,6*(uint64_t)w)!=0) return -10; }
                         u256_t r; uint8_t h[32]; keccak256(evm->mem+oo,ll,h);
                         for(int k=0;k<8;k++) r.d[7-k]=((uint32_t)h[k*4]<<24)|((uint32_t)h[k*4+1]<<16)|((uint32_t)h[k*4+2]<<8)|h[k*4+3];
                         push_u256(evm,r); evm->pc++; break; }
            case 0x30: push_u256(evm,evm->address); evm->pc++; break;
            case 0x31: { u256_t a,r; pop_u256(evm,&a); u256_zero(&r); push_u256(evm,r); evm->pc++; break; }
            case 0x32: push_u256(evm,evm->caller); evm->pc++; break; // ORIGIN stubbed as caller
            case 0x33: push_u256(evm,evm->caller); evm->pc++; break;
            case 0x34: push_u256(evm,evm->callvalue); evm->pc++; break;
            case 0x35: { u256_t off,r; pop_u256(evm,&off); uint32_t o;
                         u256_zero(&r);
                         if(u256_to_u32(&off,&o)==0){
                             for(int k=0;k<32;k++){
                                 uint64_t idx=(uint64_t)o+k;
                                 uint8_t bb=(idx<evm->calldata_len)?evm->calldata[idx]:0;
                                 r.d[7-k/4] |= (uint32_t)bb << (8*(3-(k%4)));
                             }
                         }
                         push_u256(evm,r); evm->pc++; break; }
            case 0x36: { u256_t r; u256_from_u64(&r, (uint64_t)((uint32_t)evm->calldata_len)); push_u256(evm,r); evm->pc++; break; }
            case 0x37: { u256_t dOff,oOff,len; pop_u256(evm,&dOff); pop_u256(evm,&oOff); pop_u256(evm,&len);
                         uint32_t dd,oo,ll; if(u256_to_u32(&dOff,&dd)!=0||u256_to_u32(&oOff,&oo)!=0||u256_to_u32(&len,&ll)!=0) return -5;
                         { int rr=charge_mem_expand(evm,dd,ll); if(rr!=0) return rr; }
                         { uint32_t w=(ll+31)/32; if(check_gas(evm,3*(uint64_t)w)!=0) return -10; }
                         for(uint32_t k=0;k<ll;k++){ uint64_t idx=(uint64_t)oo+k; evm->mem[dd+k]=(idx<evm->calldata_len)?evm->calldata[idx]:0; }
                         evm->pc++; break; }
            case 0x38: { u256_t r; u256_from_u64(&r, (uint64_t)((uint32_t)evm->code_len)); push_u256(evm,r); evm->pc++; break; }
            case 0x39: { u256_t dOff,oOff,len; pop_u256(evm,&dOff); pop_u256(evm,&oOff); pop_u256(evm,&len);
                         uint32_t dd,oo,ll; if(u256_to_u32(&dOff,&dd)!=0||u256_to_u32(&oOff,&oo)!=0||u256_to_u32(&len,&ll)!=0) return -5;
                         { int rr=charge_mem_expand(evm,dd,ll); if(rr!=0) return rr; }
                         { uint32_t w=(ll+31)/32; if(check_gas(evm,3*(uint64_t)w)!=0) return -10; }
                         for(uint32_t k=0;k<ll;k++){ uint64_t idx=(uint64_t)oo+k; evm->mem[dd+k]=(idx<evm->code_len)?evm->code[idx]:0; }
                         evm->pc++; break; }
            case 0x5E: { u256_t dOff,oOff,len; pop_u256(evm,&dOff); pop_u256(evm,&oOff); pop_u256(evm,&len);
                         uint32_t dd,oo,ll; if(u256_to_u32(&dOff,&dd)!=0||u256_to_u32(&oOff,&oo)!=0||u256_to_u32(&len,&ll)!=0) return -5;
                         { int rr=charge_mem_expand(evm,dd,ll); if(rr!=0) return rr; }
                         { int rr=charge_mem_expand(evm,oo,ll); if(rr!=0) return rr; }
                         { uint32_t w=(ll+31)/32; if(check_gas(evm,3*(uint64_t)w)!=0) return -10; }
                         memmove(evm->mem+dd,evm->mem+oo,ll); evm->pc++; break; }
            case 0x3A: { u256_t r; u256_zero(&r); push_u256(evm,r); evm->pc++; break; }
            case 0x3B: { u256_t a,r; pop_u256(evm,&a); u256_zero(&r); push_u256(evm,r); evm->pc++; break; }
            case 0x3C: { u256_t a,dOff,oOff,len; pop_u256(evm,&a); pop_u256(evm,&dOff); pop_u256(evm,&oOff); pop_u256(evm,&len);
                         uint32_t dd,ll; if(u256_to_u32(&dOff,&dd)!=0||u256_to_u32(&len,&ll)!=0) return -5;
                         { int rr=charge_mem_expand(evm,dd,ll); if(rr!=0) return rr; }
                         { uint32_t w=(ll+31)/32; if(check_gas(evm,3*(uint64_t)w)!=0) return -10; }
                         memset(evm->mem+dd,0,ll); evm->pc++; break; }
            case 0x3D: { u256_t r; u256_from_u64(&r, (uint64_t)((uint32_t)evm->retdata_len)); push_u256(evm,r); evm->pc++; break; }
            case 0x3E: { u256_t dOff,oOff,len; pop_u256(evm,&dOff); pop_u256(evm,&oOff); pop_u256(evm,&len);
                         uint32_t dd,oo,ll; if(u256_to_u32(&dOff,&dd)!=0||u256_to_u32(&oOff,&oo)!=0||u256_to_u32(&len,&ll)!=0) return -5;
                         if((uint64_t)oo+ll > evm->retdata_len) return -5;
                         { int rr=charge_mem_expand(evm,dd,ll); if(rr!=0) return rr; }
                         { uint32_t w=(ll+31)/32; if(check_gas(evm,3*(uint64_t)w)!=0) return -10; }
                         memcpy(evm->mem+dd,evm->retdata+oo,ll); evm->pc++; break; }
            case 0x3F: { u256_t a,r; pop_u256(evm,&a); u256_zero(&r); push_u256(evm,r); evm->pc++; break; }
            case 0x40: { u256_t a,r; pop_u256(evm,&a); u256_zero(&r); push_u256(evm,r); evm->pc++; break; }
            case 0x41: case 0x42: case 0x44: { u256_t r; u256_zero(&r); push_u256(evm,r); evm->pc++; break; }
            case 0x43: { u256_t r; u256_zero(&r); r.d[0]=(uint32_t)evm->block_number; r.d[1]=(uint32_t)(evm->block_number>>32); push_u256(evm,r); evm->pc++; break; }
            case 0x45: { u256_t r; u256_zero(&r); r.d[0]=(uint32_t)evm->gas_limit; r.d[1]=(uint32_t)(evm->gas_limit>>32); push_u256(evm,r); evm->pc++; break; }
            case 0x46: { u256_t r; u256_zero(&r); r.d[0]=(uint32_t)evm->chainid; r.d[1]=(uint32_t)(evm->chainid>>32); push_u256(evm,r); evm->pc++; break; }
            case 0x47: push_u256(evm,evm->callvalue); evm->pc++; break; // balance is 0 + callvalue
            case 0x48: { u256_t r; u256_zero(&r); push_u256(evm,r); evm->pc++; break; }
            case 0x50: { u256_t a; pop_u256(evm,&a); evm->pc++; break; }
            case 0x51: { u256_t off,r; pop_u256(evm,&off); uint32_t o; if(u256_to_u32(&off,&o)!=0) return -5;
                         { int rr=charge_mem_expand(evm,o,32); if(rr!=0) return rr; }
                         u256_zero(&r);
                         for(int k=0;k<8;k++) r.d[7-k]=((uint32_t)evm->mem[o+k*4]<<24)|((uint32_t)evm->mem[o+k*4+1]<<16)|((uint32_t)evm->mem[o+k*4+2]<<8)|evm->mem[o+k*4+3];
                         push_u256(evm,r); evm->pc++; break; }
            case 0x52: { u256_t off,v; pop_u256(evm,&off); pop_u256(evm,&v); uint32_t o; if(u256_to_u32(&off,&o)!=0) return -5;
                         { int rr=charge_mem_expand(evm,o,32); if(rr!=0) return rr; }
                         for(int k=0;k<8;k++){ uint32_t w=v.d[7-k]; evm->mem[o+k*4+0]=(w>>24)&0xFF; evm->mem[o+k*4+1]=(w>>16)&0xFF; evm->mem[o+k*4+2]=(w>>8)&0xFF; evm->mem[o+k*4+3]=w&0xFF; }
                         evm->pc++; break; }
            case 0x53: { u256_t off,v; pop_u256(evm,&off); pop_u256(evm,&v); uint32_t o; if(u256_to_u32(&off,&o)!=0) return -5;
                         { int rr=charge_mem_expand(evm,o,1); if(rr!=0) return rr; }
                         evm->mem[o]=(uint8_t)(v.d[0]&0xFF); evm->pc++; break; }
            case 0x54: { u256_t key,r; pop_u256(evm,&key);
                         int idx=slot_find_or_create(evm,&key); if(idx<0) return -6;
                         if(check_gas(evm, evm->storage[idx].warm?100:2100)!=0) return -10;
                         evm->storage[idx].warm=1;
                         push_u256(evm,evm->storage[idx].val); evm->pc++; break; }
            case 0x55: { u256_t key,v; pop_u256(evm,&key); pop_u256(evm,&v);
                         int idx=slot_find_or_create(evm,&key); if(idx<0) return -6;
                         uint64_t cost=0;
                         sstore_cost(evm,idx,&key,&evm->storage[idx].val,&v,&cost);
                         if(check_gas(evm,cost)!=0) return -10;
                         evm->storage[idx].val=v; evm->pc++; break; }
            case 0x56: { u256_t d; pop_u256(evm,&d); uint32_t dd; if(u256_to_u32(&d,&dd)!=0) return -4;
                         if(!jumpdest_valid(evm,dd)) return -4; evm->pc=dd; break; }
            case 0x57: { u256_t d,c; pop_u256(evm,&d); pop_u256(evm,&c); uint32_t dd; if(u256_to_u32(&d,&dd)!=0) return -4;
                         if(!u256_is_zero(&c)){ if(!jumpdest_valid(evm,dd)) return -4; evm->pc=dd; } else evm->pc++; break; }
            case 0x58: { u256_t r; u256_from_u64(&r, (uint64_t)((uint32_t)evm->pc)); push_u256(evm,r); evm->pc++; break; }
            case 0x59: { u256_t r; u256_from_u64(&r, (uint64_t)((uint32_t)evm->mem_size)); push_u256(evm,r); evm->pc++; break; }
            case 0x5A: { u256_t r; u256_from_u64(&r, (uint64_t)((uint32_t)(evm->gas_limit-evm->gas_used))); push_u256(evm,r); evm->pc++; break; }
            case 0x5B: evm->pc++; break;
            case 0x5F: { u256_t r; u256_zero(&r); push_u256(evm,r); evm->pc++; break; }
            case 0xF3: case 0xFD: { u256_t o,l; pop_u256(evm,&o); pop_u256(evm,&l); uint32_t oo,ll;
                         if(u256_to_u32(&o,&oo)!=0||u256_to_u32(&l,&ll)!=0) return -5;
                         { int rr=charge_mem_expand(evm,oo,ll); if(rr!=0) return rr; }
                         evm->ret_off=oo; evm->ret_len=ll;
                         evm->reverted=(op==0xFD); evm->halted=1; return 0; }
            case 0xFE: return -8;
            case 0xFF: return -7; // no SELFDESTRUCT in v1
            case 0xF0: case 0xF1: case 0xF2: case 0xF4: case 0xF5: return -7;
            case 0xFA: { // STATICCALL: identity (0x04) only; <0x100 or predeploy -> -12; else -7
                         u256_t gas,a,argsOff,argsSize,retOff,retSize;
                         pop_u256(evm,&gas); pop_u256(evm,&a); pop_u256(evm,&argsOff);
                         pop_u256(evm,&argsSize); pop_u256(evm,&retOff); pop_u256(evm,&retSize);
                         uint32_t ao,as_,ro,rs;
                         if(u256_to_u32(&argsOff,&ao)!=0||u256_to_u32(&argsSize,&as_)!=0||
                            u256_to_u32(&retOff,&ro)!=0||u256_to_u32(&retSize,&rs)!=0) return -5;
                         { int rr=charge_mem_expand(evm,ao,as_); if(rr!=0) return rr; }
                         { int rr=charge_mem_expand(evm,ro,rs); if(rr!=0) return rr; }
                         // Target is the low 160 bits (d[0..4]); high bits truncated.
                         int small = a.d[4]==0 && a.d[3]==0 && a.d[2]==0 && a.d[1]==0;
                         // OP L1Block predeploy 0x4200...0015
                         int is_l1block = (a.d[4]==0x42000000 && a.d[3]==0 && a.d[2]==0 && a.d[1]==0 && a.d[0]==0x15);
                         if(small && a.d[0]==0x04){
                             if(check_gas(evm, evm->addr04_warm?100:2600)!=0) return -10;
                             evm->addr04_warm=1;
                             { uint32_t w=(as_+31)/32; if(check_gas(evm,15+3*(uint64_t)w)!=0) return -10; }
                             if(as_ > EVM_RETDATA_MAX) return -5;
                             memcpy(evm->retdata, evm->mem+ao, as_);
                             evm->retdata_len=as_;
                             { uint32_t cp = as_<rs?as_:rs; memcpy(evm->mem+ro, evm->mem+ao, cp); memset(evm->mem+ro+cp,0,rs-cp); }
                             u256_t r; u256_from_u64(&r, (uint64_t)(1)); push_u256(evm,r); evm->pc++; break;
                         }
                         if((small && a.d[0]<0x100) || is_l1block) return -12;
                         return -7; }
            default: break;
        }

        if(op>=0x60 && op<=0x7F){
            int n=op-0x5F; u256_t v; u256_zero(&v);
            for(int k=0;k<n;k++){
                uint8_t bb=(evm->pc+1+k < evm->code_len)?evm->code[evm->pc+1+k]:0;
                int bitpos=(n-1-k)*8;
                v.d[bitpos/32] |= (uint32_t)bb << (bitpos%32);
            }
            push_u256(evm,v); evm->pc+=1+n;
        } else if(op>=0x80 && op<=0x8F){
            int n=op-0x80; u256_t *p=peek_u256(evm,n); push_u256(evm,*p); evm->pc++;
        } else if(op>=0x90 && op<=0x9F){
            int n=op-0x90+1; u256_t tmp=evm->stack[evm->sp]; evm->stack[evm->sp]=evm->stack[evm->sp-n]; evm->stack[evm->sp-n]=tmp; evm->pc++;
        } else if(op>=0xA0 && op<=0xA4){
            int nt=op-0xA0; u256_t o,l; pop_u256(evm,&o); pop_u256(evm,&l);
            uint32_t oo,ll; if(u256_to_u32(&o,&oo)!=0||u256_to_u32(&l,&ll)!=0) return -5;
            { int rr=charge_mem_expand(evm,oo,ll); if(rr!=0) return rr; }
            if(check_gas(evm,375*(uint64_t)nt+8*(uint64_t)ll)!=0) return -10;
            if(evm->nlogs>=EVM_LOGS_MAX || ll>EVM_LOG_DATA_MAX) return -11;
            evm_log_t *lg=&evm->logs[evm->nlogs++];
            lg->ntopics=nt; lg->data_len=ll;
            for(int t=0;t<nt;t++){ u256_t tv; pop_u256(evm,&tv);
                for(int k=0;k<8;k++){ uint32_t w=tv.d[7-k]; lg->topics[t][k*4+0]=(w>>24)&0xFF; lg->topics[t][k*4+1]=(w>>16)&0xFF; lg->topics[t][k*4+2]=(w>>8)&0xFF; lg->topics[t][k*4+3]=w&0xFF; } }
            memcpy(lg->data, evm->mem+oo, ll);
            evm->pc++;
        }
    }
    return 0;
}
