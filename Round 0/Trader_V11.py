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




class Trader:
    def __init__(self):
        self.fv_history = []
        self.max_history = 5
        self.entry_price = 0 

    def run(self, state: TradingState):
        result = {}
        PRODUCT = 'TOMATOES'
        LIMIT = 20
        
        # --- FINAL TUNED CONFIG ---
        SKEW_FACTOR = 0.5     # Aggressive skew to keep inventory near zero
        VOL_ADJ_SENS = 0.1    # How much to widen spread based on low volume
        BASE_SPREAD = 2.0     # Minimum profit per trade
        MAX_INV_PCT = 0.85    # Leave 15% room for emergency exits

        if PRODUCT in state.order_depths:
            order_depth = state.order_depths[PRODUCT]
            current_pos = state.position.get(PRODUCT, 0)
            
            if not order_depth.buy_orders or not order_depth.sell_orders:
                return result, 0, ""

            best_bid = max(order_depth.buy_orders.keys())
            best_ask = min(order_depth.sell_orders.keys())
            mid_price = (best_bid + best_ask) / 2

            # 1. DYNAMIC VOLATILITY MEASUREMENT
            # We measure 'thinness' of the book
            total_vol = sum(order_depth.buy_orders.values()) + abs(sum(order_depth.sell_orders.values()))
            # If total_vol is low, spread increases
            dynamic_spread = BASE_SPREAD + (1000 / (total_vol + 1)) * VOL_ADJ_SENS

            # 2. SIGNAL (The 'Alpha')
            bid_vol = sum(order_depth.buy_orders.values())
            ask_vol = abs(sum(order_depth.sell_orders.values()))
            book_imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-9)
            
            raw_fv = mid_price + (-0.80 * book_imbalance)
            self.fv_history.append(raw_fv)
            if len(self.fv_history) > self.max_history: self.fv_history.pop(0)
            fair_value = sum(self.fv_history) / len(self.fv_history)

            # 3. CENTER & QUOTES
            center_price = fair_value - (current_pos * SKEW_FACTOR)
            
            orders = []
            
            # 4. BID (Buy)
            bid_price = int(round(center_price - dynamic_spread))
            if current_pos < (LIMIT * MAX_INV_PCT):
                buy_qty = LIMIT - current_pos
                orders.append(Order(PRODUCT, min(bid_price, best_ask - 1), buy_qty))

            # 5. ASK (Sell)
            ask_price = int(round(center_price + dynamic_spread))
            if current_pos > -(LIMIT * MAX_INV_PCT):
                sell_qty = LIMIT + current_pos
                orders.append(Order(PRODUCT, max(ask_price, best_bid + 1), -sell_qty))

            result[PRODUCT] = orders

        return result, 0, ""