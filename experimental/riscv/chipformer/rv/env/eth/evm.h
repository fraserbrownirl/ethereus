#pragma once
#include <stdint.h>
#include <stddef.h>
#include "u256.h"

// RAM budget (32 KB total, ~4 KB C stack): evm_t lives in BSS (~13 KB).
// Stack 128 (4 KB), mem 4 KB, storage 32 slots (ERC-20 touches <=4/call),
// jumpmap 1.5 KB (12 KB code cap).
// Code and fixture stay in DROM (pointers, no copies); return data is an
// (off,len) window into mem, not a copy.
#define EVM_STACK_MAX 128
#define EVM_MEM_MAX (4*1024)
#define EVM_STORAGE_SLOTS 32
#define EVM_GAS_LIMIT 10000000
#define EVM_CALLDATA_MAX 1024
#define EVM_RETDATA_MAX 1024
#define EVM_LOGS_MAX 2
#define EVM_LOG_DATA_MAX 64
#define EVM_LOG_TOPICS_MAX 4
#define EVM_CODE_MAX (12*1024) // file bytecode cap; jumpmap sized to it

// Fixture slot, layout-identical to builtin_slot_t in builtins.gen.h
// (asserted in builtins.c). DROM-resident; orig values for EIP-2200.
typedef struct { uint8_t key[32]; uint8_t val[32]; } evm_fixture_slot_t;

typedef struct { u256_t key; u256_t val; uint8_t warm; } evm_slot_t;

typedef struct {
    uint8_t topics[EVM_LOG_TOPICS_MAX][32];
    int ntopics;
    uint8_t data[EVM_LOG_DATA_MAX];
    size_t data_len;
} evm_log_t;

typedef struct {
    u256_t stack[EVM_STACK_MAX];
    int sp; // -1 empty
    uint8_t mem[EVM_MEM_MAX];
    size_t mem_size;
    evm_slot_t storage[EVM_STORAGE_SLOTS];
    const uint8_t *code; // DROM (built-ins) — no copy
    size_t code_len;
    size_t pc;
    uint64_t gas_used; // gross execution gas (pre-refund), like py-evm
    uint64_t gas_limit;
    int halted;
    int reverted;
    size_t ret_off, ret_len; // window into mem
    u256_t caller;
    u256_t address;
    u256_t callvalue;
    uint8_t calldata[EVM_CALLDATA_MAX];
    size_t calldata_len;
    uint8_t retdata[EVM_RETDATA_MAX];
    size_t retdata_len;
    uint64_t chainid;
    uint64_t block_number;
    evm_log_t logs[EVM_LOGS_MAX];
    int nlogs;
    const evm_fixture_slot_t *fixture;
    size_t fixture_len;
    int addr04_warm; // identity precompile touched
    int nslots; // storage occupancy: entries [0,nslots) valid, no zeroing
    uint8_t jumpmap[EVM_CODE_MAX/8]; // valid-JUMPDEST bitmap, built at init
} evm_t;

// Return codes: 0 ok (check reverted), -2 invalid opcode, -3 stack,
// -4 bad jump, -5 mem capacity, -6 storage full, -7 unsupported op,
// -8 invalid (0xFE), -10 out of gas, -11 log overflow,
// -12 chain-specific precompile.
// Returns 0, or -1 if code_len exceeds EVM_CODE_MAX (caller prints too big).
int evm_init(evm_t *evm, const uint8_t *code, size_t code_len,
             const uint8_t *calldata, size_t calldata_len,
             const u256_t *callvalue, const u256_t *caller, uint64_t chainid,
             const evm_fixture_slot_t *fixture, size_t fixture_len);
int evm_run(evm_t *evm);
int evm_mem_expand(evm_t *evm, size_t offset, size_t len);
// Original (fixture) value of a slot, 0 if absent. Used for SLOT diff output.
void evm_slot_orig(evm_t *evm, const u256_t *key, u256_t *out);
