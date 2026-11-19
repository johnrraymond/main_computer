"""Contract Language v2: escrowed ETH state machines with recipient-isolated credits.

A new compiler, leaving the verified v1 push-payment path unmodified.  This is
experimental tooling, not an audited contract implementation or a deployment API.
"""
from __future__ import annotations
import re
from tools import nanojev_contract_state_machine as v1
from tools.nanojev_contract_solidity_smoke import SmokeFailure

LANG = 'nanojev.contract.state_machine.v2'
RESERVED = {'withdraw', 'withdraw_to', 'pending_withdrawals', 'total_pending',
            'withdraw_lock', 'credit', 'receive', 'fallback', 'fund'}


def validate(raw: dict) -> dict:
    if not isinstance(raw, dict) or set(raw) != {
        'language','asset','payer','worker','reviewer','amount_wei','deadline_seconds',
        'rules','settlement_mode'
    } or raw.get('language') != LANG or raw.get('settlement_mode') != 'pull':
        raise SmokeFailure('contract_validation', 'Require explicit v2 pull-payment Contract Language')
    original={k:v for k,v in raw.items() if k!='settlement_mode'}
    original['language']=v1.LANG
    spec=v1.validate(original)
    if any(r['name'] in RESERVED or r['name'].startswith('withdraw') for r in spec['rules']):
        raise SmokeFailure('contract_validation', 'Transition collides with credit withdrawal API')
    # Any credited payee must receive at least one wei. Otherwise a zero-credit
    # beneficiary would have no executable withdrawal despite their allocation.
    for r in spec['rules']:
        for p in r['payments'][:-1]:
            if spec['amount_wei'] * p['bps'] // 10000 == 0:
                raise SmokeFailure('contract_validation', 'Zero-wei beneficiary credit')
    return {**spec,'language':LANG,'settlement_mode':'pull'}


def old(spec: dict) -> dict:
    spec=validate(spec)
    return {**{k:v for k,v in spec.items() if k!='settlement_mode'}, 'language':v1.LANG}


def upgrade(spec: dict) -> dict:
    return validate({**v1.validate(spec),'language':LANG,'settlement_mode':'pull'})


def source(raw: dict, name: str) -> str:
    spec=validate(raw)
    if not re.fullmatch(r'NanoJevPull[0-9]{2}',name):
        raise SmokeFailure('solidity_emit','Invalid pull compiler class name')
    caps,_ = v1.state_ids(old(spec))
    methods=[]
    for rule in spec['rules']:
        lines=[f'        require(msg.sender == {rule["actor"]}, "not authorized");',
               f'        require(state == State.{caps[rule["from"]]}, "wrong state");']
        if rule['when']=='before_deadline':
            lines.append('        require(block.timestamp < fundedAt + DEADLINE_SECONDS, "deadline expired");')
        if rule['when']=='at_or_after_deadline':
            lines.append('        require(block.timestamp >= fundedAt + DEADLINE_SECONDS, "too early");')
        lines.append(f'        state = State.{caps[rule["to"]]};')
        for i,p in enumerate(rule['payments']):
            if i+1<len(rule['payments']):
                amount=f'(AMOUNT_WEI * {p["bps"]}) / 10000'
            elif i==0:
                amount='AMOUNT_WEI'
            else:
                amount='AMOUNT_WEI - ('+' + '.join(f'share{j}' for j in range(i))+')'
            lines.append(f'        uint256 share{i} = {amount};')
        for i,p in enumerate(rule['payments']):
            lines += [f'        pendingWithdrawals[{p["to"]}] += share{i};',
                      f'        totalPending += share{i};',
                      f'        emit CreditAssigned({p["to"]}, share{i});']
        methods.append('    function '+rule['name']+'() external {\n'+'\n'.join(lines)+'\n    }')
    return f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
/// @notice EXPERIMENTAL bounded ETH settlement compiler. UNAUDITED; DO NOT DEPLOY.
contract {name} {{
    enum State {{ {', '.join(caps.values())} }}
    State public state;
    address payable public immutable payer;
    address payable public immutable worker;
    address payable public immutable reviewer;
    uint256 public constant AMOUNT_WEI = {spec['amount_wei']};
    uint256 public constant DEADLINE_SECONDS = {spec['deadline_seconds']};
    uint256 public fundedAt;
    mapping(address => uint256) public pendingWithdrawals;
    uint256 public totalPending;
    bool private withdrawalLock;
    event CreditAssigned(address indexed beneficiary, uint256 amount);
    event CreditWithdrawn(address indexed beneficiary, address indexed receiver, uint256 amount);

    constructor(address payable worker_, address payable reviewer_) {{
        require(worker_ != address(0) && reviewer_ != address(0), "zero actor");
        require(worker_ != reviewer_ && worker_ != msg.sender && reviewer_ != msg.sender, "duplicate actor");
        payer = payable(msg.sender);
        worker = worker_;
        reviewer = reviewer_;
    }}
    function fund() external payable {{
        require(msg.sender == payer, "payer only");
        require(state == State.Unfunded, "already funded");
        require(msg.value == AMOUNT_WEI, "wrong amount");
        fundedAt = block.timestamp;
        state = State.Funded;
    }}
    /// @notice Only the beneficiary can claim their credit, to an address they choose.
    /// @dev Credit is cleared before the external transfer, with a reentrancy guard.
    function withdrawTo(address payable destination) public {{
        require(!withdrawalLock, "withdrawal reentrant");
        require(destination != address(0), "zero destination");
        uint256 amount = pendingWithdrawals[msg.sender];
        require(amount > 0, "nothing owed");
        withdrawalLock = true;
        pendingWithdrawals[msg.sender] = 0;
        totalPending -= amount;
        (bool ok,) = destination.call{{value: amount}}("");
        require(ok, "withdrawal failed");
        withdrawalLock = false;
        emit CreditWithdrawn(msg.sender, destination, amount);
    }}
    function withdraw() external {{ withdrawTo(payable(msg.sender)); }}
{chr(10).join(methods)}
}}
'''


def tests(raw: dict, name: str) -> tuple[str,int]:
    """Build a Foundry oracle from separately pinned gold, NEVER inferred LLM JSON.

    Generates per-rule payout credit, authorization, boundary, withdrawal and
    rejecting-recipient checks.  Also checks an independent adversarial property:
    rejection on one recipient cannot revert state-machine settlement.
    """
    spec=validate(raw)
    base=old(spec)
    _, state_ids=v1.state_ids(base)
    items=[]
    def add(fn,lines,param=''):
        if fn.startswith('testFail'):
            raise SmokeFailure('solidity_emit','Legacy Foundry test name')
        items.append('    function '+fn+'('+param+') public {\n'+'\n'.join(lines)+'\n    }')
    def addr(role):
        return {'payer':'address(this)', 'worker':'WORKER','reviewer':'REVIEWER'}[role]
    def clock(rule, wrong=False):
        if rule['when']=='always':return []
        after=(rule['when']=='at_or_after_deadline') != wrong
        return ['        vm.warp(c.fundedAt() + DEADLINE'+(');' if after else ' - 1);')]
    def action(rule, bad=False, revert=False):
        caller='STRANGER' if bad else ('address(c.worker())' if rule['actor']=='worker' else 'address(c.reviewer())' if rule['actor']=='reviewer' else 'address(this)')
        lines=[f'        vm.prank({caller});']
        if revert:
            lines += [f'        (bool ok,) = address(c).call(abi.encodeWithSignature("{rule["name"]}()"));',
                      '        require(!ok, "unauthorized/state/timing action accepted");']
        else:
            lines.append(f'        c.{rule["name"]}();')
        return lines
    def prep(rule, rejecting_worker=False):
        lines=[f'        {name} c = funded({str(rejecting_worker).lower()});']
        for prior in v1.route(base,rule['from']):
            lines += clock(prior)+action(prior)
        return lines
    add('testNoUnauthorizedFunding',[f'        {name} c = fresh(false);',
        '        vm.deal(STRANGER, AMOUNT);','        vm.prank(STRANGER);',
        '        (bool ok,) = address(c).call{value: AMOUNT}(abi.encodeWithSignature("fund()"));',
        '        require(!ok, "unauthorized funding");'])
    add('testRejectWrongDeposit',[f'        {name} c = fresh(false);',
        '        (bool ok,) = address(c).call{value: AMOUNT - 1}(abi.encodeWithSignature("fund()"));',
        '        require(!ok, "wrong deposit");'])
    add('testRejectDoubleFunding',[f'        {name} c = funded(false);',
        '        (bool ok,) = address(c).call{value: AMOUNT}(abi.encodeWithSignature("fund()"));',
        '        require(!ok, "duplicate funding");'])
    add('testFuzzRejectWrongAmount',[f'        {name} c = fresh(false);',
        '        uint256 value = uint256(seed) % AMOUNT;',
        '        (bool ok,) = address(c).call{value: value}(abi.encodeWithSignature("fund()"));',
        '        require(!ok, "wrong amount");'],'uint96 seed')
    add('testConstructorRejectsSameRoles',[f'        try new {name}(WORKER, WORKER) returns ({name} c) {{',
        '            require(address(c) != address(0), "constructor");',
        '            revert("duplicate roles");', '        } catch {}'])
    add('testZeroCreditCannotWithdraw',[f'        {name} c = funded(false);',
        '        (bool ok,) = address(c).call(abi.encodeWithSignature("withdraw()"));',
        '        require(!ok, "uncredited withdrawal");'])
    for rule in spec['rules']:
        suffix=''.join(x.capitalize() for x in rule['name'].split('_'))
        add('testRejectUnfunded'+suffix,[f'        {name} c = fresh(false);']+action(rule,revert=True))
        add('testRejectUnauthorized'+suffix,prep(rule)+clock(rule)+action(rule,bad=True,revert=True))
        if rule['when']!='always':
            add('testRejectWrongTime'+suffix,prep(rule)+clock(rule,wrong=True)+action(rule,revert=True))
        happy=prep(rule)+clock(rule)+['        uint256 priorPending = c.totalPending();']+action(rule)
        happy += [f'        require(uint256(c.state()) == {state_ids[rule["to"]]}, "wrong state");']
        if rule['payments']:
            happy += ['        require(c.totalPending() == AMOUNT, "sum credits unequal deposit");',
                      '        require(address(c).balance == AMOUNT, "settlement pushed ETH");']
            running=0
            for i,p in enumerate(rule['payments']):
                if i+1<len(rule['payments']):
                    value=f'(AMOUNT * {p["bps"]}) / 10000'
                elif i==0:value='AMOUNT'
                else:value='AMOUNT - ('+' + '.join(
                    f'(AMOUNT * {q["bps"]}) / 10000' for q in rule['payments'][:i])+')'
                happy += [f'        require(c.pendingWithdrawals({addr(p["to"])}) == {value}, "wrong credit");']
                running+=p['bps']
            happy += ['        require(c.totalPending() <= address(c).balance, "liabilities exceed ETH");']
        else:
            happy += ['        require(c.totalPending() == priorPending, "nonsettlement credited ETH");']
        add('testGoldCredits'+suffix,happy)
        if rule['payments']:
            add('testCannotSettleTwice'+suffix,prep(rule)+clock(rule)+action(rule)+action(rule,revert=True))
            # Credits can be claimed in arbitrary order, and cannot be claimed twice.
            for p in rule['payments']:
                recipient=addr(p['to'])
                # Use alternate EOA receiver so contract-wallet recipients can recover too.
                lines=prep(rule)+clock(rule)+action(rule)
                lines += [f'        uint256 credit = c.pendingWithdrawals({recipient});',
                          '        uint256 previousBalance = DESTINATION.balance;',
                          f'        vm.prank({recipient});',
                          '        c.withdrawTo(DESTINATION);',
                          '        require(DESTINATION.balance - previousBalance == credit, "wrong withdrawal");',
                          f'        require(c.pendingWithdrawals({recipient}) == 0, "double credit");',
                          '        require(c.totalPending() == AMOUNT - credit, "wrong remaining liability");',
                          '        require(address(c).balance == AMOUNT - credit, "wrong custody");',
                          f'        vm.prank({recipient});',
                          '        (bool ok,) = address(c).call(abi.encodeWithSignature("withdraw()"));',
                          '        require(!ok, "double withdrawal");']
                add('testWithdraw'+suffix+p['to'].title(),lines)
            # If a worker address cannot accept ETH, crediting still settles the
            # agreement, and reviewer/payer can withdraw independently.
            if any(p['to']=='worker' for p in rule['payments']):
                lines=prep(rule,True)+clock(rule)+action(rule)
                lines += [f'        require(uint256(c.state()) == {state_ids[rule["to"]]}, "rejector blocked settlement");',
                          '        require(c.pendingWithdrawals(address(c.worker())) > 0, "rejector not credited");',
                          '        require(c.totalPending() == AMOUNT, "credits missing");',
                          '        RejectETH recipient = RejectETH(payable(address(c.worker())));',
                          '        bool failed = recipient.attemptWithdraw(address(c));',
                          '        require(failed, "receiver should reject ETH");',
                          '        require(c.pendingWithdrawals(address(recipient)) > 0, "credit lost on withdrawal revert");']
                others=[p for p in rule['payments'] if p['to']!='worker']
                if others:
                    p=others[0];recipient=addr(p['to'])
                    lines += [f'        uint256 independent = c.pendingWithdrawals({recipient});',
                              '        uint256 previous = DESTINATION.balance;',
                              f'        vm.prank({recipient});', '        c.withdrawTo(DESTINATION);',
                              '        require(DESTINATION.balance - previous == independent, "other payee blocked");']
                # The rejecting contract can direct withdrawal to an accepting address.
                lines += ['        uint256 workerCredit = c.pendingWithdrawals(address(recipient));',
                          '        uint256 targetBefore = DESTINATION.balance;',
                          '        recipient.claimTo(address(c), DESTINATION);',
                          '        require(DESTINATION.balance - targetBefore == workerCredit, "alternate withdrawal failed");',
                          '        require(c.pendingWithdrawals(address(recipient)) == 0, "redirected credit uncleared");']
                add('testRejectorCannotBlockSettlement'+suffix,lines)
                # Regression against a reentrant payee: CEI and the withdrawal
                # guard must prohibit spending the same credit twice.
                reenter=[f'        ReenterETH{name} attacker = new ReenterETH{name}();',
                         '        vm.deal(address(this), AMOUNT * 4);',
                         f'        {name} c = new {name}(payable(address(attacker)), REVIEWER);',
                         '        c.fund{value: AMOUNT}();']
                for prior in v1.route(base,rule['from']):
                    reenter += clock(prior)+action(prior)
                reenter += clock(rule)+action(rule)+[
                    '        uint256 credit = c.pendingWithdrawals(address(attacker));',
                    '        require(credit > 0, "reentrancy fixture uncredited");',
                    '        attacker.claim(address(c));',
                    '        require(attacker.reentryBlocked(), "reentrancy accepted");',
                    '        require(address(attacker).balance == credit, "double payee payment");',
                    '        require(c.pendingWithdrawals(address(attacker)) == 0, "credit reused");',
                    '        require(c.totalPending() == AMOUNT - credit, "liability corrupted");']
                add('testWithdrawalReentrancyBlocked'+suffix,reenter)
    return f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/{name}.sol";
interface Vm{name} {{
    function deal(address who, uint256 amount) external;
    function prank(address who) external;
    function warp(uint256 timestamp) external;
}}
contract RejectETH {{
    receive() external payable {{ revert("reject ETH"); }}
    function attemptWithdraw(address c) external returns (bool failed) {{
        (bool ok,) = c.call(abi.encodeWithSignature("withdraw()"));
        return !ok;
    }}
    function claimTo(address c, address payable receiver) external {{
        {name}(c).withdrawTo(receiver);
    }}
}}
contract ReenterETH{name} {{
    address public target;
    bool public reentryBlocked;
    receive() external payable {{
        (bool ok,) = target.call(abi.encodeWithSignature("withdrawTo(address)", payable(address(this))));
        reentryBlocked = !ok;
    }}
    function claim(address c) external {{
        target = c;
        {name}(c).withdrawTo(payable(address(this)));
    }}
}}
contract {name}SmokeTest {{
    Vm{name} internal constant vm = Vm{name}(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 internal constant AMOUNT = {spec['amount_wei']};
    uint256 internal constant DEADLINE = {spec['deadline_seconds']};
    address payable internal constant WORKER = payable(address(0xBEEF));
    address payable internal constant REVIEWER = payable(address(0xCAFE));
    address payable internal constant DESTINATION = payable(address(0xD00D));
    address internal constant STRANGER = address(0xBAD);
    receive() external payable {{}}
    function fresh(bool rejectingWorker) internal returns ({name} c) {{
        vm.deal(address(this), AMOUNT * 4);
        c = new {name}(rejectingWorker ? payable(address(new RejectETH())) : WORKER, REVIEWER);
    }}
    function funded(bool rejectingWorker) internal returns ({name} c) {{
        c = fresh(rejectingWorker);
        c.fund{{value: AMOUNT}}();
    }}
{chr(10).join(items)}
}}
''',len(items)
