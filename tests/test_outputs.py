"""Grades the audit reports. test.sh compiled the agent's TypeScript and ran it against the week
the agent's engine served, a held-back week and a small week reached by host name, capturing
stdout under /opt/run. Nothing here recomputes a report: every figure is compared with the report
the Python rule book solution/generate_feed.py wrote for that week in advance, which the first
check holds equal to the report the separately written TypeScript reference printed for it."""
import json
import os

import pytest

RUN = "/opt/run"
TRUTH = "/opt/truth"
SETS = ["sample", "hidden", "edge"]
FIELDS = ("delivered_mcg", "over_s", "under_s", "peak")


def read_text(path):
    """Opens without following a symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as fh:
        return fh.read().decode("utf-8")


def report(name):
    text = read_text(os.path.join(RUN, f"report_{name}.json")).removesuffix("\n")
    assert text, f"the {name} run printed nothing"
    assert "\n" not in text, f"the {name} run printed more than one line"
    rep = json.loads(text)
    assert isinstance(rep, dict) and set(rep) == {"orders"}, "the report is an object whose one member is orders"
    assert isinstance(rep["orders"], dict)
    return rep["orders"]


def golden(name):
    return json.loads(read_text(os.path.join(TRUTH, "golden", name + ".json")))["orders"]


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def test_two_references_agree():
    """Every week's expected report comes from two independent implementations of SPEC.md: the
    Python rule book (golden/) and the compiled TypeScript reference run against the engine
    (golden_ref/). They must be identical before anything is graded against them."""
    for name in SETS:
        a = json.loads(read_text(os.path.join(TRUTH, "golden", name + ".json")))
        b = json.loads(read_text(os.path.join(TRUTH, "golden_ref", name + ".json")))
        assert a == b, f"the two references disagree on the {name} week"


def test_build_succeeded():
    """The collected sources compiled with the grader's tsconfig into dist/main.js."""
    assert read_text(os.path.join(RUN, "build_check.txt")).strip() == "ok", "src did not compile to dist/main.js"


@pytest.mark.parametrize("name", SETS)
def test_one_member_per_order(name):
    """Each run printed one JSON line with a member for every order in its week and no other."""
    got, want = report(name), golden(name)
    if name == "hidden":
        assert any(all(r[f] == 0 for f in FIELDS) for r in want.values()), "the held-back week keeps an order that never ran"
    assert set(got) == set(want), f"missing {sorted(set(want) - set(got))}, extra {sorted(set(got) - set(want))}"
    for oid, row in got.items():
        assert isinstance(row, dict) and set(row) == set(FIELDS), f"{oid}: members {sorted(row)}"
        assert all(is_int(row[f]) for f in FIELDS), f"{oid}: every figure is a whole number"


@pytest.mark.parametrize("name", SETS)
@pytest.mark.parametrize("field", FIELDS)
def test_figures_match(name, field):
    """Every order's figure matches the week's audited report exactly."""
    got, want = report(name), golden(name)
    wrong = [f"{oid}: {got.get(oid, {}).get(field)} != {want[oid][field]}"
             for oid in sorted(want) if got.get(oid, {}).get(field) != want[oid][field]]
    assert not wrong, f"{len(wrong)} of {len(want)} orders wrong on {field}: " + "; ".join(wrong[:6])


@pytest.mark.parametrize("name", SETS)
def test_engine_read_to_the_end(name):
    """The engine served its last page to the run and answered BUSY on the way, so the run
    waited out a BUSY and read the week to the end."""
    log = read_text(os.path.join(RUN, f"engine_{name}.log")).splitlines()
    assert any(" PAGE " in ln and ln.endswith(" END") for ln in log), "the last page was never served"
    assert any(" BUSY " in ln for ln in log), "the engine never answered BUSY"
    first_busy = next(i for i, ln in enumerate(log) if " BUSY " in ln)
    assert any(" PAGE " in ln for ln in log[first_busy:]), "no page was served after a BUSY"
