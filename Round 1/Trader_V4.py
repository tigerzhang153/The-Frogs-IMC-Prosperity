from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
import string
import json

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PEPPER = "INTARIAN_PEPPER_ROOT"
OSMIUM = "ASH_COATED_OSMIUM"
LIMIT = 80  # Round 1 Limit

class Trader:
    def run(self, state: TradingState):
        # 1. State Persistence
        trader_data = {"prev_ts": 0, "day": 0}
        if state.traderData:
            try:
                trader_data = json.loads(state.traderData)
            except:
                pass

        # Detect new trading days to update Pepper Root drift baseline
        if state.timestamp < trader_data.get("prev_ts", 0):
            trader_data["day"] = trader_data.get("day", 0) + 1
        
        trader_data["prev_ts"] = state.timestamp
        result = {}

        for symbol in [PEPPER, OSMIUM]:
            if symbol not in state.order_depths:
                continue
                
            orders: List[Order] = []
            depth = state.order_depths[symbol]
            pos = state.position.get(symbol, 0)
            
            # --- PRODUCT SPECIFIC TUNING ---
            if symbol == PEPPER:
                # Drift: 10k base + 1k/day + 0.001/tick drift
                fair = 10000.0 + (trader_data["day"] + 2) * 1000.0 + state.timestamp * 0.001
                # Target capturing ~6 ticks of the 12-tick mean spread
                half_spread = 5 
                skew_intensity = 0.2  # Shift 1 tick for every 5 units of position
            else:
                # Osmium mean reverts strictly to 10k
                fair = 10000.0
                # Target capturing ~7 ticks of the 16-tick mean spread
                half_spread = 6
                skew_intensity = 0.3 # Shift 1 tick for every ~3 units of position

            # --- 2. AGGRESSIVE SNIPING (Market Taking) ---
            # We "snipe" any existing orders that cross our fair value by even 1 tick
            
            # Sniping Sells (Buy)
            for price, vol in sorted(depth.sell_orders.items()):
                if price <= (fair - 1): 
                    buy_qty = min(abs(vol), LIMIT - pos)
                    if buy_qty > 0:
                        orders.append(Order(symbol, price, buy_qty))
                        pos += buy_qty

            # Sniping Buys (Sell)
            for price, vol in sorted(depth.buy_orders.items(), reverse=True):
                if price >= (fair + 1):
                    sell_qty = min(vol, LIMIT + pos)
                    if sell_qty > 0:
                        orders.append(Order(symbol, price, -sell_qty))
                        pos -= sell_qty

            # --- 3. LADDERED MARKET MAKING (Quoting) ---
            # Instead of one big order, we place 2 layers to capture volatility spikes
            skew = pos * skew_intensity
            
            # Layer 1: Near the spread
            bid_1 = int(round(fair - half_spread - skew))
            ask_1 = int(round(fair + half_spread - skew))
            
            # Layer 2: Deep liquidity (1-2 ticks further out)
            bid_2 = bid_1 - 2
            ask_2 = ask_1 + 2

            # Divide remaining capacity into the ladder
            can_buy = LIMIT - pos
            can_sell = LIMIT + pos

            if can_buy > 0:
                # Place 60% at L1, 40% at L2
                vol1 = int(can_buy * 0.6)
                vol2 = can_buy - vol1
                if vol1 > 0: orders.append(Order(symbol, bid_1, vol1))
                if vol2 > 0: orders.append(Order(symbol, bid_2, vol2))
                
            if can_sell > 0:
                vol1 = int(can_sell * 0.6)
                vol2 = can_sell - vol1
                if vol1 > 0: orders.append(Order(symbol, ask_1, -vol1))
                if vol2 > 0: orders.append(Order(symbol, ask_2, -vol2))

            result[symbol] = orders

        return result, 0, json.dumps(trader_data)