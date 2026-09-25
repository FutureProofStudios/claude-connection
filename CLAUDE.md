# Money engine: operating manual

This repo runs Rob's money system. Read `PLAN.md` once for the why. This file
is the how, and every session (chat or scheduled routine) follows it.

## Start of every session

```
pip install -q -r requirements.txt   # only if `import yaml` fails
python engine/money.py check
python engine/money.py report
```

Read the report before doing anything else. It is the current state of the
money. Don't re-derive it from the inbox.

## Files

| Path | What | Private? |
|---|---|---|
| `data/ledger.yaml` | Facts: settings, accounts, debts, receivables, pipeline, recurring, one_offs, jobs | yes |
| `data/bets.yaml` | Predictions, logged before the outcome | yes |
| `data/playbook.md` | The rules the system runs, with evidence | yes |
| `data/history.yaml` | One snapshot per day of cash, debt, net | yes |
| `reports/` | Generated reports | yes |
| `engine/money.py` | All math. Run tests after any change: `python -m unittest discover engine` | no |
| `data/rules.md` | Rob's rules in plain words; `python engine/money.py plan` fills its `{{numbers}}` and renders `reports/money-rules.pdf`. Keep it in sync with the playbook | yes |
| `data/richlife.md` | The plain-English plan (Ramit Sethi-style): same numbers, charts from `engine/charts.py`, rendered to `reports/rich-life-plan.pdf` by the same `plan` command. Write it for a smart reader new to money words | yes |

**Privacy gate.** Before any commit that touches `data/` or `reports/`, run
`sh engine/is_private.sh`. It must print `private`. If it doesn't, don't
commit those paths. Tell Rob instead. While the repo is public, `.gitignore` also blocks
them. Remove those two lines the first time the gate passes.

## Rules for editing the ledger

- Every number has a `source`: a file name or an email subject plus date.
  If you don't know a value, leave it null. Never guess a balance, APR, or
  amount.
- Dates are `YYYY-MM-DD`. Money is a plain number with no `$`.
- Account and card ids use the last 4 digits only. Never store full account
  numbers, logins, or PINs.
- When a receivable is paid, set `status: paid` and `paid_on`, and add or
  update the matching `jobs` entry with `paid_on`. That feeds days-to-cash.
- When a pipeline item is decided, set `stage: won` or `lost` and
  `decided_on`. That feeds win rate. Don't delete it.
- When a one-off happens, set `status: done`.
- After editing, run `check`. Zero errors before you commit.

## Guardrails

- **Drafts only.** Never send a client email, pay a bill, move money, or
  accept anything. Draft it and tell Rob.
- **Math only in the engine.** If a number in a report or email didn't come
  out of `money.py`, don't state it. If the engine can't compute something
  you need, add it to the engine with a test.
- **The accountant is the source of truth** for bookkeeping, payroll, and tax.
  Flag differences to Rob and never contradict them.
- **One playbook change per week**, and only with Rob's yes.
- **Quiet days:** see `quiet_days` in ledger settings. On those days the only
  alerts allowed are bills coming due.

## The three loops

### Daily (weekday mornings)
1. Run `check` and `report`.
2. Scan Gmail since the last snapshot for money events: payments received,
   invoices, quotes, yeses and nos, new inquiries, statements, bills. Scan
   Drive's statements folder for new files.
3. Update `data/ledger.yaml` (balances, receivables, pipeline `last_touch`,
   new items). Every change needs a source.
4. Look for Rob's replies to the latest report email. A "yes" to a playbook
   proposal means apply it (see Weekly step 4). A "no" means record that in
   the playbook's rejected list.
5. Run `snapshot`, then `report --write`.
6. Email Rob only if something changed that he has to act on: money landed,
   a bill due within 3 days, a lead gone quiet, a bet to grade. Keep it to 5
   lines or fewer. No email when nothing changed.
7. Commit data and report (after the privacy gate).

### Weekly (Friday)
1. Everything in Daily.
2. Grade every bet whose `check_on` has passed: set `outcome`, `scored_on`,
   and one line of `lesson`.
3. Log new bets for the week ahead, especially a `cash_forecast` bet:
   "pooled cash on <date 4 weeks out> will be <engine's forecast>".
4. Read the scoreboard and graded bets. Propose **at most one** playbook
   change with its evidence, or propose none. When Rob says yes, edit
   `data/playbook.md`: change the rule, bump its version, and add the date and
   evidence.
5. Email Rob the Friday one-pager: net position and change, the sweep
   decision, what's owed to him, leads to nudge, graded bets, and the one
   proposal.

### Monthly (after the accountant sends the month's books)
1. Compare the ledger with the books: income, card balances, anything
   missing.
2. Every miss becomes a fix to the system itself. That could be a better
   search in the daily scan, an engine change (with a test), or a clearer
   rule in this file. Commit each fix with the miss it came from in the
   message.
3. Delete anything (a rule, a report section, a field) that hasn't changed
   a decision in 4 weeks.
4. Check the forecast error and bet calibration trend. If the system
   predicts worse than Rob's gut (`p_rob`), say so.
