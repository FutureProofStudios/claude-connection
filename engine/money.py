#!/usr/bin/env python3
"""Money engine: every calculation the system relies on.

Reads data/ledger.yaml, data/bets.yaml and data/history.yaml and works out
net position, the 13-week cash forecast, the safe sweep amount, debt payoff
order and projections, and the scoreboard. Models never do this arithmetic:
they edit the data files and run this script.

Usage:
  python engine/money.py check                      # validate the data
  python engine/money.py report [--today D] [--write]
  python engine/money.py snapshot [--today D]       # append today's totals to history
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"

DEFAULTS = {
    "safety_margin": 3000.0,      # never plan to go below this much cash
    "tax_reserve": 0.0,           # cash that is already the government's
    "horizon_weeks": 13,
    "quiet_after_workdays": 5,    # a warm lead untouched this long gets a nudge
    "stale_after_days": 7,        # balances older than this get flagged
    "at_risk_after_days": 30,     # receivables this late stop counting as cash
    "due_soon_days": 10,          # how far ahead "Do this week" looks for bills
    "extra_scenarios": [0, 1000, 2500, 5000],
    "stage_probability": {"inquiry": 0.1, "quoted": 0.3, "verbal": 0.7, "booked": 1.0},
}
OPEN_STAGES = ("inquiry", "quoted", "verbal", "booked")
MIN_FLOOR = 40.0   # card minimum floor
MIN_PCT = 0.01     # card minimum = interest + 1% of balance (Chase/Amex style)


# ---------------------------------------------------------------- basics

def to_date(v):
    if v is None or isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v))


def add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def workdays_between(a: dt.date, b: dt.date) -> int:
    """Mon-Fri days after a, up to and including b."""
    n, d = 0, a
    while d < b:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def money(x: float | None, cents: bool = False) -> str:
    if x is None:
        return "?"
    s = f"{abs(x):,.2f}" if cents else f"{abs(x):,.0f}"
    return f"-${s}" if x < 0 else f"${s}"


def pct(x: float | None) -> str:
    return "?" if x is None else f"{x * 100:.2f}%"


def load_yaml(path: Path, default):
    if not path.exists():
        return default
    with open(path) as f:
        data = yaml.safe_load(f)
    return default if data is None else data


def settings(ledger: dict) -> dict:
    s = dict(DEFAULTS)
    s.update(ledger.get("settings") or {})
    return s


# ---------------------------------------------------------------- recurring dates

def occurrences(item: dict, start: dt.date, end: dt.date) -> list[dt.date]:
    """Dates in [start, end) on which a recurring item happens."""
    cadence = item.get("cadence", "monthly")
    if cadence == "monthly":
        day = int(item["day"])
        out, d = [], dt.date(start.year, start.month, 1)
        while d < end:
            x = dt.date(d.year, d.month, min(day, calendar.monthrange(d.year, d.month)[1]))
            if start <= x < end:
                out.append(x)
            d = add_months(d, 1)
        return out
    anchor = to_date(item["anchor"])  # any date it happened or will happen
    if cadence in ("weekly", "biweekly"):
        step = 7 if cadence == "weekly" else 14
        k = (start - anchor).days // step
        x = anchor + dt.timedelta(days=k * step)
        out = []
        while x < end:
            if x >= start:
                out.append(x)
            x += dt.timedelta(days=step)
        return out
    if cadence in ("quarterly", "annual"):
        step = 3 if cadence == "quarterly" else 12
        k = ((start.year - anchor.year) * 12 + start.month - anchor.month) // step - 1
        out = []
        while True:
            x = add_months(anchor, k * step)
            if x >= end:
                return out
            if x >= start:
                out.append(x)
            k += 1
    raise ValueError(f"unknown cadence {cadence!r} on {item.get('name')}")


# ---------------------------------------------------------------- debts

def apr_on(debt: dict, when: dt.date) -> float:
    promo = debt.get("promo")
    if promo and to_date(promo.get("ends")) and when < to_date(promo["ends"]):
        return float(promo.get("rate", 0.0))
    return float(debt.get("apr") or 0.0)


def is_over_limit(debt: dict, balance: float | None = None) -> bool:
    b = debt["balance"] if balance is None else balance
    return bool(debt.get("limit")) and b > float(debt["limit"])


def payoff_targets(debts: list[dict], today: dt.date, balances: dict | None = None) -> list[dict]:
    """Where extra money goes: over-limit cards first, then highest APR.

    Installment loans are skipped unless marked prepay_ok, since many have
    fixed schedules or low rates.
    """
    bal = balances or {d["id"]: d["balance"] for d in debts}
    live = [d for d in debts
            if bal[d["id"]] > 0.005 and (d.get("kind", "card") == "card" or d.get("prepay_ok"))]
    return sorted(live, key=lambda d: (not is_over_limit(d, bal[d["id"]]), -apr_on(d, today)))


def required_payment(debt: dict, balance_after_interest: float, interest: float) -> float:
    if balance_after_interest <= 0.005:
        return 0.0
    if debt.get("kind", "card") == "loan":
        return min(balance_after_interest, float(debt.get("payment") or debt.get("min_payment") or 0))
    principal = balance_after_interest - interest
    return min(balance_after_interest, max(MIN_FLOOR, interest + MIN_PCT * principal))


@dataclass
class Payoff:
    months: int | None              # None = not paid off within the cap
    total_interest: float
    payoff_month: dict = field(default_factory=dict)   # id -> month index
    balances: dict = field(default_factory=dict)       # balances when the run stopped


def simulate(debts: list[dict], today: dt.date, extra: float = 0.0,
             minimums_only: bool = False, max_months: int = 600,
             stop_after: int | None = None) -> Payoff:
    """Month-by-month payoff.

    minimums_only: pay each month's formula minimum, which shrinks as the
    balance does. Otherwise hold the total monthly payment at today's total of
    minimums plus `extra`, and send everything beyond the minimums to
    payoff_targets() order (freed-up minimums roll forward).
    """
    debts = [d for d in debts if d.get("balance") is not None
             and (d.get("kind", "card") == "card"
                  or (d.get("kind") == "loan" and (d.get("payment") or d.get("min_payment"))))]
    bal = {d["id"]: float(d["balance"]) for d in debts}
    budget = None
    if not minimums_only:
        budget = extra
        for d in debts:
            i = bal[d["id"]] * apr_on(d, today) / 12
            budget += required_payment(d, bal[d["id"]] + i, i)
    total_interest, done = 0.0, {}
    month = 0
    while any(b > 0.005 for b in bal.values()):
        if month >= max_months or (stop_after is not None and month >= stop_after):
            return Payoff(None if stop_after is None else month, total_interest, done, bal)
        when = add_months(today, month)
        paid = 0.0
        for d in debts:
            if bal[d["id"]] <= 0.005:
                continue
            i = bal[d["id"]] * apr_on(d, when) / 12
            total_interest += i
            bal[d["id"]] += i
            p = required_payment(d, bal[d["id"]], i)
            bal[d["id"]] -= p
            paid += p
        if budget is not None:
            left = budget - paid
            for t in payoff_targets(debts, when, bal):
                if left <= 0.005:
                    break
                p = min(left, bal[t["id"]])
                bal[t["id"]] -= p
                left -= p
        month += 1
        for d in debts:
            if bal[d["id"]] <= 0.005 and d["id"] not in done:
                done[d["id"]] = month
                bal[d["id"]] = 0.0
    return Payoff(month, total_interest, done, bal)


# ---------------------------------------------------------------- cash forecast

@dataclass
class Event:
    date: dt.date
    amount: float          # + in, - out
    label: str
    kind: str              # receivable | pipeline | bill | income | minimum | one_off
    committed: bool = True
    note: str = ""


def cash_events(ledger: dict, today: dt.date, end: dt.date) -> list[Event]:
    s = settings(ledger)
    ev: list[Event] = []

    for r in ledger.get("receivables") or []:
        if r.get("status") == "paid":
            continue
        when = to_date(r.get("expected") or r.get("due"))
        if when is None or when >= end:
            continue
        late = (today - to_date(r["due"])).days if r.get("due") else 0
        note = f"{late} days late" if late > 0 else ""
        if when < today:
            when = today
        ev.append(Event(when, float(r["amount"]), f"{r['client']}: {r.get('what', '')}".strip(": "),
                        "receivable", committed=late <= s["at_risk_after_days"], note=note))

    probs = s["stage_probability"]
    for p in ledger.get("pipeline") or []:
        when = to_date(p.get("expected_cash"))
        if p.get("stage") not in OPEN_STAGES or not p.get("amount") or when is None:
            continue
        if today <= when < end:
            prob = float(p.get("p", probs.get(p["stage"], 0)))
            ev.append(Event(when, float(p["amount"]) * prob, f"{p['client']} ({prob:.0%})",
                            "pipeline", committed=False))

    # A recurring item tied to an account starts the day after that account's
    # balance date: anything on or before it is already in the balance, and
    # anything between it and today has happened but isn't in the balance yet.
    balance_dates = {a["id"]: to_date(a.get("balance_date")) for a in ledger.get("accounts") or []}
    for b in ledger.get("recurring") or []:
        if b.get("active", True) is False or b.get("in_forecast", True) is False:
            continue
        sign = 1 if b.get("direction") == "in" else -1
        bd = balance_dates.get(b.get("account"))
        start = bd + dt.timedelta(days=1) if bd else today
        for x in occurrences(b, start, end):
            ev.append(Event(max(x, today), sign * float(b["amount"]), b["name"],
                            "income" if sign > 0 else "bill",
                            note="since the last balance, likely already happened" if x < today else ""))

    for o in ledger.get("one_offs") or []:
        if o.get("status") == "done" or o.get("amount") is None or o.get("date") is None:
            continue
        when = to_date(o["date"])
        if when >= end:
            continue
        note = "overdue" if when < today else ""
        sign = 1 if o.get("direction") == "in" else -1
        ev.append(Event(max(when, today), sign * float(o["amount"]), o["name"], "one_off",
                        committed=o.get("committed", True), note=note))

    for d in ledger.get("debts") or []:
        if d.get("autopay_from_forecast_excluded"):
            continue
        amt = d.get("payment") if d.get("kind") == "loan" else d.get("min_payment")
        due = to_date(d.get("due_date"))
        if not amt or not d.get("balance"):
            continue
        day = due.day if due else 1
        for x in occurrences({"cadence": "monthly", "day": day}, today, end):
            ev.append(Event(x, -float(amt), f"{d['name']} minimum", "minimum"))

    return sorted(ev, key=lambda e: (e.date, e.amount))


@dataclass
class Week:
    start: dt.date
    cash_in: float
    cash_out: float
    balance: float
    pipeline: float
    events: list


def forecast(ledger: dict, today: dt.date):
    s = settings(ledger)
    n = int(s["horizon_weeks"])
    end = today + dt.timedelta(weeks=n)
    events = cash_events(ledger, today, end)
    start_cash = sum(float(a["balance"]) for a in ledger.get("accounts") or []
                     if a.get("in_forecast", True) and a.get("balance") is not None)
    weeks, bal = [], start_cash
    for i in range(n):
        ws = today + dt.timedelta(days=7 * i)
        we = ws + dt.timedelta(days=7)
        wk = [e for e in events if ws <= e.date < we]
        cin = sum(e.amount for e in wk if e.amount > 0 and e.committed)
        cout = sum(-e.amount for e in wk if e.amount < 0 and e.committed)
        pipe = sum(e.amount for e in wk if not e.committed and e.amount > 0)
        bal += cin - cout
        weeks.append(Week(ws, cin, cout, bal, pipe, wk))
    return start_cash, weeks


def sweep(ledger: dict, start_cash: float, weeks: list[Week]):
    """Most you can send to debt today without the forecast ever dropping
    below safety margin + tax reserve. Returns (amount, low_balance, low_week_start, floor)."""
    s = settings(ledger)
    floor = float(s["safety_margin"]) + float(s["tax_reserve"])
    low, low_at = start_cash, None
    for w in weeks:
        if w.balance < low:
            low, low_at = w.balance, w.start
    amount = max(0.0, (low - floor) // 50 * 50)
    return amount, low, low_at, floor


# ---------------------------------------------------------------- bets and scoreboard

def score_bet(b: dict):
    """Brier score for yes/no bets (0 is perfect, 0.25 is a coin flip),
    absolute % error for amounts, days late for dates."""
    o = b.get("outcome")
    if o is None:
        return None
    kind = b.get("kind", "yes_no")
    if kind == "yes_no":
        return (float(b["p"]) - (1.0 if o else 0.0)) ** 2
    if kind == "amount":
        pred = float(b["predicted"])
        return abs(float(o) - pred) / abs(pred) if pred else None
    if kind == "date":
        return (to_date(o) - to_date(b["predicted"])).days
    return None


def scoreboard(ledger: dict, bets: list, today: dt.date) -> dict:
    out = {}
    jobs = [j for j in ledger.get("jobs") or [] if j.get("fee") and j.get("days_total")]
    recent = [j for j in jobs if to_date(j.get("last_work_date") or j.get("paid_on") or today)
              >= today - dt.timedelta(days=180)]
    if len(recent) >= 3:
        out["effective_day_rate"] = sum(j["fee"] for j in recent) / sum(j["days_total"] for j in recent)
    decided = [p for p in (ledger.get("pipeline") or []) + (ledger.get("closed") or [])
               if p.get("stage") in ("won", "lost") and p.get("decided_on")]
    decided = sorted(decided, key=lambda p: to_date(p["decided_on"]))[-10:]
    if decided:
        out["win_rate"] = sum(p["stage"] == "won" for p in decided) / len(decided)
        out["win_rate_n"] = len(decided)
    dtc = [(to_date(j["paid_on"]) - to_date(j["last_work_date"])).days
           for j in ledger.get("jobs") or [] if j.get("paid_on") and j.get("last_work_date")]
    if dtc:
        out["days_to_cash"] = sum(dtc) / len(dtc)
    yn = [score_bet(b) for b in bets if b.get("kind", "yes_no") == "yes_no" and b.get("outcome") is not None]
    if yn:
        out["brier"] = sum(yn) / len(yn)
        out["brier_n"] = len(yn)
    fc = [score_bet(b) for b in bets if b.get("tag") == "cash_forecast" and b.get("outcome") is not None]
    if fc:
        out["forecast_error"] = sum(fc) / len(fc)
    debts = [d for d in ledger.get("debts") or [] if d.get("balance")]
    out["interest_per_month"] = sum(d["balance"] * apr_on(d, today) / 12 for d in debts)
    return out


# ---------------------------------------------------------------- check

REQUIRED = {
    "accounts": ("id", "name", "balance", "balance_date", "source"),
    "debts": ("id", "name", "balance", "balance_date", "source"),
    "receivables": ("id", "client", "amount", "due", "status", "source"),
    "pipeline": ("id", "client", "stage", "source"),
    "recurring": ("name", "amount", "cadence", "source"),
    "one_offs": ("name", "date", "source"),
}


def check(ledger: dict, bets: list, today: dt.date) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    s = settings(ledger)
    for section, fields in REQUIRED.items():
        seen = set()
        for i, item in enumerate(ledger.get(section) or []):
            label = f"{section}[{i}] {item.get('id') or item.get('name', '')}".strip()
            for f in fields:
                if item.get(f) is None:
                    (errors if f in ("id", "name", "amount", "balance", "stage") else warnings).append(
                        f"{label}: missing {f}")
            if item.get("id"):
                if item["id"] in seen:
                    errors.append(f"{label}: duplicate id")
                seen.add(item["id"])
            if item.get("cadence") and item["cadence"] != "monthly" and not item.get("anchor"):
                errors.append(f"{label}: {item['cadence']} needs an anchor date")
            if item.get("cadence") == "monthly" and item.get("day") is None:
                errors.append(f"{label}: monthly needs a day")
    for section in ("accounts", "debts", "receivables", "one_offs"):
        for item in ledger.get(section) or []:
            if item.get("estimated"):
                warnings.append(f"{item.get('name') or item.get('client') or item.get('id')}: "
                                f"figure is an estimate, replace with the real number")
    if not s.get("tax_reserve_confirmed"):
        warnings.append(f"Tax reserve ({money(s['tax_reserve'])}) not confirmed by the accountant")
    for a in ledger.get("accounts") or []:
        bd = to_date(a.get("balance_date"))
        if bd and (today - bd).days > s["stale_after_days"]:
            warnings.append(f"{a['name']}: balance is {(today - bd).days} days old ({bd})")
    for d in ledger.get("debts") or []:
        if d.get("kind", "card") == "card" and d.get("apr") is None:
            warnings.append(f"{d['name']}: APR unknown, payoff order is a guess")
        if d.get("kind", "card") == "card" and d.get("balance") and not d.get("min_payment"):
            warnings.append(f"{d['name']}: minimum payment unknown, forecast misses it")
        if d.get("kind") == "loan" and not d.get("payment"):
            warnings.append(f"{d['name']}: loan payment unknown")
        bd = to_date(d.get("balance_date"))
        if bd and (today - bd).days > 35:
            warnings.append(f"{d['name']}: balance is {(today - bd).days} days old ({bd})")
    ids = set()
    for b in bets:
        if b.get("id") in ids:
            errors.append(f"bet {b.get('id')}: duplicate id")
        ids.add(b.get("id"))
        if b.get("kind", "yes_no") == "yes_no" and b.get("p") is None:
            errors.append(f"bet {b.get('id')}: yes/no bet needs p")
        if b.get("kind") in ("amount", "date") and b.get("predicted") is None:
            errors.append(f"bet {b.get('id')}: needs predicted")
        if not b.get("check_on"):
            errors.append(f"bet {b.get('id')}: needs check_on")
    return errors, warnings


# ---------------------------------------------------------------- report

def totals(ledger: dict):
    cash = sum(float(a["balance"]) for a in ledger.get("accounts") or [] if a.get("balance") is not None)
    debt = sum(float(d["balance"]) for d in ledger.get("debts") or [] if d.get("balance") is not None)
    return cash, debt, cash - debt


def baseline_debt(history: list, today: dt.date) -> float | None:
    """Debt today if only minimums had been paid since the first snapshot."""
    if not history or not history[0].get("debts"):
        return None
    first = history[0]
    start = to_date(first["date"])
    months = (today.year - start.year) * 12 + today.month - start.month
    debts = [dict(d) for d in first["debts"]]
    run = simulate(debts, start, minimums_only=True, stop_after=max(0, months))
    return sum(run.balances.values())


def report(ledger: dict, bets: list, history: list, today: dt.date) -> str:
    s = settings(ledger)
    L = []
    cash, debt, net = totals(ledger)
    L.append(f"# Money report: {today:%a %b %-d, %Y}")
    L.append("")
    L.append(f"**Net position: {money(net)}** (cash {money(cash)}, debt {money(debt)})")
    card_debt = sum(float(d["balance"]) for d in ledger.get("debts") or []
                    if d.get("kind", "card") == "card" and d.get("balance"))
    L.append(f"Card debt: **{money(card_debt)}**. This is the expensive part, and the part the sweep attacks.")
    prev =[h for h in history if to_date(h["date"]) < today]
    if prev:
        p = prev[-1]
        L.append(f"Change since {to_date(p['date']):%b %-d}: {money(net - p['net'])}")
    base = baseline_debt(history, today)
    if base is not None:
        L.append(f"Debt vs. minimums-only path: {money(debt - base)} "
                 f"({'ahead' if debt <= base else 'behind'})")
    L.append("")

    start_cash, weeks = forecast(ledger, today)
    amount, low, low_at, floor = sweep(ledger, start_cash, weeks)
    debts = [d for d in ledger.get("debts") or [] if d.get("balance")]
    targets = payoff_targets(debts, today)
    horizon_end = today + dt.timedelta(days=int(s["due_soon_days"]))

    # ---- Do this week: headline, then dated items in date order, then undated
    head, dated, other = [], [], []
    if amount > 0 and targets:
        t = targets[0]
        head.append(f"**Sweep {money(amount)} to {t['name']}** "
                    f"({'over limit' if is_over_limit(t) else pct(apr_on(t, today)) + ' APR'}). "
                    f"Cash still stays above {money(floor)} for the next {s['horizon_weeks']} weeks.")
    elif low_at:
        head.append(f"**No sweep.** Cash is forecast to drop to {money(low)} in the week of "
                    f"{low_at:%b %-d}. That's {money(floor - low)} under the {money(floor)} floor, "
                    f"so that much more has to be collected or booked by then.")
    for w in weeks:
        for e in w.events:
            if e.date <= horizon_end and e.amount < 0 and e.committed \
                    and not (e.kind == "bill" and -e.amount < 100):
                dated.append((e.date, f"pay {e.label}, {money(-e.amount, cents=True)}"
                              + (f" ({e.note})" if e.note else "")))
    for r in ledger.get("receivables") or []:
        if r.get("status") == "paid":
            continue
        due = to_date(r.get("due"))
        if due and due <= horizon_end:
            late = (today - due).days
            what = f"{r['client']} {r.get('what', '')}".strip()
            if late > 0:
                other.append(f"Chase {what}, {money(r['amount'], cents=True)}: {late} days late")
            else:
                dated.append((due, f"check {what} arrived, {money(r['amount'], cents=True)}"))
    for p in ledger.get("pipeline") or []:
        if p.get("stage") not in OPEN_STAGES or p.get("stage") == "booked":
            continue
        lt = to_date(p.get("last_touch"))
        db = to_date(p.get("decision_by"))
        if db and db <= horizon_end:
            dated.append((db, f"decision due, {p['client']}"
                          + (f" ({money(p['amount'])})" if p.get("amount") else "")))
        elif lt and workdays_between(lt, today) >= s["quiet_after_workdays"]:
            other.append(f"Nudge {p['client']}: quiet {workdays_between(lt, today)} working days")
    for b in bets:
        if b.get("outcome") is None and to_date(b["check_on"]) <= today:
            other.append(f"Grade bet {b['id']}: {b['claim']}")
    todo = head + [f"{d:%a %b %-d}: {t}" for d, t in sorted(dated, key=lambda x: x[0])] + other
    L.append("## Do this week")
    L += [f"- {t}" for t in todo] or ["- Nothing due."]
    L.append("")

    # ---- Forecast
    L.append(f"## Cash, next {s['horizon_weeks']} weeks")
    L.append(f"Starting cash {money(start_cash)}. Counts only money that's owed or certain; "
             "weighted pipeline is shown separately and never spent.")
    L.append("")
    L.append("| Week of | In | Out | End cash | Pipeline (weighted) | Big items |")
    L.append("|---|--:|--:|--:|--:|---|")
    for w in weeks:
        big = [f"{e.label} {money(e.amount)}" for e in w.events if abs(e.amount) >= 1000 and e.committed]
        flag = " ⚠" if w.balance < floor else ""
        L.append(f"| {w.start:%b %-d} | {money(w.cash_in)} | {money(w.cash_out)} | "
                 f"{money(w.balance)}{flag} | {money(w.pipeline) if w.pipeline else ''} | "
                 f"{'; '.join(big)} |")
    L.append("")

    # ---- Debt
    L.append("## Debt")
    L.append("| Debt | Balance | Limit | APR | Interest/mo | Minimum | Due |")
    L.append("|---|--:|--:|--:|--:|--:|---|")
    for d in sorted(debts, key=lambda d: -d["balance"]):
        limit = money(d["limit"]) if d.get("limit") else ""
        if is_over_limit(d):
            limit += " (over)"
        pay = d.get("payment") or d.get("min_payment")
        due = f"{to_date(d['due_date']):%b %-d}" if d.get("due_date") else "?"
        L.append(f"| {d['name']} | {money(d['balance'])} | {limit} | {pct(d.get('apr'))} | "
                 f"{money(d['balance'] * apr_on(d, today) / 12)} | "
                 f"{money(pay, cents=True) if pay else '?'} | {due} |")
    sb = scoreboard(ledger, bets, today)
    L.append("")
    L.append(f"Interest right now: about **{money(sb['interest_per_month'])} a month**.")
    if targets:
        L.append("Extra money goes to, in order: " + ", ".join(
            f"{i + 1}. {t['name']}" for i, t in enumerate(targets)) + ".")
    L.append("")

    # ---- Payoff paths (cards; loans run on their own schedules)
    cards = [d for d in debts if d.get("kind", "card") == "card"]
    L.append("## Card payoff paths")
    L.append("| Monthly card payments | Cards at $0 | Total interest |")
    L.append("|---|---|--:|")
    mo = simulate(cards, today, minimums_only=True)
    L.append(f"| Minimums only (they shrink) | {_when(today, mo.months)} | {money(mo.total_interest)} |")
    for extra in s["extra_scenarios"]:
        run = simulate(cards, today, extra=float(extra))
        label = "Today's minimums, held steady" if not extra else f"Today's minimums + {money(extra)}"
        L.append(f"| {label} | {_when(today, run.months)} | {money(run.total_interest)} |")
    loans = [d for d in debts if d.get("kind") == "loan"]
    if loans:
        L.append("")
        L.append("Loans on schedule (no extra payments planned): " + "; ".join(
            f"{d['name']} paid off {_when(today, simulate([d], today, minimums_only=True).months)}"
            for d in loans if d.get("payment") or d.get("min_payment")) + ".")
    L.append("")

    # ---- Decisions with a deadline
    decisions = sorted(ledger.get("decisions") or [], key=lambda x: to_date(x.get("by")) or dt.date.max)
    open_dec = [x for x in decisions if not x.get("done")]
    if open_dec:
        L.append("## Decisions with a deadline")
        for x in open_dec:
            by = f"by {to_date(x['by']):%b %-d}" if x.get("by") else "no deadline"
            worth = f", worth {money(x['worth'])}/yr" if x.get("worth") else ""
            L.append(f"- {x['what']} ({by}{worth})")
        L.append("")

    # ---- Receivables and pipeline
    recv = [r for r in ledger.get("receivables") or [] if r.get("status") != "paid"]
    L.append(f"## Money owed to you: {money(sum(r['amount'] for r in recv))}")
    for r in sorted(recv, key=lambda r: to_date(r.get("due")) or dt.date.max):
        due = f"due {to_date(r['due']):%b %-d}" if r.get("due") else "no due date"
        L.append(f"- {r['client']}, {r.get('what', '')}: {money(r['amount'], cents=True)}, {due}"
                 + (f" ({r['status']})" if r.get("status") not in (None, "sent") else ""))
    L.append("")
    pipe = [p for p in ledger.get("pipeline") or [] if p.get("stage") in OPEN_STAGES]
    probs = s["stage_probability"]
    weighted = sum(float(p["amount"]) * float(p.get("p", probs.get(p["stage"], 0)))
                   for p in pipe if p.get("amount"))
    L.append(f"## Pipeline: {money(sum(p['amount'] for p in pipe if p.get('amount')))} quoted, "
             f"{money(weighted)} weighted")
    for p in sorted(pipe, key=lambda p: -(p.get("amount") or 0)):
        bits = [p["stage"]]
        if p.get("decision_by"):
            bits.append(f"decision by {to_date(p['decision_by']):%b %-d}")
        if p.get("next_action"):
            bits.append(p["next_action"])
        L.append(f"- {p['client']}: {money(p['amount']) if p.get('amount') else 'no number yet'}. {'; '.join(bits)}")
    L.append("")

    # ---- Bets and scoreboard
    open_bets = [b for b in bets if b.get("outcome") is None]
    L.append("## Open bets")
    for b in sorted(open_bets, key=lambda b: to_date(b["check_on"])):
        guess = (f"{float(b['p']):.0%}" if b.get("kind", "yes_no") == "yes_no" else str(b.get("predicted")))
        rob = f", Rob {float(b['p_rob']):.0%}" if b.get("p_rob") is not None else ""
        L.append(f"- {b['id']} (check {to_date(b['check_on']):%b %-d}): {b['claim']}: {guess}{rob}")
    L.append("")
    none = "not enough data yet"
    L.append("## Scoreboard")
    L.append(f"- Net position: {money(net)}")
    L.append("- Effective day rate: " + (money(sb["effective_day_rate"]) if "effective_day_rate" in sb else none))
    L.append("- Win rate: " + (f"{sb['win_rate']:.0%} of last {sb['win_rate_n']}" if "win_rate" in sb else none))
    L.append("- Days to cash: " + (f"{sb['days_to_cash']:.0f}" if "days_to_cash" in sb else none))
    L.append("- Bet calibration (Brier; 0 is perfect, 0.25 is a coin flip): "
             + (f"{sb['brier']:.3f} over {sb['brier_n']} bets" if "brier" in sb else "no graded bets yet"))
    L.append("- Forecast error: " + (f"{sb['forecast_error']:.0%}" if "forecast_error" in sb else "no graded forecasts yet"))
    L.append("")

    errors, warnings = check(ledger, bets, today)
    verify = ledger.get("to_verify") or []
    if errors or warnings or verify:
        L.append("## Data gaps")
        L += [f"- ERROR: {e}" for e in errors] + [f"- {w}" for w in warnings] + [f"- {v}" for v in verify]
        L.append("")
    return "\n".join(L)


def _when(today: dt.date, months: int | None) -> str:
    if months is None:
        return "not within 50 years"
    return f"{add_months(today, months):%b %Y} ({months} mo)"


# ---------------------------------------------------------------- CLI

def load_all():
    ledger = load_yaml(DATA / "ledger.yaml", {})
    bets = load_yaml(DATA / "bets.yaml", {}).get("bets") or []
    history = load_yaml(DATA / "history.yaml", {}).get("snapshots") or []
    return ledger, bets, history


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "report", "snapshot"])
    ap.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    ap.add_argument("--write", action="store_true", help="save the report to reports/YYYY-MM-DD.md")
    a = ap.parse_args(argv)
    ledger, bets, history = load_all()

    if a.command == "check":
        errors, warnings = check(ledger, bets, a.today)
        for e in errors:
            print(f"ERROR   {e}")
        for w in warnings:
            print(f"warning {w}")
        print(f"{len(errors)} errors, {len(warnings)} warnings")
        return 1 if errors else 0

    if a.command == "report":
        text = report(ledger, bets, history, a.today)
        print(text)
        if a.write:
            REPORTS.mkdir(exist_ok=True)
            (REPORTS / f"{a.today}.md").write_text(text + "\n")
        return 0

    if a.command == "snapshot":
        cash, debt, net = totals(ledger)
        snap = {"date": a.today, "cash": round(cash, 2), "debt": round(debt, 2), "net": round(net, 2),
                "debts": [{k: d.get(k) for k in ("id", "name", "kind", "balance", "apr", "limit",
                                                  "min_payment", "payment", "promo") if d.get(k) is not None}
                          for d in ledger.get("debts") or [] if d.get("balance") is not None]}
        history = [h for h in history if to_date(h["date"]) != a.today] + [snap]
        with open(DATA / "history.yaml", "w") as f:
            yaml.safe_dump({"snapshots": history}, f, sort_keys=False)
        print(f"snapshot {a.today}: net {money(net)}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
