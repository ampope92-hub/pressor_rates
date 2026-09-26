#!/usr/bin/env bash
# Compile the audit tool. Run it afterwards from /app/audit as: node dist/main.js
set -euo pipefail
cd /app/audit
tsc -p /app/audit
