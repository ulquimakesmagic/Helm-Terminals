// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

contract MockPonsEscrow {
    mapping(address => uint256) public balanceOf;
    function credit(address recipient) external payable { balanceOf[recipient] += msg.value; }
    function claim() external returns (uint256 amount) {
        amount = balanceOf[msg.sender];
        require(amount > 0);
        balanceOf[msg.sender] = 0;
        (bool ok,) = msg.sender.call{value: amount}("");
        require(ok);
    }
}
contract RejectEther { receive() external payable { revert(); } }
contract Reenter {
    address public target;
    function setTarget(address target_) external { target = target_; }
    receive() external payable {
        // Attempt a second withdrawal while the first is in flight.
        (bool reentered,) = target.call(abi.encodeWithSignature("release(address)", address(this)));
        require(!reentered, "reentrant withdrawal was accepted");
    }
}
