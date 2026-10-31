#pragma once
#include <stdint.h>
#include <stddef.h>
#include "u256.h"

#define EVM_STACK_MAX 256
#define EVM_MEM_MAX (8*1024)
#define EVM_STORAGE_SLOTS 128
#define EVM_CODE_MAX (24*1024)
#define EVM_GAS_LIMIT 10000000

typedef struct { u256_t key; u256_t val; int used; } evm_slot_t;

typedef struct {
    u256_t stack[EVM_STACK_MAX];
    int sp; // -1 empty
    uint8_t mem[EVM_MEM_MAX];
    size_t mem_size; // actual expanded size
    evm_slot_t storage[EVM_STORAGE_SLOTS];
    uint8_t code[EVM_CODE_MAX];
    size_t code_len;
    size_t pc;
    uint64_t gas_used;
    uint64_t gas_limit;
    int halted;
    int reverted;
    uint8_t *ret_data;
    size_t ret_len;
    uint8_t ret_buf[8192];
    // context
    u256_t caller;
    u256_t address;
    u256_t callvalue;
    uint8_t calldata[4096];
    size_t calldata_len;
    uint64_t chainid;
    // logs for debugging
} evm_t;

void evm_init(evm_t *evm, const uint8_t *code, size_t code_len, const uint8_t *calldata, size_t calldata_len, u256_t callvalue, uint64_t chainid);
int evm_run(evm_t *evm); // 0 success/revert, <0 fault
int evm_storage_load(evm_t *evm, const u256_t *key, u256_t *out);
int evm_storage_store(evm_t *evm, const u256_t *key, const u256_t *val);
int evm_mem_expand(evm_t *evm, size_t offset, size_t len);
uint64_t evm_gas_cost(uint8_t opcode); // simplified

// helper to create address from hex string for context
int evm_parse_addr(const char *hex, u256_t *out);
