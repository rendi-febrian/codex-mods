#!/usr/bin/env bash
# Build the plugin archive the way the desktop app's uploader expects it.
#
# Two rules, both learned from the archives OpenAI ships in
# /Applications/ChatGPT.app/Contents/Resources/plugin-signatures/openai-bundled:
#
#   1. The plugin's files sit at the ARCHIVE ROOT — `.codex-plugin/plugin.json`
#      is a top-level entry. An archive that wraps the plugin in a folder
#      (`myplugin/.codex-plugin/plugin.json`) is rejected by the upload endpoint
#      with HTTP 400, which the dialog shows as "Couldn't add plugin."
#   2. No directory entries (`zip -D`), and an `integrity.json` listing every
#      file with its sha256, as the official bundles carry.
#
#   ./scripts/build-archive.sh [output.zip]
#
# Default output: ~/Downloads/codex-context-bar.zip
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$HOME/Downloads/codex-context-bar.zip}"

cd "$PLUGIN_DIR"
NAME="$(python3 -c "import json;print(json.load(open('.codex-plugin/plugin.json'))['name'])")"

# Every file the plugin ships, with the manifest first so the archive opens the
# way the official ones do. Stray files never make it in.
FILES=()
while IFS= read -r f; do FILES+=("$f"); done < <(
  find .codex-plugin skills scripts assets -type f \
    ! -name '.DS_Store' ! -name '*.pyc' 2>/dev/null | sort
)
for f in README.md LICENSE; do [ -f "$f" ] && FILES+=("$f"); done
[ -f .codex-plugin/plugin.json ] || { echo "missing .codex-plugin/plugin.json" >&2; exit 1; }

# integrity.json: {"files":[{"path","sha256"}]}, the shape the official bundles use.
python3 - "${FILES[@]}" <<'PY'
import hashlib, json, os, sys
files = sys.argv[1:]
rows = []
for f in sorted(files, key=lambda p: (p != "./.codex-plugin/plugin.json", p)):
    # `lstrip("./")` would eat the dot of `.codex-plugin`; normalise properly.
    rel = os.path.relpath(f, ".")
    with open(f, "rb") as fh:
        rows.append({"path": rel, "sha256": hashlib.sha256(fh.read()).hexdigest()})
with open("integrity.json", "w", encoding="utf-8") as fh:
    json.dump({"files": rows}, fh, separators=(",", ":"))
print(f"integrity.json: {len(rows)} file(s), first is {rows[0]['path']}")
PY

rm -f "$OUT"
zip -qrX -D "$OUT" "${FILES[@]}" integrity.json

echo
echo "wrote $OUT"
echo "entries (root-level manifest first, no wrapper directory):"
unzip -l "$OUT" | sed -n '1,10p'
echo
shasum -a 256 "$OUT"
