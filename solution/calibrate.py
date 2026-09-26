#!/usr/bin/env python3
"""Measures every misreading the rule book can express: for each set's generating constants and
eight fresh seeds, how many graded fields (delivered_mcg, over_s, under_s, peak per order) move
when that one reading is switched. A misreading is separated when it moves at least one field on
every seed."""
import sys

import generate_feed as g

FLAGS = ["rate_unrounded", "rate_truncated", "rate_dosing_weight", "pump_dose_as_dose",
         "weight_holds_rate", "bag_holds_rate", "alarm_ignored", "channels_not_summed",
         "limits_retroactive", "latest_charted_weight", "late_charting_counted", "latest_filed",
         "dry_is_scale", "correction_ignored", "correction_from_filing", "first_correction_wins",
         "first_version_weight", "no_stated_fallback", "under_when_idle",
         "delivered_floor_per_interval", "peak_rounded"]


def moved(a, b):
    n = 0
    for k in a:
        for f in a[k]:
            n += a[k][f] != b[k][f]
    return n


def main():
    sets = sys.argv[1:] or ["sample", "hidden", "edge", "example"]
    for name in sets:
        cfg = g.CONFIGS[name]
        rows = {f: [] for f in FLAGS}
        nfields = []
        for s in range(8):
            lines, _ = g.build(cfg, 90000 + 17 * s + len(name))
            ref = g.audit(lines)
            nfields.append(4 * len(ref))
            for f in FLAGS:
                rows[f].append(moved(ref, g.audit(lines, frozenset({f}))))
        print(f"== {name}: fields per seed {nfields}")
        for f in FLAGS:
            r = rows[f]
            print(f"  {f:30s} min {min(r):4d}  zero-seeds {sum(1 for x in r if x == 0)}  {r}")


if __name__ == "__main__":
    main()
