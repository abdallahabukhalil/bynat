"""PERF-02: `extract_keywords` on unique texts with one combined pattern."""

import argparse

import numpy as np
import pandas as pd
from _common import best_of, print_table
from test_equivalence import _legacy_extract_keywords

from bynat.cleaning import text

KEYWORDS = [
    "python", "java", "c++", ".net", "sql", "excel", "data analyst", "data",
    "machine learning", "aws", "docker", "kubernetes", "react", "node", "go",
]
WORDS = KEYWORDS + ["senior", "junior", "engineer", "developer", "remote", "team", "the", "and"]


def make_frame(rows, unique_texts=5_000, seed=0):
    rng = np.random.default_rng(seed)
    pool = [" ".join(rng.choice(WORDS, size=rng.integers(3, 9))) for _ in range(unique_texts)]
    return pd.DataFrame({"title": rng.choice(pool, size=rows), "body": rng.choice(pool, size=rows)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", type=int, nargs="+", default=[10_000, 100_000, 1_000_000])
    parser.add_argument("--legacy-max", type=int, default=1_000_000, help="skip the 0.3.0 run above this size")
    args = parser.parse_args()

    rows = []

    for size in args.sizes:
        frame = make_frame(size)

        for remove in (False, True):
            label = f"{size:>9,} rows remove={remove}"
            current, current_time = best_of(
                lambda: text.extract_keywords(frame, "title", "body", remove_extracted=remove, skill=KEYWORDS)
            )
            legacy_time = None

            if size <= args.legacy_max:
                legacy, legacy_time = best_of(
                    lambda: _legacy_extract_keywords(frame, "title", "body", remove_extracted=remove, skill=KEYWORDS),
                    repeat=1,
                )
                pd.testing.assert_frame_equal(current, legacy)

            rows.append((label, legacy_time, current_time))

    print_table(rows)


if __name__ == "__main__":
    main()
