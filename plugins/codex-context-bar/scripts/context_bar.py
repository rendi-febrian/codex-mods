#!/usr/bin/env python3
"""Context bar for Codex — context length, tokens in/out, estimated cost.

Reads a Codex session rollout (JSONL) and prints a one-line bar plus, on
request, a per-turn ledger. Standard library only; no network.

Where the numbers come from
---------------------------
A Codex session is a rollout file: one JSON object per line. Two line types
carry everything needed:

  session_meta        once at the top: model, model_context_window, cwd
  token_usage_record  one per model call, with the API's own figures:
                        usage.input_tokens        the request's context size
                        usage.cached_input_tokens the part served from cache
                        usage.output_tokens       tokens the model produced
                        usage.reasoning_output_tokens
                        turn_token_usage          the whole turn's totals
                        thread_token_usage        the thread's totals so far

The context window's fill is the LAST record's `usage.input_tokens`: that is
the request the model answered, so it is what the window held. A turn's own
input side (what it added across every call in the turn) is
`turn_token_usage.input_tokens`.

Usage
-----
  context_bar.py                 latest session, one-line bar
  context_bar.py --ledger        also the last 12 turns
  context_bar.py --session <id>  a specific session id (prefix is enough)
  context_bar.py --prices gpt-6=1.25/10/0.125
                                 rates per million tokens: in/out/cached
  context_bar.py --json          machine-readable
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

SESSIONS_ROOT = os.path.expanduser("~/.codex/sessions")
CONFIG_PATH = os.path.expanduser("~/.codex/context-bar.json")

# Context windows, for sessions whose rollout does not name one. Override a
# model here or in ~/.codex/context-bar.json ({"windows": {"<model>": 400000}}).
WINDOWS = {
    "gpt-6": 258400,
    "gpt-5": 258400,
    "default": 258400,
}

WEATHER = [
    (90, "↯", "Compact soon", "red"),
    (75, "☇", "Storm", "magenta"),
    (50, "☂", "Showers", "blue"),
    (25, "☁", "Cloudy", "cyan"),
    (0, "☀", "Clear", "yellow"),
]
SPARK = "▁▂▃▄▅▆▇█"
SPARK_TURNS = 12


# ---------- finding the session --------------------------------------------


def all_rollouts() -> list[str]:
    return sorted(
        glob.glob(os.path.join(SESSIONS_ROOT, "*", "*", "*", "rollout-*.jsonl")),
        key=os.path.getmtime,
    )


def pick_rollout(session: str | None) -> str | None:
    files = all_rollouts()
    if not files:
        return None
    if not session or session == "auto":
        return files[-1]
    if os.path.isfile(session):
        return session
    matches = [f for f in files if session in os.path.basename(f)]
    return matches[-1] if matches else None


# ---------- reading it ------------------------------------------------------


def read_session(path: str) -> dict:
    meta: dict = {}
    records: list[dict] = []
    turn_context: dict = {}
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"token_usage_record"' in line:
                try:
                    records.append(json.loads(line)["payload"])
                except (ValueError, KeyError):
                    continue
            elif '"session_meta"' in line and not meta:
                try:
                    meta = json.loads(line)["payload"]
                except (ValueError, KeyError):
                    continue
            elif '"turn_context"' in line and not turn_context:
                try:
                    turn_context = json.loads(line)["payload"]
                except (ValueError, KeyError):
                    continue

    # The model lives on the turn context; the window on the session meta.
    model = turn_context.get("model") or meta.get("model")
    window = meta.get("model_context_window") or WINDOWS.get(model) or 0
    return {
        "meta": meta,
        "turn_context": turn_context,
        "model": model,
        "window": window,
        "records": records,
        "path": path,
    }


def turns_of(records: list[dict]) -> list[dict]:
    """Group the model calls into turns, keeping each turn's own totals."""
    order: list[str] = []
    seen: dict[str, list[dict]] = {}
    for rec in records:
        tid = rec.get("turn_id") or rec.get("root_turn_id") or "?"
        if tid not in seen:
            seen[tid] = []
            order.append(tid)
        seen[tid].append(rec)

    turns = []
    for tid in order:
        calls = seen[tid]
        last = calls[-1]
        usage = last.get("usage") or {}
        total = last.get("turn_token_usage") or usage
        cached = sum((c.get("usage") or {}).get("cached_input_tokens") or 0 for c in calls)
        write = sum((c.get("usage") or {}).get("cache_write_input_tokens") or 0 for c in calls)
        turns.append(
            {
                "turn_id": tid,
                "calls": len(calls),
                # What the window held on the turn's last call.
                "context_tokens": usage.get("input_tokens") or 0,
                "cached": cached,
                # What the turn cost across all of its calls.
                "input": total.get("input_tokens") or 0,
                "output": total.get("output_tokens") or 0,
                "reasoning": total.get("reasoning_output_tokens") or 0,
                "cache_write": write or total.get("cache_write_input_tokens") or 0,
                "total": total.get("total_tokens") or 0,
                "at": last.get("timestamp") or "",
            }
        )
    return turns


# ---------- formatting ------------------------------------------------------


def compact(n) -> str:
    if n is None:
        return "—"
    n = int(n)
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        k = n / 1000
        return (f"{k:.1f}" if k < 10 else f"{round(k)}").replace(".0", "") + "k"
    return f"{n / 1_000_000:.2f}M"


def money(usd) -> str:
    if usd is None:
        return "—"
    if usd >= 100:
        return f"${usd:.0f}"
    if usd >= 1:
        return f"${usd:.2f}"
    return f"${usd:.4f}"


def weather_for(percent: float):
    for low, icon, word, color in WEATHER:
        if percent >= low:
            return icon, word, color
    return WEATHER[-1][1:]


def sparkline(turns: list[dict]) -> str:
    values = [t["context_tokens"] for t in turns[-SPARK_TURNS:]]
    if not values:
        return ""
    top = max(values) or 1
    return "".join(SPARK[min(len(SPARK) - 1, int(v / top * (len(SPARK) - 1)))] for v in values)


def rates_for(model: str | None, table: dict) -> list[float] | None:
    if not table:
        return None
    for key, value in table.items():
        if key == "default" or (model and key.lower() in model.lower()):
            return value
    return table.get("default")


def estimate(turns: list[dict], rates) -> float | None:
    if not rates:
        return None
    in_rate, out_rate, cached_rate = (list(rates) + [0, 0, 0])[:3]
    total = 0.0
    for t in turns:
        uncached = max(0, t["input"] - t["cached"])
        total += uncached / 1e6 * in_rate
        total += t["cached"] / 1e6 * cached_rate
        total += t["output"] / 1e6 * out_rate
    return total


def windows_table(config: dict) -> dict:
    table = dict(WINDOWS)
    table.update({k: int(v) for k, v in (config.get("windows") or {}).items()})
    return table


def window_for(model, config: dict, meta: dict) -> int:
    if meta.get("model_context_window"):
        return int(meta["model_context_window"])
    table = windows_table(config)
    for key, value in table.items():
        if key == "default" or (model and key.lower() in model.lower()):
            return int(value)
    return int(table.get("default", 0))


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def parse_prices(spec: str | None, config: dict) -> dict:
    table: dict[str, list[float]] = {}
    for key, value in (config.get("prices") or {}).items():
        table[key] = [float(x) for x in value]
    if spec:
        for part in spec.split(","):
            if "=" not in part:
                continue
            name, nums = part.split("=", 1)
            table[name.strip()] = [float(x) for x in nums.replace("/", " ").split()]
    return table


# ---------- output ----------------------------------------------------------


def band_line(session: dict, turns: list[dict], rates) -> str:
    window = session.get("window") or 0
    last = turns[-1] if turns else None
    context = last["context_tokens"] if last else 0
    percent = (context / window * 100) if window else 0
    icon, word, _ = weather_for(percent)
    sent = sum(t["input"] for t in turns)
    got = sum(t["output"] for t in turns)
    est = estimate(turns, rates)
    model = session.get("model") or "?"

    calls = sum(t["calls"] for t in turns)
    parts = [
        f"{icon} {word} {percent:.0f}%",
        f"  {compact(context)}/{compact(window)}",
        f"  {sparkline(turns)}",
        f"  turn +{compact(last['input'] if last else 0)} in / {compact(last['output'] if last else 0)} out",
        f"  ({calls} calls)",
        f"  session Σ {compact(sent)}/{compact(got)}",
    ]
    if est is not None:
        parts.append(f"  est {money(est)}")
    parts.append(f"  {model}")
    return "".join(parts)


def ledger_lines(turns: list[dict], rates) -> list[str]:
    out = []
    for i, t in enumerate(turns[-SPARK_TURNS:], 1):
        row = (
            f" {i:2}  ctx {compact(t['context_tokens']):>6}"
            f"  in {compact(t['input']):>7}  out {compact(t['output']):>5}"
            f"  cache ↺{compact(t['cached']):>7}  calls {t['calls']:>2}"
        )
        if rates:
            per = estimate([t], rates)
            row += f"  {money(per)}"
        out.append(row)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Context bar for Codex sessions")
    parser.add_argument("--session", default="auto", help="session id, rollout path, or 'auto'")
    parser.add_argument("--ledger", action="store_true", help="print the per-turn ledger")
    parser.add_argument("--prices", help="rates per Mtok, e.g. gpt-6=1.25/10/0.125")
    parser.add_argument("--window", type=int, help="override the context window size")
    parser.add_argument("--watch", type=float, metavar="SECONDS", help="redraw every N seconds")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    path = pick_rollout(args.session)
    if not path:
        print("no Codex session found under ~/.codex/sessions", file=sys.stderr)
        return 1

    session = read_session(path)
    turns = turns_of(session["records"])
    config = load_config()
    model = session.get("model")
    rates = rates_for(model, parse_prices(args.prices, config))
    session["window"] = args.window or window_for(model, config, session["meta"])

    window = session["window"]
    context = turns[-1]["context_tokens"] if turns else 0
    percent = round(context / window * 100, 1) if window else 0

    if args.json:
        print(
            json.dumps(
                {
                    "session": os.path.basename(path),
                    "model": model,
                    "cwd": session["meta"].get("cwd"),
                    "window": window,
                    "context_tokens": context,
                    "percent": percent,
                    "turns": turns,
                    "totals": {
                        "input": sum(t["input"] for t in turns),
                        "output": sum(t["output"] for t in turns),
                        "cached": sum(t["cached"] for t in turns),
                    },
                    "estimated_usd": estimate(turns, rates),
                    "rate_source": CONFIG_PATH if os.path.exists(CONFIG_PATH) else None,
                },
                indent=1,
            )
        )
        return 0

    def draw() -> None:
        path2 = pick_rollout(args.session)
        sess2 = read_session(path2) if path2 else session
        turns2 = turns_of(sess2["records"])
        sess2["window"] = args.window or window_for(sess2.get("model"), config, sess2["meta"])
        print(band_line(sess2, turns2, rates))
        if args.ledger:
            print()
            for row in ledger_lines(turns2, rates):
                print(row)
        if not rates:
            print("(no price rates set — pass --prices or write ~/.codex/context-bar.json)")

    draw()

    if args.watch:
        import time

        try:
            while True:
                time.sleep(max(1.0, args.watch))
                if not args.json:
                    print("\033[2J\033[H", end="")  # clear the screen, home the cursor
                draw()
        except KeyboardInterrupt:
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
