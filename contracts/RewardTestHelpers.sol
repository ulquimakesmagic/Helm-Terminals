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
    bytes public payload;
    function setTarget(address target_) external {
        target = target_;
        // Default matches HelmPonsRewards' release(address); HelmRewardsSplitter tests set their own.
        payload = abi.encodeWithSignature("release(address)", address(this));
    }
    function setPayload(bytes calldata payload_) external { payload = payload_; }
    receive() external payable {
        // Attempt a second withdrawal while the first is in flight.
        (bool reentered,) = target.call(payload);
        require(!reentered, "reentrant withdrawal was accepted");
    }
}
contract MockERC20 {
    event Transfer(address indexed from, address indexed to, uint256 value);
    mapping(address => uint256) public balanceOf;
    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
        emit Transfer(address(0), to, amount);
    }
    function transfer(address to, uint256 amount) external returns (bool) {
        require(balanceOf[msg.sender] >= amount);
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        emit Transfer(msg.sender, to, amount);
        return true;
    }
}
