#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

case "${1:-generate}" in
  generate)
    python3 src/main.py "${@:2}"
    ;;
  no-ai)
    python3 src/main.py --no-ai "${@:2}"
    ;;
  serve)
    python3 src/main.py "${@:2}"
    if command -v jekyll >/dev/null 2>&1; then
      jekyll serve --source docs --host 127.0.0.1 --port "${PORT:-4000}"
    elif command -v bundle >/dev/null 2>&1 && [ -f Gemfile ]; then
      bundle exec jekyll serve --source docs --host 127.0.0.1 --port "${PORT:-4000}"
    else
      echo "Jekyll is not installed. The digest was generated in docs/_posts/."
      echo "Install Jekyll or push to GitHub Pages to view the site."
    fi
    ;;
  *)
    echo "Usage:"
    echo "  ./run.sh generate [--config config/sources.yml]"
    echo "  ./run.sh no-ai [--config config/sources.yml]"
    echo "  ./run.sh serve [--config config/sources.yml]"
    exit 2
    ;;
esac
