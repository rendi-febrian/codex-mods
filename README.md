# rendifebrian-codex-mods

A Codex plugin marketplace.

| Plugin | What it does |
|---|---|
| [`codex-context-bar`](./plugins/codex-context-bar) | Context bar for a Codex session: window fill, tokens in/out per turn, cached tokens, and estimated cost — read from the session's own rollout, so it costs nothing but the read. |

## Install

```bash
codex plugin marketplace add /path/to/codex-mods
codex plugin add codex-context-bar@rendifebrian-codex-mods
```

Verify:

```bash
codex plugin list      # → codex-context-bar@rendifebrian-codex-mods  installed, enabled
```

Then, in the ChatGPT desktop app's Codex pane or the Codex CLI, ask for the
context bar — the skill loads on that request.

From a git host, the same two commands take `owner/repo`:

```bash
codex plugin marketplace add rendi-febrian/codex-mods
codex plugin add codex-context-bar@rendifebrian-codex-mods
```

## Layout

```
.agents/plugins/marketplace.json                        the catalog
plugins/codex-context-bar/                              one plugin
    .codex-plugin/plugin.json                           its manifest
    skills/context-bar/SKILL.md                         what the model reads
    scripts/context_bar.py                              the readout
    scripts/selftest.py                                 checks it on synthetic data
```

To publish a second plugin, add a folder under `plugins/` and an entry to the
`plugins` array with `{"source": {"source": "local", "path": "./plugins/<folder>"}}`
whose `name` matches that folder's `.codex-plugin/plugin.json`.

## Why a plugin and not a status line

Codex has no status-line hook, so a readout has to come from somewhere the model
can run. A plugin's skill is that: Codex loads `SKILL.md` when the request
matches, and the script inside reads the session rollout that Codex already
writes. Nothing is drawn by the host, and no model call is spent on the numbers.
