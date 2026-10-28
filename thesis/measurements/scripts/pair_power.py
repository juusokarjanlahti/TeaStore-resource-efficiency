#!/usr/bin/env python3
"""How many pairs the fresh-JVM cache series needs: power of the paired comparison.

In the fresh-JVM series (SETUP.md, D6) every pair runs on JVM instances of its own, so the
difference within a pair carries the difference between two instances on top of the cache
effect. This simulates the test aggregate.py applies, the exact two-sided Wilcoxon
signed-rank test, on normally distributed pair differences, and prints how often it reaches
p < 0.05. It also prints the expected half-width of the 95 % confidence interval for the
mean difference, which is what the result can be read to.

The inputs are planning figures, fixed on 6.10.2026 before any data of the series existed:
  --effect  1.6 % is the session 1 cache series, which ran one instance per configuration
            and so cannot tell the effect from the instance difference; 1.0 % is a smaller
            effect that the 1.10.2026 budget sweep leaves possible.
  --sd      In the sweep, two instances of one configuration differed by 1.5 % (3 MiB,
            session 1 vs the sweep) and by 3.4 % (8 vs 16 MiB, identical work), which puts
            the SD of the difference of two instances near 2.6 %; with run-to-run noise of
            about 0.4 % per run, near 2.75 %. 2.0 % is the optimistic end.
The decision they fed is in KUVIOT_JA_TAULUKOT.md, section 3.

    python pair_power.py
    python pair_power.py --pairs 10 20 30 --sd 2.75 --effect 1.0 1.6
"""

import argparse
import math
import random

from aggregate import t_critical, wilcoxon_exact

ALPHA = 0.05


def power(pairs, effect, sd, trials, rng):
    """Share of simulated series of `pairs` differences, mean -effect, in which the test
    rejects at ALPHA."""
    rejected = 0
    for _ in range(trials):
        differences = [rng.gauss(-effect, sd) for _ in range(pairs)]
        if wilcoxon_exact(differences) < ALPHA:
            rejected += 1
    return rejected / trials


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pairs", type=int, nargs="+", default=[10, 15, 20])
    parser.add_argument("--sd", type=float, nargs="+", default=[2.0, 2.75],
                        help="SD of a pair's difference, %% of the baseline")
    parser.add_argument("--effect", type=float, nargs="+", default=[1.0, 1.6],
                        help="true saving, %% of the baseline")
    parser.add_argument("--trials", type=int, default=4000, help="simulated series per cell")
    parser.add_argument("--seed", type=int, default=20261006)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    columns = "".join(f"{f'{n} pairs':>10}" for n in args.pairs)
    print(f"Power of the exact two-sided Wilcoxon signed-rank test at alpha {ALPHA}, "
          f"{args.trials} simulated series per cell\n")
    print(f"{'SD [%]':>8}{'effect [%]':>12}{columns}")
    for sd in args.sd:
        for effect in args.effect:
            cells = "".join(f"{power(n, effect, sd, args.trials, rng):>10.0%}" for n in args.pairs)
            print(f"{sd:>8.2f}{effect:>12.2f}{cells}")

    print("\nExpected half-width of the 95 % CI for the mean difference [%]\n")
    print(f"{'SD [%]':>8}{'':>12}{columns}")
    for sd in args.sd:
        cells = "".join(f"{t_critical(n - 1) * sd / math.sqrt(n):>10.2f}" for n in args.pairs)
        print(f"{sd:>8.2f}{'':>12}{cells}")


if __name__ == "__main__":
    main()
