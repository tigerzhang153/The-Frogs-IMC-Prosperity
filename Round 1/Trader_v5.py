from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
import string
import json

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PEPPER = "INTARIAN_PEPPER_ROOT"
OSMIUM = "ASH_COATED_OSMIUM"
LIMIT = 80 

class Trader:
    def run(self, state: TradingState):
        # 1. State Persistence
        trader_data = {"pepper_mid": 10000.0} # Initialize with a baseline
        if state.traderData:
            try:
                trader_data = json.loads(state.traderData)
            except:
                pass
        
        result = {}

        for symbol in [PEPPER, OSMIUM]:
            if symbol not in state.order_depths:
                continue
                
            orders: List[Order] = []
            depth = state.order_depths[symbol]
            pos = state.position.get(symbol, 0)
            
            # --- FAIR VALUE CALCULATION ---
            # Extract current market mid-price
            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
            
            if best_bid and best_ask:
                market_mid = (best_bid + best_ask) / 2.0
            else:
                market_mid = trader_data.get(f"{symbol}_mid", 10000.0)

            # Update persistent mid-price (EMA-style smoothing to avoid noise)
            alpha = 0.4 
            fair = (alpha * market_mid) + (1 - alpha) * trader_data.get(f"{symbol}_mid", market_mid)
            trader_data[f"{symbol}_mid"] = fair

            # --- DYNAMIC PARAMETERS ---
            if symbol == PEPPER:
                # Pepper is trending; we need to stay closer to the spread to get filled
                half_spread = 3 
                skew_intensity = 0.25 
            else:
                # Osmium mean-reverts; we can afford to be slightly wider to capture the bounce
                half_spread = 5
                skew_intensity = 0.4

            # --- 2. AGGRESSIVE MARKET TAKING ---
            # If the market crosses our fair value, we take the liquidity instantly
            for price, vol in depth.sell_orders.items():
                if price <= fair - 1: # Profitable buy
                    qty = min(abs(vol), LIMIT - pos)
                    if qty > 0:
                        orders.append(Order(symbol, price, qty))
                        pos += qty

            for price, vol in depth.buy_orders.items():
                if price >= fair + 1: # Profitable sell
                    qty = min(vol, LIMIT + pos)
                    if qty > 0:
                        orders.append(Order(symbol, price, -qty))
                        pos -= qty

            # --- 3. LADDERED MARKET MAKING ---
            skew = pos * skew_intensity
            
            # Calculate Quote Prices
            bid_price = int(round(fair - half_spread - skew))
            ask_price = int(round(fair + half_spread - skew))

            # Ensure we aren't quoting inside the spread unless necessary
            if best_bid and bid_price > best_bid + 1: bid_price = best_bid + 1
            if best_ask and ask_price < best_ask - 1: ask_price = best_ask - 1

            # Inventory-based sizing: The more room we have, the more we quote
            buy_size = LIMIT - pos
            sell_size = LIMIT + pos

            if buy_size > 0:
                orders.append(Order(symbol, bid_price, buy_size))
            if sell_size > 0:
                orders.append(Order(symbol, ask_price, -sell_size))

            result[symbol] = orders

        return result, 0, json.dumps(trader_data)