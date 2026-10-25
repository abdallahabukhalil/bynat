"""PERF-04: one-pass hierarchical IQR for `handle_outliers` and `drop_outliers`."""

import argparse

import numpy as np
import pandas as pd
from _common import best_of, print_table
from test_equivalence import _legacy_drop_outliers, _legacy_handle_outliers

from bynat.cleaning import stats

HIERARCHY = ["country", "title", "company"]


def make_frame(rows, seed=0):
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(
        {
            "country": rng.choice([f"c{i}" for i in range(30)], size=rows),
            "title": rng.choice([f"t{i}" for i in range(200)], size=rows),
            "company": rng.choice([f"k{i}" for i in range(2_000)], size=rows),
            "salary": rng.lognormal(10, 1, size=rows),
            "bonus": rng.normal(100, 30, size=rows),
        }
    )
    frame.loc[rng.random(rows) < 0.05, "salary"] = np.nan
    return frame


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", type=int, nargs="+", default=[10_000, 100_000, 1_000_000])
    args = parser.parse_args()

    rows = []

    for size in args.sizes:
        frame = make_frame(size)

        for name, current_func, legacy_func in (
            ("handle", stats.handle_outliers, _legacy_handle_outliers),
            ("drop", stats.drop_outliers, _legacy_drop_outliers),
        ):
            current, current_time = best_of(lambda: current_func(frame, "salary", "bonus", hierarchy_cols=HIERARCHY))
            legacy, legacy_time = best_of(
                lambda: legacy_func(frame, "salary", "bonus", hierarchy_cols=HIERARCHY), repeat=1
            )
            pd.testing.assert_frame_equal(current, legacy)
            rows.append((f"{size:>9,} rows {name}", legacy_time, current_time))

    print_table(rows)


if __name__ == "__main__":
    main()
