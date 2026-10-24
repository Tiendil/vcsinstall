#!/usr/bin/bash

set -e

echo "run tach"

./bin/dev.sh uv run -- tach check

echo "run autoflake"

./bin/dev.sh uv run -- autoflake --check --quiet ./vcsinstall

echo "run codespell"

./bin/dev.sh uv run -- codespell ./vcsinstall

echo "run flake8"

./bin/dev.sh uv run -- flake8 ./vcsinstall

echo "run mypy"

./bin/dev.sh uv run -- mypy --show-traceback ./vcsinstall
