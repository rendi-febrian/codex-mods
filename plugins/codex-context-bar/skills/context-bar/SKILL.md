---
name: context-bar
description: Report this session's context window fill, token in/out per turn, cached tokens, and estimated cost as a one-line bar plus a per-turn ledger. Use when the user asks how full the context is, how many tokens a session or turn used, what it cost, or where the usage went — and whenever they run context-bar or ask for "context bar".
---

# Context bar

Print a readout of the current Codex session's context window, token usage and
estimated cost. Everything is computed from the session's own rollout file, so
there is no network call and no extra token spend beyond reading this skill.

## Run it

From this plugin's root (the directory holding `.codex-plugin/`):

```bash
python3 scripts/context_bar.py                 # newest session, one line
python3 scripts/context_bar.py --ledger        # add the per-turn ledger
python3 scripts/context_bar.py --prices gpt-6=1.25/10/0.125
python3 scripts/context_bar.py --watch 5        # redraw every 5 seconds
python3 scripts/context_bar.py --session <id>   # a specific session
python3 scripts/context_bar.py --json           # machine-readable
```

`--prices` is rates in USD per million tokens as `in/out/cached`. Without rates
the bar omits the cost and says so.

## What the numbers mean

- **Window fill** is the last model call's `usage.input_tokens` over the
  session's `model_context_window` — that request is what the window held. The
  script falls back to its own per-model table when the rollout names no
  window, and `--window N` overrides it.
- **A turn's `in`** is that turn's own input side across every model call in it
  (`turn_token_usage.input_tokens`), which is what the turn added; a turn with
  many tool calls is one turn, not many.
- **`cache ↺`** is cached input read; it is billed at the cached rate, which is
  why the cost line separates it.
- Data comes from `~/.codex/sessions/<year>/<month>/<day>/rollout-*.jsonl`
  (`token_usage_record`, `session_meta`, `turn_context` lines).

## Output

One line, in this shape:

```
☂ Showers 57%  148k/258k  █  turn +3.97M in / 21k out  (47 calls)  session Σ 3.97M/21k  est $0.88  gpt-6-luna
```

- Weather for how full the window is: `<25% ☀ Clear`, `25–49% ☁ Cloudy`,
  `50–74% ☂ Showers`, `75–89% ☇ Storm`, `≥90% ↯ Compact soon`.
- Sparkline of the last 12 turns when there is more than one.
- `est` is an estimate from the rates given; say it is an estimate.

With `--ledger`, print the per-turn rows as the script prints them. Keep numbers
compact (`148k`, `$0.88`) and the bar to one line.

## Rates

Rates are not invented: either the user passes `--prices`, or a rate table sits
in `~/.codex/context-bar.json`:

```json
{
  "prices": { "gpt-6": [1.25, 10, 0.125] },
  "windows": { "gpt-6": 258400 }
}
```

If no rates are configured, report tokens only and say the cost needs rates.
Never state a dollar figure you did not compute from rates you were given.
