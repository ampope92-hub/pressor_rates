#!/bin/bash
# Reference: the audit tool in solution/src/main.ts, placed in the project and built the way the
# grader builds it.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
rm -rf /app/audit/src
mkdir -p /app/audit/src
cp "$HERE/src/main.ts" /app/audit/src/main.ts
/app/audit/build.sh
