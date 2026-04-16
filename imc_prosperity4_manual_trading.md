# IMC Prosperity 4 — Manual Trading Round 1 Notes

---

## Context

**Products:** Dryland Flax (`DRYLAND_FLAX`) and Ember Mushrooms (`EMBER_MUSHROOM`)

**Auction Rules:**
- Submit a single limit order (price, quantity)
- Exchange selects a clearing price that: (1) maximizes total traded volume, then (2) breaks ties by choosing the higher price
- All bids ≥ clearing price and asks ≤ clearing price execute at the clearing price
- Allocation: price priority, then time priority
- You submit last — you are last in line at any price level you join

**Guaranteed Buyback (after auction):**
- `DRYLAND_FLAX`: 30 per unit (no fees) → effective buyback = **30**
- `EMBER_MUSHROOM`: 20 per unit (fee: 0.10 per unit traded) → effective buyback = **19.90**

---

## Round Structure

This is a one-shot sealed-bid auction. You submit a single limit order (price + quantity) for each product. You can revise orders before the round closes — only the last submission counts. After the auction clears, you immediately sell everything you bought to the Merchant Guild at the fixed buyback price. There is no continuous trading.

---

## How the Clearing Price Works

The exchange finds the single price where the most trading can happen:

- At any price P: **Volume = min(total bids at ≥P, total asks at ≤P)**
- The clearing price is the P that maximizes this volume
- Ties broken by higher price
- Everyone transacts at the clearing price regardless of their individual bid

**Key insight:** Bidding higher doesn't cost you more — you pay the clearing price, not your bid. Bidding higher only improves your queue priority.

---

## How Allocation Works

Priority: best price first, then earliest time. Since you submit last, you are at the back of the queue at any price level you join. If a price level is oversubscribed, people ahead of you fill first and you may get partial or zero fill.

---

## Profit Formula

> **Profit per unit = Buyback price − Clearing price**

To maximize profit: find the largest quantity you can buy where the clearing price stays below the buyback price.

---

## Dryland Flax Order Book (stale snapshot)

| Side | Price | Volume |
|------|-------|--------|
| Bid  | 30    | 30k    |
| Bid  | 29    | 5k     |
| Bid  | 28    | 12k    |
| Bid  | 27    | 28k    |
| Ask  | 28    | 40k    |
| Ask  | 31    | 20k    |
| Ask  | 32    | 20k    |
| Ask  | 33    | 30k    |

### Clearing Price Calculation (without your order)

| Price | Bids ≥ P | Asks ≤ P | Volume |
|-------|----------|----------|--------|
| 27    | 75k      | 0        | 0      |
| **28**| **47k**  | **40k**  | **40k** ← max |
| 29    | 35k      | 40k      | 35k    |
| 30    | 30k      | 40k      | 30k    |
| 31    | 0        | 60k      | 0      |

**Natural clearing price = 28**

### How Supply Gets Exhausted

At clearing price 28, total supply = 40k. Bids fill in priority order:

1. 30k@30 fills → **10k remaining**
2. 5k@29 fills → **5k remaining**
3. 12k@28 fills (only 5k left) → **0 remaining**

The existing orders perfectly exhaust all 40k supply before you get a turn. Bidding at 28 or below gives zero fill.

### Strategy Analysis

To get fill, you must bid at 29 or 30 to jump queue over some existing orders. Your bid quantity determines the clearing price:

**Bidding at price 30:**

| Quantity Q | Clearing Price | Your Fill | Profit/unit | Total Profit |
|------------|---------------|-----------|-------------|--------------|
| Q < 5k     | 28            | Q         | 2.00        | 2Q (~9,998 max) |
| 5k ≤ Q < 10k | 29          | Q         | 1.00        | Q (~9,999 max) |
| Q ≥ 10k    | 30            | 10k       | 0.00        | 0            |

**Apparent optimal: Bid price 30, quantity 9,999 → profit ≈ 9,999**

### Why This May Be Impossible in Practice

The order book is stale. If other competition participants also have the same buyback deal at 30 and are bidding aggressively, B@30 may have already grown well beyond 30k. Once B@30 reaches 40k (without your order), clearing shifts to 30 and profit = 0.

Given competitive dynamics, the Dryland Flax opportunity may have already been competed away by the time you submit.

**Note on the 28k@27 bid:** This order is completely inert — it can never execute since the lowest ask is 28. It contributes zero volume at any viable clearing price. Likely a game design choice to test whether players correctly identify and ignore irrelevant orders.

---

## Ember Mushroom Order Book (stale snapshot)

| Side | Price | Volume |
|------|-------|--------|
| Bid  | 20    | 43k    |
| Bid  | 19    | 17k    |
| Bid  | 18    | 6k     |
| Bid  | 17    | 5k     |
| Bid  | 16    | 10k    |
| Bid  | 15    | 5k     |
| Bid  | 14    | 10k    |
| Bid  | 13    | 7k     |
| Ask  | 12    | 20k    |
| Ask  | 13    | 25k    |
| Ask  | 14    | 35k    |
| Ask  | 15    | 6k     |
| Ask  | 16    | 5k     |
| Ask  | 17    | 0      |
| Ask  | 18    | 10k    |
| Ask  | 19    | 12k    |

**Effective buyback = 20 − 0.10 = 19.90 → max profitable clearing price = 19**

### Clearing Price Calculation (without your order)

| Price | Bids ≥ P | Asks ≤ P | Volume |
|-------|----------|----------|--------|
| 12    | 103k     | 20k      | 20k    |
| 13    | 103k     | 45k      | 45k    |
| 14    | 96k      | 80k      | 80k    |
| **15**| **86k**  | **86k**  | **86k** ← max |
| 16    | 81k      | 91k      | 81k    |
| 17    | 71k      | 91k      | 71k    |
| 18    | 66k      | 101k     | 66k    |
| 19    | 60k      | 113k     | 60k    |
| 20    | 43k      | 113k     | 43k    |

**Natural clearing price = 15** (both sides = 86k, perfect match)

**Profit per unit at clearing 15 = 19.90 − 15 = 4.90**

### Strategy Analysis

With bid at price 19, quantity Q:

| Q       | Clearing | Volume | Your Fill | Profit/unit | Total Profit |
|---------|----------|--------|-----------|-------------|--------------|
| Q < 5k  | 15       | 86k    | Q         | 4.90        | 4.90Q        |
| Q ≥ 5k, Q < 20k | 16 | 91k | Q       | 3.90        | 3.90Q        |
| Q = 19,999 | 16  | 91k    | 19,999    | 3.90        | **77,996**   |
| Q ≥ 20k | 17      | 91k    | Q         | 2.90        | 2.90Q        |

**Clearing shifts from 16 to 17 when bids at ≥17 reach 91k.** Currently 71k; needs Q ≥ 20k at bid ≥17 to trigger.

### Fill Calculation at Clearing 16, Q = 19,999, Bid = 19

Priority order at clearing 16:
1. 43k@20 existing fills → 48k remaining
2. 17k@19 existing fills → 31k remaining
3. Me 19,999@19 fills → **19,999 fills** (31k > 19,999) ✓

### Optimal Order

**Bid price: 19, Quantity: 19,999 → Expected profit ≈ 77,996 XIRECs**

**Alternative (from other sources): Bid price 17, Quantity 19k → Profit = 74,100**

Both give clearing price 16. The bid price doesn't affect what you pay (you pay clearing price). The difference is queue position and quantity precision. Q=19,999 vs 19,000 accounts for ~3,900 additional profit.

### Fragility Warning

With Q=19,999 at bid 19, you are 1 unit away from clearing shifting to 17:
- Volume at P=17 with your order: min(71k + 19,999, 91k) = 90,999
- If any other bid at ≥17 increases by even 1 unit, P=17 ties P=16 → clearing shifts to 17
- Profit drops to 2.90/unit: 19,999 × 2.90 = 57,997

A more conservative Q (~17,000) gives ~66,300 with a 2k unit buffer.

---

## Game Theory Considerations

### Stale Order Book
The order book is explicitly marked as stale. Other participants may still be adjusting. Any analysis is based on a snapshot, not the final state.

### Collective Action Problem
If all participants have the same buyback deal and reason identically:
- Everyone bids at the maximum profitable price with large quantities
- Cumulative bids at that price grow → clearing price rises
- Everyone's profit collapses to zero
- The opportunity is competed away

### Bid Shading
In uniform-price multi-unit auctions, bidding your true maximum is not always a dominant strategy. Rational players shade bids downward to avoid pushing clearing price up. The equilibrium involves bidding somewhat below true value.

### Your Positional Advantage
Submitting last is a significant information advantage — you see the (near-)final order book before committing. Waiting as long as possible before submitting maximizes information.

### Practical Implication
Without a model of other players' behavior, there is no objectively correct answer. Higher expected profit comes with higher variance. Build in buffer on quantity to protect against clearing price blowup from other participants.

---

## Key Takeaways

1. **Bid price = your ceiling, not what you pay.** You always pay the clearing price.
2. **Bid at your maximum profitable price** to get the best queue priority.
3. **Your quantity affects the clearing price** — large enough quantities push clearing up and can eliminate your margin.
4. **The stale order book is a real risk.** Optimize against it but build in buffer.
5. **Inert orders (e.g., 28k@27 for Flax) are noise** — filter them out of your analysis.
6. **Dryland Flax:** Opportunity may be competed away. Thin margins even in best case.
7. **Ember Mushroom:** Cleaner opportunity. Optimal ≈ bid 19, quantity 19,999, profit ≈ 77,996.
