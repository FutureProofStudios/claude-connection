"""Tests for the money engine. Run: python -m unittest discover engine"""
import datetime as dt
import unittest

import money as m

D = dt.date


def ledger(**kw):
    base = {"settings": {"safety_margin": 1000, "tax_reserve": 0, "horizon_weeks": 4},
            "accounts": [{"id": "chk", "name": "Checking", "balance": 10000,
                          "balance_date": D(2026, 9, 24), "source": "t"}]}
    base.update(kw)
    return base


class Dates(unittest.TestCase):
    def test_add_months_clamps_to_month_end(self):
        self.assertEqual(m.add_months(D(2026, 1, 31), 1), D(2026, 2, 28))
        self.assertEqual(m.add_months(D(2026, 11, 15), 3), D(2027, 2, 15))
        self.assertEqual(m.add_months(D(2026, 3, 15), -3), D(2025, 12, 15))

    def test_monthly_occurrences(self):
        got = m.occurrences({"cadence": "monthly", "day": 31}, D(2026, 9, 24), D(2026, 12, 1))
        self.assertEqual(got, [D(2026, 9, 30), D(2026, 10, 31), D(2026, 11, 30)])

    def test_biweekly_and_annual(self):
        got = m.occurrences({"cadence": "biweekly", "anchor": D(2026, 9, 4)}, D(2026, 9, 24), D(2026, 10, 20))
        self.assertEqual(got, [D(2026, 10, 2), D(2026, 10, 16)])
        got = m.occurrences({"cadence": "annual", "anchor": D(2025, 12, 1)}, D(2026, 9, 24), D(2027, 1, 1))
        self.assertEqual(got, [D(2026, 12, 1)])

    def test_workdays(self):
        # Fri -> next Fri is 5 working days
        self.assertEqual(m.workdays_between(D(2026, 9, 18), D(2026, 9, 25)), 5)


class Debts(unittest.TestCase):
    def test_card_minimum_is_interest_plus_one_percent(self):
        bal, apr = 10000.0, 0.24
        i = bal * apr / 12
        self.assertAlmostEqual(m.required_payment({"kind": "card"}, bal + i, i), i + 100)
        self.assertEqual(m.required_payment({"kind": "card"}, 30.0, 0.5), 30.0)  # never more than owed

    def test_over_limit_first_then_highest_apr(self):
        debts = [{"id": "a", "balance": 5000, "apr": 0.29, "limit": 10000},
                 {"id": "b", "balance": 12000, "apr": 0.18, "limit": 11000},
                 {"id": "c", "balance": 3000, "apr": 0.22},
                 {"id": "loan", "kind": "loan", "balance": 9000, "apr": 0.30}]
        self.assertEqual([d["id"] for d in m.payoff_targets(debts, D(2026, 9, 24))], ["b", "a", "c"])

    def test_promo_rate_until_it_ends(self):
        d = {"apr": 0.25, "promo": {"rate": 0.0, "ends": D(2027, 1, 1)}}
        self.assertEqual(m.apr_on(d, D(2026, 12, 31)), 0.0)
        self.assertEqual(m.apr_on(d, D(2027, 1, 1)), 0.25)

    def test_zero_interest_payoff_is_exact(self):
        debts = [{"id": "a", "balance": 1000, "apr": 0.0, "min_payment": 40}]
        run = m.simulate(debts, D(2026, 9, 24), extra=160)  # budget = 40 floor + 160
        self.assertEqual(run.months, 5)
        self.assertAlmostEqual(run.total_interest, 0.0)

    def test_extra_money_beats_minimums_only(self):
        debts = [{"id": "a", "balance": 20000, "apr": 0.22}, {"id": "b", "balance": 8000, "apr": 0.28}]
        mins = m.simulate(debts, D(2026, 9, 24), minimums_only=True)
        held = m.simulate(debts, D(2026, 9, 24))
        more = m.simulate(debts, D(2026, 9, 24), extra=1000)
        self.assertGreater(mins.months, held.months)
        self.assertGreater(held.months, more.months)
        self.assertGreater(held.total_interest, more.total_interest)
        self.assertLess(more.payoff_month["b"], more.payoff_month["a"])  # avalanche: 28% first

    def test_stop_after_returns_balances(self):
        debts = [{"id": "a", "balance": 1000, "apr": 0.0}]
        run = m.simulate(debts, D(2026, 9, 24), minimums_only=True, stop_after=2)
        self.assertAlmostEqual(run.balances["a"], 1000 - 40 - 40)


class Forecast(unittest.TestCase):
    def test_sweep_is_limited_by_the_lowest_future_week(self):
        lg = ledger(one_offs=[{"name": "Payroll", "amount": 7000, "date": D(2026, 10, 10), "source": "t"}],
                    receivables=[{"id": "r", "client": "X", "amount": 2000, "due": D(2026, 10, 15),
                                  "status": "sent", "source": "t"}])
        start, weeks = m.forecast(lg, D(2026, 9, 24))
        amount, low, low_at, floor = m.sweep(lg, start, weeks)
        self.assertEqual(low, 3000)
        self.assertEqual(amount, 2000)  # 3000 low point - 1000 safety margin

    def test_shortfall_gives_no_sweep(self):
        lg = ledger(one_offs=[{"name": "Payroll", "amount": 12000, "date": D(2026, 10, 1), "source": "t"}])
        start, weeks = m.forecast(lg, D(2026, 9, 24))
        amount, low, low_at, floor = m.sweep(lg, start, weeks)
        self.assertEqual(amount, 0)
        self.assertEqual(low, -2000)
        self.assertEqual(low_at, D(2026, 10, 1))

    def test_very_late_receivable_is_not_counted_as_cash(self):
        lg = ledger(receivables=[{"id": "r", "client": "X", "amount": 5000, "due": D(2026, 7, 1),
                                  "status": "late", "source": "t"}])
        start, weeks = m.forecast(lg, D(2026, 9, 24))
        self.assertEqual(weeks[-1].balance, 10000)

    def test_tax_holdback_comes_off_every_client_payment(self):
        lg = ledger(receivables=[{"id": "r", "client": "X", "amount": 4000, "due": D(2026, 10, 1),
                                  "status": "sent", "source": "t"}])
        lg["settings"]["tax_holdback_pct"] = 0.25
        start, weeks = m.forecast(lg, D(2026, 9, 24))
        self.assertEqual(weeks[-1].balance, 13000)

    def test_pipeline_never_counts_as_cash(self):
        lg = ledger(pipeline=[{"id": "p", "client": "Y", "amount": 10000, "stage": "quoted",
                               "expected_cash": D(2026, 10, 5), "source": "t"}])
        start, weeks = m.forecast(lg, D(2026, 9, 24))
        self.assertEqual(weeks[-1].balance, 10000)
        self.assertAlmostEqual(sum(w.pipeline for w in weeks), 3000)

    def test_recurring_starts_after_the_accounts_balance_date(self):
        # Balance dated today already includes today's bill; a stale balance
        # still owes the bills since, which land in week one.
        bill = {"name": "Rent", "amount": 500, "cadence": "monthly", "day": 22, "account": "chk", "source": "t"}
        lg = ledger(recurring=[dict(bill)])
        start, weeks = m.forecast(lg, D(2026, 9, 24))
        self.assertEqual(weeks[-1].balance, 10000)
        lg["accounts"][0]["balance_date"] = D(2026, 9, 20)
        start, weeks = m.forecast(lg, D(2026, 9, 24))
        self.assertEqual(weeks[0].balance, 9500)
        self.assertIn("already happened", weeks[0].events[0].note)

    def test_card_minimums_come_from_debts(self):
        lg = ledger(debts=[{"id": "c", "name": "Card", "balance": 5000, "apr": 0.2, "min_payment": 150,
                            "due_date": D(2026, 10, 1), "balance_date": D(2026, 9, 4), "source": "t"}])
        start, weeks = m.forecast(lg, D(2026, 9, 24))
        self.assertEqual(weeks[-1].balance, 10000 - 150)


class Bets(unittest.TestCase):
    def test_scoring(self):
        self.assertAlmostEqual(m.score_bet({"kind": "yes_no", "p": 0.8, "outcome": True}), 0.04)
        self.assertAlmostEqual(m.score_bet({"kind": "yes_no", "p": 0.8, "outcome": False}), 0.64)
        self.assertAlmostEqual(m.score_bet({"kind": "amount", "predicted": 1000, "outcome": 900}), 0.1)
        self.assertEqual(m.score_bet({"kind": "date", "predicted": D(2026, 9, 26), "outcome": D(2026, 9, 30)}), 4)
        self.assertIsNone(m.score_bet({"kind": "yes_no", "p": 0.5}))

    def test_check_catches_bad_bets(self):
        errors, _ = m.check({}, [{"id": "b1", "claim": "x"}, {"id": "b1", "claim": "y", "p": 0.5,
                                                             "check_on": D(2026, 9, 30)}], D(2026, 9, 24))
        self.assertTrue(any("needs p" in e for e in errors))
        self.assertTrue(any("duplicate" in e for e in errors))


class Report(unittest.TestCase):
    def test_report_renders(self):
        lg = ledger(debts=[{"id": "c", "name": "Card", "balance": 5000, "apr": 0.2, "min_payment": 150,
                            "limit": 4000, "due_date": D(2026, 10, 1), "balance_date": D(2026, 9, 4),
                            "source": "t"}])
        text = m.report(lg, [], [], D(2026, 9, 24))
        self.assertIn("Net position", text)
        self.assertIn("(over)", text)
        self.assertIn("Sweep", text)


if __name__ == "__main__":
    unittest.main()
