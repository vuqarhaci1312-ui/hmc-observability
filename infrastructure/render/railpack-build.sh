#!/bin/bash
set -eu
cd "$(dirname "$0")/../.."
npm install --prefix frontend
npm run build --prefix frontend
pip install ./backend
mkdir -p .local
curl -fsSL -o /tmp/grafana.deb https://dl.grafana.com/oss/release/grafana_11.4.0_amd64.deb
dpkg -x /tmp/grafana.deb .local/grafana
rm -f /tmp/grafana.deb
