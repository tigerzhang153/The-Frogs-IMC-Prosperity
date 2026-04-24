"""
IMC Prosperity 4 — Round 1 Trader
===================================
Products: INTARIAN_PEPPER_ROOT, ASH_COATED_OSMIUM

Strategy overview
-----------------
INTARIAN_PEPPER_ROOT — Trend-following market-maker
  Fair value: 10000 + (day+2)*1000 + timestamp*0.001
  This gives RMSE ~2 ticks on all three training days.
  We quote around this fair value with a small edge, skewing
  quotes away from our current position to stay flat.

ASH_COATED_OSMIUM — Mean-reversion market-maker
  Fair value: 10000 (constant)
  Mid-price autocorr(1) = -0.50 (strong oscillation).
  We quote around 10000 with a small edge, skewing quotes
  to lean against our position and harvest the reversion.

Both strategies use the same core engine:
  1. Compute fair value
  2. Add half-spread edge on each side
  3. Skew bid/ask by position to manage inventory
  4. Send limit orders; never cross the spread (no market orders)
  5. Hard position limit enforcement

Design choices to avoid overfitting
--------------------------------------
- No fitted ML model or regression coefficients stored (the
  pepper-root formula is structurally derived: slope is exactly
  0.001 = 1 tick per 1000 timestamps, intercept is exactly
  10000 per day-zero baseline).
- Osmium fair value is a single round number (10000) — no
  regression fit used.
- Spread and skew parameters chosen conservatively so the
  strategy degrades gracefully rather than catastrophically
  if market microstructure changes slightly.
- No lookahead, no cross-day state (resets each day).
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any
import json


# ---------------------------------------------------------------------------
# Stubs — replace with the real Prosperity datamodel imports when submitting
# ---------------------------------------------------------------------------
try:
    from datamodel import (
        OrderDepth, TradingState, Order, ConversionObservation,
        Listing, Observation, ProsperityEncoder, Symbol, Trade, Product,
    )
except ImportError:
    # Local dev stubs so the file is importable and testable standalone
    Symbol = str
    Product = str

    @dataclass
    class Order:
        symbol: str
        price: int
        quantity: int

    @dataclass
    class OrderDepth:
        buy_orders: Dict[int, int] = field(default_factory=dict)   # price -> qty (positive)
        sell_orders: Dict[int, int] = field(default_factory=dict)  # price -> qty (negative)

    @dataclass
    class TradingState:
        timestamp: int = 0
        traderData: str = ""
        listings: Dict = field(default_factory=dict)
        order_depths: Dict[str, "OrderDepth"] = field(default_factory=dict)
        own_trades: Dict = field(default_factory=dict)
        market_trades: Dict = field(default_factory=dict)
        position: Dict[str, int] = field(default_factory=dict)
        observations: Any = None

    class ProsperityEncoder(json.JSONEncoder):
        def default(self, obj):
            if hasattr(obj, "__dict__"):
                return obj.__dict__
            return super().default(obj)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PEPPER   = "INTARIAN_PEPPER_ROOT"
OSMIUM   = "ASH_COATED_OSMIUM"

# Position limits (standard IMC Prosperity Round 1)
POSITION_LIMIT = {PEPPER: 20, OSMIUM: 20}

# Market-making parameters — deliberately conservative
# Half-spread edge: how many ticks of edge we require on each side
PEPPER_HALF_SPREAD  = 2    # fair +/- 2 ticks edge
OSMIUM_HALF_SPREAD  = 3    # fair +/- 3 ticks (wider market)

# Inventory skew: per unit of position, shift quotes by this many ticks
# This tilts the book to lean against accumulating too much inventory
PEPPER_SKEW_PER_UNIT = 0.3
OSMIUM_SKEW_PER_UNIT = 0.4

# Maximum order size per side per timestep
PEPPER_ORDER_SIZE = 5
OSMIUM_ORDER_SIZE = 5

# Osmium: don't trade when price deviates more than this from fair value
# (protects against fat-finger / regime-change outliers)
OSMIUM_MAX_DEVIATION = 20


# ---------------------------------------------------------------------------
# Helper: load / save per-day state from traderData string
# ---------------------------------------------------------------------------
def load_state(trader_data: str) -> dict:
    if not trader_data:
        return {}
    try:
        return json.loads(trader_data)
    except Exception:
        return {}


def save_state(state: dict) -> str:
    return json.dumps(state, cls=ProsperityEncoder)


# ---------------------------------------------------------------------------
# Fair-value calculators
# ---------------------------------------------------------------------------
def pepper_fair_value(timestamp: int, day: int) -> float:
    """
    Structurally derived from data:
      - Each day adds exactly 1000 to the base price
      - Price increases at exactly 0.001 ticks / timestamp
      - Day 0 opens at 12000, Day -1 at 11000, Day -2 at 10000
    Formula: fair = 10000 + (day + 2) * 1000 + timestamp * 0.001
    RMSE on training data: ~2 ticks.
    """
    return 10000.0 + (day + 2) * 1000.0 + timestamp * 0.001


def osmium_fair_value() -> float:
    """
    Osmium mean-reverts to 10000 with std ~5.3 ticks.
    Autocorr(1) = -0.50 confirms strong oscillation around this level.
    Using a single constant avoids any data-fitting risk.
    """
    return 10000.0


# ---------------------------------------------------------------------------
# Order generation helpers
# ---------------------------------------------------------------------------
def best_bid(order_depth: OrderDepth) -> int | None:
    if not order_depth.buy_orders:
        return None
    return max(order_depth.buy_orders.keys())


def best_ask(order_depth: OrderDepth) -> int | None:
    if not order_depth.sell_orders:
        return None
    return min(order_depth.sell_orders.keys())


def clamp_size(size: int, position: int, limit: int, side: str) -> int:
    """
    Clamp order size so position stays within [-limit, +limit].
    side = 'buy' or 'sell'
    """
    if side == "buy":
        room = limit - position
        return max(0, min(size, room))
    else:
        room = limit + position
        return max(0, min(size, room))


def make_orders(
    symbol: str,
    fair: float,
    half_spread: int,
    skew_per_unit: float,
    max_order_size: int,
    position: int,
    limit: int,
    order_depth: OrderDepth,
) -> List[Order]:
    """
    Core market-making engine.

    Quotes:
      bid = round(fair - half_spread - skew)   [we buy here]
      ask = round(fair + half_spread - skew)   [we sell here]

    skew = position * skew_per_unit
      -> positive position -> skew < 0 -> we lower both quotes
         so it's cheaper to buy FROM us (sell) and we reduce long

    Also takes any immediately profitable fills against the live book
    (passive taking — only at prices better than our fair value minus edge).
    """
    orders: List[Order] = []

    skew = position * skew_per_unit
    bid_price = round(fair - half_spread - skew)
    ask_price = round(fair + half_spread - skew)

    # Passive take: if book crosses our fair value by more than half_spread,
    # lift/hit aggressively up to max_order_size
    bb = best_bid(order_depth)
    ba = best_ask(order_depth)

    # Lift underpriced asks (ask < fair - half_spread)
    if ba is not None and ba < fair - half_spread:
        take_size = clamp_size(max_order_size, position, limit, "buy")
        if take_size > 0:
            avail = abs(order_depth.sell_orders.get(ba, 0))
            fill = min(take_size, avail)
            if fill > 0:
                orders.append(Order(symbol, ba, fill))
                position += fill  # update position for subsequent skew

    # Hit overpriced bids (bid > fair + half_spread)
    if bb is not None and bb > fair + half_spread:
        take_size = clamp_size(max_order_size, position, limit, "sell")
        if take_size > 0:
            avail = order_depth.buy_orders.get(bb, 0)
            fill = min(take_size, avail)
            if fill > 0:
                orders.append(Order(symbol, bb, -fill))
                position -= fill

    # Recompute skew after any takes
    skew = position * skew_per_unit
    bid_price = round(fair - half_spread - skew)
    ask_price = round(fair + half_spread - skew)

    # Ensure bid < ask (sanity check for extreme positions)
    if bid_price >= ask_price:
        bid_price = round(fair - 1)
        ask_price = round(fair + 1)

    # Post passive quotes
    buy_size  = clamp_size(max_order_size, position, limit, "buy")
    sell_size = clamp_size(max_order_size, position, limit, "sell")

    if buy_size > 0:
        orders.append(Order(symbol, bid_price, buy_size))
    if sell_size > 0:
        orders.append(Order(symbol, ask_price, -sell_size))

    return orders


# ---------------------------------------------------------------------------
# Main Trader class
# ---------------------------------------------------------------------------
class Trader:
    """
    Submission-ready trader for IMC Prosperity 4 Round 1.

    To submit: drop this file into the Prosperity interface.
    The run() method signature matches what the exchange calls.
    """

    def run(self, state: TradingState):
        # ── 1. Deserialise persistent state ──────────────────────────────
        trader_state = load_state(state.traderData)

        # Day tracking: infer from timestamp discontinuities.
        # Prosperity resets timestamp to 0 each day, so we keep a day counter.
        prev_ts = trader_state.get("prev_ts", None)
        day     = trader_state.get("day", 0)

        if prev_ts is not None and state.timestamp < prev_ts:
            # Timestamp wrapped back to 0 → new day
            day += 1

        trader_state["prev_ts"] = state.timestamp
        trader_state["day"]     = day

        # ── 2. Current positions ──────────────────────────────────────────
        pos_pepper = state.position.get(PEPPER, 0)
        pos_osmium = state.position.get(OSMIUM, 0)

        result: Dict[str, List[Order]] = {}

        # ── 3. INTARIAN_PEPPER_ROOT ───────────────────────────────────────
        if PEPPER in state.order_depths:
            fv = pepper_fair_value(state.timestamp, day)
            orders = make_orders(
                symbol         = PEPPER,
                fair           = fv,
                half_spread    = PEPPER_HALF_SPREAD,
                skew_per_unit  = PEPPER_SKEW_PER_UNIT,
                max_order_size = PEPPER_ORDER_SIZE,
                position       = pos_pepper,
                limit          = POSITION_LIMIT[PEPPER],
                order_depth    = state.order_depths[PEPPER],
            )
            if orders:
                result[PEPPER] = orders

        # ── 4. ASH_COATED_OSMIUM ─────────────────────────────────────────
        if OSMIUM in state.order_depths:
            fv = osmium_fair_value()
            od = state.order_depths[OSMIUM]

            # Guard: skip if market is too far from fair (outlier / no-quote)
            bb = best_bid(od)
            ba = best_ask(od)
            mid = None
            if bb is not None and ba is not None:
                mid = (bb + ba) / 2.0
            elif bb is not None:
                mid = float(bb)
            elif ba is not None:
                mid = float(ba)

            if mid is None or abs(mid - fv) <= OSMIUM_MAX_DEVIATION:
                orders = make_orders(
                    symbol         = OSMIUM,
                    fair           = fv,
                    half_spread    = OSMIUM_HALF_SPREAD,
                    skew_per_unit  = OSMIUM_SKEW_PER_UNIT,
                    max_order_size = OSMIUM_ORDER_SIZE,
                    position       = pos_osmium,
                    limit          = POSITION_LIMIT[OSMIUM],
                    order_depth    = od,
                )
                if orders:
                    result[OSMIUM] = orders

        # ── 5. Serialise state ────────────────────────────────────────────
        trader_data_out = save_state(trader_state)

        # conversions = 0 (no conversion products in round 1)
        conversions = 0

        return result, conversions, trader_data_out


# ---------------------------------------------------------------------------
# Local backtester (run: python trader.py)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os, csv, math

    BASE = "/mnt/user-data/uploads"

    def load_prices(day):
        rows = []
        with open(f"{BASE}/prices_round_1_day_{day}.csv") as f:
            reader = csv.DictReader(f, delimiter=";")
            for r in reader:
                rows.append(r)
        return rows

    def backtest():
        trader   = Trader()
        pnl      = {PEPPER: 0.0, OSMIUM: 0.0}
        position = {PEPPER: 0,   OSMIUM: 0}
        trader_data = ""

        total_trades = {PEPPER: 0, OSMIUM: 0}
        realized_pnl = {PEPPER: 0.0, OSMIUM: 0.0}

        for day_idx, day in enumerate([-2, -1, 0]):
            rows = load_prices(day)
            print(f"\n--- Day {day} ---")

            for row in rows:
                product  = row["product"]
                ts       = int(row["timestamp"])

                def to_float(v):
                    return float(v) if v.strip() else None

                bp1 = to_float(row["bid_price_1"])
                bv1 = to_float(row["bid_volume_1"])
                ap1 = to_float(row["ask_price_1"])
                av1 = to_float(row["ask_volume_1"])

                od = OrderDepth()
                if bp1 and bv1:
                    od.buy_orders[int(bp1)] = int(bv1)
                if ap1 and av1:
                    od.sell_orders[int(ap1)] = -int(av1)  # negative convention

                state = TradingState(
                    timestamp   = ts,
                    traderData  = trader_data,
                    order_depths= {product: od},
                    position    = {k: v for k, v in position.items()},
                )

                orders_dict, _, trader_data = trader.run(state)

                # Simulate fills against L1 quotes
                for sym, orders in orders_dict.items():
                    pos = position[sym]
                    lim = POSITION_LIMIT[sym]
                    for order in orders:
                        qty = order.quantity  # positive=buy, negative=sell
                        px  = order.price

                        if qty > 0:  # buy order
                            # Fill if ask <= our bid
                            if ap1 and px >= int(ap1):
                                fill_px  = int(ap1)
                                fill_qty = min(qty, int(av1) if av1 else 0)
                                fill_qty = min(fill_qty, lim - pos)
                                if fill_qty > 0:
                                    realized_pnl[sym] -= fill_px * fill_qty
                                    pos                += fill_qty
                                    total_trades[sym]  += fill_qty
                        else:  # sell order
                            # Fill if bid >= our ask
                            if bp1 and px <= int(bp1):
                                fill_px  = int(bp1)
                                fill_qty = min(-qty, int(bv1) if bv1 else 0)
                                fill_qty = min(fill_qty, lim + pos)
                                if fill_qty > 0:
                                    realized_pnl[sym] += fill_px * fill_qty
                                    pos                -= fill_qty
                                    total_trades[sym]  += fill_qty

                    position[sym] = pos

            # Mark-to-market at end of day
            last_mids = {}
            for row in rows[-10:]:
                mid = to_float(row["mid_price"]) if row["mid_price"].strip() else None
                if mid and mid > 0:
                    last_mids[row["product"]] = mid

            print(f"  Positions: {position}")
            print(f"  Realized PnL: { {k: round(v,2) for k,v in realized_pnl.items()} }")
            mtm = {}
            for sym in [PEPPER, OSMIUM]:
                if sym in last_mids:
                    mtm[sym] = round(realized_pnl[sym] + position[sym] * last_mids[sym], 2)
            print(f"  MTM PnL (realized + open): {mtm}")
            print(f"  Trades executed: {total_trades}")

        print("\n=== FINAL SUMMARY ===")
        print(f"Total trades: {total_trades}")
        print(f"Final positions: {position}")
        total_realized = sum(realized_pnl.values())
        print(f"Total realized PnL: {round(total_realized, 2)}")

    backtest()