#!/bin/sh
set -eu

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
if [ "$#" -eq 0 ]; then
  exec "$script_dir/dev.sh" infra
fi

exec "$script_dir/dev.sh" "$@"
