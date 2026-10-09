---
name: context-bar
description: Report this session's context window fill, token in/out per turn, cached tokens, and estimated cost — as a one-line bar, a per-turn ledger, or an inline card in the conversation. Use when the user asks how full the context is, how many tokens a session or turn used, what it cost, where the usage went, or asks for the "context bar".
---

# Context bar

Report the current Codex session's context window, token usage and estimated
cost. Everything is computed from the session's own rollout file, so the only
cost is this read — no model call is spent producing the numbers.

## Run it

From this plugin's root (the directory holding `.codex-plugin/`):

```bash
python3 scripts/context_bar.py                 # one line, newest session
python3 scripts/context_bar.py --ledger        # add the per-turn ledger
python3 scripts/context_bar.py --card          # inline card in the conversation
python3 scripts/context_bar.py --session <id>  # a specific session
python3 scripts/context_bar.py --json          # machine-readable
python3 scripts/context_bar.py --watch 5        # redraw every 5 seconds
```

## The card (prefer this when the user wants to *see* it)

`--card` writes an HTML fragment to the thread's visualizations directory and
prints exactly one line:

```
::codex-inline-vis{file="/Users/…/context-bar-….html"}
```

Codex's desktop app renders that line as an inline card in the conversation, so
the numbers get a panel with tiles, bars per turn, and the cost column instead
of a wall of text. Three rules make it work:

- Emit the reference line **by itself**, nothing else on that line, in your
  final response for the turn — not in commentary or progress updates.
- The file must be an HTML **fragment**: no `<!doctype>`, `<html>`, `<head>` or
  `<body>`, no `fetch`/XHR, no external resources. `--card` already produces
  that shape; do not hand-write a different one.
- Do not call the card an artifact, attachment, or download, and do not also
  paste a table of the same numbers. One short sentence plus the line.

If the reference line is not the whole line, or the file is not a fragment, the
host falls back to showing the raw text.

## What the numbers mean

- **Window fill** is the last model call's `usage.input_tokens` over the
  session's window — that request is what the window held. The window comes from
  Codex's own catalog (`~/.codex/models_cache.json`: `context_window` ×
  `effective_context_window_percent`, so 272000 × 95% = 258400 for the GPT-6
  family); `--window N` overrides it.
- **A turn's `in`** is that turn's own input side across every model call in it
  (`turn_token_usage.input_tokens`): a turn with many tool calls is one turn,
  not many.
- **`cache ↺`** is cached input read, billed at the cached rate — which is why
  the cost line separates it.
- Data comes from `~/.codex/sessions/<year>/<month>/<day>/rollout-*.jsonl`
  (`token_usage_record`, `session_meta`, `turn_context` lines).

## Output

One line, in this shape:

```
☂ Showers 71%  183k/258k  ▇█  turn +183k in / 130 out  (76 calls)  session Σ 9.94M/36k  est $0.1488  gpt-6-luna
```

- Weather for how full the window is: `<25% ☀ Clear`, `25–49% ☁ Cloudy`,
  `50–74% ☂ Showers`, `75–89% ☇ Storm`, `≥90% ↯ Compact soon`.
- Sparkline of the last 12 turns, shown when there is more than one.
- `est` is an estimate from the rates configured, never a guessed price.

With `--ledger`, the per-turn rows print as the script prints them. Keep numbers
compact (`148k`, `$0.88`).

## Rates

Rates are not invented. `~/.codex/context-bar.json` holds them, and
`--prices` overrides the file for a run:

```json
{
  "prices": { "gpt-6-luna": [0.1, 0.5, 0.01, 0.125] }
}
```

`[input, output, cached_read, cache_write]` in USD per million tokens; the key
is matched as a substring of the model slug, most specific match first. If no
rate matches the session's model, report tokens only and say the cost needs
rates — never state a dollar figure you did not compute.

OpenAI bills a request whose prompt passes the threshold (272,000 tokens for the
GPT-6 family) at 2× input and cache rates and 1.5× output for the whole
request; the script applies that by default. Override it under `long_context`:
`threshold_tokens`, `input_multiplier`, `cached_multiplier`, `output_multiplier`.
