"""Draft a plain-English valuation memo from a ledger entry.

Numbers come only from the computation. An LLM (optional) is used for prose,
and every figure in its draft is checked against the computed facts; a draft
that introduces a number not supported by the facts is rejected and the
deterministic template is used instead. AI writes, the math decides.
"""
from __future__ import annotations

import os
import re

NUM_RE = re.compile(r"(?<![\w.])\$?-?\d[\d,]*(?:\.\d+)?%?")
ALLOWED_SMALL = {str(i) for i in range(0, 13)} | {"409", "409A", "2025", "2026", "2027"}


def facts_from_entry(entry: dict) -> dict:
    out = entry["outputs"]
    p = entry["params"]
    return {
        "anchor_security": p.get("anchor"),
        "anchor_price": p.get("price"),
        "term_years": p.get("years"),
        "volatility": p.get("vol"),
        "risk_free_rate": p.get("rate"),
        "implied_equity_value": out.get("equity_value"),
        "common_marketable_per_share": out.get("common_marketable"),
        "dlom": out.get("dlom"),
        "common_fmv_per_share": out.get("common_fmv"),
        "dlom_method": out.get("dlom_method"),
        "ledger_hash": entry.get("hash"),
    }


def _fmt(f: dict) -> dict:
    return {
        "eq": f"${f['implied_equity_value']:,.0f}",
        "anchor": f["anchor_security"],
        "price": f"${f['anchor_price']:,.4f}",
        "mkt": f"${f['common_marketable_per_share']:,.4f}",
        "dlom": f"{f['dlom']:.1%}",
        "fmv": f"${f['common_fmv_per_share']:,.4f}",
        "vol": f"{f['volatility']:.0%}",
        "term": f"{f['term_years']:g}",
        "rate": f"{f['risk_free_rate']:.2%}",
        "method": f["dlom_method"].title(),
        "hash": f["ledger_hash"][:12],
    }


def template_memo(f: dict) -> str:
    v = _fmt(f)
    return (
        "Common Stock Fair Market Value - Summary\n\n"
        f"We calibrated total equity value to the most recent arm's-length financing, the {v['anchor']} "
        f"at {v['price']} per share, using the Option Pricing Method backsolve. The implied total equity "
        f"value is {v['eq']}, assuming a {v['term']}-year term to liquidity, {v['vol']} volatility and a "
        f"{v['rate']} risk-free rate.\n\n"
        f"The OPM allocates {v['mkt']} per share to common stock on a marketable basis. Applying a "
        f"{v['dlom']} discount for lack of marketability ({v['method']} model) gives a concluded fair "
        f"market value of {v['fmv']} per common share.\n\n"
        f"Computation reference: ledger entry {v['hash']}."
    )


def _allowed_numbers(f: dict) -> set[str]:
    allowed = set(ALLOWED_SMALL)
    for s in _fmt(f).values():
        allowed |= {m.strip("$%").replace(",", "") for m in NUM_RE.findall(s)}
    return allowed


def unsupported_numbers(text: str, f: dict) -> list[str]:
    allowed = _allowed_numbers(f)
    bad = []
    for m in NUM_RE.findall(text):
        key = m.strip("$%").replace(",", "")
        if key and key not in allowed:
            bad.append(m)
    return bad


def ai_memo(f: dict, model: str | None = None) -> tuple[str, str]:
    """Return (memo_text, source) where source is 'ai' or 'template:<reason>'."""
    base = template_memo(f)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return base, "template:no-api-key"
    try:
        import anthropic  # optional dependency
    except ImportError:
        return base, "template:anthropic-not-installed"
    client = anthropic.Anthropic()
    prompt = (
        "Rewrite this 409A summary for a startup founder who is not a finance expert. Keep it under 200 "
        "words, keep every figure exactly as written, and do not add any new numbers, dates or "
        "percentages.\n\n" + base
    )
    msg = client.messages.create(
        model=model or os.environ.get("CAPSTACK_MODEL", "claude-sonnet-5-5"),
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    bad = unsupported_numbers(text, f)
    if bad:
        return base, f"template:ai-draft-rejected (unsupported figures {bad})"
    return text, "ai"
