import numpy as np
import json
from typing import Any
import math

import json
from typing import Any
from datamodel import *
from datamodel import Listing, Observation, Order, OrderDepth, ProsperityEncoder, Symbol, Trade, TradingState

class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state: TradingState, orders: dict[Symbol, list[Order]], conversions: int, trader_data: str) -> None:
        base_length = len(
            self.to_json(
                [
                    self.compress_state(state, ""),
                    self.compress_orders(orders),
                    conversions,
                    "",
                    "",
                ]
            )
        )

        # We truncate state.traderData, trader_data, and self.logs to the same max. length to fit the log limit
        max_item_length = (self.max_log_length - base_length) // 3

        print(
            self.to_json(
                [
                    self.compress_state(state, self.truncate(state.traderData, max_item_length)),
                    self.compress_orders(orders),
                    conversions,
                    self.truncate(trader_data, max_item_length),
                    self.truncate(self.logs, max_item_length),
                ]
            )
        )

        self.logs = ""


from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List


class Trader:
    def __init__(self):
        # Using a list instead of deque for fair value smoothing
        self.fv_history = []
        self.max_history = 4
        self.entry_price = 0 

    def run(self, state: TradingState):
        result = {}
        PRODUCT = 'TOMATOES'
        LIMIT = 20
        
        # --- RISK & MM CONFIG ---
        STOP_LOSS = 25.0      # Points per unit
        SKEW_FACTOR = 0.45    # Price shift per unit of inventory
        MIN_SPREAD = 2        # Minimum ticks from fair value
        MAX_INV_PCT = 0.9     # Don't exceed 18 units (90% of 20)

        if PRODUCT in state.order_depths:
            order_depth = state.order_depths[PRODUCT]
            current_pos = state.position.get(PRODUCT, 0)
            
            # 1. GET MARKET PRICES
            if not order_depth.buy_orders or not order_depth.sell_orders:
                return result, 0, ""

            best_bid = max(order_depth.buy_orders.keys())
            best_ask = min(order_depth.sell_orders.keys())
            mid_price = (best_bid + best_ask) / 2

            # 2. SIGNAL: IMBALANCE (Heatmap 0.27 Alpha)
            bid_vol = sum(order_depth.buy_orders.values())
            ask_vol = abs(sum(order_depth.sell_orders.values()))
            book_imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-9)
            
            # Calculate Fair Value (using the inverted sign found earlier)
            raw_fv = mid_price + (-0.78 * book_imbalance)
            
            # Manual Deque Logic using a List
            self.fv_history.append(raw_fv)
            if len(self.fv_history) > self.max_history:
                self.fv_history.pop(0)
            
            fair_value = sum(self.fv_history) / len(self.fv_history)

            # 3. EMERGENCY STOP LOSS CHECK
            is_stopped_out = False
            if current_pos > 0 and (mid_price - self.entry_price) < -STOP_LOSS:
                is_stopped_out = True
            elif current_pos < 0 and (mid_price - self.entry_price) > STOP_LOSS:
                is_stopped_out = True

            orders = []

            # 4. EXECUTION
            if is_stopped_out:
                # Liquidate immediately
                price = best_bid if current_pos > 0 else best_ask
                orders.append(Order(PRODUCT, price, -current_pos))
                self.entry_price = 0 
            else:
                # Inventory Skew: Adjust the 'center' based on what we hold
                # Positive pos (Long) -> Shift center DOWN to encourage selling
                center_price = fair_value - (current_pos * SKEW_FACTOR)
                
                # Calculate our Quotes
                bid_price = int(round(center_price - MIN_SPREAD))
                ask_price = int(round(center_price + MIN_SPREAD))

                # Place Buy Order (if under safety limit)
                if current_pos < (LIMIT * MAX_INV_PCT):
                    buy_qty = LIMIT - current_pos
                    orders.append(Order(PRODUCT, min(bid_price, best_ask - 1), buy_qty))

                # Place Sell Order (if under safety limit)
                if current_pos > -(LIMIT * MAX_INV_PCT):
                    sell_qty = LIMIT + current_pos
                    orders.append(Order(PRODUCT, max(ask_price, best_bid + 1), -sell_qty))

            # Update Entry Price Tracking
            if current_pos != 0 and self.entry_price == 0:
                self.entry_price = mid_price
            elif current_pos == 0:
                self.entry_price = 0

            result[PRODUCT] = orders

        return result, 0, ""