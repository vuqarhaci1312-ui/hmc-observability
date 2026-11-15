#!/bin/bash
set -eu
cd "$(dirname "$0")"
exec bash infrastructure/render/start.sh
