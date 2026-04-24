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



class Trader:
    def run(self, state: TradingState):
        result = {}
        TOMATOES_LIMIT = 20
        FAIR_VALUE     = 5010
        STRONG_BUY     = 4993   # aggressively buy here
        MILD_BUY       = 5000   # lightly buy here
        MILD_SELL      = 5020   # lightly sell here
        STRONG_SELL    = 5030   # aggressively sell here

        if 'TOMATOES' in state.order_depths:
            order_depth: OrderDepth = state.order_depths['TOMATOES']
            orders = []
            current_pos = state.position.get('TOMATOES', 0)

            mid_price = None
            if order_depth.buy_orders and order_depth.sell_orders:
                best_bid = max(order_depth.buy_orders.keys())
                best_ask = min(order_depth.sell_orders.keys())
                mid_price = (best_bid + best_ask) / 2

            if mid_price is not None:

                if mid_price <= STRONG_BUY:
                    # Aggressively buy — price is very low
                    buy_amount = TOMATOES_LIMIT - current_pos
                    if buy_amount > 0:
                        orders.append(Order('TOMATOES', best_ask, buy_amount))

                elif mid_price <= MILD_BUY:
                    # Lightly buy
                    buy_amount = min(10, TOMATOES_LIMIT - current_pos)
                    if buy_amount > 0:
                        orders.append(Order('TOMATOES', best_ask, buy_amount))

                elif mid_price >= STRONG_SELL:
                    # Aggressively sell — price is very high
                    sell_amount = TOMATOES_LIMIT + current_pos
                    if sell_amount > 0:
                        orders.append(Order('TOMATOES', best_bid, -sell_amount))

                elif mid_price >= MILD_SELL:
                    # Lightly sell
                    sell_amount = min(10, TOMATOES_LIMIT + current_pos)
                    if sell_amount > 0:
                        orders.append(Order('TOMATOES', best_bid, -sell_amount))

                else:
                    # Price near fair value — post passive orders both sides
                    buy_amount = TOMATOES_LIMIT - current_pos
                    sell_amount = TOMATOES_LIMIT + current_pos
                    if buy_amount > 0:
                        orders.append(Order('TOMATOES', FAIR_VALUE - 2, buy_amount))
                    if sell_amount > 0:
                        orders.append(Order('TOMATOES', FAIR_VALUE + 2, -sell_amount))

            result['TOMATOES'] = orders

        return result, 0, ""