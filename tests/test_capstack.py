import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from capstack import audit, fund, memo, opm, portfolio, qsbs
from capstack.captable import CapTable, Security, load_cap_table
from capstack.cli import main
from capstack.waterfall import breakpoints, payoff

EX = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def table():
    return load_cap_table(EX / "cap_table.csv")


# ---------------- waterfall / OPM
def test_breakpoints_match_hand_calculation(table):
    # LP stack 10M (A) and 12M (A+Seed); strikes 0.30, 0.65; Seed converts at 0.80; A at 2.50
    assert breakpoints(table) == pytest.approx([10e6, 12e6, 14.4e6, 17.62e6, 19.12e6, 40.37e6], rel=1e-6)


def test_payoffs_sum_to_equity_value(table):
    for v in (5e6, 11e6, 16e6, 30e6, 90e6):
        assert sum(payoff(table, v).values()) == pytest.approx(v, rel=1e-9)


def test_opm_allocation_conserves_value(table):
    a = opm.allocate(table, 27.5e6, 3, 0.039, 0.55)
    assert sum(a.values.values()) == pytest.approx(27.5e6, rel=1e-6)


def test_common_only_gets_everything():
    t = CapTable([Security("Common", "common", 1_000_000)])
    a = opm.allocate(t, 5e6, 2, 0.04, 0.6)
    assert a.per_share["Common"] == pytest.approx(5.0)


def test_backsolve_recovers_round_price(table):
    a = opm.backsolve(table, "Series A", 2.50, 3, 0.039, 0.55)
    assert a.per_share["Series A"] == pytest.approx(2.50, abs=1e-6)
    assert a.per_share["Common"] < a.per_share["Series Seed"] < a.per_share["Series A"]


def test_dlom_ranges():
    d = opm.dlom_finnerty(3, 0.55)
    assert 0.15 < d < 0.25
    assert opm.dlom_finnerty(1, 0.55) < d  # longer term, bigger discount
    assert opm.dlom_chaffe(3, 0.55) > d    # Chaffe sits above Finnerty


def test_bs_call_put_call_parity():
    c = opm.bs_call(100, 90, 2, 0.04, 0.5)
    p = c - 100 + 90 * __import__("math").exp(-0.08)
    assert p > 0 and c > 10


# ---------------- QSBS
def test_qsbs_regimes():
    data = qsbs.load(EX / "qsbs.json")
    res = {h.holder: h for h in qsbs.screen(data, date(2026, 11, 5))}
    assert res["Founder A"].excludable_gain == 10_000_000  # $10M cap binds
    assert res["Early Employee"].excludable_gain == 0       # <5y pre-OBBBA
    assert res["Secondary Buyer"].excludable_gain == 0      # not original issuance
    assert res["Series B Investor"].lots[0].regime == "post-OBBBA"


def test_post_obbba_tiers():
    assert qsbs._exclusion(date(2025, 8, 1), 3.1) == ("post-OBBBA", 0.50)
    assert qsbs._exclusion(date(2025, 8, 1), 4.0) == ("post-OBBBA", 0.75)
    assert qsbs._exclusion(date(2025, 8, 1), 5.0) == ("post-OBBBA", 1.00)
    assert qsbs._exclusion(date(2010, 1, 1), 6.0) == ("pre-OBBBA", 0.75)


def test_excluded_field_fails():
    data = {"issuer": {"entity_type": "C-corp", "domestic": True, "industry": "management consulting",
                       "active_business_pct": 0.9, "gross_assets_at_issuance": 1e6},
            "lots": [{"holder": "X", "acquired": "2018-01-01", "basis": 1, "original_issuance": True,
                      "sale_date": "2026-01-02", "proceeds": 100}]}
    assert qsbs.screen(data)[0].excludable_gain == 0


# ---------------- capital calls
def test_capital_call_ties_to_cent():
    lps = fund.load_lps(EX / "lps.csv")
    for amt in ("4250000", "1000000.01", "333333.33", "0.07"):
        n = fund.allocate_call(lps, amt)
        assert sum(x.amount for x in n) == Decimal(amt)


def test_excused_lp_gets_nothing_and_others_absorb():
    lps = fund.load_lps(EX / "lps.csv")
    n = {x.lp: x for x in fund.allocate_call(lps, "1000000", excused={"Uinta Fund of Funds"})}
    assert n["Uinta Fund of Funds"].amount == 0
    assert sum(x.amount for x in n.values()) == Decimal("1000000.00")


def test_call_over_unfunded_rejected():
    lps = [fund.LP("A", Decimal(100), Decimal(95)), fund.LP("B", Decimal(100), Decimal(0))]
    with pytest.raises(ValueError):
        fund.allocate_call(lps, "50", basis="commitment")


# ---------------- portfolio
def test_portfolio_flags_seeded_problems():
    f = portfolio.check(portfolio.load(EX / "kpis.csv"))
    rules = {(x.company, x.period, x.rule) for x in f}
    assert ("Saltflat Robotics", "2025Q4", "unit_scale") in rules
    assert ("Lumen Health Data", "2025Q2", "balance_sheet") in rules
    assert ("Copperline Payments", "2026Q1", "revenue_outlier") in rules
    # the quarter after a scale slip is compared to the last clean quarter, so it is not flagged
    assert not any(x.company == "Saltflat Robotics" and x.period == "2026Q1" for x in f)


# ---------------- audit ledger
def test_ledger_detects_tampering(tmp_path):
    led = tmp_path / "l.jsonl"
    src = tmp_path / "in.csv"
    src.write_text("x\n1\n")
    for i in range(3):
        audit.append(led, "demo", {"f": str(src)}, {"i": i}, {"v": i})
    assert audit.verify(led, check_inputs=True) == []
    lines = led.read_text().splitlines()
    e = json.loads(lines[1]); e["outputs"]["v"] = 999
    lines[1] = json.dumps(e)
    led.write_text("\n".join(lines) + "\n")
    assert any("altered" in p for p in audit.verify(led))
    src.write_text("x\n2\n")
    assert any("changed since" in p for p in audit.verify(led, check_inputs=True))


def test_cli_end_to_end_replay(tmp_path, capsys):
    led = str(tmp_path / "l.jsonl")
    args = ["409a", str(EX / "cap_table.csv"), "--anchor", "Series A", "--price", "2.5",
            "--years", "3", "--vol", "0.55", "--rate", "0.039", "--ledger", led]
    assert main(args) == 0
    assert main(["audit", "verify", "--inputs", "--ledger", led]) == 0
    assert main(["audit", "replay", "1", "--ledger", led]) == 0
    assert "REPRODUCED" in capsys.readouterr().out


# ---------------- memo number guard
def _facts():
    return {"anchor_security": "Series A", "anchor_price": 2.5, "term_years": 3, "volatility": 0.55,
            "risk_free_rate": 0.039, "implied_equity_value": 27542595.0, "common_marketable_per_share": 1.4255,
            "dlom": 0.2005, "common_fmv_per_share": 1.1397, "dlom_method": "finnerty", "ledger_hash": "ab" * 32}


def test_template_passes_its_own_guard():
    f = _facts()
    assert memo.unsupported_numbers(memo.template_memo(f), f) == []


def test_guard_rejects_invented_numbers():
    f = _facts()
    bad = "Common is worth $1.25 per share after a 22% discount."
    assert set(memo.unsupported_numbers(bad, f)) == {"$1.25", "22%"}


def test_ai_memo_falls_back_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    text, source = memo.ai_memo(_facts())
    assert source == "template:no-api-key" and "$1.1397" in text
