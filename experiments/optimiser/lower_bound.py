"""Perfect-foresight benchmark for the cooling electricity over ARBITRARY schedules.

The thermostat policies of the paper are a two-parameter family (setpoint, two pre-cooling depths in fixed windows).
To show how much any other schedule could save, this module solves the linear program

    minimise    sum_t dt sum_i  P_i(q_it, T_a(t))                                (electricity of the units)
    subject to  C (x_t - x_t-1)/dt = b(t) - A x_t - E q_t                       (implicit Euler, every node)
                x_0 = x_T                                                       (periodic horizon)
                v_min <= V_i,t <= ceiling,  0 <= q_it <= Q_i phi_cap(T_a)       (air nodes of conditioned rooms)

over every unit's heat removal q_it at every time step, with full knowledge of the weather. x stacks the air and
structure temperatures of every zone (the two-node zones of hvac_savings.model, with the same partitions, ground,
solar and sky drives); E applies each unit to its room's air node. P_i is the convex hull of the four modes (off, low,
medium, high): a piecewise-linear function whose incremental slopes are the extra electricity per kW of heat removed
between consecutive modes (they increase because the higher modes are less efficient). The LP may run any mode at any
time, pre-cool any room down to v_min at the hours of highest COP, and coordinate the rooms. It is idealised: modes
modulate continuously, with no cycling losses, standby or thermostat differential, and the buoyancy-driven door
exchange is linearised about a temperature difference dT0 (k |dT|^1.5 -> k sqrt(dT0) dT).

The reference is the same linear model held at the ceiling without pre-cooling (each conditioned room at the ceiling
whenever it needs cooling, floating below it otherwise; the limit of vanishing ripple). The gap
(E_hold - E_LP) / E_hold is what an ideal schedule could save over holding the ceiling with the same idealised units.
Horizons: a periodic design day (the mean or hottest day of a month), or a run of consecutive days (periodic over
the run), 15-min steps.
"""
import numpy as np
from scipy.optimize import linprog
import time
from scipy import sparse
from hvac_savings import model as M


def _drives(city, env, month, kind="mean", days=None):
    """Hourly outdoor temperature and the nodal drives (air, structure) for the horizon: the mean or hottest day of a
    month (kind), or the consecutive days `days` (0-based day indices of the month)."""
    REP0, MS0 = M.REP, M.MSCALE
    try:
        M.REP = "all"; M.MSCALE = np.ones(12)
        Ta, moh, Wa, Iv, ghi = M.ambient(city); S_opq, S_win, S_skw = M.solar_drive(city, env)
    finally:
        M.REP, M.MSCALE = REP0, MS0
    sel = np.where(moh == month)[0]; n = len(sel) // 24
    rs = lambda a: a[sel].reshape(n, 24, *a.shape[1:])
    T, So, Sw, Sk = rs(Ta), rs(S_opq), rs(S_win), rs(S_skw)
    if days is not None:
        d = list(days); return (T[d].reshape(-1), So[d].reshape(-1, M.NZ), Sw[d].reshape(-1, M.NZ), Sk[d].reshape(-1, M.NZ))
    if kind == "hottest":
        k = int(T.mean(1).argmax()); return T[k], So[k], Sw[k], Sk[k]
    return T.mean(0), So.mean(0), Sw.mean(0), Sk.mean(0)


def _system(city, env, month, dT0):
    """Linear two-node model: A (2NZ x 2NZ) such that the net loss of every node is A x, and the drive b(h) [kW]."""
    e = M.ENVELOPES[env]; zm = M.TWO_NODE
    G_O = M.glazing(); Awin = (G_O * M.A_EXT_O).sum(1)
    k_inf = M.ACH * M.AREA * M.H_AIR * M._b["air_density"] * M._b["air_cp_kJkgK"] / 3600
    K_win = e['U_WIN'] * Awin / 1000 + k_inf
    K_opq = e['U_WALL'] * (M.A_EXT - Awin) / 1000 + M.is_roof * M.AREA * e['U_ROOF'] / 1000
    Kgr = M.is_grnd * M.U_GRND_ISO * M.AREA / 1000; Tg = M.ground_temperature(city)[month]
    K_am = zm["k_am_W_m2K"] * M.AREA / 1000; f = zm["radiant_fraction"]
    AVV = np.diag(K_win + K_am)
    if M.DOORS.shape[1]:
        AVV = AVV + M.DOORS @ np.diag(M.K_DOOR * np.sqrt(dT0)) @ M.DOORS.T
    AWW = np.diag(K_opq + Kgr + M.Krow + K_am) - M.Kc
    A = np.block([[AVV, -np.diag(K_am)], [-np.diag(K_am), AWW]])
    C = np.r_[zm["air_fraction"] * M.CZ, (1 - zm["air_fraction"]) * M.CZ]
    gain_w = M.GAINF * M.AREA / M.A_REF; ph = M._sch["gain_solar_phase_hr"]
    def b(Tah, So, Sw, Sk, hr):
        g = (M.GAINb + M.GAINd * max(np.sin((hr - ph) / 24 * 2 * np.pi), 0)) * gain_w
        return np.r_[K_win * Tah - Sk + (1 - f) * g, K_opq * Tah + So + Kgr * Tg + Sw + f * g]
    return A, C, b


def _segments(cf):
    """Convex electricity of heat removal: segment widths (fractions of Q) and incremental slopes (kW_e per kW_th)."""
    f = M.FR[1:]; p = f / (M.COOLc[1:] * cf)                  # electricity per kW of capacity at each mode
    fr = np.r_[0, f]; pw = np.r_[0, p]
    slopes = np.diff(pw) / np.diff(fr); assert np.all(np.diff(slopes) >= -1e-12), "mode efficiencies not ordered"
    return np.diff(fr), slopes


def solve(city, env="compliant", month=6, kind="mean", days=None, dT0=0.5, steps_per_hour=4, ceiling=None, vmin=None,
          gamma=None, iters=6, tol_rel=1e-4, method="slp", slp_iters=8, trust0=1.0, trust_min=0.02):
    """Return the benchmark E_LP and the hold-at-ceiling energy E_hold [kWh_e per day] of the current scope (M.set_scope).
    vmin: lowest temperature a conditioned room may be pre-cooled to (default: bottom of the safe set).
    gamma: fractional COP loss per K of indoor air below the 27 degC rating condition (lower evaporating temperature),
    the same factor as in the simulation engine (default: model.GAMMA_IN); handled by fixed-point iteration on the LP
    solution (gamma = 0: COP depends on the outdoor air only). With gamma > 0 the program is not linear; it is
    re-solved with the indoor factor evaluated at the previous solution (at most `iters` solves, or until the objective
    changes by less than tol_rel), and every iterate's schedule is priced with its own indoor factor. The cheapest of these
    schedules is feasible for the idealised model, so its gap is an achievable saving (a lower bound on the optimal
    saving at that gamma). With gamma = 0 the gap is exact for gamma = 0 and an upper bound on the optimal saving at
    any gamma >= 0: g(V) is non-decreasing and V <= ceiling, so fixing g at g(ceiling) lowers every cost, and the
    reference, held at the ceiling, scales by the same factor. The two bracket the optimal saving at gamma > 0."""
    gamma = M.GAMMA_IN if gamma is None else gamma
    gin = lambda V: np.clip(1.0 - gamma * (M._copi["ref_C"] - V), M._copi["lo"], M._copi["hi"])
    ceiling = M.COMFORT[1] if ceiling is None else ceiling; vmin = M.SAFE[0] if vmin is None else vmin
    Ta, So, Sw, Sk = _drives(city, env, month, kind, days); H = len(Ta)
    A, C, bfun = _system(city, env, month, dT0)
    nz = M.NZ; nx = 2 * nz; cz = np.where(M.COND)[0]; nc = len(cz); dt = 1.0 / steps_per_hour; T = H * steps_per_hour
    hidx = np.repeat(np.arange(H), steps_per_hour)
    Bt = np.array([bfun(Ta[h], So[h], Sw[h], Sk[h], h % 24) for h in hidx])          # (T, nx) drive, stepwise per hour
    cf = M.cop_factor(Ta[hidx]); capf = M.cap_factor(Ta[hidx]) if M.CAP_DERATE else np.ones(T)
    widths, slopes = zip(*[_segments(c) for c in cf]); widths = np.array(widths); slopes = np.array(slopes)  # (T, 3)
    nseg = widths.shape[1]
    nV = T * nx
    It = sparse.identity(T, format="csr"); Sh = sparse.csr_matrix((np.ones(T), (np.arange(T), (np.arange(T) - 1) % T)), shape=(T, T))
    Cd = sparse.diags(C / dt)
    AV = sparse.kron(It, Cd + sparse.csr_matrix(A)) - sparse.kron(Sh, Cd)
    Ez = sparse.csr_matrix((np.ones(nc), (cz, np.arange(nc))), shape=(nx, nc))       # unit k acts on the air node of cz[k]
    Aq = sparse.kron(It, sparse.kron(Ez, np.ones((1, nseg))))
    Aeq = sparse.hstack([AV, Aq]).tocsr(); beq = Bt.ravel()
    lo = np.full(nV, -np.inf); hi = np.full(nV, np.inf)
    lo.reshape(T, nx)[:, cz] = vmin; hi.reshape(T, nx)[:, cz] = ceiling
    Qc = M.QC[cz][None, :] * capf[:, None]                                           # (T, nc) available capacity
    qh = widths[:, None, :] * Qc[:, :, None]
    cost_q = (slopes * dt)[:, None, :] * np.ones((1, nc, 1))
    bounds = np.c_[np.r_[lo, np.zeros(qh.size)], np.r_[hi, qh.ravel()]]
    pen = np.full((T, nc), float(gin(ceiling)))
    t0 = time.perf_counter(); n_solves = 0; hist = []; converged = not gamma; best = None
    for it in range((min(iters, 2) if method == "slp" else iters) if gamma else 1):
        c_it = np.r_[np.zeros(nV), (cost_q / pen[:, :, None]).ravel()]
        lp = linprog(c_it, A_eq=Aeq, b_eq=beq, bounds=bounds, method="highs-ipm")   # interior point: much faster than simplex on these sparse, banded programs
        n_solves += 1
        if lp.status != 0:
            raise RuntimeError(f"LP failed: {lp.message}")
        Xi = lp.x[:nV].reshape(T, nx); qi = lp.x[nV:].reshape(T, nc, nseg)
        E_true = float((qi * cost_q / gin(Xi[:, cz])[:, :, None]).sum())   # this schedule priced with its own indoor factor
        if best is None or E_true < best[0]:
            best = (E_true, Xi, qi)
        dJ = abs(lp.fun - hist[-1]) / max(abs(lp.fun), 1e-12) if hist else np.inf
        hist.append(float(lp.fun))
        if gamma and dJ < tol_rel:
            converged = True; break
        pen = gin(Xi[:, cz])                                          # COP multiplier at the room's air temperature
    if gamma and method == "slp":
        # sequential linear programming on the true cost sum s q / g(V), from the best iterate so far: first-order model in
        # (q, V) about the incumbent, V confined to a trust region; a step is kept only if the exactly priced cost falls
        Vidx = (np.arange(T)[:, None] * nx + cz[None, :])                       # positions of the conditioned air nodes in x
        seeds = [best]
        b_h = bounds.copy(); b_h[Vidx.ravel(), 0] = ceiling - 0.01            # second seed: no pre-cooling (rooms near the ceiling)
        lp = linprog(np.r_[np.zeros(nV), (cost_q / float(gin(ceiling))).ravel()], A_eq=Aeq, b_eq=beq, bounds=b_h, method="highs-ipm"); n_solves += 1
        if lp.status == 0:
            Xh = lp.x[:nV].reshape(T, nx); qh_ = lp.x[nV:].reshape(T, nc, nseg)
            seeds.append((float((qh_ * cost_q / gin(Xh[:, cz])[:, :, None]).sum()), Xh, qh_))
        results = []; slp_hist = []
        for seed in seeds:
            E0, Xc, qc = seed; delta = trust0
            slp_hist.append(E0 / (H / 24))
            for k in range(slp_iters):
                g0 = gin(Xc[:, cz])
                grad_V = -gamma * (qc * cost_q).sum(2) / g0 ** 2                      # d/dV of sum_s s q / g(V) at the incumbent
                cV = np.zeros(nV); cV[Vidx.ravel()] = grad_V.ravel()
                c_k = np.r_[cV, (cost_q / g0[:, :, None]).ravel()]
                b_k = bounds.copy()
                lo_k = np.maximum(vmin, Xc[:, cz] - delta); hi_k = np.minimum(ceiling, Xc[:, cz] + delta)
                b_k[Vidx.ravel(), 0] = lo_k.ravel(); b_k[Vidx.ravel(), 1] = hi_k.ravel()
                lp = linprog(c_k, A_eq=Aeq, b_eq=beq, bounds=b_k, method="highs-ipm"); n_solves += 1
                if lp.status != 0:
                    delta *= 0.5
                    if delta < trust_min: break
                    continue
                Xn = lp.x[:nV].reshape(T, nx); qn = lp.x[nV:].reshape(T, nc, nseg)
                En = float((qn * cost_q / gin(Xn[:, cz])[:, :, None]).sum())
                if En < E0 - 1e-9 * abs(E0):
                    E0, Xc, qc = En, Xn, qn; delta = min(2 * delta, ceiling - vmin)
                else:
                    delta *= 0.5
                slp_hist.append(E0 / (H / 24))
                if delta < trust_min: break
            results.append((E0, Xc, qc))
        best = min(results, key=lambda r_: r_[0]); hist = hist + slp_hist
    t_solve = time.perf_counter() - t0
    E_best, X, qs_best = best
    nd = H / 24
    E_lp = E_best / nd
    E_hold, _ = _hold(A, Bt, C, cz, ceiling, dt, widths, slopes, Qc)
    E_hold /= nd * float(gin(ceiling))                               # held at the ceiling: constant indoor COP factor
    qs = qs_best
    prof = X[:, cz].mean(1).reshape(-1, 24, steps_per_hour).mean((0, 2))           # mean daily profile of the conditioned air
    qh_ = qs.sum(2).mean(1).reshape(-1, 24, steps_per_hour).mean((0, 2))
    return dict(E_lp=E_lp, E_hold=E_hold, gap_pct=100 * (E_hold - E_lp) / E_hold,
                high_mode_share=float(qs[:, :, 1:].sum() / max(qs.sum(), 1e-12)), V_profile=prof.round(2).tolist(),
                q_profile=qh_.round(3).tolist(), lp_min_C=float(X[:, cz].min()),
                lp_precool_K_h=float(np.clip(ceiling - X[:, cz], 0, None).sum() * dt / nc / nd),
                n_vars=int(len(c_it)), n_constraints=int(Aeq.shape[0]), n_solves=n_solves, t_solve_s=t_solve,
                converged=bool(converged), objective_history=[h / nd for h in hist])


def _cost(q, Q, w, s):
    """Electricity [kW] of removing q with the convex mode hull (segments filled in order)."""
    e = 0.0; rem = q.copy()
    for k in range(len(s)):
        take = np.minimum(rem, w[k] * Q); e += (take * s[k]).sum(); rem -= take
    return e


def _hold(A, Bt, C, cz, ceiling, dt, widths, slopes, Qc, sweeps=6):
    """The linear model with every conditioned room's air held at the ceiling whenever it needs cooling (active-set
    solve per step), iterated over the horizon until periodic."""
    nx = A.shape[0]; T = Bt.shape[0]; x = np.full(nx, ceiling); E = 0.0
    Cdt = np.diag(C / dt); Mx = Cdt + A
    held_prev = np.ones(len(cz), bool)
    for d in range(sweeps):
        E = 0.0
        for t in range(T):
            held = held_prev.copy()
            for _ in range(len(cz) + 1):
                hz = cz[held]; free = np.setdiff1d(np.arange(nx), hz)
                rhs = C / dt * x + Bt[t]
                rhs_f = rhs[free] - Mx[np.ix_(free, hz)] @ np.full(len(hz), ceiling)
                xf = np.linalg.solve(Mx[np.ix_(free, free)], rhs_f)
                xn = np.empty(nx); xn[free] = xf; xn[hz] = ceiling
                q = rhs[hz] - Mx[hz] @ xn                     # heat the held rooms' units must remove
                neg = q < 0
                over = np.zeros(len(cz), bool); over[~held] = xn[cz[~held]] > ceiling + 1e-9
                if not neg.any() and not over.any():
                    break
                hidx = np.where(held)[0]; held[hidx[neg]] = False; held[over] = True
            held_prev = held.copy()
            qq = np.zeros(len(cz)); qq[held] = np.clip(q, 0, None)
            if (qq > Qc[t] + 1e-9).any():
                raise RuntimeError("capacity exceeded while holding the ceiling")
            E += _cost(qq, Qc[t], widths[t], slopes[t]) * dt
            x = xn
        if T > 24 * 8:      # long horizons: one warm-up sweep and one counted sweep suffice
            if d >= 1: break
    return E, x
