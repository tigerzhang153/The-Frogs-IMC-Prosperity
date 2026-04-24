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
        # 1. State Persistence - Storing our calculated mid-prices
        trader_data = {}
        if state.traderData:
            try:
                trader_data = json.loads(state.traderData)
            except:
                trader_data = {}
        
        result = {}

        for symbol in [PEPPER, OSMIUM]:
            if symbol not in state.order_depths:
                continue
                
            orders: List[Order] = []
            depth = state.order_depths[symbol]
            pos = state.position.get(symbol, 0)
            
            # --- 2. CALCULATE LIVE FAIR VALUE (Volume Weighted) ---
            # We look at the top level of the book to see where the "real" price is
            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
            
            if best_bid and best_ask:
                # Volume weighting prevents getting fooled by tiny "bait" orders
                b_vol = depth.buy_orders[best_bid]
                a_vol = abs(depth.sell_orders[best_ask])
                vwap_mid = (best_bid * a_vol + best_ask * b_vol) / (a_vol + b_vol)
            else:
                vwap_mid = trader_data.get(f"{symbol}_mid", 10000.0)

            # Smooth the fair value to avoid chasing micro-noise
            prev_fair = trader_data.get(f"{symbol}_mid", vwap_mid)
            fair = 0.5 * vwap_mid + 0.5 * prev_fair
            trader_data[f"{symbol}_mid"] = fair

            # --- 3. DYNAMIC STRATEGY PARAMETERS ---
            if symbol == PEPPER:
                # Tight spreads for trending roots to maximize fill rate
                half_spread = 2 
                skew_intensity = 0.3 # Stronger skew to keep position near zero
            else:
                # Wider spreads for Osmium to capture mean-reversion profits
                half_spread = 4
                skew_intensity = 0.4

            # --- 4. SNIPING ENGINE (Aggressive Taking) ---
            # If anyone is selling cheaper than our fair value, buy it now.
            for price, vol in depth.sell_orders.items():
                if price <= fair - 1:
                    qty = min(abs(vol), LIMIT - pos)
                    if qty > 0:
                        orders.append(Order(symbol, price, qty))
                        pos += qty

            # If anyone is buying higher than our fair value, sell it now.
            for price, vol in depth.buy_orders.items():
                if price >= fair + 1:
                    qty = min(vol, LIMIT + pos)
                    if qty > 0:
                        orders.append(Order(symbol, price, -qty))
                        pos -= qty

            # --- 5. MARKET MAKING QUOTES ---
            # Shift quotes based on inventory (Skewing)
            skew = pos * skew_intensity
            bid_price = int(round(fair - half_spread - skew))
            ask_price = int(round(fair + half_spread - skew))

            # Never allow quotes to be uncompetitive
            if best_bid: bid_price = max(bid_price, best_bid - 1)
            if best_ask: ask_price = min(ask_price, best_ask + 1)
            
            # Ensure we don't cross ourselves
            if bid_price >= ask_price:
                bid_price, ask_price = int(fair - 1), int(fair + 1)

            # Maximize volume within limits
            buy_size = LIMIT - pos
            sell_size = LIMIT + pos

            if buy_size > 0:
                orders.append(Order(symbol, bid_price, buy_size))
            if sell_size > 0:
                orders.append(Order(symbol, ask_price, -sell_size))

            result[symbol] = orders

        return result, 0, json.dumps(trader_data)