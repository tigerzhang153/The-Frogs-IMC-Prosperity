"""
ETF Mean-Reversion Strategy v2
No hardcoded premium — learns premium via slow EMA from first tick.

Combines:
1. Spread-based stat-arb for ETF baskets (adaptive EMA premium, decay=0.9999)
2. OU-based market making for ALL other products including constituents
"""
from datamodel import OrderDepth, TradingState, Order
import json
import math

# Position limits per product
POS_LIMIT = {
    'EMERALDS': 80,
    'TOMATOES': 80,
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
}

# ---- ETF Configuration ----
BASKET_WEIGHTS = {
    'PICNIC_BASKET1': [6, 3, 1],   # [CROISSANTS, JAMS, DJEMBES]
    'PICNIC_BASKET2': [4, 2, 0],
}
CONSTITUENTS = ['CROISSANTS', 'JAMS', 'DJEMBES']
BASKETS = ['PICNIC_BASKET1', 'PICNIC_BASKET2']

ETF_PARAMS = {
    'PICNIC_BASKET1': {
        'ema_decay': 0.9999,     # very slow EMA, no hardcoded premium
        'threshold': 80,
        'inv_skew': 2.0,
    },
    'PICNIC_BASKET2': {
        'ema_decay': 0.9999,
        'threshold': 50,
        'inv_skew': 2.0,
    },
}

# ---- OU MM Configuration ----
DEFAULT_OU_PARAMS = {
    'mu': None, 'mu_decay': 0.995, 'theta': 0.1, 'sigma': 3.0,
    'take_sigma': 0.0, 'inv_skew': 1.0, 'ema_decay': 0.98,
}

OU_OVERRIDE = {
    'RAINFOREST_RESIN': {'mu': 10000.0, 'mu_decay': None, 'theta': 0.14, 'sigma': 3.9, 'take_sigma': None, 'inv_skew': 1.19, 'ema_decay': 0.982},
    'KELP':             {'mu_decay': 0.995, 'theta': 0.14, 'sigma': 7.5, 'inv_skew': 1.19, 'ema_decay': 0.982},
    'SQUID_INK':        {'mu_decay': 0.996, 'theta': 0.004, 'sigma': 2.1, 'inv_skew': 1.19, 'ema_decay': 0.982},
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


def get_ou_params(symbol):
    cfg = dict(DEFAULT_OU_PARAMS)
    cfg.update(OU_OVERRIDE.get(symbol, {}))
    return cfg


def get_mid(od):
    if od.buy_orders and od.sell_orders:
        return (max(od.buy_orders) + min(od.sell_orders)) / 2
    return None


# ---- ETF Spread Trading (baskets only) ----

def etf_trade(basket_name, basket_od, constituent_ods, positions, etf_state):
    """
    Spread-based mean-reversion for baskets.
    Phase 1 (warmup): collect spread samples, estimate premium via Bayesian updating.
    Phase 2 (trading): use estimated premium for spread signal + MM.
    No hardcoded premium — everything learned from data.
    """
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

    # --- Adaptive premium via EMA (same pattern as OU's adaptive mu) ---
    ema_premium = etf_state.get('sp', None)
    decay = cfg['ema_decay']
    ema_premium = raw_spread if ema_premium is None else decay * ema_premium + (1 - decay) * raw_spread

    premium = ema_premium
    dev = raw_spread - premium

    basket_limit = POS_LIMIT[basket_name]
    basket_pos = positions.get(basket_name, 0)
    max_buy = basket_limit - basket_pos
    max_sell = basket_limit + basket_pos
    orders = []

    threshold = cfg['threshold']

    # --- Spread signal: aggressive taking ---
    if dev > threshold and max_sell > 0:
        orders.append(Order(basket_name, best_bid, -max_sell))
        max_sell = 0
    elif dev < -threshold and max_buy > 0:
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

    # --- MM quotes with spread-aware skew ---
    fair = theo + premium
    inv_skew = round(cfg['inv_skew'] * basket_pos / basket_limit)
    fair_skew = round(fair - mid)
    skew = fair_skew - inv_skew

    bid_p = min(best_bid + skew, int(math.floor(mid)))
    ask_p = max(best_ask + skew, int(math.ceil(mid)))
    if bid_p >= ask_p:
        bid_p, ask_p = int(math.floor(mid)) - 1, int(math.ceil(mid)) + 1

    if max_buy > 0:
        orders.append(Order(basket_name, bid_p, max_buy))
    if max_sell > 0:
        orders.append(Order(basket_name, ask_p, -max_sell))

    return orders, {'sp': ema_premium}


# ---- OU Market Making ----

def ou_trade(symbol, order_depth, position, state_params):
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

    max_buy, max_sell = pos_limit - position, pos_limit + position
    orders = []
    take_sigma = cfg['take_sigma']

    if take_sigma is not None:
        buy_thresh = ou_fair - take_sigma * ou_std
        sell_thresh = ou_fair + take_sigma * ou_std
    else:
        buy_thresh, sell_thresh = wall_mid - 1, wall_mid + 1

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
    bid_price, ask_price = int(bid_wall + 1), int(ask_wall - 1)

    for bp, bv in sorted_bids:
        if bv > 1 and bp + 1 < wall_mid:
            bid_price = max(bid_price, bp + 1)
        elif bp < wall_mid:
            bid_price = max(bid_price, bp)
        break

    for sp, sv in sorted_asks:
        if abs(sv) > 1 and sp - 1 > wall_mid:
            ask_price = min(ask_price, sp - 1)
        elif sp > wall_mid:
            ask_price = min(ask_price, sp)
        break

    bid_price = min(bid_price + skew, int(math.floor(wall_mid)))
    ask_price = max(ask_price + skew, int(math.ceil(wall_mid)))
    if bid_price >= ask_price:
        bid_price, ask_price = int(math.floor(wall_mid)) - 1, int(math.ceil(wall_mid)) + 1

    if max_buy > 0:
        orders.append(Order(symbol, bid_price, max_buy))
    if max_sell > 0:
        orders.append(Order(symbol, ask_price, -max_sell))

    return orders, {'lm': mid, 'ev': ema_var, 'tn': ema_tn, 'td': ema_td, 'km': ema_mu}


class Trader:

    def __init__(self):
        # Cache ETF state across days (survives traderData reset in backtester)
        # In real competition, traderData persists so this is just a safety net.
        self._etf_cache = {}

    def run(self, state: TradingState):
        td = json.loads(state.traderData) if state.traderData else {}
        result, new_td = {}, {}

        constituent_ods = {c: state.order_depths[c] for c in CONSTITUENTS if c in state.order_depths}

        # ETF spread trading for baskets
        for basket in BASKETS:
            if basket in state.order_depths and len(constituent_ods) == len(CONSTITUENTS):
                etf_key = f'etf_{basket}'
                # Prefer traderData state; fall back to instance cache from prev day
                etf_state = td.get(etf_key) or self._etf_cache.get(etf_key, {})
                orders, etf_st = etf_trade(
                    basket, state.order_depths[basket], constituent_ods,
                    dict(state.position), etf_state
                )
                new_td[etf_key] = etf_st
                self._etf_cache[etf_key] = etf_st  # cache for next day
                if orders:
                    result[basket] = orders

        # OU MM for all other products (including constituents)
        for symbol, od in state.order_depths.items():
            if symbol in BASKETS:
                continue  # baskets handled above
            if symbol not in POS_LIMIT:
                continue
            if od.buy_orders and od.sell_orders:
                orders, params = ou_trade(symbol, od, state.position.get(symbol, 0), td.get(symbol, {}))
                result[symbol] = orders
                new_td[symbol] = params

        trader_data = json.dumps(new_td)
        logs = json.dumps({"GENERAL": {"TS": state.timestamp, "POS": dict(state.position)}})
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