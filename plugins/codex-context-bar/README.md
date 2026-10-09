# codex-context-bar

A **Codex plugin** that reports this session's context window fill, tokens
in/out per turn, cached tokens, and estimated cost — read from the session's own
rollout file, so it costs nothing but the read. Ships as a one-line bar, a
per-turn ledger, and an **inline card** in the conversation.

```
☂ Showers 71%  183k/258k  ▇█  turn +183k in / 130 out  (76 calls)  session Σ 9.94M/36k  est $0.1488  gpt-6-luna
```

| Part | What it shows |
|---|---|
| `☂ Showers 71%` | Weather for how full the window is: ☀ Clear <25%, ☁ Cloudy 25–49%, ☂ Showers 50–74%, ☇ Storm 75–89%, ↯ Compact soon ≥90% |
| `183k/258k` | The last model call's context tokens over the session's window |
| `▇█` | Sparkline of the last 12 turns (only when there is more than one) |
| `turn +… in / … out` | This turn's own input side and output, summed over every model call in it |
| `(76 calls)` | How many model calls that turn took — a turn with tool calls is still one turn |
| `session Σ` | The thread's totals so far |
| `est $0.1488` | Estimated cost from the configured rates |

## Run it

```bash
cd plugins/codex-context-bar
python3 scripts/context_bar.py                    # newest session, one line
python3 scripts/context_bar.py --ledger           # add the per-turn rows
python3 scripts/context_bar.py --card             # write the inline card
python3 scripts/context_bar.py --session 01a11995 # a specific session
python3 scripts/context_bar.py --json             # machine-readable
python3 scripts/context_bar.py --watch 5          # redraw every 5 seconds
```

Python 3 only, standard library only, no network.

## The inline card

`--card` writes a self-contained HTML **fragment** to the thread's
visualizations directory and prints one line:

```
::codex-inline-vis{file="/Users/…/context-bar-….html"}
```

Codex's desktop app renders that line as an inline card: a header with the
weather and window, tiles for context / session in / session out / cost / model,
and one row per turn with a bar, the turn's tokens, its call count, and its own
cost.

The host is strict about the shape — the fragment must have no `<!doctype>`,
`<html>`, `<head>`, `<body>`, `fetch`/XHR, or external resources, and the
reference must be the only thing on its line. `--card` produces exactly that,
which is why the skill tells the model to run it rather than hand-write HTML.
Open the file in a browser if you want a closer look; the host's own frame is
what styles it in the app.

## Where the numbers come from

Codex writes every session to a rollout file under
`~/.codex/sessions/<year>/<month>/<day>/rollout-*.jsonl`. Three line types carry
everything needed:

| Line | What it holds |
|---|---|
| `session_meta` | once at the top: cwd, cli version, `model_context_window` when the host sets one |
| `turn_context` | the model and reasoning effort for the session's turns |
| `token_usage_record` | one per model call: `usage.input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens`, plus `turn_token_usage` and `thread_token_usage` |

The window fill is the last record's `usage.input_tokens`. A turn's `in` is its
`turn_token_usage.input_tokens`.

The **window size** comes from Codex's own catalog, `~/.codex/models_cache.json`:
`context_window` × `effective_context_window_percent` (272000 × 95% = 258400 for
the GPT-6 family), so a new model is right without editing the script. A
`windows` entry in the config file overrides it.

## Rates

Nothing is invented. Either pass `--prices in/out/cached[/cacheWrite]`, or keep a
table at `~/.codex/context-bar.json`:

```json
{
  "prices": {
    "gpt-6-luna": [0.1, 0.5, 0.01, 0.125],
    "gpt-6-sol":  [2.0, 10.0, 0.2, 2.5]
  },
  "long_context": {
    "threshold_tokens": 272000,
    "input_multiplier": 2.0,
    "cached_multiplier": 2.0,
    "output_multiplier": 1.5
  }
}
```

USD per million tokens, matched as a substring of the model slug (most specific
first). `long_context` is OpenAI's published rule — a request past the threshold
bills the whole request at 2× input/cache and 1.5× output — and is the script's
built-in default; the entry above only makes it visible. With no rate matching
the model, the bar reports tokens only and says the cost needs rates.

## Tests

```bash
python3 scripts/selftest.py     # builds a synthetic session, checks every number
python3 scripts/build-archive.sh # package for the desktop app's uploader
```

`selftest.py` is hermetic: it runs with `--config none`, so your own rate table
cannot change the result. It checks the window, the percent, turn grouping, the
per-turn input side and summed cached reads, session totals, the cost
arithmetic, the long-context multiplier, the `--window` override, and that the
bar prints exactly one line.

## Install

```bash
codex plugin marketplace add /Users/rendifebrian/Projects/codex-mods
codex plugin add codex-context-bar@rendifebrian-codex-mods
```

Then ask for the context bar in a session and the skill loads; `codex plugin
list` shows it as `installed, enabled`.
