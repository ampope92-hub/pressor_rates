#!/bin/bash

# Verifier entrypoint. Runs in the verifier container, sees only /app/audit/src from the agent's
# box, fetches nothing. It compiles the collected TypeScript, then runs the tool three times
# against an engine it starts from the copies baked into this image: the week the agent's own
# engine served (on another address, port, page size and load pattern), a week the agent has
# never seen, and a small week reached by host name.

set -uo pipefail
cd /
mkdir -p /logs/verifier
echo 0 > /logs/verifier/reward.txt
exec 2>&1

# Root runs this script and later pytest, after agent code has run as the build user, so no
# directory on root's PATH may be writable by anyone else.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
hash -r
for d in ${PATH//:/ }; do
  real="$(/usr/bin/readlink -f "$d" 2>/dev/null)" || continue
  [ -d "$real" ] || continue
  owner="$(/usr/bin/stat -c %u "$real")"
  mode="$(/usr/bin/stat -c %a "$real")"
  if [ "$owner" != "0" ] || [ $(( 8#$mode & 8#022 )) -ne 0 ]; then
    echo "FATAL: $d (-> $real) is on root's PATH but is owner $owner mode $mode."
    echo 0 > /logs/verifier/reward.txt
    exit 0
  fi
done
echo "every directory on root's PATH is root-owned and closed to writing by anyone else"

NODE=/usr/bin/node
TSC=/usr/local/lib/node_modules/typescript/lib/tsc.js
if [ ! -x "$NODE" ] || [ ! -f "$TSC" ]; then
  echo "FATAL: node or the TypeScript compiler is missing"
  exit 0
fi

/usr/bin/install -d -m 700 -o root -g root /opt/run

as_runner() {
  setpriv --clear-groups --no-new-privs --reuid runner --regid runner "$@"
}

# One graded run reaches the engine by name. SPEC.md says ENGINE_HOST holds a host name or an
# address.
BY_NAME=engine.icu.internal
printf '%s\t%s\n' 127.0.0.4 "$BY_NAME" >> /etc/hosts
if ! /usr/bin/getent hosts "$BY_NAME" >/dev/null 2>&1; then
  echo "FATAL: $BY_NAME does not resolve here, so the named run cannot be graded."
  exit 0
fi

# Close the verifier's code, the weeks, the engine and the reward directory to the build user
# before anything untrusted runs, and prove it with a listing as that user.
chmod 700 /tests /logs/verifier /opt/truth /opt/engine /opt/run 2>/dev/null || true
if as_runner bash -c 'test -r /tests/test_outputs.py || ls /opt/truth || ls /opt/engine || ls /opt/run' >/dev/null 2>&1; then
  echo "FATAL: /tests, the weeks or the engine are still reachable by the unprivileged build user."
  echo 0 > /logs/verifier/reward.txt
  exit 0
fi
echo "verifier files closed to the unprivileged build user"

# A root-owned pristine copy of the collected sources; the build starts from it.
rm -rf /opt/src_pristine
mkdir -p /opt/src_pristine
cp -a /app/audit/src /opt/src_pristine/src 2>/dev/null || mkdir -p /opt/src_pristine/src
chown -R root:root /opt/src_pristine
chmod -R go-w /opt/src_pristine
chmod 700 /opt/src_pristine

# Compile. Only TypeScript under src reaches the compiler: links and every file that is not a
# .ts file are deleted, the tsconfig is the grader's, and the compiler runs as the build user
# through the launcher, so it can start no other program. What it emits becomes root's.
rm -rf /app/audit
install -d -o runner -g runner -m 755 /app/audit
cp -a /opt/src_pristine/src /app/audit/src
find /app/audit/src -type l -delete 2>/dev/null || true
find /app/audit/src ! -type d ! -name '*.ts' -delete 2>/dev/null || true
find /app/audit/src -name '*.d.ts' -delete 2>/dev/null || true
install -m 444 -o root -g root /opt/graded_tsconfig.json /app/audit/tsconfig.json
chown -R runner:runner /app/audit/src
chmod 755 /app/audit
chown runner:runner /app/audit
echo "collected TypeScript files: $(find /app/audit/src -type f -name '*.ts' | wc -l)"
find /app/audit/src -type f -exec touch {} + 2>/dev/null || true
rm -rf /home/runner && install -d -o runner -g runner -m 755 /home/runner
( cd /app/audit && exec setsid /usr/bin/timeout --signal=KILL 600 \
    setpriv --clear-groups --no-new-privs --reuid runner --regid runner \
    env HOME=/home/runner /usr/local/bin/tool_launch "$NODE" "$TSC" -p /app/audit ) &
bpid=$!
wait "$bpid"
bstatus=$?
echo "tsc exit status: $bstatus"
kill -KILL -- -"$bpid" 2>/dev/null || true
as_runner bash -c 'kill -9 -1' 2>/dev/null || true
if [ "$bstatus" -eq 0 ] && [ -f /app/audit/dist/main.js ] && [ ! -L /app/audit/dist/main.js ]; then
  echo ok > /opt/run/build_check.txt
else
  echo failed > /opt/run/build_check.txt
  rm -f /app/audit/dist/main.js
fi
chown -R root:root /app/audit
chmod -R go-w /app/audit
find /app/audit/src -type f -exec chmod 600 {} + 2>/dev/null || true
chmod -R a+rX /app/audit/dist 2>/dev/null || true

ENGINE_PID=""
start_engine() {   # $1 set, $2 host, $3 port, $4 page, $5 busy list, $6 busy ms
  rm -f /opt/run/ready
  /usr/local/bin/python3 /opt/engine/engine.py --data "/opt/truth/$1" --host "$2" --port "$3" \
      --page "$4" --busy "$5" --busy-ms "$6" --ready-file /opt/run/ready \
      --log "/opt/run/engine_$1.log" >> "/opt/run/engine_$1.err" 2>&1 &
  ENGINE_PID=$!
  local i
  for i in $(/usr/bin/seq 1 300); do
    [ -f /opt/run/ready ] && return 0
    /usr/bin/sleep 0.1
  done
  echo "FATAL: the engine never came up on $2:$3"
  return 1
}

stop_engine() {
  [ -n "$ENGINE_PID" ] || return 0
  kill -TERM "$ENGINE_PID" 2>/dev/null || true
  wait "$ENGINE_PID" 2>/dev/null
  ENGINE_PID=""
}

# $1 host, $2 port, $3 capture. The compiled tool, as the build user, from /app/audit, through
# the launcher so neither node nor anything it loads can start another program, for at most the
# ten minutes the instruction states. Scratch space the build user can write is swept first, so
# no run can leave anything for the next.
run_tool() {
  local host="$1" port="$2" out="$3" pid
  /usr/bin/find /tmp -mindepth 1 -maxdepth 1 ! -name 'pytest_cache' -exec /usr/bin/rm -rf {} + 2>/dev/null || true
  rm -rf /home/runner && install -d -o runner -g runner -m 755 /home/runner
  ( cd /app/audit && exec setsid /usr/bin/timeout --signal=KILL 600 \
      setpriv --clear-groups --no-new-privs --reuid runner --regid runner \
      env HOME=/home/runner ENGINE_HOST="$host" ENGINE_PORT="$port" \
      /usr/local/bin/tool_launch "$NODE" /app/audit/dist/main.js ) > "$out" &
  pid=$!
  wait "$pid"
  echo "run exit status: $? (engine $host:$port)"
  kill -KILL -- -"$pid" 2>/dev/null || true
  as_runner bash -c 'kill -9 -1' 2>/dev/null || true
  chown root:root "$out" 2>/dev/null || true
  chmod 600 "$out" 2>/dev/null || true
}

echo "=== the agent's own week, on another address, port, page size and load pattern ==="
start_engine sample 127.0.0.2 7811 33 1,26 400 || exit 0
run_tool 127.0.0.2 7811 /opt/run/report_sample.json
stop_engine

echo "=== a week the agent has never seen ==="
start_engine hidden 127.0.0.1 7812 64 2,31 300 || exit 0
run_tool 127.0.0.1 7812 /opt/run/report_hidden.json
stop_engine

echo "=== a small week reached by host name ==="
start_engine edge 127.0.0.4 7813 7 1,21 350 || exit 0
run_tool "$BY_NAME" 7813 /opt/run/report_edge.json
stop_engine

as_runner bash -c 'kill -9 -1' 2>/dev/null || true
for s in sample hidden edge; do
  echo "$s: $(grep -c ' PAGE ' /opt/run/engine_$s.log) pages, $(grep -c ' BUSY' /opt/run/engine_$s.log) busy, $(grep -c ' OPEN' /opt/run/engine_$s.log) connections, $(grep -c ' ERR' /opt/run/engine_$s.log) errors"
done

/usr/local/bin/python3 -P -m pytest -o cache_dir=/tmp/pytest_cache \
  --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
if [ "$?" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
