# codex-context-bar

A **Codex plugin** that reports this session's context window fill, tokens
in/out per turn, cached tokens, and estimated cost — read from the session's own
rollout file, so it costs nothing but the read.

```
☂ Showers 57%  148k/258k  █  turn +3.97M in / 21k out  (47 calls)  session Σ 3.97M/21k  est $0.88  gpt-6-luna
```

| Part | What it shows |
|---|---|
| `☂ Showers 57%` | Weather for how full the window is: ☀ Clear <25%, ☁ Cloudy 25–49%, ☂ Showers 50–74%, ☇ Storm 75–89%, ↯ Compact soon ≥90% |
| `148k/258k` | The last model call's context tokens over the session's window |
| `█` | Sparkline of the last 12 turns (only when there is more than one) |
| `turn +… in / … out` | This turn's own input side and output, summed over every model call in it |
| `(47 calls)` | How many model calls that turn took — a turn with tool calls is still one turn |
| `session Σ` | The thread's totals so far |
| `est $0.88` | Estimated cost from the rates you give it |

## Run it

```bash
cd plugins/codex-context-bar
python3 scripts/context_bar.py                          # newest session
python3 scripts/context_bar.py --ledger                 # per-turn rows
python3 scripts/context_bar.py --prices gpt-6=1.25/10/0.125
python3 scripts/context_bar.py --watch 5                # redraw every 5s
python3 scripts/context_bar.py --session 01a11995       # a specific session
python3 scripts/context_bar.py --json
```

Python 3 only, standard library only, no network.

## Where the numbers come from

Codex writes every session to a rollout file under
`~/.codex/sessions/<year>/<month>/<day>/rollout-*.jsonl`. Three line types carry
everything needed:

| Line | What it holds |
|---|---|
| `session_meta` | once at the top: cwd, cli version, `model_context_window` when the host sets one |
| `turn_context` | the model and effort for the session's turns |
| `token_usage_record` | one per model call: `usage.input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens`, plus `turn_token_usage` and `thread_token_usage` |

The window fill is the last record's `usage.input_tokens`. A turn's `in` is its
`turn_token_usage.input_tokens`.

## Rates

Nothing is invented. Either pass `--prices in/out/cached`, or keep a table at
`~/.codex/context-bar.json`:

```json
{
  "prices": { "gpt-6": [1.25, 10, 0.125] },
  "windows": { "gpt-6": 258400 }
}
```

`prices` is USD per million tokens; `windows` overrides the context-window
fallback for models the rollout does not name. With no rates, the bar reports
tokens only.

## Install

```bash
codex plugin marketplace add /Users/rendifebrian/Projects/codex-mods
codex plugin add codex-context-bar@rendifebrian-codex-mods
```

Then in the ChatGPT desktop app or the Codex CLI, ask for the context bar (or
run `context-bar`) and the skill loads. `codex plugin list` shows it as
`installed, enabled`.
