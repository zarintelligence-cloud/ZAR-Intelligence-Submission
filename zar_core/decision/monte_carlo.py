from __future__ import annotations
import random
from collections.abc import Callable


class MonteCarlo:
    def run(self, simulate: Callable[[random.Random], float], success: Callable[[float], bool],
            n: int = 5000, seed: int = 42) -> dict[str, float]:
        rng = random.Random(seed)
        values = sorted(simulate(rng) for _ in range(n))
        q = lambda p: values[min(n - 1, int(p * n))]
        return {"runs": n, "success_rate": sum(success(v) for v in values) / n,
                "mean": sum(values) / n, "p50": q(0.5), "p80": q(0.8), "p90": q(0.9),
                "min": values[0], "max": values[-1]}
