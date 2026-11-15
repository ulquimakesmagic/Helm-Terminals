// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

interface IPonsEscrowV2 {
    function balanceOf(address recipient) external view returns (uint256);
    function claim() external returns (uint256);
}

/// @notice Native ETH rewards only. No owner, upgrades, recipient changes or arbitrary calls.
/// Pons itself retains its documented power to redirect future protocol rewards.
contract HelmPonsRewardsV2 {
    IPonsEscrowV2 public constant ESCROW = IPonsEscrowV2(0xd3AFEB2a57f70eF218Aa82451c51B2fb0416Ac9e);
    uint256 public constant HELM_BPS = 250;
    address public immutable creator;
    address public immutable treasury;
    uint256 public totalReleased;
    mapping(address => uint256) public released;
    bool private entered;

    event Released(address indexed recipient, uint256 amount);
    event ReleaseDeferred(address indexed recipient);
    error InvalidRecipient();
    error ReentrantCall();
    error TransferFailed();

    constructor(address creator_, address treasury_) {
        if (creator_ == address(0) || treasury_ == address(0) || creator_ == treasury_) revert InvalidRecipient();
        creator = creator_;
        treasury = treasury_;
    }

    modifier nonReentrant() {
        if (entered) revert ReentrantCall();
        entered = true;
        _;
        entered = false;
    }

    receive() external payable {}

    /// @dev Cumulative rounding prevents splitting claims into tiny amounts to avoid Helm's share.
    /// Donations/forced ETH follow the same split; these are receipts, not proof of trading revenue.
    function due(address recipient) public view returns (uint256) {
        if (recipient != creator && recipient != treasury) revert InvalidRecipient();
        uint256 received = address(this).balance + totalReleased;
        uint256 helm = (received / 10000) * HELM_BPS + (received % 10000) * HELM_BPS / 10000;
        return (recipient == treasury ? helm : received - helm) - released[recipient];
    }

    function collect() public nonReentrant {
        if (ESCROW.balanceOf(address(this)) != 0) ESCROW.claim();
    }

    /// @notice Anyone may pay gas, but ETH always goes to the immutable beneficiary.
    function release(address recipient) external nonReentrant {
        uint256 amount = due(recipient);
        if (amount == 0) return;
        released[recipient] += amount;
        totalReleased += amount;
        (bool ok,) = payable(recipient).call{value: amount}("");
        if (!ok) revert TransferFailed();
        emit Released(recipient, amount);
    }

    /// @notice Convenient independent payouts. A rejecting beneficiary cannot block the other.
    /// Direct collect()/release() remain available for beneficiaries needing more gas.
    function distribute() external {
        collect();
        try this.release{gas: 150000}(creator) {} catch { emit ReleaseDeferred(creator); }
        try this.release{gas: 150000}(treasury) {} catch { emit ReleaseDeferred(treasury); }
    }
}

/// @notice Permissionless CREATE2 deployment, including deployment by third parties before launch.
/// Identical parameters always produce identical beneficiaries; no initializer can be hijacked.
/// Helm uses a different launchKey for every launch and freezes this factory address in its preview.
contract HelmPonsRewardsV2Factory {
    event SplitterCreated(address indexed splitter, address indexed creator, address indexed treasury, bytes32 launchKey);

    function predict(address creator, address treasury, bytes32 launchKey) public view returns (address) {
        bytes32 initHash = keccak256(abi.encodePacked(type(HelmPonsRewardsV2).creationCode, abi.encode(creator, treasury)));
        return address(uint160(uint256(keccak256(abi.encodePacked(bytes1(0xff), address(this), launchKey, initHash)))));
    }

    function deploy(address creator, address treasury, bytes32 launchKey) public returns (HelmPonsRewardsV2 splitter) {
        address predicted = predict(creator, treasury, launchKey);
        if (predicted.code.length != 0) return HelmPonsRewardsV2(payable(predicted));
        splitter = new HelmPonsRewardsV2{salt: launchKey}(creator, treasury);
        emit SplitterCreated(address(splitter), creator, treasury, launchKey);
    }

    /// @notice Works even when rewards accrued before deployment at the predicted recipient.
    function distribute(address creator, address treasury, bytes32 launchKey) external {
        deploy(creator, treasury, launchKey).distribute();
    }
}
