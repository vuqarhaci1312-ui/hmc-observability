#!/bin/bash
set -eu
if [ -x /opt/render/start.sh ]; then
  exec /opt/render/start.sh
fi
cd "$(dirname "$0")"
exec bash infrastructure/render/start.sh
