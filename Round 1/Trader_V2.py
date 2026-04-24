from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
import string
import json

# ---------------------------------------------------------------------------
# Constants & Configuration
# ---------------------------------------------------------------------------
PEPPER = "INTARIAN_PEPPER_ROOT"
OSMIUM = "ASH_COATED_OSMIUM"

# Updated for Round 1 position limits
POSITION_LIMIT = {PEPPER: 80, OSMIUM: 80}

# Market-making parameters
PEPPER_HALF_SPREAD = 2
OSMIUM_HALF_SPREAD = 3

# Inventory skew: shifts quotes to lean against position accumulation
PEPPER_SKEW_PER_UNIT = 0.15  # Adjusted for larger 80 limit
OSMIUM_SKEW_PER_UNIT = 0.20

PEPPER_ORDER_SIZE = 10
OSMIUM_ORDER_SIZE = 10

OSMIUM_MAX_DEVIATION = 25

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def pepper_fair_value(timestamp: int, day: int) -> float:
    """
    Derived price trend: base + daily offset + drift.
    """
    return 10000.0 + (day + 2) * 1000.0 + timestamp * 0.001

def osmium_fair_value() -> float:
    """
    Osmium oscillates around a 10000 mean.
    """
    return 10000.0

def clamp_size(size: int, current_pos: int, limit: int, side: str) -> int:
    if side == "buy":
        return max(0, min(size, limit - current_pos))
    else:
        return max(0, min(size, limit + current_pos))

# ---------------------------------------------------------------------------
# Main Trader Class
# ---------------------------------------------------------------------------

class Trader:
    def run(self, state: TradingState):
        """
        The main entry point for the IMC Prosperity engine.
        """
        # 1. Handle persistent state
        trader_data = {}
        if state.traderData:
            try:
                trader_data = json.loads(state.traderData)
            except:
                trader_data = {}

        prev_ts = trader_data.get("prev_ts", 0)
        day = trader_data.get("day", 0)
        if state.timestamp < prev_ts:
            day += 1
        
        trader_data["prev_ts"] = state.timestamp
        trader_data["day"] = day

        result = {}

        # 2. Process each product
        for symbol in [PEPPER, OSMIUM]:
            if symbol not in state.order_depths:
                continue
                
            orders: List[Order] = []
            order_depth = state.order_depths[symbol]
            current_pos = state.position.get(symbol, 0)
            limit = POSITION_LIMIT[symbol]
            
            # Determine Fair Value
            if symbol == PEPPER:
                fv = pepper_fair_value(state.timestamp, day)
                half_spread = PEPPER_HALF_SPREAD
                skew_val = PEPPER_SKEW_PER_UNIT
                max_qty = PEPPER_ORDER_SIZE
            else:
                fv = osmium_fair_value()
                half_spread = OSMIUM_HALF_SPREAD
                skew_val = OSMIUM_SKEW_PER_UNIT
                max_qty = OSMIUM_ORDER_SIZE
                
                # Osmium Guard: Avoid trading if mid-price is too far from mean
                all_bids = list(order_depth.buy_orders.keys())
                all_asks = list(order_depth.sell_orders.keys())
                if all_bids and all_asks:
                    mid = (max(all_bids) + min(all_asks)) / 2.0
                    if abs(mid - fv) > OSMIUM_MAX_DEVIATION:
                        continue

            # 3. Aggressive "Passive Take" 
            # If market offers a price better than our fair +/- edge, take it immediately
            
            # Buy underpriced sells
            sorted_asks = sorted(order_depth.sell_orders.items())
            for ask_price, ask_qty in sorted_asks:
                if ask_price < fv - half_spread:
                    buy_qty = clamp_size(abs(ask_qty), current_pos, limit, "buy")
                    if buy_qty > 0:
                        orders.append(Order(symbol, ask_price, buy_qty))
                        current_pos += buy_qty
            
            # Sell overpriced bids
            sorted_bids = sorted(order_depth.buy_orders.items(), reverse=True)
            for bid_price, bid_qty in sorted_bids:
                if bid_price > fv + half_spread:
                    sell_qty = clamp_size(bid_qty, current_pos, limit, "sell")
                    if sell_qty > 0:
                        orders.append(Order(symbol, bid_price, -sell_qty))
                        current_pos -= sell_qty

            # 4. Market Making (Passive Quotes)
            # Apply skew: if we are long, we lower our prices to discourage buying and encourage selling.
            skew = current_pos * skew_val
            bid_price = int(round(fv - half_spread - skew))
            ask_price = int(round(fv + half_spread - skew))
            
            # Final sanity check to avoid crossing our own spread
            if bid_price >= ask_price:
                bid_price = int(fv - 1)
                ask_price = int(fv + 1)

            final_buy_qty = clamp_size(max_qty, current_pos, limit, "buy")
            if final_buy_qty > 0:
                orders.append(Order(symbol, bid_price, final_buy_qty))
                
            final_sell_qty = clamp_size(max_qty, current_pos, limit, "sell")
            if final_sell_qty > 0:
                orders.append(Order(symbol, ask_price, -final_sell_qty))

            result[symbol] = orders

        # 5. Export state and return
        new_trader_data = json.dumps(trader_data)
        return result, 0, new_trader_data