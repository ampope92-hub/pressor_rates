#!/usr/bin/env python3
"""The ICU interface engine's archive for one audit week, served over a line protocol on TCP.

    engine.py --data DIR --host 127.0.0.1 --port 7710 [--page N] [--busy 1,23] [--busy-ms 250]
              [--ready-file PATH] [--log PATH]

DIR/feed.txt holds the messages in the order the engine filed them, one per line.

    FEED            first page
    FEED <token>    the page after the one that handed out <token>
    QUIT            hang up

A page is `PAGE <n> <next>` followed by n message lines; <next> is a token or END. `BUSY <ms>`
means nothing was served: the same request is answered once <ms> milliseconds have passed since
the BUSY. A connection is closed after its twentieth answer. Anything else gets `ERR <reason>`.
"""
import argparse
import os
import random
import secrets
import socket
import socketserver
import sys
import threading
import time

PER_CONNECTION = 20


class Engine:
    def __init__(self, lines, page, busy, busy_ms, log):
        self.lines = lines
        self.page = page
        self.busy = set(busy)
        self.busy_ms = busy_ms
        self.log_path = log
        self.lock = threading.Lock()
        self.count = 0
        self.tokens = {}
        self.hold_until = None     # (request key, time) after a BUSY
        self.rng = random.Random(len(lines) * 7919 + page)

    def log(self, msg):
        if not self.log_path:
            return
        with self.lock, open(self.log_path, "a") as fh:
            fh.write(f"{time.time():.3f} {msg}\n")

    def token(self, index):
        tok = secrets.token_urlsafe(9)
        self.tokens[tok] = index
        return tok

    def answer(self, req, conn_id):
        parts = req.split()
        if not parts or parts[0] != "FEED" or len(parts) > 2:
            self.log(f"c{conn_id} ERR {req[:40]!r}")
            return ["ERR unknown request"]
        with self.lock:
            self.count += 1
            n = self.count
            if len(parts) == 1:
                start = 0
            elif parts[1] in self.tokens:
                start = self.tokens[parts[1]]
            else:
                self.log_nolock(f"c{conn_id} ERR token")
                return ["ERR unknown token"]
            now = time.monotonic()
            if self.hold_until is not None:
                key, until = self.hold_until
                if key == start and now < until:
                    left = max(1, int((until - now) * 1000))
                    self.log_nolock(f"c{conn_id} BUSY-AGAIN {start} {left}")
                    return [f"BUSY {left}"]
                if key == start:
                    self.hold_until = None
            if n in self.busy:
                self.hold_until = (start, now + self.busy_ms / 1000)
                self.log_nolock(f"c{conn_id} BUSY {start}")
                return [f"BUSY {self.busy_ms}"]
            end = min(start + self.page, len(self.lines))
            nxt = "END" if end >= len(self.lines) else self.token(end)
            self.log_nolock(f"c{conn_id} PAGE {start} {end - start} {'END' if nxt == 'END' else 'MORE'}")
            return [f"PAGE {end - start} {nxt}"] + self.lines[start:end]

    def log_nolock(self, msg):
        if self.log_path:
            with open(self.log_path, "a") as fh:
                fh.write(f"{time.time():.3f} {msg}\n")


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        eng = self.server.engine
        with eng.lock:
            eng.conns = getattr(eng, "conns", 0) + 1
            cid = eng.conns
        eng.log(f"c{cid} OPEN")
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        answers = 0
        try:
            while answers < PER_CONNECTION:
                raw = self.rfile.readline(4096)
                if not raw:
                    break
                req = raw.decode("ascii", "replace").strip()
                if req == "QUIT":
                    break
                out = ("\n".join(eng.answer(req, cid)) + "\n").encode()
                # a page leaves in pieces, the way a busy engine writes it
                p = 0
                while p < len(out):
                    k = eng.rng.randint(1, 1400)
                    self.connection.sendall(out[p:p + k])
                    p += k
                answers += 1
        except (TimeoutError, ConnectionError, OSError):
            pass
        eng.log(f"c{cid} CLOSE after {answers}")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7710)
    ap.add_argument("--page", type=int, default=40)
    ap.add_argument("--busy", default="1,23")
    ap.add_argument("--busy-ms", type=int, default=250)
    ap.add_argument("--ready-file")
    ap.add_argument("--log")
    a = ap.parse_args()
    with open(os.path.join(a.data, "feed.txt"), encoding="ascii") as fh:
        lines = fh.read().splitlines()
    busy = [int(x) for x in a.busy.split(",") if x]
    srv = Server((a.host, a.port), Handler)
    srv.engine = Engine(lines, a.page, busy, a.busy_ms, a.log)
    if a.ready_file:
        with open(a.ready_file, "w") as fh:
            fh.write("ready\n")
    print(f"engine: {len(lines)} messages on {a.host}:{a.port}, page {a.page}", file=sys.stderr, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
