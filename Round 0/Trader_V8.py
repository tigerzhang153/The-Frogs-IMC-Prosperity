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
        LIMIT = 20
        FAIR  = 10000

        if 'TOMATOES' in state.order_depths:
            order_depth = state.order_depths['TOMATOES']
            orders = []
            current_pos = state.position.get('TOMATOES', 0)

            # Step 1: take any mispriced orders (guaranteed profit)
            for ask_price, ask_vol in sorted(order_depth.sell_orders.items()):
                if ask_price < FAIR:
                    buy_amount = min(-ask_vol, LIMIT - current_pos)
                    if buy_amount > 0:
                        orders.append(Order('TOMATOES', ask_price, buy_amount))
                        current_pos += buy_amount

            for bid_price, bid_vol in sorted(order_depth.buy_orders.items(), reverse=True):
                if bid_price > FAIR:
                    sell_amount = min(bid_vol, LIMIT + current_pos)
                    if sell_amount > 0:
                        orders.append(Order('TOMATOES', bid_price, -sell_amount))
                        current_pos -= sell_amount

            # Step 2: post skewed passive quotes with remaining capacity
            skew = -round(current_pos / LIMIT * 2)
            bid_price = 9999 + skew
            ask_price = 10001 + skew

            buy_amount  = LIMIT - current_pos
            sell_amount = LIMIT + current_pos

            if buy_amount > 0:
                orders.append(Order('TOMATOES', bid_price, buy_amount))
            if sell_amount > 0:
                orders.append(Order('TOMATOES', ask_price, -sell_amount))

            result['TOMATOES'] = orders
        return result, 0, ""