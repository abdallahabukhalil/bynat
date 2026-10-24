# Benchmarks

Each script times the current implementation against the 0.3.0 algorithm
kept in `tests/test_equivalence.py`, checks that both give the same result,
and prints a table. Record the numbers in the pull request, not in the code.

```bash
python benchmarks/bench_extract_keywords.py            # 10k, 100k, 1M rows
python benchmarks/bench_outliers.py
python benchmarks/bench_extract_keywords.py --sizes 10000 --legacy-max 10000
```
