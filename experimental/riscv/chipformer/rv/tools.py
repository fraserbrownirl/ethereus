"""Tool schemas for the eth firmware. One tool, one ``cmd`` string, as the firmware parses it."""

ETH_TOOL = {"type": "function", "function": {
    "name": "eth",
    "description": "Exact Ethereum helpers, run inside the model. One command per call: "
                   "keccak <0xhex|'text'>, selector <sig>, encode <sig> <args...>, "
                   "decode <types> <0xhex>, u256 <expr>, create <deployer> <nonce>, "
                   "create2 <deployer> <salt> <initcodehash>, run <contract> <calldata> "
                   "(built-in contracts: erc20, amm). An argument too long for the call can be "
                   "named as @file. Returns the exact result or error (...).",
    "parameters": {"type": "object", "properties": {
        "cmd": {"type": "string",
                "description": "e.g. selector transfer(address,uint256) or keccak 0x6161 or "
                               "u256 0xff*0x10 or create 0x000000000000000000000000000000000000dEaD 1"}},
        "required": ["cmd"]}}}
