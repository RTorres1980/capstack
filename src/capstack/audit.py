"""Tamper-evident audit ledger.

Every capstack computation appends one JSON line recording: the operation, a
SHA-256 fingerprint of each input file, the parameters, the outputs, the
package version, and the hash of the previous entry. Changing any past entry,
reordering entries, or deleting one breaks the chain, and `capstack audit
verify` pinpoints where. `capstack audit replay` re-runs an entry against the
original inputs and confirms the outputs are reproduced.

The goal is the same property an auditor wants from a valuation workpaper:
any number in a deliverable can be traced to exact inputs and reproduced.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

GENESIS = "0" * 64


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _entry_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(_canonical(body).encode()).hexdigest()


def read(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def append(path: str | Path, op: str, inputs: dict[str, str], params: dict, outputs: dict) -> dict:
    entries = read(path)
    prev = entries[-1]["hash"] if entries else GENESIS
    entry = {
        "seq": len(entries) + 1,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "op": op,
        "version": __version__,
        "inputs": {name: {"path": str(fp), "sha256": sha256_file(fp)} for name, fp in inputs.items()},
        "params": params,
        "outputs": outputs,
        "prev": prev,
    }
    entry["hash"] = _entry_hash(entry)
    with Path(path).open("a") as f:
        f.write(_canonical(entry) + "\n")
    return entry


def verify(path: str | Path, check_inputs: bool = False) -> list[str]:
    problems: list[str] = []
    prev = GENESIS
    for i, e in enumerate(read(path), start=1):
        if e.get("seq") != i:
            problems.append(f"entry {i}: sequence number {e.get('seq')} out of order")
        if e.get("prev") != prev:
            problems.append(f"entry {i}: chain broken (prev hash does not match entry {i - 1})")
        if _entry_hash(e) != e.get("hash"):
            problems.append(f"entry {i}: contents altered after it was written")
        if check_inputs:
            for name, meta in e.get("inputs", {}).items():
                p = Path(meta["path"])
                if not p.exists():
                    problems.append(f"entry {i}: input {name} ({p}) is missing")
                elif sha256_file(p) != meta["sha256"]:
                    problems.append(f"entry {i}: input {name} ({p}) changed since the run")
        prev = e.get("hash")
    return problems
