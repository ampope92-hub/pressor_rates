#!/usr/bin/env python3
"""Writes tests/golden_ref/<week>.json: the report the compiled TypeScript reference prints for
each graded week, served by the engine exactly as the grader serves it. tests/golden/ comes from
the Python rule book in generate_feed.py; the verifier requires the two to be identical, so every
expected figure is backed by two implementations of SPEC.md written separately.

    python3 make_golden_ref.py <dir holding the compiled dist/main.js>
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.dirname(HERE)


def main():
    main_js = os.path.join(sys.argv[1], "dist", "main.js")
    for i, week in enumerate(["sample", "hidden", "edge"]):
        port = str(18600 + i)
        ready = os.path.join(HERE, f".ready_{week}")
        eng = subprocess.Popen([sys.executable, os.path.join(TASK, "tests/engine/engine.py"),
                                "--data", os.path.join(TASK, "tests/data", week), "--port", port,
                                "--busy", "1", "--busy-ms", "100", "--ready-file", ready],
                               stderr=subprocess.DEVNULL)
        try:
            while not os.path.exists(ready):
                time.sleep(0.05)
            out = subprocess.run(["node", main_js], env={**os.environ, "ENGINE_PORT": port},
                                 capture_output=True, text=True, timeout=600, check=True).stdout
        finally:
            eng.terminate()
            eng.wait()
            os.remove(ready)
        os.makedirs(os.path.join(TASK, "tests/golden_ref"), exist_ok=True)
        with open(os.path.join(TASK, "tests/golden_ref", week + ".json"), "w") as fh:
            fh.write(out)
        print(week, "written", file=sys.stderr)


if __name__ == "__main__":
    main()
