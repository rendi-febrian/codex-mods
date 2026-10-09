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
  context_bar.py --prices gpt-6-luna=0.1/0.5/0.01/0.125
                                 rates per million tokens: in/out/cached[/cacheWrite]
  context_bar.py --config none   ignore ~/.codex/context-bar.json
  context_bar.py --window N      override the context window
  context_bar.py --json          machine-readable
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone

SESSIONS_ROOT = os.path.expanduser("~/.codex/sessions")
CONFIG_PATH = os.path.expanduser("~/.codex/context-bar.json")
MODELS_CACHE = os.path.expanduser("~/.codex/models_cache.json")

# Context windows, for sessions whose rollout does not name one and whose model
# is not in Codex's own catalog. ~/.codex/context-bar.json can override either.
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
    current: str | None = None
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"token_usage_record"' in line:
                try:
                    payload = json.loads(line)["payload"]
                except (ValueError, KeyError):
                    continue
                # A session can switch models, and `usage` does not name the
                # model, so remember which one was in force for this call.
                payload["_model"] = current
                records.append(payload)
            elif '"session_meta"' in line and not meta:
                try:
                    meta = json.loads(line)["payload"]
                except (ValueError, KeyError):
                    continue
            elif '"turn_context"' in line:
                try:
                    ctx = json.loads(line)["payload"]
                except (ValueError, KeyError):
                    continue
                if not turn_context:
                    turn_context = ctx
                if ctx.get("model"):
                    current = ctx["model"]

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
                "model": (calls[-1].get("_model") or ""),
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
    """The most specific match wins; 'default' only when nothing else does.

    Specificity is the length of the matching key, not its position in the
    file: with both `gpt-6` and `gpt-6-luna` present, `gpt-6-luna` must win
    whatever order they were written in.
    """
    if not table:
        return None
    hit = None
    for key, value in table.items():
        if key != "default" and model and key.lower() in model.lower():
            if hit is None or len(key) > len(hit):
                hit = key
    return table[hit] if hit else table.get("default")


def model_catalog() -> dict:
    """{slug: {window, raw_window, fraction}} from Codex's own model cache.

    Codex writes ~/.codex/models_cache.json; `context_window` is the model's
    window and `effective_context_window_percent` is how much of it a session
    actually gets (95 for the GPT-6 family: 272000 x 0.95 = 258400). Reading it
    means a new model is right without editing this script.
    """
    try:
        with open(MODELS_CACHE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    out = {}
    for m in data.get("models") or []:
        slug, window = m.get("slug"), m.get("context_window")
        if not slug or not window:
            continue
        fraction = m.get("effective_context_window_percent") or 0
        out[slug] = {
            "window": int(window * (fraction / 100)) if fraction else int(window),
            "raw_window": int(window),
            "fraction": fraction,
        }
    return out


# A request past the threshold bills at these multipliers for the whole
# request. They are OpenAI's published rule, so they are defaults here rather
# than something the user has to configure; ~/.codex/context-bar.json can
# still override any of them.
LONG_CONTEXT_DEFAULTS = {
    "threshold_tokens": 272000,
    "input_multiplier": 2.0,
    "cached_multiplier": 2.0,
    "output_multiplier": 1.5,
}


def long_context(config: dict) -> dict:
    """The multiplier a request past the threshold is billed at."""
    lc = {**LONG_CONTEXT_DEFAULTS, **(config.get("long_context") or {})}
    return {
        "threshold": int(lc.get("threshold_tokens") or 0),
        "in_mult": float(lc.get("input_multiplier") or 1.0),
        "cached_mult": float(lc.get("cached_multiplier") or 1.0),
        "out_mult": float(lc.get("output_multiplier") or 1.0),
    }


def estimate(turns: list[dict], rates, config: dict | None = None) -> float | None:
    """Cost of the turns, with the long-context multiplier where it applies.

    The multiplier is judged per request, and a turn's window is its last
    call's input side, so that is what the threshold is measured against.

    `rates` may be a single list of rates or the whole table: with a table,
    each turn is billed at the rate of the model that made it, so a session
    that switched models mid-way is priced per call rather than all at one
    model's rate. Returns None only when nothing at all matched.
    """
    table = rates if isinstance(rates, dict) else None
    single = None if table else rates
    if not table and not single:
        return None
    lc = long_context(config or {})
    total = 0.0
    priced = False
    for t in turns:
        r = rates_for(t.get("model"), table) if table else single
        if not r:
            continue
        priced = True
        in_rate, out_rate, cached_rate = (list(r) + [0, 0, 0])[:3]
        mult_in = mult_cached = mult_out = 1.0
        if lc["threshold"] and t["context_tokens"] > lc["threshold"]:
            mult_in, mult_cached, mult_out = lc["in_mult"], lc["cached_mult"], lc["out_mult"]
        uncached = max(0, t["input"] - t["cached"])
        total += uncached / 1e6 * in_rate * mult_in
        total += t["cached"] / 1e6 * cached_rate * mult_cached
        total += t["output"] / 1e6 * out_rate * mult_out
    return total if priced else None


def windows_table(config: dict) -> dict:
    """Windows by model: this script's table, then Codex's catalog, then config.

    Precedence is deliberate — the script's table is the fallback for a machine
    with no catalog, Codex's own cache is authoritative when it has the model,
    and the user's config overrides both.
    """
    table = dict(WINDOWS)
    for slug, info in model_catalog().items():
        table[slug] = info["window"]
    table.update({k: int(v) for k, v in (config.get("windows") or {}).items()})
    return table


def window_for(model, config: dict, meta: dict) -> int:
    if meta.get("model_context_window"):
        return int(meta["model_context_window"])
    table = windows_table(config)
    if model and model in table:
        return int(table[model])
    hit = None
    for key, value in table.items():
        if key != "default" and model and key.lower() in model.lower():
            if hit is None or len(key) > len(hit):
                hit = key
    if hit:
        return int(table[hit])
    return int(table.get("default", 0))


def load_config(path: str | None = None) -> dict:
    target = path or CONFIG_PATH
    if target in ("", "none"):
        return {}
    try:
        with open(target, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def parse_prices(spec: str | None, config: dict) -> dict:
    """Rates by model key. `--prices` wins over the config file for its models.

    The spec goes in first so it is the first key the matcher sees: a later
    duplicate cannot shadow an explicit command-line rate.
    """
    table: dict[str, list[float]] = {}
    for part in (spec or "").split(","):
        if "=" not in part:
            continue
        name, nums = part.split("=", 1)
        table[name.strip()] = [float(x) for x in nums.replace("/", " ").split()]
    for key, value in (config.get("prices") or {}).items():
        table.setdefault(key, [float(x) for x in value])
    return table


# ---------- output ----------------------------------------------------------


def band_line(session: dict, turns: list[dict], rates, config: dict) -> str:
    window = session.get("window") or 0
    last = turns[-1] if turns else None
    context = last["context_tokens"] if last else 0
    percent = (context / window * 100) if window else 0
    icon, word, _ = weather_for(percent)
    sent = sum(t["input"] for t in turns)
    got = sum(t["output"] for t in turns)
    est = estimate(turns, rates, config)
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


def ledger_lines(turns: list[dict], rates, config: dict) -> list[str]:
    out = []
    for i, t in enumerate(turns[-SPARK_TURNS:], 1):
        row = (
            f" {i:2}  ctx {compact(t['context_tokens']):>6}"
            f"  in {compact(t['input']):>7}  out {compact(t['output']):>5}"
            f"  cache ↺{compact(t['cached']):>7}  calls {t['calls']:>2}"
        )
        if rates:
            row += f"  {money(estimate([t], rates, config))}"
        out.append(row)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Context bar for Codex sessions")
    parser.add_argument("--session", default="auto", help="session id, rollout path, or 'auto'")
    parser.add_argument("--ledger", action="store_true", help="print the per-turn ledger")
    parser.add_argument("--prices", help="rates per Mtok, e.g. gpt-6=1.25/10/0.125")
    parser.add_argument("--window", type=int, help="override the context window size")
    parser.add_argument(
        "--config",
        default=CONFIG_PATH,
        help="rate table to read (default ~/.codex/context-bar.json; 'none' for built-ins only)",
    )
    parser.add_argument(
        "--long-threshold",
        type=int,
        help="override the long-context threshold in tokens (0 disables it)",
    )
    parser.add_argument("--card", action="store_true", help="write the inline card and print its reference")
    parser.add_argument("--thread", help="thread id for the card's folder (default: the rollout's own id)")
    parser.add_argument("--card-out", help="where to write the card (default: the thread's visualizations dir, else cwd)")
    parser.add_argument("--watch", type=float, metavar="SECONDS", help="redraw every N seconds")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    path = pick_rollout(args.session)
    if not path:
        print("no Codex session found under ~/.codex/sessions", file=sys.stderr)
        return 1

    session = read_session(path)
    turns = turns_of(session["records"])
    config = load_config(args.config)
    if args.long_threshold is not None:
        config["long_context"] = {
            **(config.get("long_context") or {}),
            "threshold_tokens": args.long_threshold,
        }
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
                    "estimated_usd": estimate(turns, rates, config),
                    "rates": rates,
                    "long_context": long_context(config),
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
        print(band_line(sess2, turns2, rates, config))
        if args.ledger:
            print()
            for row in ledger_lines(turns2, rates, config):
                print(row)
        if not rates:
            print("(no price rates set — pass --prices or write ~/.codex/context-bar.json)")

    if args.card:
        import hashlib

        stamp = time.strftime("%H%M%S")
        try:
            out = args.card_out or card_path(session, args.thread, stamp)
        except ValueError as err:
            print(f"cannot place the card: {err}", file=sys.stderr)
            return 2
        os.makedirs(os.path.dirname(out), exist_ok=True)
        html = card_html(session, turns, rates, config)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(html)
        # Codex renders this line as an inline card. The host keeps only the
        # basename and rebuilds the directory from the thread id, so the file
        # must sit in the thread's own visualizations folder (card_path).
        print(f'::codex-inline-vis{{file="{out}"}}')
        print(f"# wrote {out} ({len(html)} bytes, sha256 {hashlib.sha256(html.encode()).hexdigest()[:12]})")
        return 0

    draw()

    if args.watch:
        try:
            while True:
                time.sleep(max(1.0, args.watch))
                if not args.json:
                    print("\033[2J\033[H", end="")  # clear the screen, home the cursor
                draw()
        except KeyboardInterrupt:
            print()
    return 0




def card_path(session: dict, thread_id: str | None, stamp: str) -> str:
    """Where the host will actually read the card from.

    The desktop app does not use the path given — it keeps only the file's
    BASENAME and rebuilds the directory itself:

        join(codexHome, "visualizations", ...<YYYY/MM/DD>.split("/"), <threadId>)

    so the file has to exist at exactly
    `~/.codex/visualizations/<Y>/<M>/<D>/<threadId>/<name>.html`, with a name
    matching `^[a-z0-9]+(?:-[a-z0-9]+)*\\.html$`. Anything else — a nested extra
    folder, a name with underscores, a guessed thread id — makes the host
    reject the reference and fall back to showing it as raw text.
    """
    home = os.path.expanduser("~/.codex")
    tid = thread_id or session["meta"].get("id") or session["meta"].get("session_id")
    started = session["meta"].get("timestamp") or ""
    try:
        day = started[:10] if started else ""
        if not day or len(day) != 10:
            raise ValueError(day)
        y, m, d = day.split("-")
    except (ValueError, AttributeError):
        # Fall back to the thread id's own timestamp: it is a UUIDv7, whose
        # first 48 bits are milliseconds since the epoch.
        import datetime as _dt

        try:
            ms = int((tid or "").replace("-", "")[:12], 16)
            at = _dt.datetime.fromtimestamp(ms / 1000)
            y, m, d = f"{at:%Y}", f"{at:%m}", f"{at:%d}"
        except (ValueError, TypeError):
            y, m, d = time.strftime("%Y/%m/%d").split("/")
    if not tid:
        raise ValueError("no thread id in the session rollout")
    return os.path.join(home, "visualizations", y, m, d, tid, f"context-bar-{stamp}.html")


# ---------- the inline card -------------------------------------------------


def card_html(session: dict, turns: list[dict], rates, config: dict) -> str:
    """A self-contained HTML fragment Codex renders inline in the conversation.

    It is a FRAGMENT: no doctype/html/head/body, no fetch, no external assets —
    that is what the desktop's inline renderer accepts. The palette comes from
    CSS variables the host sets, with fallbacks so the file also opens in a
    browser.
    """
    window = session.get("window") or 0
    last = turns[-1] if turns else None
    context = last["context_tokens"] if last else 0
    percent = (context / window * 100) if window else 0
    icon, word, _ = weather_for(percent)
    est = estimate(turns, rates, config)
    model = session.get("model") or "?"
    sent = sum(t["input"] for t in turns)
    got = sum(t["output"] for t in turns)
    cached = sum(t["cached"] for t in turns)
    max_ctx = max([t["context_tokens"] for t in turns] + [1])
    lc = long_context(config)

    def esc(v) -> str:
        return (
            str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        )

    # A bar per turn: how full the window was, and what the turn cost.
    per_turn = []
    show = turns[-SPARK_TURNS:]
    cost_peak = max([(estimate([t], rates, config) or 0) for t in show] + [1e-9])
    for i, t in enumerate(show, len(turns) - len(show) + 1):
        pct = (t["context_tokens"] / window * 100) if window else 0
        cost = estimate([t], rates, config)
        heat = (cost or 0) / cost_peak if rates else t["context_tokens"] / max_ctx
        rows = [
            f'<div class="row">',
            f'<span class="idx">{i}</span>',
            f'<span class="bar" title="{t["context_tokens"]} tokens"><i style="width:'
            f'{max(2, min(100, pct)):.1f}%;opacity:{0.35 + 0.65 * heat:.2f}"></i></span>',
            f'<span class="num">{compact(t["context_tokens"])}/{compact(window)}</span>',
            f'<span class="num dim">in {compact(t["input"])}</span>',
            f'<span class="num dim">out {compact(t["output"])}</span>',
            f'<span class="num dim">↺{compact(t["cached"])}</span>',
            f'<span class="num dim">{t["calls"]} call{"s" if t["calls"] != 1 else ""}</span>',
        ]
        if cost is not None:
            rows.append(f'<span class="cost">{money(cost)}</span>')
        rows.append("</div>")
        per_turn.append("".join(rows))

    tiles = [
        ("context", f'{compact(context)} / {compact(window)}', f"{percent:.1f}%"),
        ("session in", compact(sent), f"↺{compact(cached)} cached"),
        ("session out", compact(got), ""),
    ]
    if est is not None:
        tiles.append(("estimated cost", money(est), "list rates"))
    if lc["threshold"]:
        tiles.append(("long-context", f">{compact(lc['threshold'])}", f"{lc['in_mult']:g}x in / {lc['out_mult']:g}x out"))
    tiles.append(("model", model, f"{len(turns)} turn{'s' if len(turns) != 1 else ''}"))

    tile_html = "".join(
        f'<div class="tile"><span class="k">{esc(k)}</span>'
        f'<span class="v">{esc(v)}</span>'
        + (f'<span class="s">{esc(s)}</span>' if s else "")
        + "</div>"
        for k, v, s in tiles
    )

    legend = "estimated from " + (
        "rates you set" if rates else "no rates set — tokens only"
    ) + " · bars show each turn's context, darkened by its cost"

    return f"""<div id="codex-context-bar" class="ccb">
<style>
  .ccb {{ --ccb-good:#3fb950; --ccb-mid:#d29922; --ccb-bad:#f85149; --ccb-line:var(--border, #2a2f36);
         font: 13px/1.45 ui-sans-serif, -apple-system, system-ui, sans-serif;
         color: var(--foreground, #e6e6e6); display:flex; flex-direction:column; gap:10px; }}
  .ccb .head {{ display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }}
  .ccb .wx {{ font-weight:600; }}
  .ccb .pct {{ font-variant-numeric: tabular-nums; opacity:.85; }}
  .ccb .spark {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; opacity:.75; letter-spacing:1px; }}
  .ccb .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:8px; }}
  .ccb .tile {{ border:1px solid var(--ccb-line); border-radius:8px; padding:7px 9px;
                display:flex; flex-direction:column; gap:2px; }}
  .ccb .k {{ font-size:10px; letter-spacing:.08em; text-transform:uppercase; opacity:.55; }}
  .ccb .v {{ font-size:15px; font-variant-numeric: tabular-nums; }}
  .ccb .s {{ font-size:11px; opacity:.55; }}
  .ccb .rows {{ display:flex; flex-direction:column; gap:3px; }}
  .ccb .row {{ display:grid; grid-template-columns: 1.6em minmax(60px,2.4fr) 6.5em 5em 4.5em 5em 4.5em 4.5em;
               align-items:center; gap:6px; font-size:12px; }}
  .ccb .row:hover {{ background:color-mix(in srgb, currentColor 6%, transparent); border-radius:4px; }}
  .ccb .idx {{ opacity:.45; text-align:right; font-variant-numeric: tabular-nums; }}
  .ccb .bar {{ height:8px; border-radius:4px; background:color-mix(in srgb, currentColor 12%, transparent);
               overflow:hidden; }}
  .ccb .bar i {{ display:block; height:100%; background:currentColor; }}
  .ccb .num {{ font-variant-numeric: tabular-nums; }}
  .ccb .dim {{ opacity:.6; }}
  .ccb .cost {{ font-variant-numeric: tabular-nums; text-align:right; }}
  .ccb .legend {{ font-size:11px; opacity:.5; }}
</style>
  <div class="head">
    <span class="wx">{esc(icon)} {esc(word)}</span>
    <span class="pct">{percent:.1f}%</span>
    <span class="num">{compact(context)} / {compact(window)}</span>
    <span class="spark">{esc(sparkline(turns))}</span>
  </div>
  <div class="tiles">{tile_html}</div>
  <div class="rows">{"".join(per_turn)}</div>
  <div class="legend">{esc(legend)}</div>
</div>
"""


if __name__ == "__main__":
    sys.exit(main())
