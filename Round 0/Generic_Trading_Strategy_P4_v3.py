"""
ETF Mean-Reversion Strategy v5 — Signal-Aware Trading + Options Theta Decay

Extends v3 (Bayesian Premium Estimation) with:
  A. Real-time signal detection (OFI, VPIN, AGG, RUNS, 3SIG)
  B. BS-based options theta decay strategy for VOLCANIC_ROCK vouchers

Combines:
1. Bayesian spread-based stat-arb for ETF baskets (signal-adjusted)
2. OU-based market making for non-basket, non-option products (signal-adjusted)
3. Options IV scalping + mean-reversion for vouchers, delta-hedged with VOLCANIC_ROCK
"""
from datamodel import OrderDepth, TradingState, Order
import json
import math

# Position limits per product
POS_LIMIT = {
    'RAINFOREST_RESIN': 50,
    'KELP': 50,
    'SQUID_INK': 50,
    'PICNIC_BASKET1': 60,
    'PICNIC_BASKET2': 100,
    'CROISSANTS': 250,
    'JAMS': 350,
    'DJEMBES': 60,
    'VOLCANIC_ROCK': 400,
    'VOLCANIC_ROCK_VOUCHER_9500': 200,
    'VOLCANIC_ROCK_VOUCHER_9750': 200,
    'VOLCANIC_ROCK_VOUCHER_10000': 200,
    'VOLCANIC_ROCK_VOUCHER_10250': 200,
    'VOLCANIC_ROCK_VOUCHER_10500': 200,
    'MAGNIFICENT_MACARONS': 75,
    'EMERALDS': 80,
    'TOMATOES': 80,
}

# ============================================================================
#  ETF BASKET STAT-ARB CONFIGURATION
#
#  Strategy: Bayesian spread trading
#    spread(t) = basket_wall_mid - SUM(w_i * constituent_wall_mid_i)
#    premium(t) = Bayesian posterior mean of spread (Welford online update)
#    deviation = spread(t) - premium(t)
#    Trade when |deviation| > threshold
# ============================================================================

BASKET_WEIGHTS = {
    # Basket composition: [CROISSANTS, JAMS, DJEMBES] units per basket
    'PICNIC_BASKET1': [6, 3, 1],
    'PICNIC_BASKET2': [4, 2, 0],
}
CONSTITUENTS = ['CROISSANTS', 'JAMS', 'DJEMBES']
BASKETS = ['PICNIC_BASKET1', 'PICNIC_BASKET2']

ETF_PARAMS = {
    'PICNIC_BASKET1': {
        # warmup: ticks before trading starts (collect spread samples for Bayesian posterior)
        #   posterior_mean = (prior_n * prior_mu + n * sample_mean) / (prior_n + n)
        #   posterior_var  = sigma^2 / (prior_n + n)  → shrinks as 1/n
        'warmup': 300,

        # prior_mu: prior mean of the spread premium (uninformative = 0)
        #   Bayesian conjugate normal-normal: N(prior_mu, sigma^2/prior_n)
        'prior_mu': 0.0,

        # prior_n: prior strength (pseudo-observations)
        #   Higher = more trust in prior, slower adaptation
        #   1 = weak prior, dominated by data after ~10 samples
        'prior_n': 1,

        # threshold: min |deviation| to trigger entry trade
        #   deviation = spread(t) - posterior_mean(t)
        #   SELL basket if deviation > threshold (spread is rich)
        #   BUY basket if deviation < -threshold (spread is cheap)
        'threshold': 80,

        # inv_skew: inventory skew factor for passive MM quotes
        #   skew = inv_skew * position / position_limit
        #   Shifts quotes away from current position to reduce inventory risk
        'inv_skew': 2.2,
    },
    'PICNIC_BASKET2': {
        'warmup': 300,
        'prior_mu': 0.0,
        'prior_n': 1,
        'threshold': 50,       # lower threshold: PB2 has tighter spreads
        'inv_skew': 2.2,
    },
}

# ============================================================================
#  OU (ORNSTEIN-UHLENBECK) MARKET MAKING CONFIGURATION
#
#  Model: dX = theta * (mu - X) * dt + sigma * dW
#    mu     = long-run mean (EMA-tracked)
#    theta  = mean-reversion speed (EMA-estimated from data)
#    sigma  = volatility (EMA-estimated from residuals)
#
#  Fair value: ou_fair = mu + (mid - mu) * exp(-theta)
#  Std dev:    ou_std  = sigma / sqrt(2*theta) * sqrt(1 - exp(-2*theta))
#
#  Quoting:
#    bid = wall_price + skew,  ask = wall_price + skew
#    skew = round(ou_fair - mid - inv_skew * position / pos_limit)
#  Taking:
#    buy  if ask_price <= ou_fair - take_sigma * ou_std
#    sell if bid_price >= ou_fair + take_sigma * ou_std
# ============================================================================

DEFAULT_OU_PARAMS = {
    # mu: fixed long-run mean. None = use EMA-tracked mean instead
    'mu': None,

    # mu_decay: EMA decay for tracking the long-run mean
    #   ema_mu(t) = mu_decay * ema_mu(t-1) + (1 - mu_decay) * mid(t)
    #   Higher decay = slower adaptation. 0.995 → half-life ~138 ticks
    #   None = use fixed 'mu' value
    'mu_decay': 0.995,

    # theta: initial mean-reversion speed (overridden by online estimate)
    #   Online estimate: theta_hat = -EMA(dev * dx) / EMA(dev^2)
    #   where dev = mid(t-1) - mu, dx = mid(t) - mid(t-1)
    #   Clamped to [0.01, 1.0]
    'theta': 0.1,

    # sigma: initial volatility (overridden by online estimate)
    #   Online estimate: sigma_hat = sqrt(EMA(residual^2))
    #   where residual = dx - theta * (mu - mid(t-1))
    'sigma': 3.0,

    # take_sigma: number of std devs for aggressive taking threshold
    #   buy_thresh  = ou_fair - take_sigma * ou_std
    #   sell_thresh = ou_fair + take_sigma * ou_std
    #   0.0 = take at fair value (aggressive), higher = more conservative
    #   None = use wall prices instead of OU-derived thresholds
    'take_sigma': 0.0,

    # inv_skew: inventory skew multiplier for passive quote offset
    #   skew_ticks = round(inv_skew * position / pos_limit)
    #   Positive position → shift quotes down (encourage selling)
    'inv_skew': 1.1,

    # ema_decay: decay factor for online theta/sigma estimation
    #   Controls how fast OU parameter estimates adapt to new data
    #   0.98 → half-life ~34 ticks
    'ema_decay': 0.98,
}

OU_OVERRIDE = {
    # RAINFOREST_RESIN: stable product, strong mean-reversion
    #   mu_decay=0.9999 (half-life ~6931 ticks) → nearly fixed mean
    #   take_sigma=None → use wall prices for taking (conservative)
    'RAINFOREST_RESIN': {'mu_decay': 0.9999, 'theta': 0.07, 'sigma': 5.0,
                         'take_sigma': None, 'inv_skew': 1.65, 'ema_decay': 0.982},

    # KELP: moderate mean-reversion, wider volatility
    'KELP': {'mu_decay': 0.995, 'theta': 0.14, 'sigma': 7.5,
             'inv_skew': 1.31, 'ema_decay': 0.982},

    # SQUID_INK: near random-walk (theta=0.004), very low MR
    #   theta=0.004 → half-life = ln(2)/0.004 = 173 ticks
    #   Signals need to be strong for this product (see SIGNAL_PARAMS)
    'SQUID_INK': {'mu_decay': 0.996, 'theta': 0.004, 'sigma': 2.1,
                  'inv_skew': 1.31, 'ema_decay': 0.982},
    'EMERALDS': {
        'mu': 10000.0, 
        'mu_decay': None,  # Keep mu fixed at 10k
        'theta': 1.0,      # Snap back instantly
        'sigma': 2.0,      # Low noise
        'take_sigma': None,  # No need for sigma-based taking — just take any mispricings vs 10k
        'inv_skew': 3,   # Symmetrical
        'ema_decay': 0.99
    },

    # TOMATOES: High volatility but eventually returns to mean.
    # Needs a wider 'sigma' to avoid getting stopped out by noise.
    'TOMATOES': {
        'mu_decay': 0.94, # Slow drift allowed
        'theta': 0.08,     # Slower reversion than Emeralds
        'sigma': 6.5,      # Higher volatility allowance
        'inv_skew': 3,  # Lean into inventory management
        'ema_decay': 0.95,   # Faster update for fair value
        'take_sigma': None
    },

}

# ============================================================================
#  SIGNAL DETECTION CONFIGURATION
#
#  5 online detectors run on market_trades per symbol:
#    1. OFI  = EMA of (buy_vol - sell_vol) / (buy_vol + sell_vol)  ∈ [-1, +1]
#    2. VPIN = mean(|buy_vol - sell_vol| / bucket_size) over window  ∈ [0, 1]
#    3. AGG  = EMA of aggressive buy/sell volumes + burst detection
#    4. RUNS = z-score of consecutive same-direction trade runs
#    5. 3SIG = z-score of (mid - price_ema) / sqrt(price_var_ema)
#
#  Combiner output per symbol:
#    direction_bias ∈ [-1, +1] = weighted avg of OFI, AGG, RUNS, 3SIG signals
#    spread_mult ∈ [1.0, 2.0] = quote widening factor (high VPIN / burst)
#    agg_burst (bool) = aggressive burst detected
#
#  Application:
#    ETF: threshold *= threshold_mult (agrees/contradicts with deviation)
#         quote skew += direction_bias * 2
#         quote width *= spread_mult
#    OU:  ou_fair += direction_bias * ou_std * fair_bias_scale
#         quote width *= spread_mult
# ============================================================================

SIGNALS_ENABLED = True  # Master toggle: False = disable all signal adjustments

SIGNAL_PARAMS = {
    # --- EMA decay rates for each detector ---
    # Each: ema(t) = decay * ema(t-1) + (1 - decay) * new_value
    # Half-life = -ln(2) / ln(decay)

    'ofi_decay': 0.95,     # OFI buy/sell volume EMA. Half-life ~13 ticks
    'agg_decay': 0.92,     # Aggressive volume EMA. Half-life ~8 ticks (faster reaction)
    'price_decay': 0.98,   # Price EMA for 3-sigma detector. Half-life ~34 ticks
    'run_decay': 0.95,     # Run length EMA baseline. Half-life ~13 ticks

    # --- VPIN bucket sizes ---
    # VPIN accumulates |classified_volume| into buckets of this size
    # When bucket full: toxicity = |buy_vol - sell_vol| / bucket_size
    # Larger buckets = smoother signal, slower to react
    'vpin_bucket_default': 50,
    'vpin_bucket_override': {
        'DJEMBES': 20,          # low volume → smaller buckets
        'PICNIC_BASKET1': 15,   # low volume
        'PICNIC_BASKET2': 20,
        'KELP': 100,            # high volume → larger buckets
        'RAINFOREST_RESIN': 80,
        'VOLCANIC_ROCK': 200,   # very high volume
    },
    'vpin_window': 20,   # number of completed buckets in rolling VPIN average

    # --- Signal combiner weights ---
    # direction_bias = SUM(weight_i * signal_i) for directional signals
    # Weights must conceptually sum to 1.0 but are not normalized
    'ofi_weight': 0.35,    # OFI: strongest directional predictor
    'agg_weight': 0.25,    # Aggressive trade direction
    'run_weight': 0.25,    # Run direction (order splitting)
    'price_weight': 0.15,  # Price anomaly direction (weakest, lagging)

    # --- Adjustment limits ---
    # spread_mult = 1.0 + penalties ∈ [1.0, max_spread_mult]
    'max_spread_mult': 2.0,

    # threshold_mult: applied to ETF entry threshold
    #   agrees (signal confirms deviation): mult = max(min_thr, 1.0 - 0.4*|bias|)
    #   contradicts: mult = min(max_thr, 1.0 + 0.5*|bias|)
    'min_threshold_mult': 0.6,
    'max_threshold_mult': 1.5,

    # fair_bias_scale: how much direction_bias shifts OU fair value
    #   ou_fair_adjusted = ou_fair + direction_bias * ou_std * fair_bias_scale
    'fair_bias_scale': 0.5,

    # --- Signal strength gating ---
    # Only apply adjustments when |direction_bias| > threshold
    # Below threshold → neutral (no bias, no widening)
    'signal_threshold_default': 0.0,   # most products: use any signal
    'signal_threshold_override': {
        'SQUID_INK': 0.35,  # random-walk: need strong signal to overcome noise
    },
}

# Per-product signal scaling: raw_signal *= scale before combining
# 0.0 = signals disabled for this product, 1.0 = full effect
SIGNAL_SCALE = {
    'VOLCANIC_ROCK': 0.0,  # VR signals are noisy (VPIN false positives, OFI not predictive)
}

# ============================================================================
#  OPTIONS STRATEGY SELECTOR
# ============================================================================

# 'baseline'       = IV scalping on strikes >= 9750 + MR on 9500 + underlying MR
# 'bull_spread'    = sell OTM (10250/10500) + buy ITM (9500/9750) + IV scalp ATM
# 'short_strangle' = sell both OTM and ITM + IV scalp ATM
# 'ratio_spread'   = buy half-pos ITM + sell full-pos OTM + IV scalp ATM
# 'theta_harvest'  = sell ATM/near-OTM (10000/10250) + buy ITM as delta hedge
OPTION_STRATEGY = 'baseline'

# OU adaptive directional bias for options
# Detects VR mean-reversion direction in real-time and biases thresholds per strike
OU_DIRECTION_PARAMS = {
    'ou_ema_decay': 0.98,       # EMA decay for online theta/sigma estimation
    'ou_mu_decay': 0.9995,      # Slow EMA for OU mean (half-life ~1386 ticks)
    'warmup': 200,              # Ticks before OU signal is trusted
    'bias_strength': 0.5,       # How much to adjust thresholds [0=none, 1=max]
    'signal_clip': 1.0,         # Clip normalized signal to this
    'enable_otm_on_direction': True,  # Enable 10250 only when OU direction favors it
}

# ============================================================================
#  OPTIONS / THETA DECAY CONFIGURATION
#
#  Pricing: Black-Scholes with vol smile
#    moneyness m = ln(K/S) / sqrt(TTE)
#    iv(m) = a*m^2 + b*m + c             (fitted quadratic)
#    theo  = BS_call(S, K, TTE, iv)
#    delta = N(d1),  vega = S * N'(d1) * sqrt(TTE)
#
#  IV Scalping (strikes >= 9750):
#    theo_diff(t) = market_wall_mid - BS_theo
#    mean_td(t)   = EMA(theo_diff, theo_norm_window)     ← persistent premium
#    switch_mean(t) = EMA(|theo_diff - mean_td|, iv_scalp_window)  ← vol of mispricing
#    edge = theo_diff - mean_td                            ← current mispricing
#
#    Adaptive thresholds (from Welford online stats):
#      iv_scalp_thr = P25(switch_mean)   ← activate when mispricing vol above bottom quartile
#      thr_open     = mean(half_spread)  ← min edge to cover bid-ask crossing cost
#      thr_close    = 0                  ← close when edge disappears
#      low_vega_adj = thr_open           ← 2x threshold for low-vega options (vega <= 1.0)
#
#    SELL if switch_mean >= iv_scalp_thr AND sell_edge >= thr_open + low_vega_adj
#    BUY  if switch_mean >= iv_scalp_thr AND buy_edge <= -(thr_open + low_vega_adj)
#    where sell_edge = best_bid - BS_theo - mean_td
#          buy_edge  = best_ask - BS_theo - mean_td
#
#  MR on 9500 (deep ITM):
#    combined_dev = (und_mid - ema_o) + (theo_diff - mean_td)
#    Adaptive: options_mr_thr = mean(|combined_dev|)  ← trade above average
#    SELL if combined_dev > options_mr_thr
#    BUY  if combined_dev < -options_mr_thr
#
#  Underlying MR (VOLCANIC_ROCK):
#    ema_o = EMA(und_mid, options_mr_window, init=0)   ← init=0 creates day-open sell bias
#    deviation = und_mid - ema_o
#    Adaptive: und_mr_thr = mean(|dev|) + 1.645*std(|dev|)  ← 95th percentile
#    Fallback (first 130 ticks): und_mr_thr = und_mid * 0.0020  ← 20bps of price
#    SELL at bid_wall+1 if deviation > und_mr_thr
#    BUY  at ask_wall-1 if deviation < -und_mr_thr
#
#  TTE: time to expiry = 1 - (365 - 8 + DAY + timestamp/1M) / 365
# ============================================================================

OPTION_UNDERLYING = 'VOLCANIC_ROCK'
OPTION_SYMBOLS = [
    'VOLCANIC_ROCK_VOUCHER_9500',   # Deep ITM (delta ~0.95)
    'VOLCANIC_ROCK_VOUCHER_9750',   # ITM (delta ~0.80)
    'VOLCANIC_ROCK_VOUCHER_10000',  # ATM (delta ~0.50, highest theta)
    'VOLCANIC_ROCK_VOUCHER_10250',  # OTM (delta ~0.25)
    'VOLCANIC_ROCK_VOUCHER_10500',  # Deep OTM (delta ~0.05)
]
OPTION_STRIKES = {s: int(s.split('_')[-1]) for s in OPTION_SYMBOLS}
ALL_OPTION_PRODUCTS = set(OPTION_SYMBOLS) | {OPTION_UNDERLYING}

# Vol smile: iv(m) = a*m^2 + b*m + c where m = ln(K/S)/sqrt(TTE)
# Fitted from historical option prices via least-squares regression
VOL_SMILE_COEFFS = [0.27362531, 0.01007566, 0.14876677]

DAYS_PER_YEAR = 365
OPTION_DAY = 5  # Current round day number (1-indexed)

OPT_PARAMS = {
    # theo_norm_window: EMA window for tracking persistent mispricing (mean_td)
    #   alpha = 2 / (window + 1)
    #   mean_td(t) = alpha * theo_diff(t) + (1-alpha) * mean_td(t-1)
    #   Captures structural premium/discount vs BS theo
    'theo_norm_window': 20,

    # iv_scalp_window: EMA window for switch_mean (volatility of mispricing)
    #   alpha = 2 / (window + 1) = 0.0198
    #   switch_mean(t) = alpha * |theo_diff - mean_td| + (1-alpha) * switch_mean(t-1)
    #   High switch_mean = enough mispricing vol to scalp profitably
    'iv_scalp_window': 100,

    # underlying_mr_window: EMA window for underlying price tracking
    #   alpha = 2 / (window + 1) = 0.182
    #   ema_u(t) = alpha * und_mid + (1-alpha) * ema_u(t-1)
    #   Currently unused (ema_o used instead), kept for potential future use
    'underlying_mr_window': 10,

    # options_mr_window: EMA window for options-level mean-reversion signal
    #   alpha = 2 / (window + 1) = 0.0645
    #   ema_o(t) = alpha * und_mid + (1-alpha) * ema_o(t-1)
    #   Used for: underlying MR deviation AND 9500 combined deviation
    #   init=0: creates large positive deviation at day open (~10000) → forced early sell
    'options_mr_window': 30,

    # warmup_ticks: minimum ticks before any option trading
    #   Allows EMAs to partially converge and collects stats for adaptive thresholds
    'warmup_ticks': 30,

    # stats_start: tick to begin collecting Welford stats for adaptive thresholds
    #   Should be >= theo_norm_window / 2 so mean_td EMA is somewhat stable
    'stats_start': 10,

    # thr_close: edge threshold to close existing positions
    #   0.0 = close when mispricing disappears (edge crosses zero)
    #   Not adaptive — zero-crossing is the principled exit
    'thr_close': 0.0,
}


# ============================================================================
#  WELFORD'S ONLINE ALGORITHM — for adaptive threshold computation
#
#  Maintains running mean and variance in O(1) per update, no stored samples.
#  State: n (count), mean, M2 (sum of squared deviations)
#
#  Update:  n += 1
#           d1 = x - mean
#           mean += d1 / n
#           d2 = x - mean   (using UPDATED mean)
#           M2 += d1 * d2
#
#  Variance = M2 / n,  Std = sqrt(M2 / n)
#  Percentile estimate (assuming normal): P(z) = mean + z * std
#    z = -0.674 → 25th pct,  z = 0 → median,  z = 0.674 → 75th pct
#    z = 1.0 → 84th pct,  z = 1.645 → 95th pct,  z = 1.96 → 97.5th pct
# ============================================================================

def _welford_update(state, prefix, value):
    """Online mean/variance update (Welford's algorithm)."""
    n_key, m_key, m2_key = f'{prefix}_n', f'{prefix}_m', f'{prefix}_m2'
    n = state.get(n_key, 0) + 1
    mean = state.get(m_key, 0.0)
    m2 = state.get(m2_key, 0.0)
    d1 = value - mean
    mean += d1 / n
    d2 = value - mean
    m2 += d1 * d2
    state[n_key] = n
    state[m_key] = mean
    state[m2_key] = m2


def _welford_pct(state, prefix, z):
    """Return mean + z*std from Welford stats, or None if insufficient data."""
    n = state.get(f'{prefix}_n', 0)
    if n < 10:
        return None
    mean = state.get(f'{prefix}_m', 0.0)
    m2 = state.get(f'{prefix}_m2', 0.0)
    std = math.sqrt(m2 / n) if m2 > 0 else 0.0
    return mean + z * std


# ---- Pure-Python Normal CDF (Abramowitz & Stegun approximation) ----

def _norm_cdf(x):
    """Standard normal CDF, accurate to ~1e-7."""
    a1, a2, a3, a4, a5 = (
        0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    )
    p = 0.3275911
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x / 2.0)
    return 0.5 * (1.0 + sign * y)


def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _get_iv(S, K, tte):
    """Implied vol from fitted smile: iv = poly(log(K/S)/sqrt(TTE))."""
    if tte <= 1e-9:
        return 0.15
    m = math.log(K / S) / math.sqrt(tte)
    # quadratic: c[0]*m^2 + c[1]*m + c[2]
    c = VOL_SMILE_COEFFS
    return c[0] * m * m + c[1] * m + c[2]


def _bs_call(S, K, tte, sigma):
    """Black-Scholes call price and delta."""
    if tte <= 1e-9:
        val = max(S - K, 0.0)
        delta = 1.0 if S > K else (0.5 if S == K else 0.0)
        return val, delta
    sqrt_t = math.sqrt(tte)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * tte) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    call_val = S * _norm_cdf(d1) - K * _norm_cdf(d2)
    delta = _norm_cdf(d1)
    return call_val, delta


def _bs_vega(S, K, tte, sigma):
    if tte <= 1e-9:
        return 0.0
    sqrt_t = math.sqrt(tte)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * tte) / (sigma * sqrt_t)
    return S * _norm_pdf(d1) * sqrt_t


def get_ou_params(symbol):
    cfg = dict(DEFAULT_OU_PARAMS)
    cfg.update(OU_OVERRIDE.get(symbol, {}))
    return cfg


def get_mid(od):
    if od.buy_orders and od.sell_orders:
        return (max(od.buy_orders) + min(od.sell_orders)) / 2
    return None


# ================================================================
#  SIGNAL DETECTORS (online / streaming, pure Python)
# ================================================================

def update_ofi(trades, mid, state):
    """Order Flow Imbalance: EMA of buy/sell volume ratio."""
    decay = SIGNAL_PARAMS['ofi_decay']
    buy_vol = state.get('ofi_bv', 0.0)
    sell_vol = state.get('ofi_sv', 0.0)

    tick_buy, tick_sell = 0.0, 0.0
    for t in trades:
        if t.price >= mid:
            tick_buy += t.quantity
        else:
            tick_sell += t.quantity

    buy_vol = decay * buy_vol + (1 - decay) * tick_buy
    sell_vol = decay * sell_vol + (1 - decay) * tick_sell
    total = buy_vol + sell_vol + 1e-9
    signal = (buy_vol - sell_vol) / total  # [-1, +1]

    state['ofi_bv'] = buy_vol
    state['ofi_sv'] = sell_vol
    return signal, state


def update_vpin(trades, mid, symbol, state):
    """VPIN-lite: volume-bucket imbalance as informed trading proxy."""
    bucket_size = SIGNAL_PARAMS['vpin_bucket_override'].get(
        symbol, SIGNAL_PARAMS['vpin_bucket_default'])
    window = SIGNAL_PARAMS['vpin_window']

    bucket_buy = state.get('vp_bb', 0.0)
    bucket_sell = state.get('vp_bs', 0.0)
    bucket_vol = state.get('vp_bv', 0.0)
    buckets = state.get('vp_bks', [])

    for t in trades:
        remaining = t.quantity
        is_buy = t.price >= mid
        while remaining > 0:
            space = bucket_size - bucket_vol
            fill = min(remaining, space)
            if is_buy:
                bucket_buy += fill
            else:
                bucket_sell += fill
            bucket_vol += fill
            remaining -= fill

            if bucket_vol >= bucket_size - 0.01:
                imbalance = abs(bucket_buy - bucket_sell) / bucket_size
                buckets.append(imbalance)
                if len(buckets) > window:
                    buckets = buckets[-window:]
                bucket_buy, bucket_sell, bucket_vol = 0.0, 0.0, 0.0

    vpin = sum(buckets) / len(buckets) if buckets else 0.0

    state['vp_bb'] = bucket_buy
    state['vp_bs'] = bucket_sell
    state['vp_bv'] = bucket_vol
    state['vp_bks'] = buckets
    return vpin, state


def update_aggressive(trades, best_bid, best_ask, state):
    """Aggressive trade detector: EMA of bid/ask-hitting volume + burst flag."""
    decay = SIGNAL_PARAMS['agg_decay']
    agg_buy_ema = state.get('ag_be', 0.0)
    agg_sell_ema = state.get('ag_se', 0.0)
    agg_total_ema = state.get('ag_te', 0.0)

    tick_agg_buy, tick_agg_sell = 0.0, 0.0
    for t in trades:
        if t.price >= best_ask:
            tick_agg_buy += t.quantity
        elif t.price <= best_bid:
            tick_agg_sell += t.quantity

    agg_buy_ema = decay * agg_buy_ema + (1 - decay) * tick_agg_buy
    agg_sell_ema = decay * agg_sell_ema + (1 - decay) * tick_agg_sell
    tick_total = tick_agg_buy + tick_agg_sell
    agg_total_ema = decay * agg_total_ema + (1 - decay) * tick_total

    total = agg_buy_ema + agg_sell_ema + 1e-9
    direction = (agg_buy_ema - agg_sell_ema) / total  # [-1, +1]
    burst = tick_total > 2.0 * agg_total_ema and agg_total_ema > 0.5

    state['ag_be'] = agg_buy_ema
    state['ag_se'] = agg_sell_ema
    state['ag_te'] = agg_total_ema
    return direction, burst, state


def update_runs(trades, mid, state):
    """Run detector: tracks consecutive same-direction trade ticks."""
    decay = SIGNAL_PARAMS['run_decay']
    run_dir = state.get('rn_d', 0)
    run_len = state.get('rn_l', 0)
    run_mean = state.get('rn_m', 1.5)
    run_var = state.get('rn_v', 1.0)

    # Determine net direction of this tick's trades
    net = 0.0
    for t in trades:
        if t.price >= mid:
            net += t.quantity
        else:
            net -= t.quantity

    if abs(net) < 1e-9:
        # No clear direction this tick — keep state
        return 0.0, run_dir, state

    tick_dir = 1 if net > 0 else -1

    if tick_dir == run_dir:
        run_len += 1
    else:
        # Run ended — update EMA of run lengths
        if run_len > 0:
            run_mean = decay * run_mean + (1 - decay) * run_len
            diff = run_len - run_mean
            run_var = decay * run_var + (1 - decay) * (diff * diff)
        run_dir = tick_dir
        run_len = 1

    std = math.sqrt(run_var + 1e-9)
    zscore = (run_len - run_mean) / std if std > 0.01 else 0.0

    state['rn_d'] = run_dir
    state['rn_l'] = run_len
    state['rn_m'] = run_mean
    state['rn_v'] = run_var
    return zscore, run_dir, state


def update_price_sigma(mid, state):
    """3-sigma price move detector."""
    decay = SIGNAL_PARAMS['price_decay']
    price_ema = state.get('ps_e', mid)
    price_var = state.get('ps_v', 0.0)

    price_ema = decay * price_ema + (1 - decay) * mid
    dev = mid - price_ema
    price_var = decay * price_var + (1 - decay) * (dev * dev)
    std = math.sqrt(price_var + 1e-9)
    zscore = dev / std if std > 0.01 else 0.0
    anomaly = abs(zscore) > 3.0

    state['ps_e'] = price_ema
    state['ps_v'] = price_var
    return zscore, anomaly, state


# ================================================================
#  SIGNAL COMBINER
# ================================================================

def combine_signals(ofi_signal, vpin_value, agg_direction, agg_burst,
                    run_zscore, run_dir, price_zscore, price_anomaly,
                    symbol=''):
    """Combine 5 signal outputs into 3 adjustment factors."""
    scale = SIGNAL_SCALE.get(symbol, 1.0)
    if scale == 0.0:
        return {'direction_bias': 0.0, 'spread_mult': 1.0, 'agg_burst': False}

    sp = SIGNAL_PARAMS

    # --- Direction bias: weighted average of directional signals ---
    run_component = run_dir * min(run_zscore / 3.0, 1.0) if run_zscore > 0 else 0.0
    price_component = max(-1.0, min(1.0, price_zscore / 3.0))

    direction_bias = (
        sp['ofi_weight'] * ofi_signal +
        sp['agg_weight'] * agg_direction +
        sp['run_weight'] * run_component +
        sp['price_weight'] * price_component
    )
    direction_bias = max(-1.0, min(1.0, direction_bias))

    # --- Spread multiplier: defensive widening ---
    spread_mult = 1.0
    if vpin_value > 0.5:
        spread_mult += 0.5 * (vpin_value - 0.5)  # up to +0.25 at vpin=1.0
    if agg_burst:
        spread_mult += 0.3
    if price_anomaly:
        spread_mult += 0.2
    spread_mult = min(spread_mult, sp['max_spread_mult'])

    # Apply per-product scaling
    direction_bias *= scale
    spread_mult = 1.0 + (spread_mult - 1.0) * scale
    scaled_burst = agg_burst and scale >= 0.5

    # Gate: only apply adjustments when signal is strong enough
    # Below threshold → return neutral (base v3 strategy unchanged)
    sig_thresh = sp.get('signal_threshold_override', {}).get(
        symbol, sp.get('signal_threshold_default', 0.15))
    if abs(direction_bias) < sig_thresh and not scaled_burst:
        return {'direction_bias': 0.0, 'spread_mult': 1.0, 'agg_burst': False}

    return {
        'direction_bias': direction_bias,
        'spread_mult': spread_mult,
        'agg_burst': scaled_burst,
    }


def compute_all_signals(symbol, trades, od, sig_state):
    """Run all signal detectors for a symbol. Returns adjustments + updated state."""
    if not od.buy_orders or not od.sell_orders:
        return {'direction_bias': 0.0, 'spread_mult': 1.0, 'agg_burst': False}, sig_state

    best_bid = max(od.buy_orders)
    best_ask = min(od.sell_orders)
    mid = (best_bid + best_ask) / 2.0

    if not trades:
        # No trades this tick — run only price detector, decay others via no-op
        price_z, price_anom, sig_state = update_price_sigma(mid, sig_state)
        # Return near-neutral adjustments (stale EMA signals decay naturally)
        ofi_bv = sig_state.get('ofi_bv', 0.0)
        ofi_sv = sig_state.get('ofi_sv', 0.0)
        ofi_sig = (ofi_bv - ofi_sv) / (ofi_bv + ofi_sv + 1e-9)
        ag_be = sig_state.get('ag_be', 0.0)
        ag_se = sig_state.get('ag_se', 0.0)
        agg_dir = (ag_be - ag_se) / (ag_be + ag_se + 1e-9)
        run_z = 0.0
        run_d = sig_state.get('rn_d', 0)
        vpin_bks = sig_state.get('vp_bks', [])
        vpin_val = sum(vpin_bks) / len(vpin_bks) if vpin_bks else 0.0

        return combine_signals(ofi_sig, vpin_val, agg_dir, False,
                               run_z, run_d, price_z, price_anom,
                               symbol=symbol), sig_state

    # Run all 5 detectors
    ofi_signal, sig_state = update_ofi(trades, mid, sig_state)
    vpin_value, sig_state = update_vpin(trades, mid, symbol, sig_state)
    agg_direction, agg_burst, sig_state = update_aggressive(trades, best_bid, best_ask, sig_state)
    run_zscore, run_dir, sig_state = update_runs(trades, mid, sig_state)
    price_zscore, price_anomaly, sig_state = update_price_sigma(mid, sig_state)

    adjustments = combine_signals(ofi_signal, vpin_value, agg_direction, agg_burst,
                                  run_zscore, run_dir, price_zscore, price_anomaly,
                                  symbol=symbol)
    return adjustments, sig_state


# ================================================================
#  ETF SPREAD TRADING (Bayesian premium, signal-adjusted)
# ================================================================

def etf_trade(basket_name, basket_od, constituent_ods, positions, etf_state, adjustments=None):
    """
    Bayesian premium estimation using Welford's online algorithm.
    Signal adjustments modify threshold and quote placement.
    """
    if adjustments is None:
        adjustments = {'direction_bias': 0.0, 'spread_mult': 1.0, 'agg_burst': False}

    cfg = ETF_PARAMS[basket_name]
    weights = BASKET_WEIGHTS[basket_name]

    buys, sells = basket_od.buy_orders, basket_od.sell_orders
    if not buys or not sells:
        return [], etf_state

    best_bid, best_ask = max(buys), min(sells)
    mid = (best_bid + best_ask) / 2
    wall_mid = (min(buys) + max(sells)) / 2

    const_wall_mids = []
    for c in CONSTITUENTS:
        if c not in constituent_ods:
            return [], etf_state
        cod = constituent_ods[c]
        if not cod.buy_orders or not cod.sell_orders:
            return [], etf_state
        const_wall_mids.append((min(cod.buy_orders) + max(cod.sell_orders)) / 2)

    theo = sum(w * p for w, p in zip(weights, const_wall_mids))
    raw_spread = wall_mid - theo

    # --- Welford's online algorithm for posterior ---
    n = etf_state.get('n', 0)
    mean = etf_state.get('mean', cfg['prior_mu'])
    m2 = etf_state.get('m2', 0.0)

    n += 1
    delta = raw_spread - mean
    mean += delta / (cfg['prior_n'] + n)
    delta2 = raw_spread - mean
    m2 += delta * delta2

    premium = mean
    dev = raw_spread - premium

    basket_limit = POS_LIMIT[basket_name]
    basket_pos = positions.get(basket_name, 0)
    max_buy = basket_limit - basket_pos
    max_sell = basket_limit + basket_pos
    orders = []

    warmup = cfg['warmup']
    threshold = cfg['threshold']

    # --- Signal-adjusted threshold ---
    # If signal direction agrees with spread deviation → lower threshold (aggressive)
    # If contradicts → raise threshold (cautious)
    direction_bias = adjustments['direction_bias']
    spread_mult = adjustments['spread_mult']
    sp = SIGNAL_PARAMS

    if abs(direction_bias) > 0.1:
        # dev > 0 means spread is rich (sell signal), dev < 0 means cheap (buy signal)
        # direction_bias > 0 means buy pressure, < 0 means sell pressure
        # Agreement: dev > 0 and bias < 0 (sell pressure confirms rich spread)
        #            dev < 0 and bias > 0 (buy pressure confirms cheap spread)
        agrees = (dev > 0 and direction_bias < 0) or (dev < 0 and direction_bias > 0)
        if agrees:
            threshold_mult = max(sp['min_threshold_mult'],
                                 1.0 - 0.4 * abs(direction_bias))
        else:
            threshold_mult = min(sp['max_threshold_mult'],
                                 1.0 + 0.5 * abs(direction_bias))
    else:
        threshold_mult = 1.0

    effective_threshold = threshold * threshold_mult

    # --- Only trade after warmup ---
    if n >= warmup:
        # Spread signal: aggressive taking
        if dev > effective_threshold and max_sell > 0:
            orders.append(Order(basket_name, best_bid, -max_sell))
            max_sell = 0
        elif dev < -effective_threshold and max_buy > 0:
            orders.append(Order(basket_name, best_ask, max_buy))
            max_buy = 0
        else:
            # Close at zero crossing
            if dev > 0 and basket_pos > 0 and max_sell > 0:
                v = min(basket_pos, max_sell)
                orders.append(Order(basket_name, best_bid, -v))
                max_sell -= v
            elif dev < 0 and basket_pos < 0 and max_buy > 0:
                v = min(-basket_pos, max_buy)
                orders.append(Order(basket_name, best_ask, v))
                max_buy -= v

        # MM quotes with spread-aware skew + signal adjustments
        fair = theo + premium
        inv_skew = round(cfg['inv_skew'] * basket_pos / basket_limit)
        fair_skew = round(fair - mid)
        signal_skew = round(direction_bias * 2)  # signal-based quote bias
        skew = fair_skew - inv_skew + signal_skew

        bid_p = min(best_bid + skew, int(math.floor(mid)))
        ask_p = max(best_ask + skew, int(math.ceil(mid)))

        # Widen quotes by spread_mult during high-VPIN / burst periods
        if spread_mult > 1.0:
            half_widen = int((spread_mult - 1.0) * (ask_p - bid_p) / 2)
            bid_p -= half_widen
            ask_p += half_widen

        if bid_p >= ask_p:
            bid_p, ask_p = int(math.floor(mid)) - 1, int(math.ceil(mid)) + 1

        if max_buy > 0:
            orders.append(Order(basket_name, bid_p, max_buy))
        if max_sell > 0:
            orders.append(Order(basket_name, ask_p, -max_sell))

    return orders, {'n': n, 'mean': mean, 'm2': m2}


# ================================================================
#  OU MARKET MAKING (signal-adjusted)
# ================================================================

def ou_trade(symbol, order_depth, position, state_params, adjustments=None):
    if adjustments is None:
        adjustments = {'direction_bias': 0.0, 'spread_mult': 1.0, 'agg_burst': False}

    buys, sells = order_depth.buy_orders, order_depth.sell_orders
    if not buys or not sells:
        return [], state_params

    cfg = get_ou_params(symbol)
    pos_limit = POS_LIMIT.get(symbol, 50)
    ema_decay = cfg['ema_decay']
    last_mid, ema_var, ema_tn, ema_td, ema_mu = (
        state_params.get(k, d) for k, d in [('lm', None), ('ev', 0.0), ('tn', 0.0), ('td', 0.0), ('km', None)]
    )

    bid_wall, ask_wall = min(buys), max(sells)
    best_bid, best_ask = max(buys), min(sells)
    wall_mid = (bid_wall + ask_wall) / 2
    mid = (best_bid + best_ask) / 2

    sorted_bids = sorted(buys.items(), key=lambda x: -x[0])
    sorted_asks = sorted(sells.items(), key=lambda x: x[0])

    mu = cfg['mu']
    if cfg['mu_decay'] is not None:
        ema_mu = mid if ema_mu is None else cfg['mu_decay'] * ema_mu + (1 - cfg['mu_decay']) * mid
        mu = ema_mu

    theta_est, sigma_est = cfg['theta'], cfg['sigma']
    if last_mid is not None:
        dx = mid - last_mid
        dev = last_mid - mu
        ema_tn = ema_decay * ema_tn + (1 - ema_decay) * (dev * dx)
        ema_td = ema_decay * ema_td + (1 - ema_decay) * (dev * dev)
        if ema_td > 0.01:
            theta_est = max(0.01, min(-ema_tn / ema_td, 1.0))
        residual = dx - theta_est * (mu - last_mid)
        ema_var = ema_decay * ema_var + (1 - ema_decay) * (residual * residual)
        sigma_est = math.sqrt(max(ema_var, 0.01))

    ou_fair = mu + (mid - mu) * math.exp(-theta_est)
    ou_std = math.sqrt(max(
        sigma_est**2 / (2 * theta_est) * (1 - math.exp(-2 * theta_est)) if theta_est > 0.001 else sigma_est**2,
        0.01
    ))

    # --- Signal adjustments to fair value ---
    direction_bias = adjustments['direction_bias']
    spread_mult = adjustments['spread_mult']
    agg_burst = adjustments['agg_burst']

    # Bias fair value in direction of detected flow
    ou_fair += direction_bias * ou_std * SIGNAL_PARAMS['fair_bias_scale']

    max_buy, max_sell = pos_limit - position, pos_limit + position
    orders = []
    take_sigma = cfg['take_sigma']

    if take_sigma is not None:
        buy_thresh = ou_fair - take_sigma * ou_std
        sell_thresh = ou_fair + take_sigma * ou_std
    else:
        buy_thresh, sell_thresh = wall_mid - 1, wall_mid + 1

    # Widen take thresholds during aggressive bursts
    if agg_burst:
        burst_widen = 0.5 * ou_std
        buy_thresh -= burst_widen
        sell_thresh += burst_widen

    for price, vol in sorted_asks:
        if max_buy <= 0:
            break
        vol = abs(vol)
        if price <= buy_thresh:
            v = min(vol, max_buy)
            orders.append(Order(symbol, price, v))
            max_buy -= v
        elif price <= (ou_fair if take_sigma is not None else wall_mid) and position < 0:
            v = min(vol, max_buy, -position)
            if v > 0:
                orders.append(Order(symbol, price, v))
                max_buy -= v

    for price, vol in sorted_bids:
        if max_sell <= 0:
            break
        if price >= sell_thresh:
            v = min(vol, max_sell)
            orders.append(Order(symbol, price, -v))
            max_sell -= v
        elif price >= (ou_fair if take_sigma is not None else wall_mid) and position > 0:
            v = min(vol, max_sell, position)
            if v > 0:
                orders.append(Order(symbol, price, -v))
                max_sell -= v

    skew = round(ou_fair - mid - cfg['inv_skew'] * position / pos_limit)
    # Add signal direction to skew
    skew += round(direction_bias)

    bid_price, ask_price = int(bid_wall + 1), int(ask_wall - 1)

    for bp, bv in sorted_bids:
        if bv > 1 and bp + 1 < wall_mid:
            bid_price = max(bid_price, bp + 1)
        elif bp < wall_mid:
            bid_price = max(bid_price, bp)
        break

    for sp_price, sv in sorted_asks:
        if abs(sv) > 1 and sp_price - 1 > wall_mid:
            ask_price = min(ask_price, sp_price - 1)
        elif sp_price > wall_mid:
            ask_price = min(ask_price, sp_price)
        break

    bid_price = min(bid_price + skew, int(math.floor(wall_mid)))
    ask_price = max(ask_price + skew, int(math.ceil(wall_mid)))

    # Widen quotes by spread_mult during high-VPIN / burst periods
    if spread_mult > 1.0:
        widen = int((spread_mult - 1.0) * 2)
        bid_price -= widen
        ask_price += widen

    if bid_price >= ask_price:
        bid_price, ask_price = int(math.floor(wall_mid)) - 1, int(math.ceil(wall_mid)) + 1

    if max_buy > 0:
        orders.append(Order(symbol, bid_price, max_buy))
    if max_sell > 0:
        orders.append(Order(symbol, ask_price, -max_sell))

    return orders, {'lm': mid, 'ev': ema_var, 'tn': ema_tn, 'td': ema_td, 'km': ema_mu}


# ================================================================
#  OPTIONS THETA DECAY (IV scalping + MR + delta hedge)
# ================================================================

def _ema_update(old_val, new_val, window):
    """EMA update: alpha = 2/(window+1)."""
    alpha = 2.0 / (window + 1)
    return alpha * new_val + (1.0 - alpha) * old_val


def option_trade(state, positions, opt_state):
    """
    Options theta decay strategy with spread-based hedging:
    1. Price each voucher via BS with vol smile
    2. Rank by mispricing: sell rich, buy cheap (natural hedge via spreads)
    3. IV scalping on individual vouchers when vol-of-mispricing is high
    4. MR on deep-ITM (9500)
    5. Hedge residual delta with cheapest-to-hedge option or underlying (MR only)
    """
    cfg = OPT_PARAMS
    all_orders = {}

    # Get underlying mid
    und_od = state.order_depths.get(OPTION_UNDERLYING)
    if not und_od or not und_od.buy_orders or not und_od.sell_orders:
        return all_orders, opt_state

    und_best_bid = max(und_od.buy_orders)
    und_best_ask = min(und_od.sell_orders)
    und_mid = (und_best_bid + und_best_ask) / 2.0
    und_bid_wall = min(und_od.buy_orders)
    und_ask_wall = max(und_od.sell_orders)

    # TTE calculation
    tick_frac = state.timestamp / 100.0 / 10000.0
    tte = 1.0 - (DAYS_PER_YEAR - 8 + OPTION_DAY + tick_frac) / DAYS_PER_YEAR
    if tte <= 1e-6:
        return all_orders, opt_state

    # ---- Online OU estimation for VR underlying ----
    ou_cfg = OU_DIRECTION_PARAMS
    ou_lm = opt_state.get('ou_lm', None)
    ou_ev = opt_state.get('ou_ev', 0.0)
    ou_tn = opt_state.get('ou_tn', 0.0)
    ou_td_val = opt_state.get('ou_td', 0.0)
    ou_mu = opt_state.get('ou_mu', None)
    ou_decay = ou_cfg['ou_ema_decay']

    if ou_mu is None:
        ou_mu = und_mid
    else:
        ou_mu = ou_cfg['ou_mu_decay'] * ou_mu + (1 - ou_cfg['ou_mu_decay']) * und_mid

    ou_theta_est = 0.1
    ou_sigma_est = 3.0

    if ou_lm is not None:
        ou_dx = und_mid - ou_lm
        ou_dev = ou_lm - ou_mu
        ou_tn = ou_decay * ou_tn + (1 - ou_decay) * (ou_dev * ou_dx)
        ou_td_val = ou_decay * ou_td_val + (1 - ou_decay) * (ou_dev * ou_dev)
        if ou_td_val > 0.01:
            ou_theta_est = max(0.001, min(-ou_tn / ou_td_val, 1.0))
        ou_resid = ou_dx - ou_theta_est * (ou_mu - ou_lm)
        ou_ev = ou_decay * ou_ev + (1 - ou_decay) * (ou_resid * ou_resid)
        ou_sigma_est = math.sqrt(max(ou_ev, 0.01))

    opt_state['ou_lm'] = und_mid
    opt_state['ou_ev'] = ou_ev
    opt_state['ou_tn'] = ou_tn
    opt_state['ou_td'] = ou_td_val
    opt_state['ou_mu'] = ou_mu

    # OU directional signal
    ou_drift = und_mid - ou_mu  # positive = above mean, will revert down
    ou_stat_std = ou_sigma_est / math.sqrt(2 * ou_theta_est) if ou_theta_est > 0.001 else 100.0
    ou_signal = min(abs(ou_drift) / ou_stat_std, ou_cfg['signal_clip']) if ou_stat_std > 0 else 0.0

    # Update underlying EMAs (init at 0 like FH)
    ema_u = _ema_update(
        opt_state.get('ema_u', 0), und_mid, cfg['underlying_mr_window'])
    ema_o = _ema_update(
        opt_state.get('ema_o', 0), und_mid, cfg['options_mr_window'])
    opt_state['ema_u'] = ema_u
    opt_state['ema_o'] = ema_o
    ema_o_dev = und_mid - ema_o

    tick_count = opt_state.get('tick', 0) + 1
    opt_state['tick'] = tick_count

    # ---- Adaptive underlying MR threshold (Welford on |deviation|) ----
    #
    # deviation = und_mid - ema_o  (ema_o has init=0, window=30)
    #
    # Phase 1 (ticks 1-100): EMA converges from 0 → ~und_mid
    #   deviations are artificially large (>1000 initially), NOT collected
    #   fallback threshold = 20bps of price (e.g., 20 for VR ~10000)
    #   init=0 creates profitable day-open sell bias during this phase
    #
    # Phase 2 (ticks 101+): EMA has converged, collect |deviation| samples
    #   Welford update: n, mean, M2 track distribution of |deviation|
    #   After 30+ samples: und_mr_thr = mean + 1.645 * std  (95th percentile)
    #   Only trade when deviation is in the top 5% → ~2-3% of ticks trigger
    #
    # Math: assuming |deviation| ~ half-normal(sigma),
    #   mean ≈ sigma * sqrt(2/pi), std ≈ sigma * sqrt(1 - 2/pi)
    #   95th pct ≈ 1.96 * sigma (close to 2-sigma of the raw deviation)
    mr_n = opt_state.get('mr_n', 0)
    mr_mean = opt_state.get('mr_mean', 0.0)
    mr_m2 = opt_state.get('mr_m2', 0.0)
    if tick_count > 100:
        abs_dev = abs(ema_o_dev)
        mr_n += 1
        d1 = abs_dev - mr_mean
        mr_mean += d1 / mr_n
        d2 = abs_dev - mr_mean
        mr_m2 += d1 * d2
    opt_state['mr_n'] = mr_n
    opt_state['mr_mean'] = mr_mean
    opt_state['mr_m2'] = mr_m2

    if mr_n > 30:
        mr_std = math.sqrt(mr_m2 / mr_n) if mr_m2 > 0 else 1.0
        und_mr_thr = mr_mean + 1.645 * mr_std  # 95th percentile
    else:
        und_mr_thr = und_mid * 0.0020  # fallback: 20bps of price

    # ---- Phase A: Gather pricing for all vouchers ----
    voucher_info = {}  # vsym -> {strike, theo, delta, vega, wall_mid, best_bid, best_ask, ...}
    for vsym in OPTION_SYMBOLS:
        strike = OPTION_STRIKES[vsym]
        vod = state.order_depths.get(vsym)
        if not vod:
            continue

        v_buys = vod.buy_orders
        v_sells = vod.sell_orders
        v_best_bid = max(v_buys) if v_buys else None
        v_best_ask = min(v_sells) if v_sells else None
        v_bid_wall = min(v_buys) if v_buys else None
        v_ask_wall = max(v_sells) if v_sells else None

        if v_best_bid is not None and v_best_ask is not None:
            v_wall_mid = (v_bid_wall + v_ask_wall) / 2.0
        elif v_best_ask is not None:
            v_wall_mid = v_best_ask - 0.5
            v_best_bid = v_best_ask - 1
        elif v_best_bid is not None:
            v_wall_mid = v_best_bid + 0.5
            v_best_ask = v_best_bid + 1
        else:
            continue

        iv = _get_iv(und_mid, strike, tte)
        theo, delta = _bs_call(und_mid, strike, tte, iv)
        vega = _bs_vega(und_mid, strike, tte, iv)

        theo_diff = v_wall_mid - theo

        # Update EMAs
        td_key = f'{vsym}_td'
        mean_theo_diff = _ema_update(
            opt_state.get(td_key, 0.0), theo_diff, cfg['theo_norm_window'])
        opt_state[td_key] = mean_theo_diff

        ad_key = f'{vsym}_ad'
        switch_mean = _ema_update(
            opt_state.get(ad_key, 0.0),
            abs(theo_diff - mean_theo_diff),
            cfg['iv_scalp_window'])
        opt_state[ad_key] = switch_mean

        # Mispricing score: positive = rich (sell), negative = cheap (buy)
        edge = theo_diff - mean_theo_diff

        voucher_info[vsym] = {
            'strike': strike, 'theo': theo, 'delta': delta, 'vega': vega,
            'wall_mid': v_wall_mid, 'best_bid': v_best_bid, 'best_ask': v_best_ask,
            'theo_diff': theo_diff, 'mean_td': mean_theo_diff,
            'switch_mean': switch_mean, 'edge': edge,
            'pos': positions.get(vsym, 0),
        }

    # ---- Collect Welford stats for adaptive thresholds ----
    # Collected continuously after stats_start; thresholds update every tick
    # adp_hs: half-spread = (best_ask - best_bid) / 2  → cost to cross the book
    # adp_sm: switch_mean = EMA(|theo_diff - mean_td|) → vol of mispricing
    # adp_cd: |combined_dev| = |ema_o_dev + iv_dev|    → 9500 MR signal magnitude
    if tick_count > cfg['stats_start'] and voucher_info:
        for vsym, info in voucher_info.items():
            strike = info['strike']
            if strike >= 9750:
                half_spread = (info['best_ask'] - info['best_bid']) / 2.0
                _welford_update(opt_state, 'adp_hs', half_spread)
                _welford_update(opt_state, 'adp_sm', info['switch_mean'])
            if strike == 9500:
                iv_dev = info['theo_diff'] - info['mean_td']
                _welford_update(opt_state, 'adp_cd', abs(ema_o_dev + iv_dev))

    if tick_count < cfg['warmup_ticks']:
        return all_orders, opt_state

    # ---- Compute adaptive thresholds from Welford stats ----
    #
    # thr_open = mean(half_spread)  [z=0, i.e. median]
    #   Rationale: edge must exceed half the bid-ask spread to cover crossing cost
    #   sell_edge = best_bid - BS_theo - mean_td;  profitable only if > half_spread
    thr_open = _welford_pct(opt_state, 'adp_hs', 0.0) or 0.5
    thr_close = cfg['thr_close']
    #
    # low_vega_adj = thr_open  (doubles the threshold for vega <= 1.0 options)
    #   Rationale: low-vega options have same spread cost but less edge per vol unit
    #   Total threshold: thr_open + low_vega_adj = 2 * thr_open
    low_vega_adj = thr_open
    #
    # iv_scalp_thr = P25(switch_mean)  [z=-0.674]
    #   Rationale: activate IV scalping when mispricing vol is above bottom quartile
    #   Lower percentile = more permissive (scalp more often)
    iv_scalp_thr = _welford_pct(opt_state, 'adp_sm', -0.674) or 0.7
    #
    # options_mr_thr = mean(|combined_dev|)  [z=0, i.e. median]
    #   Rationale: trade 9500 when combined deviation exceeds its own average
    #   combined_dev = underlying MR signal + option mispricing signal
    options_mr_thr = _welford_pct(opt_state, 'adp_cd', 0.0) or 5.0


    # ---- Phase B: Trade decisions based on OPTION_STRATEGY ----
    desired_trades = {}  # vsym -> qty
    pending_delta = 0.0

    ou_ready = tick_count >= ou_cfg['warmup']

    if OPTION_STRATEGY == 'baseline':
        # IV scalping + MR on 9500, with OU adaptive directional bias
        ranked = sorted(voucher_info.items(), key=lambda x: -x[1]['edge'])
        for vsym, info in ranked:
            strike = info['strike']
            pos = info['pos']
            pos_limit = POS_LIMIT[vsym]
            max_buy = pos_limit - pos
            max_sell = pos_limit + pos
            switch_mean = info['switch_mean']
            vega = info['vega']

            # ---- OU adaptive directional bias per strike ----
            # VR above mu (drift>0) -> reverts DOWN -> favor buying ITM, selling OTM
            # VR below mu (drift<0) -> reverts UP   -> favor buying OTM, selling ITM
            buy_mult = 1.0
            sell_mult = 1.0

            if ou_ready and ou_signal > 0.1:
                bias = ou_cfg['bias_strength'] * ou_signal
                revert_toward_strike = (ou_drift > 0 and strike < und_mid) or \
                                       (ou_drift < 0 and strike > und_mid)
                if revert_toward_strike:
                    buy_mult = 1.0 - bias   # easier to buy
                    sell_mult = 1.0 + bias   # harder to sell
                else:
                    buy_mult = 1.0 + bias   # harder to buy
                    sell_mult = 1.0 - bias   # easier to sell

            # ---- Strike gating ----
            if strike >= 10500:
                continue

            if strike == 10250:
                # Enable 10250 only when OU says VR reverting up toward it
                if not (ou_ready and ou_cfg['enable_otm_on_direction'] and ou_drift < 0):
                    continue

            if strike >= 9750:
                if switch_mean >= iv_scalp_thr:
                    lva = low_vega_adj if vega <= 1.0 else 0.0
                    sell_edge = info['theo_diff'] - info['wall_mid'] + info['best_bid'] - info['mean_td']
                    buy_edge = info['theo_diff'] - info['wall_mid'] + info['best_ask'] - info['mean_td']

                    # Apply OU directional bias to thresholds
                    adj_sell_thr = (thr_open + lva) * sell_mult
                    adj_buy_thr = (thr_open + lva) * buy_mult

                    if sell_edge >= adj_sell_thr and max_sell > 0:
                        desired_trades[vsym] = -max_sell
                        pending_delta += -max_sell * info['delta']
                    elif sell_edge >= thr_close and pos > 0:
                        desired_trades[vsym] = -pos
                        pending_delta += -pos * info['delta']
                    elif buy_edge <= -adj_buy_thr and max_buy > 0:
                        desired_trades[vsym] = max_buy
                        pending_delta += max_buy * info['delta']
                    elif buy_edge <= -thr_close and pos < 0:
                        desired_trades[vsym] = -pos
                        pending_delta += -pos * info['delta']
                else:
                    if pos > 0:
                        desired_trades[vsym] = -pos
                        pending_delta += -pos * info['delta']
                    elif pos < 0:
                        desired_trades[vsym] = -pos
                        pending_delta += -pos * info['delta']

            if strike == 9500:
                iv_dev = info['theo_diff'] - info['mean_td']
                combined_dev = ema_o_dev + iv_dev
                if combined_dev > options_mr_thr and max_sell > 0:
                    desired_trades[vsym] = -max_sell
                    pending_delta += -max_sell * info['delta']
                elif combined_dev < -options_mr_thr and max_buy > 0:
                    desired_trades[vsym] = max_buy
                    pending_delta += max_buy * info['delta']

    elif OPTION_STRATEGY == 'bull_spread':
        # Sell OTM calls (10250, 10500) to capture theta
        # Buy ITM calls (9500, 9750) as downside hedge / delta
        # ATM (10000) uses IV scalping as before
        for vsym, info in voucher_info.items():
            strike = info['strike']
            pos = info['pos']
            pos_limit = POS_LIMIT[vsym]
            max_buy = pos_limit - pos
            max_sell = pos_limit + pos
            switch_mean = info['switch_mean']
            vega = info['vega']

            if strike >= 10250:
                # OTM: sell to collect theta (only when rich or flat)
                sell_edge = info['theo_diff'] - info['mean_td']
                if sell_edge >= -thr_open and max_sell > 0:
                    desired_trades[vsym] = -max_sell
                    pending_delta += -max_sell * info['delta']
                elif sell_edge < -thr_open and pos < 0:
                    # Close short if option becomes cheap
                    desired_trades[vsym] = -pos
                    pending_delta += -pos * info['delta']

            elif strike <= 9750:
                # ITM: buy for delta hedge (only when cheap or flat)
                buy_edge = info['theo_diff'] - info['mean_td']
                if buy_edge <= thr_open and max_buy > 0:
                    desired_trades[vsym] = max_buy
                    pending_delta += max_buy * info['delta']
                elif buy_edge > thr_open and pos > 0:
                    # Close long if option becomes rich
                    desired_trades[vsym] = -pos
                    pending_delta += -pos * info['delta']

            elif strike == 10000:
                # ATM: IV scalping as baseline
                if switch_mean >= iv_scalp_thr:
                    lva = low_vega_adj if vega <= 1.0 else 0.0
                    s_edge = info['theo_diff'] - info['wall_mid'] + info['best_bid'] - info['mean_td']
                    b_edge = info['theo_diff'] - info['wall_mid'] + info['best_ask'] - info['mean_td']
                    if s_edge >= (thr_open + lva) and max_sell > 0:
                        desired_trades[vsym] = -max_sell
                        pending_delta += -max_sell * info['delta']
                    elif b_edge <= -(thr_open + lva) and max_buy > 0:
                        desired_trades[vsym] = max_buy
                        pending_delta += max_buy * info['delta']

    elif OPTION_STRATEGY == 'short_strangle':
        # Sell OTM calls (10250, 10500) AND sell ITM calls (9500, 9750) to collect theta
        # ATM (10000): IV scalping
        for vsym, info in voucher_info.items():
            strike = info['strike']
            pos = info['pos']
            pos_limit = POS_LIMIT[vsym]
            max_buy = pos_limit - pos
            max_sell = pos_limit + pos
            switch_mean = info['switch_mean']
            vega = info['vega']

            if strike == 10000:
                # ATM: IV scalping
                if switch_mean >= iv_scalp_thr:
                    lva = low_vega_adj if vega <= 1.0 else 0.0
                    s_edge = info['theo_diff'] - info['wall_mid'] + info['best_bid'] - info['mean_td']
                    b_edge = info['theo_diff'] - info['wall_mid'] + info['best_ask'] - info['mean_td']
                    if s_edge >= (thr_open + lva) and max_sell > 0:
                        desired_trades[vsym] = -max_sell
                        pending_delta += -max_sell * info['delta']
                    elif b_edge <= -(thr_open + lva) and max_buy > 0:
                        desired_trades[vsym] = max_buy
                        pending_delta += max_buy * info['delta']
                else:
                    if pos != 0:
                        desired_trades[vsym] = -pos
                        pending_delta += -pos * info['delta']
            else:
                # Both ITM and OTM: sell to collect theta when rich or flat
                sell_edge = info['theo_diff'] - info['mean_td']
                if sell_edge >= -thr_open and max_sell > 0:
                    desired_trades[vsym] = -max_sell
                    pending_delta += -max_sell * info['delta']
                elif sell_edge < -(thr_open * 2) and pos < 0:
                    # Close if option becomes very cheap (adverse move)
                    desired_trades[vsym] = -pos
                    pending_delta += -pos * info['delta']

    elif OPTION_STRATEGY == 'ratio_spread':
        # Buy 1x ITM (9500/9750), sell 2x OTM (10250/10500)
        # ATM (10000): IV scalping
        # Ratio creates net short vega + collects extra theta
        for vsym, info in voucher_info.items():
            strike = info['strike']
            pos = info['pos']
            pos_limit = POS_LIMIT[vsym]
            max_buy = pos_limit - pos
            max_sell = pos_limit + pos
            switch_mean = info['switch_mean']
            vega = info['vega']

            if strike >= 10250:
                # OTM: sell full position (theta collection)
                if max_sell > 0:
                    desired_trades[vsym] = -max_sell
                    pending_delta += -max_sell * info['delta']

            elif strike <= 9750:
                # ITM: buy half position (delta hedge, less capital)
                half_limit = pos_limit // 2
                target = half_limit - pos
                if target > 0 and max_buy > 0:
                    qty = min(target, max_buy)
                    desired_trades[vsym] = qty
                    pending_delta += qty * info['delta']
                elif pos > half_limit:
                    # Trim if over target
                    trim = pos - half_limit
                    desired_trades[vsym] = -trim
                    pending_delta += -trim * info['delta']

            elif strike == 10000:
                # ATM: IV scalping
                if switch_mean >= iv_scalp_thr:
                    lva = low_vega_adj if vega <= 1.0 else 0.0
                    s_edge = info['theo_diff'] - info['wall_mid'] + info['best_bid'] - info['mean_td']
                    b_edge = info['theo_diff'] - info['wall_mid'] + info['best_ask'] - info['mean_td']
                    if s_edge >= (thr_open + lva) and max_sell > 0:
                        desired_trades[vsym] = -max_sell
                        pending_delta += -max_sell * info['delta']
                    elif b_edge <= -(thr_open + lva) and max_buy > 0:
                        desired_trades[vsym] = max_buy
                        pending_delta += max_buy * info['delta']

    elif OPTION_STRATEGY == 'theta_harvest':
        # Sell richest-theta strikes (ATM/near-OTM: 10000, 10250)
        # Hedge with ITM (9500, 9750) — buy to offset delta
        # 10500: sell if any premium, otherwise ignore

        # Sort by theta/vega ratio to find richest theta
        for vsym, info in voucher_info.items():
            strike = info['strike']
            pos = info['pos']
            pos_limit = POS_LIMIT[vsym]
            max_buy = pos_limit - pos
            max_sell = pos_limit + pos
            vega = info['vega']

            if strike in (10000, 10250):
                # Sell for theta — these have highest theta
                sell_edge = info['theo_diff'] - info['mean_td']
                if sell_edge >= -thr_open and max_sell > 0:
                    desired_trades[vsym] = -max_sell
                    pending_delta += -max_sell * info['delta']
                elif sell_edge < -(thr_open * 2) and pos < 0:
                    desired_trades[vsym] = -pos
                    pending_delta += -pos * info['delta']

            elif strike <= 9750:
                # ITM: buy as delta hedge
                # Size proportional to short delta from sells
                buy_edge = info['theo_diff'] - info['mean_td']
                if buy_edge <= thr_open and max_buy > 0:
                    desired_trades[vsym] = max_buy
                    pending_delta += max_buy * info['delta']
                elif buy_edge > thr_open * 2 and pos > 0:
                    desired_trades[vsym] = -pos
                    pending_delta += -pos * info['delta']

            elif strike == 10500:
                # Deep OTM: sell if any premium left
                if info['theo'] > 1.0 and max_sell > 0:
                    desired_trades[vsym] = -max_sell
                    pending_delta += -max_sell * info['delta']

    # ---- Phase C: Execute orders ----
    for vsym, qty in desired_trades.items():
        if qty == 0 or vsym not in voucher_info:
            continue
        info = voucher_info[vsym]
        pos = info['pos']
        pos_limit = POS_LIMIT[vsym]

        # Clamp to position limits
        if qty > 0:
            qty = min(qty, pos_limit - pos)
            if qty > 0:
                all_orders[vsym] = [Order(vsym, info['best_ask'], qty)]
        elif qty < 0:
            qty = max(qty, -(pos_limit + pos))
            if qty < 0:
                all_orders[vsym] = [Order(vsym, info['best_bid'], qty)]

    # ---- Phase E: Underlying pure MR (adaptive threshold) ----
    und_pos = positions.get(OPTION_UNDERLYING, 0)
    und_limit = POS_LIMIT[OPTION_UNDERLYING]
    und_max_buy = und_limit - und_pos
    und_max_sell = und_limit + und_pos
    und_orders = []

    if und_mr_thr is not None:
        if ema_o_dev > und_mr_thr and und_max_sell > 0:
            und_orders.append(Order(OPTION_UNDERLYING, und_bid_wall + 1, -und_max_sell))
        elif ema_o_dev < -und_mr_thr and und_max_buy > 0:
            und_orders.append(Order(OPTION_UNDERLYING, und_ask_wall - 1, und_max_buy))

    if und_orders:
        all_orders[OPTION_UNDERLYING] = und_orders

    return all_orders, opt_state


# ================================================================
#  TRADER
# ================================================================

class Trader:

    def __init__(self):
        self._etf_cache = {}

    def run(self, state: TradingState):
        td = json.loads(state.traderData) if state.traderData else {}
        result, new_td = {}, {}

        # ---- Phase 1: Compute signals for all symbols ----
        signal_adjustments = {}
        if SIGNALS_ENABLED:
            for symbol, od in state.order_depths.items():
                if symbol not in POS_LIMIT:
                    continue
                sig_key = f'sig_{symbol}'
                sig_state = td.get(sig_key, {})
                trades = state.market_trades.get(symbol, [])
                adj, sig_state = compute_all_signals(symbol, trades, od, sig_state)
                signal_adjustments[symbol] = adj
                new_td[sig_key] = sig_state

        # ---- Phase 2: ETF spread trading for baskets ----
        constituent_ods = {c: state.order_depths[c] for c in CONSTITUENTS if c in state.order_depths}

        for basket in BASKETS:
            if basket in state.order_depths and len(constituent_ods) == len(CONSTITUENTS):
                etf_key = f'etf_{basket}'
                etf_state = td.get(etf_key) or self._etf_cache.get(etf_key, {})
                orders, etf_st = etf_trade(
                    basket, state.order_depths[basket], constituent_ods,
                    dict(state.position), etf_state,
                    adjustments=signal_adjustments.get(basket),
                )
                new_td[etf_key] = etf_st
                self._etf_cache[etf_key] = etf_st
                if orders:
                    result[basket] = orders

        # ---- Phase 3: Options theta decay for VOLCANIC_ROCK + vouchers ----
        opt_state = td.get('opt', {})
        opt_orders, opt_state = option_trade(state, dict(state.position), opt_state)
        new_td['opt'] = opt_state
        result.update(opt_orders)

        # ---- Phase 4: OU MM for all other products (skip options + baskets) ----
        for symbol, od in state.order_depths.items():
            if symbol in BASKETS or symbol in ALL_OPTION_PRODUCTS:
                continue
            if symbol not in POS_LIMIT:
                continue
            if od.buy_orders and od.sell_orders:
                orders, params = ou_trade(
                    symbol, od, state.position.get(symbol, 0),
                    td.get(symbol, {}),
                    adjustments=signal_adjustments.get(symbol),
                )
                result[symbol] = orders
                new_td[symbol] = params

        # ---- Phase 5: Serialize and export ----
        trader_data = json.dumps(new_td)

        # Log signal summary for post-hoc analysis
        sig_log = {}
        for sym, adj in signal_adjustments.items():
            if abs(adj['direction_bias']) > 0.05 or adj['spread_mult'] > 1.05:
                sig_log[sym] = {
                    'db': round(adj['direction_bias'], 3),
                    'sm': round(adj['spread_mult'], 3),
                    'ab': adj['agg_burst'],
                }
        logs = json.dumps({
            "GENERAL": {"TS": state.timestamp, "POS": dict(state.position)},
            "SIG": sig_log,
        })

        self._export(state, result, 0, trader_data, logs)
        return result, 0, trader_data

    def _export(self, state, orders, conversions, trader_data, logs):
        obs = state.observations
        compressed = [
            [state.timestamp, state.traderData,
             [[l.symbol, l.product, l.denomination] for l in state.listings.values()],
             {s: [od.buy_orders, od.sell_orders] for s, od in state.order_depths.items()},
             [[t.symbol, t.price, t.quantity, t.buyer or "", t.seller or "", t.timestamp] for ts in state.own_trades.values() for t in ts],
             [[t.symbol, t.price, t.quantity, t.buyer or "", t.seller or "", t.timestamp] for ts in state.market_trades.values() for t in ts],
             state.position,
             [obs.plainValueObservations, {p: [c.bidPrice, c.askPrice, c.transportFees, c.exportTariff, c.importTariff, c.sugarPrice, c.sunlightIndex] for p, c in obs.conversionObservations.items()}]],
            [[o.symbol, o.price, o.quantity] for ol in orders.values() for o in ol],
            conversions, trader_data, logs,
        ]
        print(json.dumps(compressed, separators=(',', ':')))
