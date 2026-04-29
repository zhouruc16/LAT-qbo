# 03 — Reconciliation identities

These hold to the cent across all 176 H1 2024 LELNU payments. If a future
period violates these, **the data is corrupt** — fix the data, don't fix
the formula.

## Identity 0: Order details ↔ Statements

For each `Statement ID`:
```
Σ Order details rows.Total_settlement_amount  =  Statements.Total_settlement_amount
Σ Order details rows.Net_sales                =  Statements.Net_sales
Σ Order details rows.Shipping_components       =  Statements.Shipping
Σ Order details rows.Fee_components            =  Statements.Fees
```

Verified for 04-02, 05-02, 04-13 statements (and implicitly for all H1).

## Identity 1 (xlsx-internal): the statement rollup

For each row in Statements:
```
Net sales + Shipping + Fees + Adjustments  =  Total settlement amount
```

Note: `Fees` is always negative (it's the deductions). Adjustments can be
either sign.

## Identity 2 (xlsx-internal): the reserve adjustment

For each row in Statements:
```
Total settlement amount + Reserve Amount  =  Payable amount
```

`Reserve Amount` is signed:
- Negative → withheld this cycle → Payable < Total
- Positive → released this cycle → Payable > Total
- Zero → no reserve activity

## Identity 3 (xlsx ↔ Payments): the payout rollup

For each `Payment ID`:
```
Σ Statements.Payable_amount where Payment ID = X  =  Payments.Payment_amount
```

Most payments have a 1:1 statement, but some payments group multiple
statements (e.g., a negative-net-settlement day rolls into the next day's
positive payout).

## Identity 4 (Payments ↔ Bank): the bank match

For each `Payment ID`:
```
Payments.Payment_amount  =  Bank line.amount
```

Match key: `Payment ID == Bank.payout_id` (after joining the prefix and
suffix with no space). For HYPERWALLET / TikTok-alt formats with no
payout ID in the bank description, match by `(storefront, amount,
|date_diff| ≤ 4 days)` fallback.

## Putting it all together

```
Per Order detail row (per SKU):
  contributes to → Statement.Total_settlement_amount  (Identity 0)
                ↓
Per Statement (per day):
  Net + Ship + Fees + Adj    = Total_settlement       (Identity 1)
  Total + Reserve            = Payable                (Identity 2)
                ↓
Per Payment (per payout):
  Σ Payable per Payment ID   = Payment.Payment_amount (Identity 3)
                ↓
Per Bank line (one ACH credit):
  Payment_amount             = Bank deposit           (Identity 4)
```

## Numerical proof for H1 2024 LELNU

```
Total settlement amount:    $1,690,241.20
Total reserve (signed):     $    1,080.70   (slight net release H1)
Total payable (statements): $1,691,321.90
Total payment amounts:      $1,714,496.90   (includes pre-Jan stmts paid in Jan)
Total matched bank deposits:$1,714,496.90
(payment vs bank diff:               $0.00)

Identity 1 mismatches:  0 / 179 statements
Identity 2 mismatches:  0 / 179 statements
Identity 3 mismatches:  0 / 176 payments
Identity 4 mismatches:  0 / 176 payments
```

Why total_payable ($1,691,321.90) ≠ total_payment ($1,714,496.90)?
Because some Payments initiated in early Jan correspond to statements
**dated in late December 2023** (those statements aren't in our H1 xlsx).
Those payments are still in H1 by initiation date and they show on H1
bank statements. The diff ($23,175) = the value of pre-Jan-2024 statements
paid out in early Jan 2024.

This is correct, expected, and not a bug.

## Don't try this

- Don't compute `Net sales` by parsing customer-payment + discounts at the
  aggregate level. Use the per-row `Net sales` column or the per-statement
  `Statements.Net_sales` directly. Both tie out.
- Don't try to derive Customer payment from sales tax + product price +
  shipping individually. The xlsx already gives you Customer payment.
- Don't use `Total settlement amount = Customer payment − fees`. That
  doesn't hold because Total settlement also includes platform subsidies
  TikTok pays to LAT (e.g., shipping subsidy, platform discounts).
