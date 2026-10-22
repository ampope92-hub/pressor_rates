# ICU Pressor Dosing Audit

An ICU medication safety committee needs its weekly vasopressor audit. The agent gets a stub TypeScript tool at `/app/audit/src/main.ts` and must make it read a week of messages from the hospital's interface engine over TCP (`ENGINE_HOST` / `ENGINE_PORT`). The engine pages its output and sheds load with BUSY replies. The messages cover charted weights, pressor order versions and smart-pump programming events. The tool prints one line of JSON reporting, per order, the micrograms delivered (`delivered_mcg`), the seconds above and below the ordered dose range (`over_s`, `under_s`) and the peak dose. `/app/audit/SPEC.md` describes the protocol, the message formats and how the pumps and pharmacy behave on this unit. `/app/audit/examples` holds one previously audited week with its signed report. The stack is TypeScript on Node.

## What makes it hard

The core difficulty is deciding **which weight a per-kg dose is per kg of**. On a real ICU the charted weight moves throughout a stay, and most of that movement is not the patient:

- Bed scales include the mattress and linen.
- Nurses chart weights hours after taking them.
- Bed weights are re-filed as corrections (status C), sometimes twice.
- The renal team charts a dry weight nobody measured.
- Fluid resuscitation adds kilograms daily.

Pharmacy verifies each order version against one scale weight: the one *taken* last among those already *filed* when it signed. A later correction changes that weight back to when it was taken, but a weight taken earlier and filed late does not count.

The pump knows none of this. It computes its rate from the weight the nurse keyed in, rounds to 0.1 mL/h, holds the dose on weight or bag changes, stops on alarms, and two channels overlap at bag changes.

So delivered drug follows the pump's weight, while compliance against the order follows pharmacy's corrected weight. A tool that swaps them, trusts the pump's dose display, or applies a correction from the time it was filed produces a well-formed report that even matches the signed example week, and is still wrong.

The rule book in `solution/generate_feed.py` expresses 21 such misreadings:

- On the shipped week, each one moves between 6 and 56 of 68 graded figures.
- On the held-back week, each one moves between 5 and 72 of 104.
- Every one moves at least one figure on each of 8 fresh seeds.
- The signed example week does **not** change under 10 of them, so it cannot be used to catch those mistakes.

## How it's graded

The verifier runs in a separate container and keeps only `.ts` files from `/app/audit/src`. It compiles them with its own `tsconfig.json` as an unprivileged user, through a seccomp launcher (`tests/launch.c`) that blocks any other `exec`. The compiled tool then runs three times, ten minutes each, against an engine the verifier starts:

1. The shipped week, on a different address, port, page size and BUSY pattern.
2. A held-back week of 26 orders with a different month, unit habits, bag size and concentrations. It includes one order signed and discontinued without ever running, and one per-kg order run on a per-minute pump program.
3. A five-order week reached by the host name `engine.icu.internal`.

Every `delivered_mcg`, `over_s`, `under_s` and `peak` must equal the expected report exactly. The engine logs must also show that each run handled BUSY and fetched through the last page. Expected reports come from two separately written implementations of the spec, the Python rule book and the compiled TypeScript reference, and a check requires them to agree for every week.

Anti-cheat measures: the tests, data, engine and log directory are closed to the build user and probed before anything runs; scratch space is swept between runs; and file reads use `O_NOFOLLOW`. Fourteen wrong variants run through this verifier all score 0. They include a truncated rate, ignored corrections, the pump's keyed weight used as the dosing weight, the shipped report hard-coded, and loading the TypeScript package at run time. The reference scores 1.

## Repository layout

- `instruction.md`: the task prompt given to the agent.
- `task.toml`: metadata, difficulty, solution and verification notes, and resource limits.
- `environment/`: the agent's container (`Dockerfile`), the interface engine service (`Dockerfile.engine`, `engine/`), wired together by `docker-compose.yaml`; `app/audit/` holds `SPEC.md`, the stub, `tsconfig.json`, the build script and the signed example week.
- `solution/`: `solve.sh`, the reference TypeScript tool (`src/main.ts`), the feed generator and rule book (`generate_feed.py`), the misreading calibration (`calibrate.py`) and the script that records the TypeScript reference's reports (`make_golden_ref.py`).
- `tests/`: verifier image and entrypoint (`Dockerfile`, `test.sh`), pytest checks (`test_outputs.py`), the seccomp launcher, the grader's `tsconfig`, the engine, the three graded weeks (`data/`) and their expected reports (`golden/`, `golden_ref/`).

## Running it

Requires Docker and Harbor (`uv tool install harbor` or `pip install harbor`). From the repo root:

```
harbor run -p . -a oracle
```

This runs the reference solution through the verifier and should score reward 1. To try an agent instead:

```
harbor run -p . -a <agent> -m <model>
```

For example, `-a claude-code`.

---

Task design, data generators, reference solution and verifier by Audrey Pope.
