"""
Aether Crystal Options Challenge — Monte Carlo Simulator
=========================================================
Underlying: GBM with zero risk-neutral drift, sigma = 251% annualized
Time grid: 4 steps per trading day, 252 days/year
Spot: 50

Products:
  - AC                : underlying
  - AC_50_P  (3w)     : vanilla put,  K=50, T=21d
  - AC_50_C  (3w)     : vanilla call, K=50, T=21d
  - AC_35_P  (3w)     : vanilla put,  K=35, T=21d
  - AC_40_P  (3w)     : vanilla put,  K=40, T=21d
  - AC_45_P  (3w)     : vanilla put,  K=45, T=21d
  - AC_60_C  (3w)     : vanilla call, K=60, T=21d
  - AC_50_P_2 (2w)    : vanilla put,  K=50, T=14d
  - AC_50_C_2 (2w)    : vanilla call, K=50, T=14d
  - AC_50_CO          : chooser, K=50, choose at 14d, expires 21d
  - AC_40_BP          : binary put,   K=40, payoff 10, T=21d
  - AC_45_KO          : down-and-out put, K=45, barrier=35, T=21d
"""

import numpy as np
from scipy.stats import norm

# ---------- Market parameters ----------
S0 = 50.0
SIGMA = 2.51          # 251% annualized
R = 0.0               # zero risk-neutral drift
DAYS_PER_YEAR = 252
STEPS_PER_DAY = 4
DT = 1.0 / (DAYS_PER_YEAR * STEPS_PER_DAY)   # one simulation step in years

# ---------- Black-Scholes (r=0) ----------
def bs_call(S, K, T, sigma):
    if T <= 0:
        return max(S - K, 0.0)
    sT = sigma * np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * sT * sT) / sT
    d2 = d1 - sT
    return S * norm.cdf(d1) - K * norm.cdf(d2)

def bs_put(S, K, T, sigma):
    if T <= 0:
        return max(K - S, 0.0)
    sT = sigma * np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * sT * sT) / sT
    d2 = d1 - sT
    return K * norm.cdf(-d2) - S * norm.cdf(-d1)

def bs_call_delta(S, K, T, sigma):
    if T <= 0:
        return 1.0 if S > K else 0.0
    sT = sigma * np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * sT * sT) / sT
    return norm.cdf(d1)

def bs_put_delta(S, K, T, sigma):
    return bs_call_delta(S, K, T, sigma) - 1.0

# ---------- Path simulation ----------
def simulate_paths(n_paths, n_days=21, seed=None):
    """Simulate n_paths GBM paths over n_days trading days, with STEPS_PER_DAY substeps.
       Returns array of shape (n_paths, n_days*STEPS_PER_DAY + 1)."""
    rng = np.random.default_rng(seed)
    n_steps = n_days * STEPS_PER_DAY
    z = rng.standard_normal((n_paths, n_steps))
    increments = (-0.5 * SIGMA * SIGMA * DT) + SIGMA * np.sqrt(DT) * z
    log_paths = np.concatenate(
        [np.zeros((n_paths, 1)), np.cumsum(increments, axis=1)], axis=1
    )
    return S0 * np.exp(log_paths)

# ---------- Payoff functions for each product ----------
def payoff_vanilla_call(paths, K, expiry_step):
    return np.maximum(paths[:, expiry_step] - K, 0.0)

def payoff_vanilla_put(paths, K, expiry_step):
    return np.maximum(K - paths[:, expiry_step], 0.0)

def payoff_chooser(paths, K, choose_step, expiry_step):
    """At choose_step, holder picks call or put (whichever has higher value).
       For r=0 with remaining time T_rem, value of call = bs_call, value of put = bs_put.
       Then realised payoff at expiry_step is the max(S-K,0) or max(K-S,0)."""
    S_choose = paths[:, choose_step]
    S_expiry = paths[:, expiry_step]
    T_rem = (expiry_step - choose_step) / (DAYS_PER_YEAR * STEPS_PER_DAY)
    call_val = np.array([bs_call(s, K, T_rem, SIGMA) for s in S_choose])
    put_val  = np.array([bs_put(s,  K, T_rem, SIGMA) for s in S_choose])
    pick_call = call_val >= put_val
    payoff = np.where(pick_call,
                      np.maximum(S_expiry - K, 0.0),
                      np.maximum(K - S_expiry, 0.0))
    return payoff

def payoff_binary_put(paths, K, amount, expiry_step):
    return np.where(paths[:, expiry_step] < K, amount, 0.0)

def payoff_knockout_put(paths, K, barrier, expiry_step):
    """Down-and-out put: zero if min path <= barrier any time before expiry, else vanilla put."""
    min_path = np.min(paths[:, :expiry_step + 1], axis=1)
    knocked_out = min_path <= barrier
    vanilla = np.maximum(K - paths[:, expiry_step], 0.0)
    return np.where(knocked_out, 0.0, vanilla)

# ---------- Pricing via Monte Carlo ----------
def price_all(n_paths=200_000, seed=42):
    paths = simulate_paths(n_paths, n_days=21, seed=seed)

    step_14 = 14 * STEPS_PER_DAY
    step_21 = 21 * STEPS_PER_DAY

    prices = {}
    prices['AC']         = paths[:, 0].mean()  # = 50 by construction
    prices['AC_50_P']    = payoff_vanilla_put(paths, 50, step_21).mean()
    prices['AC_50_C']    = payoff_vanilla_call(paths, 50, step_21).mean()
    prices['AC_35_P']    = payoff_vanilla_put(paths, 35, step_21).mean()
    prices['AC_40_P']    = payoff_vanilla_put(paths, 40, step_21).mean()
    prices['AC_45_P']    = payoff_vanilla_put(paths, 45, step_21).mean()
    prices['AC_60_C']    = payoff_vanilla_call(paths, 60, step_21).mean()
    prices['AC_50_P_2']  = payoff_vanilla_put(paths, 50, 14 * STEPS_PER_DAY).mean()
    prices['AC_50_C_2']  = payoff_vanilla_call(paths, 50, 14 * STEPS_PER_DAY).mean()
    prices['AC_50_CO']   = payoff_chooser(paths, 50, step_14, step_21).mean()
    prices['AC_40_BP']   = payoff_binary_put(paths, 40, 10, step_21).mean()
    prices['AC_45_KO']   = payoff_knockout_put(paths, 45, 35, step_21).mean()

    return prices

# ---------- Market quotes from screenshot ----------
QUOTES = {
    # product:     (bid,   ask,   max_size)
    'AC':         (49.975, 50.025, 200),
    'AC_50_P':    (12.00,  12.05,  50),
    'AC_50_C':    (12.00,  12.05,  50),
    'AC_35_P':    (4.33,   4.35,   50),
    'AC_40_P':    (6.50,   6.55,   50),
    'AC_45_P':    (9.05,   9.10,   50),
    'AC_60_C':    (8.80,   8.85,   50),
    'AC_50_P_2':  (9.70,   9.75,   50),
    'AC_50_C_2':  (9.70,   9.75,   50),
    'AC_50_CO':   (22.20,  22.30,  50),
    'AC_40_BP':   (5.00,   5.10,   50),
    'AC_45_KO':   (0.15,   0.175,  500),
}

# ---------- Edge analysis ----------
def edge_table(fair_values):
    print(f"{'Product':<12} {'FairVal':>10} {'Bid':>8} {'Ask':>8} "
          f"{'BuyEdge':>10} {'SellEdge':>10} {'MaxSize':>8} {'BestTotal':>12}")
    print("-" * 86)
    total_edge = 0.0
    for prod, (bid, ask, size) in QUOTES.items():
        fv = fair_values[prod]
        buy_edge  = fv - ask    # positive => buy
        sell_edge = bid - fv    # positive => sell
        if buy_edge > sell_edge and buy_edge > 0:
            best = buy_edge * size
            action = f"BUY  {size}"
        elif sell_edge > 0:
            best = sell_edge * size
            action = f"SELL {size}"
        else:
            best = 0.0
            action = "SKIP"
        total_edge += max(0, best)
        print(f"{prod:<12} {fv:>10.3f} {bid:>8.3f} {ask:>8.3f} "
              f"{buy_edge:>+10.3f} {sell_edge:>+10.3f} {size:>8d} "
              f"{best:>10.2f}  {action}")
    print("-" * 86)
    print(f"{'TOTAL EDGE (sum of best per product)':<60} {total_edge:>20.2f}")
    return total_edge

# ---------- Simulate a portfolio's PnL across N independent simulations ----------
def simulate_portfolio_pnl(positions, n_simulations=100, paths_per_sim=1, seed=123):
    """
    positions: dict of product -> (action, size, price) where action is 'BUY' or 'SELL'.
    Runs n_simulations independent paths, computes terminal PnL each time.

    PnL per unit:
      BUY  at price p: payoff - p
      SELL at price p: p - payoff
    """
    rng = np.random.default_rng(seed)
    pnls = np.zeros(n_simulations)

    for i in range(n_simulations):
        sub_seed = int(rng.integers(0, 2**31 - 1))
        paths = simulate_paths(paths_per_sim, n_days=21, seed=sub_seed)

        step_14 = 14 * STEPS_PER_DAY
        step_21 = 21 * STEPS_PER_DAY

        sim_pnl = 0.0
        for prod, (action, size, price) in positions.items():
            if prod == 'AC':
                payoff = paths[:, step_21].mean()  # underlying value at horizon
            elif prod == 'AC_50_P':
                payoff = payoff_vanilla_put(paths, 50, step_21).mean()
            elif prod == 'AC_50_C':
                payoff = payoff_vanilla_call(paths, 50, step_21).mean()
            elif prod == 'AC_35_P':
                payoff = payoff_vanilla_put(paths, 35, step_21).mean()
            elif prod == 'AC_40_P':
                payoff = payoff_vanilla_put(paths, 40, step_21).mean()
            elif prod == 'AC_45_P':
                payoff = payoff_vanilla_put(paths, 45, step_21).mean()
            elif prod == 'AC_60_C':
                payoff = payoff_vanilla_call(paths, 60, step_21).mean()
            elif prod == 'AC_50_P_2':
                payoff = payoff_vanilla_put(paths, 50, step_14).mean()
            elif prod == 'AC_50_C_2':
                payoff = payoff_vanilla_call(paths, 50, step_14).mean()
            elif prod == 'AC_50_CO':
                payoff = payoff_chooser(paths, 50, step_14, step_21).mean()
            elif prod == 'AC_40_BP':
                payoff = payoff_binary_put(paths, 40, 10, step_21).mean()
            elif prod == 'AC_45_KO':
                payoff = payoff_knockout_put(paths, 45, 35, step_21).mean()
            else:
                continue

            if action.upper() == 'BUY':
                sim_pnl += size * (payoff - price)
            else:
                sim_pnl += size * (price - payoff)

        pnls[i] = sim_pnl

    return pnls

# ---------- Main ----------
if __name__ == "__main__":
    print("=" * 90)
    print("STEP 1: Compute fair values via Monte Carlo (200,000 paths)")
    print("=" * 90)
    fv = price_all(n_paths=200_000, seed=42)
    for k, v in fv.items():
        print(f"  {k:<12} fair value = {v:>8.4f}")

    print("\n" + "=" * 90)
    print("STEP 2: Edge table — best action and edge per product")
    print("=" * 90)
    edge_table(fv)

    print("\n" + "=" * 90)
    print("STEP 3: Recommended portfolio — buy everything underpriced")
    print("=" * 90)

    # Build positions from edge table (act on positive edge)
    positions = {}
    for prod, (bid, ask, size) in QUOTES.items():
        f = fv[prod]
        buy_edge  = f - ask
        sell_edge = bid - f
        if buy_edge > sell_edge and buy_edge > 0:
            positions[prod] = ('BUY', size, ask)
        elif sell_edge > 0:
            positions[prod] = ('SELL', size, bid)

    # Print positions
    print(f"{'Product':<12} {'Action':<6} {'Size':>6} {'Price':>10}")
    print("-" * 40)
    for prod, (action, size, price) in positions.items():
        print(f"{prod:<12} {action:<6} {size:>6d} {price:>10.3f}")

    print("\n" + "=" * 90)
    print("STEP 4: Simulate portfolio PnL across 100 independent paths")
    print("=" * 90)
    pnls = simulate_portfolio_pnl(positions, n_simulations=100, paths_per_sim=1, seed=2026)
    print(f"\n  Mean PnL across 100 sims : {pnls.mean():>10.2f}")
    print(f"  Median PnL                : {np.median(pnls):>10.2f}")
    print(f"  Std deviation             : {pnls.std():>10.2f}")
    print(f"  Min / Max                 : {pnls.min():>10.2f} / {pnls.max():>10.2f}")
    print(f"  P(PnL > 0)                : {(pnls > 0).mean():>10.2%}")
    print(f"  5th / 95th percentile     : {np.percentile(pnls, 5):>10.2f} / "
          f"{np.percentile(pnls, 95):.2f}")

    # Save the PnL distribution for inspection
    #np.savetxt('/home/claude/aether/pnls.csv', pnls, fmt='%.4f',
        #       header='pnl_per_simulation')
    print("\n  100-sim PnL vector saved to pnls.csv")