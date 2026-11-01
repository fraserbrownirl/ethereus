// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

/// @notice Minimal ERC-20 for the Ethereus EVM gate (own code, MIT).
/// balanceOf is FIRST so its slots are keccak(abi.encode(addr, 0)).
/// No permit (ecrecover precompile is out of v1 scope).
contract MiniERC20 {
    mapping(address => uint256) public balanceOf;                    // slot 0
    mapping(address => mapping(address => uint256)) public allowance; // slot 1
    uint256 public totalSupply;                                       // slot 2
    uint8 public constant decimals = 18;

    event Transfer(address indexed from, address indexed to, uint256 amount);
    event Approval(address indexed owner, address indexed spender, uint256 amount);

    function approve(address spender, uint256 amount) public returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transfer(address to, uint256 amount) public returns (bool) {
        balanceOf[msg.sender] -= amount;
        unchecked { balanceOf[to] += amount; }
        emit Transfer(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) public returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        if (allowed != type(uint256).max) allowance[from][msg.sender] = allowed - amount;
        balanceOf[from] -= amount;
        unchecked { balanceOf[to] += amount; }
        emit Transfer(from, to, amount);
        return true;
    }
}
