// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

interface IERC20 {
    function balanceOf(address owner) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
}

/// @notice Generalization of HelmPonsRewards: the same immutable 94/6 accounting, but for the chain's
/// native coin and any ERC-20, so a launchpad that pays out in tokens can use the same splitter.
/// No owner, upgrades, recipient changes or arbitrary calls. The 6% is fixed per contract version.
/// Donations and forced transfers follow the same split: receipts, not proof of trading revenue.
/// Accounting is balance-driven and assumes standard ERC-20s: outgoing transfers deliver the full
/// amount, and tokens that mint, burn or rebase balances behind the splitter's back cannot be split
/// safely. There is no escrow pull here — an adapter claims to this splitter first, then distributes.
contract HelmRewardsSplitter {
    uint256 public constant HELM_BPS = 600;
    address public constant NATIVE = address(0); // real tokens can never live at address zero
    address public immutable creator;
    address public immutable treasury;
    // Cumulative per-asset accounting: asset => (party => released), asset => total released.
    mapping(address => uint256) public totalReleased;
    mapping(address => mapping(address => uint256)) public released;
    bool private entered;

    event Released(address indexed asset, address indexed recipient, uint256 amount);
    event ReleaseDeferred(address indexed asset, address indexed recipient);
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
    function due(address asset, address recipient) public view returns (uint256) {
        if (recipient != creator && recipient != treasury) revert InvalidRecipient();
        uint256 held = asset == NATIVE ? address(this).balance : IERC20(asset).balanceOf(address(this));
        uint256 received = held + totalReleased[asset];
        uint256 helm = (received / 10000) * HELM_BPS + (received % 10000) * HELM_BPS / 10000;
        return (recipient == treasury ? helm : received - helm) - released[asset][recipient];
    }

    /// @notice Anyone may pay gas, but assets always go to the immutable beneficiary.
    function release(address asset, address recipient) external nonReentrant {
        uint256 amount = due(asset, recipient);
        if (amount == 0) return;
        released[asset][recipient] += amount;
        totalReleased[asset] += amount;
        if (asset == NATIVE) {
            (bool ok,) = payable(recipient).call{value: amount}("");
            if (!ok) revert TransferFailed();
        } else if (!IERC20(asset).transfer(recipient, amount)) revert TransferFailed();
        emit Released(asset, recipient, amount);
    }

    /// @notice Convenient independent payouts of one asset. A failing beneficiary cannot block the other.
    /// Direct release() remains available for beneficiaries needing more gas.
    function distribute(address asset) external {
        try this.release{gas: 300000}(asset, creator) {} catch { emit ReleaseDeferred(asset, creator); }
        try this.release{gas: 300000}(asset, treasury) {} catch { emit ReleaseDeferred(asset, treasury); }
    }
}

/// @notice Permissionless CREATE2 deployment, including deployment by third parties before launch.
/// Identical parameters always produce identical beneficiaries; no initializer can be hijacked.
/// Helm uses a different launchKey for every launch and freezes this factory address in its preview.
/// Deploy one factory per chain; rewards can accrue at the predicted address before deployment.
contract HelmRewardsSplitterFactory {
    event SplitterCreated(address indexed splitter, address indexed creator, address indexed treasury, bytes32 launchKey);

    function predict(address creator, address treasury, bytes32 launchKey) public view returns (address) {
        bytes32 initHash = keccak256(abi.encodePacked(type(HelmRewardsSplitter).creationCode, abi.encode(creator, treasury)));
        return address(uint160(uint256(keccak256(abi.encodePacked(bytes1(0xff), address(this), launchKey, initHash)))));
    }

    function deploy(address creator, address treasury, bytes32 launchKey) public returns (HelmRewardsSplitter splitter) {
        address predicted = predict(creator, treasury, launchKey);
        if (predicted.code.length != 0) return HelmRewardsSplitter(payable(predicted));
        splitter = new HelmRewardsSplitter{salt: launchKey}(creator, treasury);
        emit SplitterCreated(address(splitter), creator, treasury, launchKey);
    }

    /// @notice Deploys counterfactually and pays one asset out in a single call — works even when
    /// rewards accrued before deployment at the predicted recipient.
    function distribute(address creator, address treasury, bytes32 launchKey, address asset) external {
        deploy(creator, treasury, launchKey).distribute(asset);
    }
}
