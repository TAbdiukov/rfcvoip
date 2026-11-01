from types import SimpleNamespace

import pytest

import rfcvoip.SIPTransport as transport
from rfcvoip.SIPTransport import SIPResolver


def record(name, weight, priority=0):
    return SimpleNamespace(target=name, weight=weight, priority=priority)


def test_zero_weight_srv_record_has_the_rfc2782_zero_ticket_chance(
    monkeypatch,
):
    weighted = record("weighted.test", 5)
    zero = record("zero.test", 0)
    tickets = []

    def ticket(low, high):
        tickets.append((low, high))
        return low

    monkeypatch.setattr(transport.random, "randint", ticket)

    selected = SIPResolver._pick_srv_record([weighted, zero])

    assert selected is zero
    assert tickets == [(0, 5)]


@pytest.mark.parametrize("ticket,expected", [(1, 0), (2, 0), (3, 1), (5, 1)])
def test_positive_srv_weights_keep_their_running_sum_ranges(
    monkeypatch, ticket, expected
):
    records = [record("a.test", 2), record("b.test", 3)]
    monkeypatch.setattr(transport.random, "randint", lambda low, high: ticket)

    assert SIPResolver._pick_srv_record(records) is records[expected]


def test_lower_priority_srv_record_cannot_win_using_its_weight(monkeypatch):
    preferred = record("preferred.test", 0, priority=1)
    fallback = record("fallback.test", 65535, priority=2)
    monkeypatch.setattr(transport.random, "choice", lambda values: values[-1])

    assert SIPResolver._pick_srv_record([fallback, preferred]) is preferred


def test_all_zero_weight_srv_records_remain_eligible(monkeypatch):
    records = [record("a.test", 0), record("b.test", 0)]
    monkeypatch.setattr(transport.random, "choice", lambda values: values[-1])

    assert SIPResolver._pick_srv_record(records) is records[-1]
