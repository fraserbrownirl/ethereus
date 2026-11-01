// Built-in contracts: solc-compiled runtime bytecode + fixtures, generated at
// build time by gen_builtins.py (same pattern as gen_selectors.py).
// This TU owns the definitions; eth.c uses extern declarations.
#include "builtins.gen.h"
#include "evm.h"

// builtin_slot_t must stay layout-identical to evm_fixture_slot_t.
_Static_assert(sizeof(builtin_slot_t) == sizeof(evm_fixture_slot_t),
              "fixture layout drift");
_Static_assert(__builtin_offsetof(builtin_slot_t, key) ==
               __builtin_offsetof(evm_fixture_slot_t, key),
              "fixture key offset drift");
_Static_assert(__builtin_offsetof(builtin_slot_t, val) ==
               __builtin_offsetof(evm_fixture_slot_t, val),
              "fixture val offset drift");
