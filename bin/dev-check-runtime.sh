#!/usr/bin/bash

set -e

./bin/dev-build-package.sh

PACKAGE="/repository/dist/vcsinstall-$(./bin/dev.sh uv version --short)-py3-none-any.whl"

echo "check that CLI works from built package outside the source tree"

./bin/dev.sh env UV_NO_SYNC=0 uv run --isolated --no-project --directory /tmp --with "$PACKAGE" vcsinstall --help

echo "check that package reports version"

./bin/dev.sh env UV_NO_SYNC=0 uv run --isolated --no-project --directory /tmp --with "$PACKAGE" vcsinstall version

echo "check that package includes skill documentation"

./bin/dev.sh env UV_NO_SYNC=0 uv run --isolated --no-project --directory /tmp --with "$PACKAGE" vcsinstall -p automation skill usage

echo "check module entry point from built package"

./bin/dev.sh env UV_NO_SYNC=0 uv run --isolated --no-project --directory /tmp --with "$PACKAGE" python -I -m vcsinstall version
