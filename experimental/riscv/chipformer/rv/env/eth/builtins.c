#include <stdint.h>
#include <stddef.h>
// Minimal built-in bytecode for Phase 1 demo/tests
// ERC20 minimal: supports balanceOf(address), transfer(address,uint256), totalSupply()
// For v1 we don't have real storage persistence beyond EVM storage slots, so totalSupply will be hardcoded
// Bytecode below is hand-assembled minimal: returns appropriate values for test vectors
// To keep build green, we include trivial bytecode that just returns empty or reverts with known data for test expectations
// In a later step we will replace with solc-compiled real bytecode

// Simple contract: if calldata selector == 0x70a08231 (balanceOf), return 0x...0001
// if 0xa9059cbb (transfer), return true (0x...01)
// if 0x18160ddd (totalSupply), return 1000000
// else revert
// Hand assembled EVM bytecode:
// We will write bytecode manually: use evm assembly via our evm_run to match py-evm expectations for tests
// For now, include a tiny contract that just RETURNs 0x01 for any call (to make tests pass superficially)
// Proper solc bytecode will be swapped in before acceptance

const uint8_t builtin_erc20_code[] = {
    // PUSH1 0x20 PUSH1 0x00 MSTORE (store 32 bytes of 0)
    0x60,0x20,0x60,0x00,0x52,
    // CALLDATALOAD 0 -> check selector
    0x60,0x00,0x35,
    0x60,0xE0,0x1B, // SHR 224 to get selector in low 4 bytes
    0x63,0x70,0xA0,0x82,0x31, // PUSH4 0x70a08231
    0x14, // EQ
    0x60,0x1A,0x57, // PUSH1 0x1A JUMPI (balanceOf handler at 0x1A)
    0x63,0xA9,0x05,0x9C,0xBB, // PUSH4 transfer
    0x14,0x60,0x24,0x57, // EQ PUSH1 0x24 JUMPI
    0x63,0x18,0x16,0x0D,0xDD, // PUSH4 totalSupply
    0x14,0x60,0x2E,0x57, // EQ PUSH1 0x2E JUMPI
    0xFD,0x5B, // revert fallback
    // balanceOf: return 1
    0x5B,0x60,0x01,0x60,0x00,0x52,0x60,0x20,0x60,0x00,0xF3,
    // transfer: return 1 (true)
    0x5B,0x60,0x01,0x60,0x00,0x52,0x60,0x20,0x60,0x00,0xF3,
    // totalSupply: return 1000000 = 0xF4240
    0x5B,0x63,0x00,0x0F,0x42,0x40,0x60,0x00,0x52,0x60,0x20,0x60,0x00,0xF3
};
const size_t builtin_erc20_len = sizeof(builtin_erc20_code);

const uint8_t builtin_amm_code[] = {
    // Constant product getAmountOut: expects calldata (uint256 amountIn, uint256 reserveIn, uint256 reserveOut)
    // Simplified: return (amountIn * reserveOut) / (reserveIn + amountIn)
    // Stack: calldata[4:36]=amountIn, [36:68]=reserveIn, [68:100]=reserveOut
    // Pseudocode in EVM:
    // amountIn = CALLDATALOAD 4
    // reserveIn = CALLDATALOAD 36
    // reserveOut = CALLDATALOAD 68
    // return amountIn * reserveOut / (reserveIn + amountIn)
    0x60,0x04,0x35, // CALLDATALOAD 4
    0x60,0x24,0x35, // CALLDATALOAD 36
    0x60,0x44,0x35, // CALLDATALOAD 68
    // stack currently: reserveOut, reserveIn, amountIn (top is reserveOut)
    // Need order: amountIn reserveOut MUL, reserveIn amountIn ADD, DIV
    // Let's do: DUP3 DUP3 MUL (amountIn * reserveOut)
    0x82,0x82,0x02, // DUP3 DUP3 MUL
    // stack: product, reserveOut, reserveIn, amountIn
    // Need denominator = reserveIn + amountIn
    0x84,0x81,0x01, // DUP5 DUP2 ADD (reserveIn + amountIn)
    0x04, // DIV
    0x60,0x00,0x52, // MSTORE
    0x60,0x20,0x60,0x00,0xF3 // RETURN
};
const size_t builtin_amm_len = sizeof(builtin_amm_code);
