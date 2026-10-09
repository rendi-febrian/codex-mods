#!/usr/bin/env python3
"""Self-test for context_bar.py — builds a synthetic rollout and checks the numbers.

    python3 scripts/selftest.py

Exits non-zero on the first mismatch. No network, no dependency on your own
sessions, so it runs the same on any machine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "context_bar.py")

WINDOW = 200000


def line(**kwargs) -> str:
    return json.dumps(kwargs)


def build_rollout(path: str) -> None:
    """One session, two turns. Turn 1: two calls. Turn 2: three calls."""
    rows = [
        {"type": "session_meta", "payload": {"id": "test-session", "cwd": "/tmp/x", "model_context_window": WINDOW}},
        {"type": "turn_context", "payload": {"turn_id": "t1", "model": "gpt-6-luna"}},
        # Turn 1, call 1: window 10k, 8k of it cached.
        {
            "type": "token_usage_record",
            "payload": {
                "turn_id": "t1",
                "usage": {"input_tokens": 10000, "cached_input_tokens": 8000, "output_tokens": 100,
                          "reasoning_output_tokens": 0, "cache_write_input_tokens": 0, "total_tokens": 10100},
                "turn_token_usage": {"input_tokens": 10000, "cached_input_tokens": 8000, "output_tokens": 100,
                                     "cache_write_input_tokens": 0, "total_tokens": 10100},
            },
        },
        # Turn 1, call 2: window 12k; the turn's totals are now 22k in / 300 out.
        {
            "type": "token_usage_record",
            "payload": {
                "turn_id": "t1",
                "usage": {"input_tokens": 12000, "cached_input_tokens": 9000, "output_tokens": 200,
                          "reasoning_output_tokens": 0, "cache_write_input_tokens": 0, "total_tokens": 12200},
                "turn_token_usage": {"input_tokens": 22000, "cached_input_tokens": 17000, "output_tokens": 300,
                                     "cache_write_input_tokens": 0, "total_tokens": 22300},
            },
        },
        # Turn 2: three calls; its window ends at 50k, its totals are 90k in / 900 out.
        {
            "type": "token_usage_record",
            "payload": {
                "turn_id": "t2",
                "usage": {"input_tokens": 20000, "cached_input_tokens": 15000, "output_tokens": 200,
                          "reasoning_output_tokens": 0, "cache_write_input_tokens": 0, "total_tokens": 20200},
                "turn_token_usage": {"input_tokens": 20000, "cached_input_tokens": 15000, "output_tokens": 200},
            },
        },
        {
            "type": "token_usage_record",
            "payload": {
                "turn_id": "t2",
                "usage": {"input_tokens": 35000, "cached_input_tokens": 28000, "output_tokens": 300,
                          "reasoning_output_tokens": 0, "cache_write_input_tokens": 0, "total_tokens": 35300},
                "turn_token_usage": {"input_tokens": 55000, "cached_input_tokens": 43000, "output_tokens": 500},
            },
        },
        {
            "type": "token_usage_record",
            "payload": {
                "turn_id": "t2",
                "usage": {"input_tokens": 50000, "cached_input_tokens": 44000, "output_tokens": 400,
                          "reasoning_output_tokens": 0, "cache_write_input_tokens": 0, "total_tokens": 50400},
                "turn_token_usage": {"input_tokens": 90000, "cached_input_tokens": 87000, "output_tokens": 900},
            },
        },
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(line(type="ignored", payload={}) + "\n")
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def run(rollout: str, *args: str) -> dict:
    out = subprocess.run(
        [sys.executable, SCRIPT, "--session", rollout, "--config", "none", "--json", *args],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def main() -> int:
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        rollout = os.path.join(tmp, "rollout-test.jsonl")
        build_rollout(rollout)

        data = run(rollout)
        checks = [
            ("window", data["window"], WINDOW),
            ("context tokens (last call of turn 2)", data["context_tokens"], 50000),
            ("percent", data["percent"], 25.0),
            ("turn count", len(data["turns"]), 2),
            ("turn 1 calls", data["turns"][0]["calls"], 2),
            ("turn 1 input side", data["turns"][0]["input"], 22000),
            ("turn 1 output", data["turns"][0]["output"], 300),
            ("turn 1 cached (summed over its calls)", data["turns"][0]["cached"], 17000),
            ("turn 2 calls", data["turns"][1]["calls"], 3),
            ("turn 2 input side", data["turns"][1]["input"], 90000),
            ("turn 2 context (last call)", data["turns"][1]["context_tokens"], 50000),
            ("turn 2 cached (summed over its calls)", data["turns"][1]["cached"], 87000),
            ("session input total", data["totals"]["input"], 112000),
            ("session output total", data["totals"]["output"], 1200),
            ("no rates means no cost", data["estimated_usd"], None),
        ]
        for name, got, want in checks:
            if got != want:
                problems.append(f"{name}: got {got!r}, want {want!r}")

        # $1/Mtok in, $10/Mtok out, $0.10/Mtok cached.
        priced = run(rollout, "--prices", "gpt-6-luna=1/10/0.1")
        # 112000 in of which 104000 cached, 1200 out
        want = (112000 - 104000) / 1e6 * 1 + 104000 / 1e6 * 0.1 + 1200 / 1e6 * 10
        if priced["estimated_usd"] is None or abs(priced["estimated_usd"] - want) > 1e-9:
            problems.append(f"cost: got {priced['estimated_usd']!r}, want {want!r}")

        # A long-context request bills the whole turn at the multiplier. The
        # synthetic last call is 50000, so a 40000 threshold catches turn 2.
        lc = run(
            rollout,
            "--prices", "gpt-6-luna=1/10/0.1",
            "--long-threshold", "40000",
        )
        want_lc = (
            (22000 - 17000) / 1e6 * 1 + 17000 / 1e6 * 0.1 + 300 / 1e6 * 10  # turn 1: plain
            + (90000 - 87000) / 1e6 * 1 * 2 + 87000 / 1e6 * 0.1 * 2 + 900 / 1e6 * 10 * 1.5  # turn 2: 2x/1.5x
        )
        if abs((lc["estimated_usd"] or 0) - want_lc) > 1e-9:
            problems.append(f"long-context cost: got {lc['estimated_usd']!r}, want {want_lc!r}")
        if lc.get("long_context", {}).get("threshold") != 40000:
            problems.append(f"long_context.threshold: got {lc.get('long_context')!r}")

        # A window override beats every table.
        forced = run(rollout, "--window", "100000")
        if forced["percent"] != 50.0:
            problems.append(f"--window: got {forced['percent']!r}, want 50.0")

        # The bar prints one line and names the model.
        band = subprocess.run(
            [sys.executable, SCRIPT, "--session", rollout, "--config", "none",
             "--prices", "gpt-6-luna=1/10/0.1"],
            capture_output=True, text=True, check=True,
        ).stdout.strip().splitlines()
        if len(band) != 1:
            problems.append(f"bar printed {len(band)} lines, want 1")
        for token in ("Cloudy", "25%", "50k/200k", "gpt-6-luna"):
            if token not in band[0]:
                problems.append(f"bar is missing {token!r}: {band[0]}")

    if problems:
        print("FAIL")
        for p in problems:
            print(" -", p)
        return 1
    print("ok — every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
