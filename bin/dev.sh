#!/usr/bin/bash

set -e

if [[ "${VCSINSTALL_DEV_CONTAINER:-}" == "1" ]]; then
    exec "$@"
fi

exec docker compose run --rm vcsinstall "$@"
