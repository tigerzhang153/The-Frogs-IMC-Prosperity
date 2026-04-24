from typing import List
import string
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




from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import collections

class Trader:
    def __init__(self):
        # Smoothing Fair Value over 5 ticks to dampen spikes
        self.fv_history = collections.deque(maxlen=5) 
        self.position_start_time = 0
        
    def run(self, state: TradingState):
        result = {}
        
        # --- 1. OPTIMIZED PARAMETERS ---
        # Based on your inverted result, we keep BETA negative
        BETA_IMBALANCE = -5.537474699352795
        INTERCEPT = -0.009508631818786474
        
        LIMIT = 20
        PRODUCT = 'TOMATOES'
        
        # Lowering these slightly to get the bot trading again
        ENTRY_THRESHOLD = 2.5  
        EXIT_THRESHOLD = 0.8   
        MAX_CONV_GAP = 8.0     
        TIME_STOP_LIMIT = 60   # Exit if held for 60 timestamps (~6 seconds)

        if PRODUCT in state.order_depths:
            order_depth = state.order_depths[PRODUCT]
            current_pos = state.position.get(PRODUCT, 0)
            
            best_bid = max(order_depth.buy_orders.keys())
            best_ask = min(order_depth.sell_orders.keys())
            mid_price = (best_bid + best_ask) / 2
            
            # --- 2. SIGNAL ---
            bid_vol = sum(order_depth.buy_orders.values())
            ask_vol = abs(sum(order_depth.sell_orders.values()))
            book_imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-9)

            expected_move = INTERCEPT + (BETA_IMBALANCE * book_imbalance)
            self.fv_history.append(mid_price + expected_move)
            fair_value = sum(self.fv_history) / len(self.fv_history)
            
            gap = fair_value - mid_price
            target_pos = current_pos
            
            # --- 3. TIME STOP TRACKING ---
            if current_pos == 0:
                self.position_start_time = state.timestamp
            
            # Check if we've exceeded the time limit
            time_elapsed = state.timestamp - self.position_start_time
            is_time_out = time_elapsed > (TIME_STOP_LIMIT * 100) # Prosperity ticks are 100ms

            # --- 4. IMPROVED EXECUTION LOGIC ---
            if is_time_out and current_pos != 0:
                target_pos = 0 # Emergency Exit
            elif current_pos == 0:
                if abs(gap) > ENTRY_THRESHOLD:
                    ratio = min(abs(gap) / MAX_CONV_GAP, 1.0)
                    target_pos = int(np.sign(gap) * LIMIT * ratio)
            else:
                if abs(gap) < EXIT_THRESHOLD:
                    target_pos = 0
                else:
                    ratio = min(abs(gap) / MAX_CONV_GAP, 1.0)
                    target_pos = int(np.sign(gap) * LIMIT * ratio)

            # --- 5. ORDERS ---
            orders = []
            if target_pos > current_pos:
                orders.append(Order(PRODUCT, best_ask, target_pos - current_pos))
            elif target_pos < current_pos:
                orders.append(Order(PRODUCT, best_bid, target_pos - current_pos))

            result[PRODUCT] = orders

        return result, 0, ""