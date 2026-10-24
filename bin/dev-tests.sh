#!/usr/bin/bash

set -e

echo "run tests"

./bin/dev.sh uv run -- pytest vcsinstall -o cache_dir=/tmp/vcsinstall-pytest-cache "$@"
