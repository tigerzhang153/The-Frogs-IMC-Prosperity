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
        LIMIT = 75
        FAIR_VALUE = 10000

        if 'EMERALDS' in state.order_depths:
            orders = []
            order_depth = state.order_depths['EMERALDS']
            current_pos = state.position.get('EMERALDS', 0)

            # --- AGGRESSIVE TRADING (take opportunities) ---

            # BUY if best ask < fair value
            if order_depth.sell_orders:
                best_ask = min(order_depth.sell_orders.keys())
                best_ask_volume = order_depth.sell_orders[best_ask]

                if best_ask < FAIR_VALUE:
                    #buy_amount = -best_ask_volume
                    buy_amount = min(-best_ask_volume, LIMIT - current_pos)
                    if buy_amount > 0:
                        orders.append(Order('EMERALDS', best_ask, buy_amount))
                        current_pos += buy_amount  # update position locally

            # SELL if best bid > fair value
            if order_depth.buy_orders:
                best_bid = max(order_depth.buy_orders.keys())
                best_bid_volume = order_depth.buy_orders[best_bid]

                if best_bid > FAIR_VALUE:
                    #sell_amount = best_bid_volume
                    sell_amount = min(best_bid_volume, LIMIT + current_pos)
                    if sell_amount > 0:
                        orders.append(Order('EMERALDS', best_bid, -sell_amount))
                        current_pos -= sell_amount  # update position locally

            # --- PASSIVE MARKET MAKING (if no strong signal) ---

            # Post bid slightly below fair value
            buy_amount = LIMIT - current_pos
            if buy_amount > 0:
                orders.append(Order('EMERALDS', FAIR_VALUE - 7, buy_amount))

            # Post ask slightly above fair value
            sell_amount = LIMIT + current_pos
            if sell_amount > 0:
                orders.append(Order('EMERALDS', FAIR_VALUE + 7, -sell_amount))

            result['EMERALDS'] = orders

        return result, 0, ""