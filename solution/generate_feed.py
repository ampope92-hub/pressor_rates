#!/usr/bin/env python3
"""Rule book and generator for the pressor audit.

Makes the ICU's interface-engine feed for one audit week (weights charted, pressor orders signed,
pump programming events) from a simulated unit, and the audit report the rule book gives for it.
The rule book is `audit()` below; it was written down before the TypeScript reference and is
independent of it. `audit(..., flags)` switches one reading at a time to a plausible wrong one,
which is what calibrate.py measures.

    python3 generate_feed.py            # rebuilds every shipped and held-back file, byte for byte

Line formats (fields separated by '|'; times are the hospital wall clock, YYYYMMDDHHMMSS):
    W|filed|stay|obs|taken|kind|kg|status          kind STATED BED STAND HOIST DRY; status F or C
    O|signed|stay|order|version|drug|unit|min|max
    D|signed|order
    P|time|channel|START|order|unit|dose|kg|conc    kg is '-' for a per-minute program
    P|time|channel|DOSE|dose
    P|time|channel|WEIGHT|kg
    P|time|channel|BAG|conc
    P|time|channel|ALARM|code
    P|time|channel|RESUME
    P|time|channel|STOP
"""
import datetime as dt
import json
import os
import random
import sys
from fractions import Fraction
from itertools import pairwise

HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.dirname(HERE)

SCALE = ("BED", "STAND", "HOIST")

# ── numbers as exact integers ────────────────────────────────────────────────────────────────
# dose: thousandths of the unit (mcg/kg/min or mcg/min); kg: tenths; conc: whole mcg/mL;
# rate: tenths of a mL/h; limits: thousandths of the order's unit.


def dec(s, places):
    """'0.125' -> 125 for places=3. Exact; rejects more places than allowed."""
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    whole, _, frac = s.partition(".")
    assert len(frac) <= places, s
    v = int(whole or "0") * 10 ** places + int((frac + "0" * places)[:places] or "0")
    return -v if neg else v


def fmt(v, places):
    s = str(v).rjust(places + 1, "0")
    whole, frac = s[:-places], s[-places:]
    frac = frac.rstrip("0")
    return whole + ("." + frac if frac else "")


def rate_parts(unit, dm, wd, c):
    """(numerator, denominator) of the rate in tenths of a mL/h."""
    if unit == "mcg/kg/min":
        return 6 * dm * wd, 100 * c
    return 6 * dm, 10 * c


def pump_rate(unit, dm, wd, c, mode="nearest"):
    n, q = rate_parts(unit, dm, wd, c)
    if mode == "exact":
        return (n, q)
    if mode == "trunc":
        return n // q
    return (2 * n + q) // (2 * q)


EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


def stamp(t):
    """Seconds on the hospital wall clock -> YYYYMMDDHHMMSS (the clock is kept as if UTC)."""
    return (EPOCH + dt.timedelta(seconds=t)).strftime("%Y%m%d%H%M%S")


def unstamp(s):
    d = dt.datetime.strptime(s + "+0000", "%Y%m%d%H%M%S%z")
    return int((d - EPOCH).total_seconds())


# ── parsing the feed ─────────────────────────────────────────────────────────────────────────

def parse(lines):
    weights = {}      # obs -> {"stay", "kind", "taken", "first_filed", "versions": [(filed, idx, dk)]}
    orders = {}       # order -> {"stay", "drug", "unit", "versions": [(signed, ver, mn, mx)], "dc"}
    pump = []         # (t, idx, channel, event, args)
    for idx, line in enumerate(lines):
        f = line.split("|")
        if f[0] == "W":
            filed, stay, obs, taken, kind, kg, status = unstamp(f[1]), f[2], f[3], unstamp(f[4]), f[5], dec(f[6], 1), f[7]
            w = weights.setdefault(obs, {"stay": stay, "kind": kind, "taken": taken, "versions": []})
            w["versions"].append((filed, idx, kg, status))
        elif f[0] == "O":
            o = orders.setdefault(f[3], {"stay": f[2], "drug": f[5], "unit": f[6], "versions": [], "dc": None})
            o["versions"].append((unstamp(f[1]), int(f[4]), dec(f[7], 3), dec(f[8], 3)))
        elif f[0] == "D":
            orders[f[2]]["dc"] = unstamp(f[1])
        elif f[0] == "P":
            pump.append((unstamp(f[1]), idx, f[2], f[3], f[4:]))
        else:
            raise ValueError(line)
    for w in weights.values():
        w["versions"].sort()
        w["first_filed"] = w["versions"][0][0]
    for o in orders.values():
        o["versions"].sort()
    return weights, orders, pump


# ── the rule book ────────────────────────────────────────────────────────────────────────────

def weight_value(w, flags, at=None):
    """The figure of one weight observation. The latest filed line stands (a C line is the same
    weight with its figure fixed, and the weight taken then was that figure)."""
    vs = w["versions"]
    if "correction_ignored" in flags:
        return vs[0][2]
    if "first_correction_wins" in flags:
        cs = [v for v in vs if v[3] == "C"]
        return (cs[0] if cs else vs[0])[2]
    if at is not None:   # correction_from_filing: only what had been filed by `at`
        cur = [v for v in vs if v[0] <= at]
        return (cur[-1] if cur else vs[0])[2]
    return vs[-1][2]


def dosing_obs(weights, stay, signed, flags):
    """The observation pharmacy verified an order version against at `signed`."""
    kinds = SCALE + (("DRY",) if "dry_is_scale" in flags else ())
    cands = []
    for obs, w in weights.items():
        if w["stay"] != stay or w["kind"] not in kinds:
            continue
        if "late_charting_counted" in flags:
            if w["taken"] > signed:
                continue
        elif w["first_filed"] > signed:
            continue
        key = w["first_filed"] if "latest_filed" in flags else w["taken"]
        cands.append((key, obs))
    if cands:
        return max(cands)[1]
    if "no_stated_fallback" in flags:
        later = sorted((w["taken"], obs) for obs, w in weights.items() if w["stay"] == stay and w["kind"] in SCALE)
        return later[0][1]
    stated = [obs for obs, w in weights.items() if w["stay"] == stay and w["kind"] == "STATED"]
    assert len(stated) == 1, stay
    return stated[0]


def audit(lines, flags=frozenset()):
    weights, orders, pump = parse(lines)
    rmode = "exact" if "rate_unrounded" in flags else ("trunc" if "rate_truncated" in flags else "nearest")
    # 1. every channel's program replayed in time order (the feed is in filing order)
    key = (lambda e: e[1]) if "feed_order" in flags else (lambda e: (e[0], e[1]))
    chan = {}
    changes = {o: [] for o in orders}          # order -> [(t, channel, program or None)]
    for t, _, ch, ev, a in sorted(pump, key=key):
        st = chan.get(ch)
        if ev == "START":
            if st is not None:
                # a new program on a channel ends whatever that channel was pushing for its order
                changes[st["order"]].append((t, ch, None))
            wd = dec(a[3], 1) if a[3] != "-" else None
            st = {"order": a[0], "unit": a[1], "dm": dec(a[2], 3), "wd": wd, "c": int(a[4]), "on": True}
            st["rate_wd"], st["rate_c"] = wd, st["c"]
            chan[ch] = st
        elif ev == "DOSE":
            st["dm"] = dec(a[0], 3)
            st["rate_wd"], st["rate_c"] = st["wd"], st["c"]
        elif ev == "WEIGHT":
            st["wd"] = dec(a[0], 1)
            if "weight_holds_rate" not in flags:
                st["rate_wd"] = st["wd"]
        elif ev == "BAG":
            st["c"] = int(a[0])
            if "bag_holds_rate" not in flags:
                st["rate_c"] = st["c"]
        elif ev == "ALARM":
            if "alarm_ignored" not in flags:
                st["on"] = False
        elif ev == "RESUME":
            st["on"] = True
        elif ev == "STOP":
            st["on"] = False
        changes[st["order"]].append((t, ch, dict(st) if st["on"] else None))
        if ev == "STOP":
            del chan[ch]

    def flow(p, Wd):
        """mcg per minute this program pushes, exactly."""
        wd = p["rate_wd"]
        if "rate_dosing_weight" in flags and p["unit"] == "mcg/kg/min":
            wd = Wd
        n, q = rate_parts(p["unit"], p["dm"], wd or 0, p["rate_c"])
        if rmode == "exact":
            r = Fraction(n, q)
        elif rmode == "trunc":
            r = n // q
        else:
            r = (2 * n + q) // (2 * q)
        return Fraction(r) * p["c"] / 600

    report = {}
    for oid, o in orders.items():
        vers = o["versions"]
        stay = o["stay"]
        if "first_version_weight" in flags:
            dobs = [dosing_obs(weights, stay, vers[0][0], flags)] * len(vers)
        else:
            dobs = [dosing_obs(weights, stay, v[0], flags) for v in vers]
        limits = [(v[2], v[3]) for v in vers]
        if "limits_retroactive" in flags:
            limits = [limits[-1]] * len(vers)
        bounds = {c[0] for c in changes[oid]} | {v[0] for v in vers}
        if "correction_from_filing" in flags:
            for ob in set(dobs):
                bounds |= {v[0] for v in weights[ob]["versions"]}
        if "latest_charted_weight" in flags:
            bounds |= {w["first_filed"] for w in weights.values() if w["stay"] == stay}
            bounds |= {v[0] for w in weights.values() if w["stay"] == stay for v in w["versions"]}
        if o["dc"] is not None:
            bounds.add(o["dc"])
        bounds = sorted(bounds)
        ch_sorted = sorted(changes[oid], key=lambda c: c[0])
        state, started = {}, {}
        delivered = Fraction(0)
        over = under = 0
        peak = None
        ci = 0
        for a, b in pairwise(bounds):
            while ci < len(ch_sorted) and ch_sorted[ci][0] <= a:
                t, ch, p = ch_sorted[ci]
                if p is None:
                    state.pop(ch, None)
                else:
                    if ch not in state:
                        started[ch] = t
                    state[ch] = p
                ci += 1
            vi = max((i for i, v in enumerate(vers) if v[0] <= a), default=None)
            # a program whose rate comes out at zero pushes nothing, so it is not running
            Wd0 = weight_value(weights[dobs[vi]], flags) if vi is not None else 0
            pushing = {ch: p for ch, p in state.items() if flow(p, Wd0) > 0}
            if not pushing:
                if "under_when_idle" in flags and vi is not None and (o["dc"] is None or a < o["dc"]):
                    under += b - a
                continue
            # the dosing weight for this stretch
            if "latest_charted_weight" in flags:
                vis = [(w["taken"], ob) for ob, w in weights.items()
                       if w["stay"] == stay and w["kind"] in SCALE and w["first_filed"] <= a]
                ob = max(vis)[1] if vis else dosing_obs(weights, stay, a, flags)
                Wd = weight_value(weights[ob], flags, at=a)
            elif "correction_from_filing" in flags:
                Wd = weight_value(weights[dobs[vi]], flags, at=a)
            else:
                Wd = weight_value(weights[dobs[vi]], flags)
            flows = {ch: flow(p, Wd) for ch, p in pushing.items()}
            delivered_here = sum(flows.values()) * (b - a) / 60
            if "delivered_floor_per_interval" in flags:
                delivered += int(delivered_here)
            else:
                delivered += delivered_here
            if "channels_not_summed" in flags:
                newest = max(pushing, key=lambda ch: (started[ch], ch))
                mcg_min = flows[newest]
            else:
                mcg_min = sum(flows.values())
            if "pump_dose_as_dose" in flags:
                dose = Fraction(sum(p["dm"] for p in pushing.values()), 1000)
            elif o["unit"] == "mcg/kg/min":
                dose = mcg_min * 10 / Wd
            else:
                dose = mcg_min
            mn, mx = limits[vi]
            if "check_ties" in flags and (dose * 1000 == mx or dose * 1000 == mn):
                raise ValueError("dose exactly on a limit")
            if dose * 1000 > mx:
                over += b - a
            if dose * 1000 < mn:
                under += b - a
            if peak is None or dose > peak:
                peak = dose
        if "check_ties" in flags and ((peak is not None and (peak * 1000).denominator == 1)
                                      or delivered.denominator == 1 and delivered != 0):
            raise ValueError("peak or total exactly whole")
        if peak is None:
            pk = 0
        elif "peak_rounded" in flags:
            pk = int(peak * 1000 + Fraction(1, 2))
        else:
            pk = int(peak * 1000)
        report[oid] = {"delivered_mcg": int(delivered), "over_s": over, "under_s": under, "peak": pk}
    return report


def degenerate(lines):
    """True when some figure sits exactly on a boundary a float could land either side of:
    a pump rate exactly half way between tenths, a dose exactly on a limit, a peak or a total
    exactly on a whole number. The generator re-rolls such a set; the data never holds one."""
    _, _, pump = parse(lines)
    # replay channels exactly, checking every rate
    chan = {}
    for t, _, ch, ev, a in sorted(pump, key=lambda e: (e[0], e[1])):
        if ev == "START":
            chan[ch] = {"unit": a[1], "dm": dec(a[2], 3), "wd": dec(a[3], 1) if a[3] != "-" else None, "c": int(a[4])}
        elif ev == "DOSE":
            chan[ch]["dm"] = dec(a[0], 3)
        elif ev == "WEIGHT":
            chan[ch]["wd"] = dec(a[0], 1)
        elif ev == "BAG":
            chan[ch]["c"] = int(a[0])
        s = chan.get(ch)
        if s:
            n, q = rate_parts(s["unit"], s["dm"], s["wd"] or 0, s["c"])
            if (2 * n) % (2 * q) == q:
                return "half-tenth rate"
            if n // q == 0 or (2 * n + q) // (2 * q) == 0:
                return "zero rate"
    return None


# ── the unit simulated ───────────────────────────────────────────────────────────────────────

DRUGS = {
    "norepinephrine": {"units": {"mcg/kg/min": 1.0}, "conc": [16, 32, 64],
                        "limits": {"mcg/kg/min": ([20, 30, 50], [200, 250, 300, 400, 500])},
                        "step": {"mcg/kg/min": 10}},
    "epinephrine": {"units": {"mcg/kg/min": 0.6, "mcg/min": 0.4}, "conc": [16, 32],
                     "limits": {"mcg/kg/min": ([10, 20], [150, 200, 300]),
                                "mcg/min": ([1000, 2000], [10000, 15000, 20000])},
                     "step": {"mcg/kg/min": 10, "mcg/min": 500}},
    "phenylephrine": {"units": {"mcg/min": 0.7, "mcg/kg/min": 0.3}, "conc": [40, 80, 160],
                       "limits": {"mcg/min": ([10000, 20000], [100000, 150000, 200000]),
                                  "mcg/kg/min": ([100, 250], [2000, 3000])},
                       "step": {"mcg/min": 5000, "mcg/kg/min": 50}},
    "dopamine": {"units": {"mcg/kg/min": 1.0}, "conc": [1600, 3200],
                  "limits": {"mcg/kg/min": ([2000, 5000], [10000, 15000, 20000])},
                  "step": {"mcg/kg/min": 500}},
}

CONFIGS = {
    # the week the agent's own engine serves (and the verifier's first run)
    "sample": {"start": "20260209000000", "stays": 11, "mattress": (4.0, 8.5), "gain": (0.6, 2.2),
               "late_p": 0.45, "corr_p": 0.45, "dry_p": 0.45, "overlap_p": 0.45, "alarm_rate": 0.06,
               "mismatch_p": 0.25, "rekey_p": 0.5, "bag_ml": 250, "second_p": 0.45, "double_corr_p": 0.5, "shuffle_p": 0.8,
               "conc_pick": 0, "drugs": ["norepinephrine", "epinephrine", "phenylephrine", "dopamine"],
               "hours": (8, 60), "early_p": 0.35},
    # the held-back week: another unit's habits and bags
    "hidden": {"start": "20260511000000", "stays": 17, "mattress": (6.0, 11.0), "gain": (1.0, 3.0),
               "late_p": 0.5, "corr_p": 0.55, "dry_p": 0.35, "overlap_p": 0.6, "alarm_rate": 0.1,
               "mismatch_p": 0.3, "rekey_p": 0.4, "bag_ml": 500, "second_p": 0.55, "double_corr_p": 0.35, "shuffle_p": 0.6,
               "conc_pick": 1, "drugs": ["norepinephrine", "epinephrine", "phenylephrine", "dopamine"],
               "hours": (6, 80), "early_p": 0.45, "never_ran": True,
               "per_min_on_kg": True},
    # a small week reached by host name
    "edge": {"start": "20260316000000", "stays": 3, "mattress": (5.0, 9.0), "gain": (0.5, 1.5),
             "late_p": 0.6, "corr_p": 0.8, "dry_p": 0.6, "overlap_p": 0.7, "alarm_rate": 0.15,
             "mismatch_p": 0.5, "rekey_p": 0.7, "bag_ml": 100, "second_p": 0.7, "double_corr_p": 0.6, "shuffle_p": 0.7,
             "conc_pick": 0, "drugs": ["norepinephrine", "epinephrine", "phenylephrine", "dopamine"],
             "hours": (5, 20), "early_p": 0.6},
    # last quarter's signed audit, shipped with its report as the worked example
    "example": {"start": "20251103000000", "stays": 3, "mattress": (0.0, 0.0), "gain": (0.4, 0.8),
                "late_p": 0.0, "corr_p": 0.0, "dry_p": 0.0, "overlap_p": 0.0, "alarm_rate": 0.0,
                "mismatch_p": 0.0, "rekey_p": 0.0, "bag_ml": 250, "second_p": 0.5, "double_corr_p": 0.0, "shuffle_p": 0.0,
                "conc_pick": 0, "drugs": ["norepinephrine", "phenylephrine"],
                "hours": (6, 14), "early_p": 0.5},
}


def gen_stay(rng, cfg, sid, t0, pumps_used):
    """One patient's stay: weights, pressor orders and the pumps that ran them."""
    lines = []   # (filing_time, tiebreak, text)
    true_kg = rng.uniform(48, 128)
    admit = t0 + rng.randint(0, 3 * 86400)
    mattress = round(rng.uniform(*cfg["mattress"]), 1) if cfg["mattress"][1] > 0 else 0.0
    gain_per_day = rng.uniform(*cfg["gain"])
    obs_n = [0]

    def new_obs():
        obs_n[0] += 1
        return f"{sid}W{obs_n[0]:02d}"

    weights = []   # (taken, first_filed, obs, kind, dk_final, dk_first)

    def chart(kind, taken, value, filed, correct_to=None, correct_at=None, second=None):
        obs = new_obs()
        dk = round(value * 10)
        lines.append((filed, 0, f"W|{stamp(filed)}|{sid}|{obs}|{stamp(taken)}|{kind}|{fmt(dk, 1)}|F"))
        final = dk
        if correct_to is not None:
            ck = round(correct_to * 10)
            lines.append((correct_at, 0, f"W|{stamp(correct_at)}|{sid}|{obs}|{stamp(taken)}|{kind}|{fmt(ck, 1)}|C"))
            final = ck
            if second is not None:
                sk = round(second[0] * 10)
                lines.append((second[1], 0, f"W|{stamp(second[1])}|{sid}|{obs}|{stamp(taken)}|{kind}|{fmt(sk, 1)}|C"))
                final = sk
        weights.append([taken, filed, obs, kind, final, dk])
        return obs

    # stated at admission (family's figure, to the nearest 5 kg)
    stated = 5 * round((true_kg + rng.uniform(-9, 9)) / 5)
    chart("STATED", admit, stated, admit + rng.randint(120, 1500))

    def true_at(t):
        return true_kg + gain_per_day * (t - admit) / 86400

    stay_hours = rng.uniform(*cfg["hours"]) + 30
    end = admit + int(stay_hours * 3600)
    # scale weights: first bed weight some time after arrival, then 05:00 daily
    first_bed = admit + rng.randint(40 * 60, 5 * 3600)
    takes = [first_bed]
    day = (admit // 86400 + 1) * 86400 + 5 * 3600
    while day < end:
        takes.append(day + rng.randint(-1800, 1800))
        day += 86400
    for i, tk in enumerate(takes):
        kind = "BED"
        r = rng.random()
        if i > 0 and r < 0.15:
            kind = rng.choice(["STAND", "HOIST"])
        val = true_at(tk) + (mattress if kind == "BED" else 0) + rng.uniform(-0.4, 0.4)
        delay = rng.randint(4 * 60, 50 * 60)
        if rng.random() < cfg["late_p"]:
            delay = rng.randint(2 * 3600, 7 * 3600)
        filed = tk + delay
        corr = corr_at = second = None
        if kind == "BED" and mattress > 0 and rng.random() < cfg["corr_p"]:
            corr = val - mattress
            corr_at = filed + rng.randint(1 * 3600, 26 * 3600)
            if rng.random() < cfg["double_corr_p"]:
                second = (corr + rng.choice([-1, 1]) * rng.uniform(0.6, 2.5), corr_at + rng.randint(1800, 8 * 3600))
        chart(kind, tk, val, filed, corr, corr_at, second)

    def bed_display(t):
        """What the bed's scale shows at t: today's weight with whatever is on the deck."""
        return true_at(t) + mattress + rng.uniform(-0.3, 0.3)

    def chart_weight_at(t):
        """The figure a nurse would read off the chart at t (latest bed weight filed)."""
        vis = [w for w in weights if w[1] <= t and w[3] in SCALE]
        if not vis:
            st = next(w for w in weights if w[3] == "STATED")
            return st[5] / 10
        return max(vis)[5] / 10

    # orders
    n_orders = 1 + (1 if rng.random() < cfg["second_p"] else 0)
    orders = []
    drugs = list(cfg["drugs"])
    for k in range(n_orders):
        drug = drugs[0] if k == 0 and "norepinephrine" in drugs else rng.choice(drugs)
        if k == 0:
            drug = "norepinephrine" if rng.random() < 0.7 else rng.choice(drugs)
        d = DRUGS[drug]
        unit = rng.choices(list(d["units"]), weights=list(d["units"].values()))[0]
        mins, maxs = d["limits"][unit]
        mn, mx = rng.choice(mins), rng.choice(maxs)
        if k == 0:
            start = admit + (rng.randint(10 * 60, 45 * 60) if rng.random() < cfg["early_p"] else rng.randint(2 * 3600, 9 * 3600))
        else:
            start = admit + rng.randint(3 * 3600, 20 * 3600)
        dur = int(rng.uniform(*cfg["hours"]) * 3600)
        stop = min(start + dur, end - 3600)
        if stop - start < 3 * 3600:
            stop = start + 3 * 3600
        orders.append((drug, unit, mn, mx, start, stop))

    order_lines = []
    for k, (drug, unit, mn, mx, start, stop) in enumerate(orders):
        oid = f"RX{sid[1:]}{k + 1}"
        vers = [(start, mn, mx)]
        tv = start
        nv = rng.randint(0, 3)
        for _ in range(nv):
            tv = tv + rng.randint(3 * 3600, 20 * 3600)
            if tv > stop - 1800:
                break
            mins, maxs = DRUGS[drug]["limits"][unit]
            nmn, nmx = rng.choice(mins), rng.choice(maxs)
            if (nmn, nmx) == vers[-1][1:]:
                nmx = rng.choice([m for m in maxs if m != nmx])
            vers.append((tv, nmn, nmx))
        for i, (tv, a, b) in enumerate(vers):
            lines.append((tv, 1, f"O|{stamp(tv)}|{sid}|{oid}|{i + 1}|{drug}|{unit}|{fmt(a, 3)}|{fmt(b, 3)}"))
        dc = stop + rng.randint(120, 1200)
        lines.append((dc, 1, f"D|{stamp(dc)}|{oid}"))
        order_lines.append((oid, drug, unit, vers, start, stop, dc))

    # cases planted against the signings just made
    def pick(sgn):
        vis = [w for w in weights if w[3] in SCALE and w[1] <= sgn]
        return max(vis, key=lambda w: w[0]) if vis else None

    signings = sorted(v[0] for ol in order_lines for v in ol[3])
    if rng.random() < cfg["shuffle_p"]:
        # a standing weight taken before the chosen bed weight but charted after it
        for sgn in rng.sample(signings, len(signings)):
            b = pick(sgn)
            if b and b[3] == "BED" and b[1] < sgn - 120 and b[0] - 3 * 3600 > admit + 600:
                tx = b[0] - rng.randint(30 * 60, 3 * 3600)
                fx = rng.randint(b[1] + 30, sgn - 30)
                chart("STAND", tx, true_at(tx) + rng.uniform(-0.4, 0.4), fx)
                break
    if rng.random() < cfg["double_corr_p"]:
        # the weight pharmacy chose is corrected twice; the later figure stands
        for sgn in rng.sample(signings, len(signings)):
            b = pick(sgn)
            if b and b[3] == "BED":
                obs = b[2]
                c1 = rng.randint(b[1] + 600, b[1] + 20 * 3600)
                v1 = b[5] / 10 - (mattress or rng.uniform(2, 5))
                v2 = v1 + rng.choice([-1, 1]) * rng.uniform(0.8, 3.0)
                c2 = c1 + rng.randint(1800, 10 * 3600)
                for at, v in ((c1, v1), (c2, v2)):
                    lines.append((at, 0, f"W|{stamp(at)}|{sid}|{obs}|{stamp(b[0])}|BED|{fmt(round(v * 10), 1)}|C"))
                b[4] = round(v2 * 10)
                break
    if rng.random() < cfg["dry_p"]:
        # the renal team's target charted shortly before a signing
        for sgn in rng.sample(signings, len(signings)):
            b = pick(sgn)
            lo = (b[0] if b else admit) + 120
            hi = sgn - 15 * 60
            if hi - lo > 1800:
                tdry = rng.randint(max(lo, hi - 3 * 3600), hi)
                chart("DRY", tdry, true_kg - rng.uniform(2, 7), tdry + rng.randint(0, 300))
                break

    # pumps
    pev = []   # (time, text)
    used_times = set()

    def tfree(t):
        while t in used_times:
            t += 1
        used_times.add(t)
        return t

    corrections = sorted([(ln[0], ln[2]) for ln in lines if ln[2].startswith("W|") and ln[2].endswith("|C")])
    for oid, drug, unit, vers, start, stop, dc in order_lines:
        used_times.clear()
        for v in vers:
            used_times.add(v[0])
        d = DRUGS[drug]
        punit = unit
        if len(d["units"]) > 1 and rng.random() < cfg["mismatch_p"]:
            punit = next(u for u in d["units"] if u != unit)
        step = d["step"][punit]
        concs = d["conc"]
        c = concs[min(cfg["conc_pick"], len(concs) - 1)] if rng.random() < 0.7 else rng.choice(concs)
        pid = f"P{pumps_used[0]:04d}"
        pumps_used[0] += 1
        chans = [pid + "A", pid + "B"]
        cur = 0
        t = tfree(start + rng.randint(60, 900))
        wkey = round(bed_display(t) if rng.random() < 0.75 else chart_weight_at(t), 1)
        if t < first_bed + 600 and rng.random() < 0.6:
            wkey = float(stated)
        wd = round(wkey * 10)
        # nurse's target in the order's unit, then keyed in the program unit
        omx = vers[0][2]

        def to_prog(od, wd_, punit=punit, unit=unit, step=step):
            # od in thousandths of the order unit -> programmed thousandths in punit
            if punit == unit:
                v = od
            elif punit == "mcg/kg/min":      # order per minute, pump per kg
                v = od * 10 / wd_
            else:                            # order per kg, pump per minute
                v = od * wd_ / 10
            return max(step, round(v / step) * step)

        def is_half(dm_, wd_, c_, punit=punit):
            n, q = rate_parts(punit, dm_, wd_ if punit == "mcg/kg/min" else 0, c_)
            return (2 * n) % (2 * q) == q

        def clean_dose(dm_, wd_, c_, step=step, is_half=is_half):
            # a keyed dose whose rate would sit exactly half way between tenths is keyed one step up
            while is_half(dm_, wd_, c_):
                dm_ += step
            return dm_

        def clean_weight(wd_, dm_, c_, punit=punit, is_half=is_half):
            while punit == "mcg/kg/min" and is_half(dm_, wd_, c_):
                wd_ += 1
            return wd_

        def keyed_rate(dm_, wd_, c_, punit=punit):
            return pump_rate(punit, dm_, wd_ if punit == "mcg/kg/min" else 0, c_)

        target = rng.uniform(0.25, 0.8) * omx
        od_cur = target
        dm = clean_dose(to_prog(target, wd), wd, c)
        ch = chans[cur]
        pev.append((t, f"P|{stamp(t)}|{ch}|START|{oid}|{punit}|{fmt(dm, 3)}|{fmt(wd, 1) if punit == 'mcg/kg/min' else '-'}|{c}"))
        bag_left = cfg["bag_ml"] * rng.uniform(0.75, 0.95)
        running = True
        last = t
        rekeyed = False
        wean_at = stop - rng.randint(20 * 60, 90 * 60)
        while True:
            gap = rng.randint(4 * 60, 45 * 60)
            nt = last + gap
            r10 = keyed_rate(dm, wd, c)
            ml_h = r10 / 10
            # bag runs low?
            t_empty = last + int(bag_left / max(ml_h, 0.1) * 3600) if running else None
            if t_empty is not None and t_empty < nt and t_empty < stop:
                tb = tfree(t_empty)
                bag_left = cfg["bag_ml"] * rng.uniform(0.85, 0.97)
                nc = c if rng.random() < 0.6 else rng.choice(concs)
                if is_half(dm, wd, nc):
                    nc = c
                if rng.random() < cfg["overlap_p"]:
                    other = chans[1 - cur]
                    wk2 = wd
                    if punit == "mcg/kg/min" and rng.random() < 0.5:
                        wk2 = clean_weight(round(round(bed_display(tb), 1) * 10), dm, nc)
                    pev.append((tb, f"P|{stamp(tb)}|{other}|START|{oid}|{punit}|{fmt(dm, 3)}|{fmt(wk2, 1) if punit == 'mcg/kg/min' else '-'}|{nc}"))
                    ts_ = tfree(tb + rng.randint(40, 300))
                    pev.append((ts_, f"P|{stamp(ts_)}|{chans[cur]}|STOP"))
                    cur = 1 - cur
                    wd = wk2
                    c = nc
                    last = ts_
                else:
                    pev.append((tb, f"P|{stamp(tb)}|{chans[cur]}|BAG|{nc}"))
                    c = nc
                    last = tb
                continue
            if nt >= stop:
                break
            if running:
                vol_step = keyed_rate(dm, wd, c) / 10 * (nt - last) / 3600
                bag_left -= vol_step
            ch = chans[cur]
            # a correction filed? maybe re-key the pump weight
            if punit == "mcg/kg/min" and not rekeyed:
                pend = [cc for cc in corrections if last < cc[0] <= nt]
                if pend and rng.random() < cfg["rekey_p"]:
                    tw = tfree(pend[0][0] + rng.randint(300, 3600))
                    if tw < stop - 600:
                        val = dec(pend[0][1].split("|")[6], 1)
                        wd = clean_weight(val, dm, c)
                        pev.append((tw, f"P|{stamp(tw)}|{ch}|WEIGHT|{fmt(wd, 1)}"))
                        rekeyed = True
                        last = tw
                        continue
            if running and rng.random() < cfg["alarm_rate"] and nt + 1000 < stop:
                ta = tfree(nt)
                pev.append((ta, f"P|{stamp(ta)}|{ch}|ALARM|{rng.choice(['OCCL-DOWN', 'OCCL-UP', 'AIR'])}"))
                tr = tfree(ta + rng.randint(30, 900))
                pev.append((tr, f"P|{stamp(tr)}|{ch}|RESUME"))
                last = tr
                continue
            # titrate
            nt = tfree(nt)
            if nt >= wean_at:
                od = od_cur * rng.uniform(0.55, 0.85)
            else:
                vi = max(i for i, v in enumerate(vers) if v[0] <= nt)
                vmn, vmx = vers[vi][1], vers[vi][2]
                u = rng.random()
                if u < 0.1:
                    od = rng.uniform(1.03, 1.25) * vmx          # titrated past the ceiling
                elif u < 0.16:
                    od = rng.uniform(0.6, 0.97) * vmn           # below the floor
                else:
                    od = od_cur * rng.uniform(0.8, 1.25)
                    od = min(max(od, 0.9 * vmn), 1.12 * vmx)
            od_cur = od
            ndm = to_prog(od, wd)
            if ndm == dm:
                ndm = dm + step
            ndm = clean_dose(ndm, wd, c)
            dm = ndm
            pev.append((nt, f"P|{stamp(nt)}|{ch}|DOSE|{fmt(dm, 3)}"))
            last = nt
        tsn = tfree(stop)
        pev.append((tsn, f"P|{stamp(tsn)}|{chans[cur]}|STOP"))
    # the pump server forwards telemetry in batches every five minutes
    for t, text in pev:
        filed = (t // 300 + 1) * 300 + 7
        lines.append((filed, 2, text))
    return lines, end


def generate(cfg, seed):
    rng = random.Random(seed)
    t0 = unstamp(cfg["start"])
    all_lines = []
    pumps_used = [rng.randint(100, 900)]
    for i in range(cfg["stays"]):
        sid = f"S{rng.randint(10, 99)}{i:02d}"
        ls, _ = gen_stay(rng, cfg, sid, t0, pumps_used)
        all_lines.extend(ls)
    if cfg.get("never_ran"):
        # an order signed and then discontinued before any pump was started for it, drawn from
        # its own generator so the rest of the week is unchanged
        r2 = random.Random(seed * 7 + 3)
        firsts = sorted(x for x in all_lines if x[2].startswith("O|") and x[2].split("|")[4] == "1")
        f = firsts[r2.randrange(len(firsts))][2].split("|")
        stay, signed = f[2], unstamp(f[1]) + r2.randint(2 * 3600, 6 * 3600)
        filed = {x[0] for x in all_lines if x[2].startswith("W|") and x[2].split("|")[2] == stay}
        while signed in filed:
            signed += 1
        oid = f"RX{stay[1:]}9"
        all_lines.append((signed, 1, f"O|{stamp(signed)}|{stay}|{oid}|1|norepinephrine|mcg/kg/min|0.02|0.3"))
        dc = signed + r2.randint(15 * 60, 50 * 60)
        all_lines.append((dc, 1, f"D|{stamp(dc)}|{oid}"))
    if cfg.get("per_min_on_kg"):
        all_lines.extend(plant_per_min_on_kg(all_lines, seed))
    all_lines.sort(key=lambda x: (x[0], x[1]))
    return [x[2] for x in all_lines]


def plant_per_min_on_kg(all_lines, seed):
    """An epinephrine order written in mcg/kg/min that the nurse ran on a per-minute program,
    drawn from its own generator so the rest of the week is unchanged. Re-drawn until its own
    figures sit on no boundary (checked on the stay's weights and the new order alone)."""
    for j in range(200):
        r3 = random.Random(seed * 11 + 5 + 1000003 * j)
        firsts = sorted(x for x in all_lines if x[2].startswith("O|") and x[2].split("|")[4] == "1")
        f = firsts[r3.randrange(len(firsts))][2].split("|")
        stay = f[2]
        wlines = [x for x in all_lines if x[2].startswith("W|") and x[2].split("|")[2] == stay]
        stated = next(dec(x[2].split("|")[6], 1) for x in wlines if x[2].split("|")[5] == "STATED")
        filed = {x[0] for x in wlines}
        signed = unstamp(f[1]) + r3.randint(3 * 3600, 8 * 3600)
        while signed in filed:
            signed += 1
        oid, ch = f"RX{stay[1:]}8", f"P9{stay[1:]}A"
        out = [(signed, 1, f"O|{stamp(signed)}|{stay}|{oid}|1|epinephrine|mcg/kg/min|0.02|0.2")]
        t = signed + r3.randint(120, 600)
        first = True
        for target in (0.08, 0.15, 0.24, 0.12, 0.015):
            dm = max(500, round(target * stated / 10 * 1000 / 500) * 500)
            while (2 * 6 * dm) % (2 * 160) == 160:          # never half way between tenths at 16 mcg/mL
                dm += 500
            if first:
                out.append((t, 2, f"P|{stamp(t)}|{ch}|START|{oid}|mcg/min|{fmt(dm, 3)}|-|16"))
                first = False
            else:
                out.append((t, 2, f"P|{stamp(t)}|{ch}|DOSE|{fmt(dm, 3)}"))
            t += r3.randint(15 * 60, 40 * 60)
        out.append((t, 2, f"P|{stamp(t)}|{ch}|STOP"))
        dc = t + r3.randint(60, 300)
        out.append((dc, 1, f"D|{stamp(dc)}|{oid}"))
        mini = [x[2] for x in sorted(wlines + out, key=lambda x: (x[0], x[1]))]
        if degenerate(mini) is None and not boundary_ties(mini):
            return out
    raise RuntimeError("no clean per-minute-on-kg order")


def build(cfg, seed):
    """The first attempt seed from `seed` whose feed holds no degenerate figure."""
    k = 0
    while True:
        lines = generate(cfg, seed * 1000 + k)
        if degenerate(lines) is None and not boundary_ties(lines):
            return lines, seed * 1000 + k
        k += 1


def boundary_ties(lines):
    """Exact ties the float route could land either side of: a dose exactly on a limit, a
    peak or a delivered total exactly on a whole number (checked with the rule book's own
    fractions), and same-second events of one order."""
    if audit_exact_checks(lines):
        return True
    try:
        audit(lines, frozenset({"check_ties"}))
    except ValueError:
        return True
    return False


def audit_exact_checks(lines):
    weights, orders, pump = parse(lines)
    # same-second events per order
    by_order = {}
    chan_order = {}
    for t, _, ch, ev, a in sorted(pump, key=lambda e: (e[0], e[1])):
        if ev == "START":
            chan_order[ch] = a[0]
        o = chan_order[ch]
        by_order.setdefault(o, []).append(t)
    for oid, o in orders.items():
        ts_ = by_order.get(oid, []) + [v[0] for v in o["versions"]]
        if len(ts_) != len(set(ts_)):
            return True
        # nothing runs outside its order
        if by_order.get(oid) and (min(by_order[oid]) < o["versions"][0][0] or max(by_order[oid]) > o["dc"]):
            return True
        # no weight filed at the very second a version is signed
        for v in o["versions"]:
            if any(w["stay"] == o["stay"] and w["first_filed"] == v[0] for w in weights.values()):
                return True
    # no two scale weights of one stay taken at the same second
    seen = set()
    for w in weights.values():
        if w["kind"] in SCALE:
            if (w["stay"], w["taken"]) in seen:
                return True
            seen.add((w["stay"], w["taken"]))
    return False


SETS = ["sample", "hidden", "edge", "example"]
SEEDS = {"sample": 4411, "hidden": 7302, "edge": 5158, "example": 2039}


def report_line(rep):
    return json.dumps({"orders": {k: rep[k] for k in sorted(rep)}}, separators=(",", ":")) + "\n"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="\n") as fh:
        fh.write(text)


def main():
    """Writes the engine's feed for the agent's week, the verifier's three feeds and their reports,
    and the signed example week the project ships."""
    for name in SETS:
        lines, used = build(CONFIGS[name], SEEDS[name])
        rep = audit(lines)
        feed = "".join(ln + "\n" for ln in lines)
        if name == "example":
            write(os.path.join(TASK, "environment/app/audit/examples/feed_20251103.txt"), feed)
            write(os.path.join(TASK, "environment/app/audit/examples/report_20251103.json"), report_line(rep))
        else:
            write(os.path.join(TASK, f"tests/data/{name}/feed.txt"), feed)
            write(os.path.join(TASK, f"tests/golden/{name}.json"), report_line(rep))
            if name == "sample":
                write(os.path.join(TASK, "environment/engine/data/feed.txt"), feed)
        print(f"{name}: seed {used}, {len(lines)} lines, {len(rep)} orders", file=sys.stderr)


if __name__ == "__main__":
    main()
