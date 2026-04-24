from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
import string
import json

# ---------------------------------------------------------------------------
# Constants & Configuration
# ---------------------------------------------------------------------------
PEPPER = "INTARIAN_PEPPER_ROOT"
OSMIUM = "ASH_COATED_OSMIUM"

# Round 1 Position Limits
LIMIT = 80

# ---------------------------------------------------------------------------
# Main Trader Class
# ---------------------------------------------------------------------------

class Trader:
    def run(self, state: TradingState):
        """
        Submission-ready for IMC Prosperity 4 - Round 1.
        """
        # 1. Recover persistent state
        trader_data = {"prev_ts": 0, "day": 0}
        if state.traderData:
            try:
                trader_data = json.loads(state.traderData)
            except:
                pass

        # Detect day transition (timestamp resets to 0)
        if state.timestamp < trader_data.get("prev_ts", 0):
            trader_data["day"] = trader_data.get("day", 0) + 1
        
        trader_data["prev_ts"] = state.timestamp
        result = {}

        # 2. Strategy Logic per Product
        for symbol in [PEPPER, OSMIUM]:
            if symbol not in state.order_depths:
                continue
                
            orders: List[Order] = []
            depth = state.order_depths[symbol]
            pos = state.position.get(symbol, 0)
            
            # --- COMPUTE FAIR VALUE ---
            if symbol == PEPPER:
                # Based on your Mid Price chart: slope is 0.001, base shifts +1000/day
                fair = 10000.0 + (trader_data["day"] + 2) * 1000.0 + state.timestamp * 0.001
                half_spread = 2
                skew_factor = 0.12  # Sensitivity to position for skewing
            else:
                # Based on your Mid Price chart: tight reversion around 10k
                fair = 10000.0
                half_spread = 3
                skew_factor = 0.20

            # --- 3. MARKET TAKING (PASSIVE) ---
            # Buy if someone is selling below our 'buy' threshold
            for price, vol in depth.sell_orders.items():
                if price < fair - half_spread:
                    buy_vol = min(abs(vol), LIMIT - pos)
                    if buy_vol > 0:
                        orders.append(Order(symbol, price, buy_vol))
                        pos += buy_vol

            # Sell if someone is buying above our 'sell' threshold
            for price, vol in depth.buy_orders.items():
                if price > fair + half_spread:
                    sell_vol = min(vol, LIMIT + pos)
                    if sell_vol > 0:
                        orders.append(Order(symbol, price, -sell_vol))
                        pos -= sell_vol

            # --- 4. MARKET MAKING (QUOTING) ---
            # Use inventory skew to pull position back to zero
            # skew > 0 (long) -> lowers quotes -> less likely to buy, more likely to sell
            skew = pos * skew_factor
            bid_price = int(round(fair - half_spread - skew))
            ask_price = int(round(fair + half_spread - skew))

            # Guard against crossing or invalid spreads
            if bid_price >= ask_price:
                bid_price, ask_price = int(fair - 1), int(fair + 1)

            # Place resting limit orders if within limits
            if pos < LIMIT:
                orders.append(Order(symbol, bid_price, LIMIT - pos))
            if pos > -LIMIT:
                orders.append(Order(symbol, ask_price, -(LIMIT + pos)))

            result[symbol] = orders

        return result, 0, json.dumps(trader_data)