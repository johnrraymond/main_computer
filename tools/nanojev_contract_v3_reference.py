"""Independent small-step reference interpreter for v3 progressive escrow tests.

Intentionally does NOT use the production graph transition or credit-allocation
helpers. This is a separately expressed operational oracle, not independent
human expert semantic gold, nor an on-chain execution proof.
"""
from __future__ import annotations
from tools import nanojev_contract_v3_language as lang


class ReferenceRevert(Exception):
    pass


class ReferenceEscrow:
    def __init__(self, raw: dict):
        self.spec = lang.validate(raw)
        self.rules = {r['name']: r for r in self.spec['rules']}
        self.funded = False
        self.state = 'unfunded'
        self.after_deadline = False
        self.unallocated = 0
        self.credits = {role: 0 for role in lang.ROLES}
        self.paid_out = {role: 0 for role in lang.ROLES}
        self.sinks = {r['to'] for r in self.spec['rules']} - {r['from'] for r in self.spec['rules']}

    def fund(self, sender: str, amount: int) -> None:
        if self.funded or sender != 'payer' or amount != self.spec['amount_wei']:
            raise ReferenceRevert('funding failed')
        self.state = 'funded'
        self.unallocated = amount
        self.funded = True

    def tick_to_deadline(self) -> None:
        if not self.funded:
            raise ReferenceRevert('not funded')
        self.after_deadline = True

    def execute(self, name: str, caller: str) -> None:
        r = self.rules[name]
        if not self.funded or self.state in self.sinks or r['actor'] != caller:
            raise ReferenceRevert('authority/terminal')
        if self.state != r['from']:
            raise ReferenceRevert('state')
        if r['when'] == 'before_deadline' and self.after_deadline:
            raise ReferenceRevert('deadline passed')
        if r['when'] == 'at_or_after_deadline' and not self.after_deadline:
            raise ReferenceRevert('too early')
        left = self.unallocated
        new_credits = dict(self.credits)
        for item in r['credits']:
            take = left if item['amount_wei'] == 'remaining' else item['amount_wei']
            if not isinstance(take, int) or isinstance(take, bool) or take < 1 or take > left:
                raise ReferenceRevert('insufficient/zero credit')
            left -= take
            new_credits[item['to']] += take
        if r['to'] in self.sinks and left != 0:
            raise ReferenceRevert('terminal residual')
        # Commit only after every condition is satisfied: atomic state+credit update.
        self.unallocated = left
        self.credits = new_credits
        self.state = r['to']

    def withdraw(self, role: str, destination_accepts: bool = True) -> int:
        if role not in self.credits or self.credits[role] <= 0 or not destination_accepts:
            raise ReferenceRevert('no credit/recipient rejection')
        wei = self.credits[role]
        self.credits[role] = 0
        self.paid_out[role] += wei
        return wei

    def conserved(self) -> bool:
        expected = self.spec['amount_wei'] if self.funded else 0
        return self.unallocated + sum(self.credits.values()) + sum(self.paid_out.values()) == expected

    def snapshot(self) -> tuple:
        return (self.state, 'at_or_after_deadline' if self.after_deadline else 'before_deadline',
                self.unallocated, tuple(self.credits[k] for k in lang.ROLES),
                tuple(self.paid_out[k] for k in lang.ROLES))
