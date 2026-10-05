#!/usr/bin/env python3
"""
optimiser.py -- comparison of the thermostat pre-cooling policies (Algorithm 2 of the paper, CompareThermostatPolicies).

The paper's optimisation method is the perfect-foresight optimal-control program of lower_bound.py; the thermostat
policies below are evaluated and compared, not searched.

The scheduling policy theta = (night_precool, prepeak_precool) is searched over the depths a thermostat with
0.5 K setpoint steps can realise, each 0-3 K below the reference cooling setpoint (experiments.json, precool_grid):
a 7 x 7 lattice of 49 distinct policies, (0, 0) being no pre-cooling. The deepest depth puts the setpoint at
20.5 degC, so with the thermostat's 0.5 K switching margin no room falls below the 20 degC floor of the safe set.
Every policy is simulated (in one batched run), so the optimum over the lattice is exact.

    python -m experiments.optimiser.optimiser            # prints the lattice optimum for each city and envelope

All physics come from model.py, which is config-driven (see experiments/configs/).
"""
import json
from pathlib import Path
import numpy as np
from hvac_savings import model as M

_EXP = json.load(open(Path(__file__).resolve().parents[2] / "experiments" / "configs" / "experiments.json"))
DEPTHS = tuple(_EXP["precool_grid"]["night_depth"])
EPS_KH = 1e-6         # numerical zero [K.h/yr]: round-off only, not a comfort tolerance


def admissible(r):
    """A policy is admissible only if every conditioned room stays within the safe set [20, 24] degC at every step of the
    simulated year: no excursion above the 24 degC ceiling (C2) and none below the 20 degC floor (C3)."""
    return r["cviol_hot"] <= EPS_KH and r.get("sviol_cold", 0.0) <= EPS_KH


def evaluate_policy(policies, city, env, tariff="volume"):
    """Algorithm 1 (EvaluatePolicy): annual cost, energy and comfort of one or more policies, simulated together.
    Thin alias of model.simulate_batch, which implements the algorithm line by line."""
    return M.simulate_batch(city, env, policies, tariff)


def optimise(city, env, tariff="volume", depths=DEPTHS):
    """Algorithm 2 (CompareThermostatPolicies), exactly as printed in the paper: every policy of the pre-cooling lattice
    (depths x depths, K) is evaluated, inadmissible policies (any excursion outside the safe set) are excluded, and the
    cheapest admissible policy is returned (None if no policy is admissible)."""
    cs, hs = M.CODE[0], M.CODE[1]
    grid = [(a, b) for a in depths for b in depths]                      # (night, pre-peak); (0, 0) = no scheduling
    R = evaluate_policy([[cs, hs, a, b] for a, b in grid], city, env, tariff)
    theta, best = None, np.inf
    for (a, b), r in zip(grid, R):
        if admissible(r) and r["bill"] < best:
            best, theta = r["bill"], (a, b)
    return dict(theta=theta, bill=float(best) if theta is not None else None, baseline_bill=float(R[grid.index((0, 0))]["bill"]),
                n_policies=len(grid), n_feasible=int(sum(admissible(r) for r in R)))


def main():
    for env in ("compliant", "noncompliant"):
        for city in ("Riyadh", "Jeddah"):
            o = optimise(city, env)
            print(f"{env:13s} {city:7s} theta={o['theta']}  bill={o['bill']:.0f} SAR  ({o['n_feasible']}/{o['n_policies']} feasible)")


if __name__ == "__main__":
    main()
