#!/usr/bin/bash

set -e

echo "run autoflake"

./bin/dev.sh uv run -- autoflake ./vcsinstall

echo "run isort"

./bin/dev.sh uv run -- isort ./vcsinstall

echo "run black"

./bin/dev.sh uv run -- black ./vcsinstall
