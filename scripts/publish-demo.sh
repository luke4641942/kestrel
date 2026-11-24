#!/usr/bin/env bash
# Build the public demo (a static, read-only copy of the dashboard on GitHub Pages) and,
# with --push, publish it. The site is the dashboard built with VITE_DEMO=1 plus a fresh
# snapshot of the API (`kestrel demo-export`, which refuses local paths and the terms in
# data/demo-banned.txt). Publishing replaces the gh-pages branch with a single new commit:
# old snapshots aren't kept, so the repository doesn't grow with each refresh.
#
#   scripts/publish-demo.sh          # build into build/demo, to look at first
#   scripts/publish-demo.sh --push   # build and publish to gh-pages
set -euo pipefail
cd "$(dirname "$0")/.."

site=build/demo
.venv/bin/kestrel demo-export build/demo-data
(cd frontend && npm run build:demo)
cp -R build/demo-data "$site/demo"
touch "$site/.nojekyll"  # serve files as they are (no Jekyll processing)
echo "Built $site ($(du -sh "$site" | cut -f1))"

if [[ "${1:-}" == "--push" ]]; then
  remote=$(git remote get-url origin)
  commit=$(git rev-parse --short HEAD)
  tmp=$(mktemp -d)
  cp -R "$site/." "$tmp"
  git -C "$tmp" init -q -b gh-pages
  git -C "$tmp" add -A
  git -C "$tmp" commit -q -m "Demo from $commit"
  git -C "$tmp" push -q --force "$remote" gh-pages
  rm -rf "$tmp"
  echo "Published to gh-pages (from $commit)"
else
  echo "Look at it: (cd build && python3 -m http.server 8765) then open"
  echo "  http://localhost:8765/demo/index.html (asset paths assume /kestrel/; use --push to publish)"
fi
