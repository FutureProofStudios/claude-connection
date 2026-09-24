# Money Engine: plan

A small system that runs the business's money, checks how its own predictions
turned out, and rewrites its own rules. Kept as simple as possible, and only
as complex as needed.

## Goal

One number goes up every week: **net position = all cash minus all debt.**
The owner sets the target and date.

## First principles

- Money only moves four ways: sell more days, charge more per day, get paid
  sooner, lose less to interest and fees. Tax is the accountant's job.
- A system can only improve if it can see where it was wrong. So every
  forecast and decision gets written down *before* the outcome, with a date
  to check it.
- People are bad at remembering follow-ups, doing interest math, and noticing
  slow drift. Software is bad at taste, relationships, and judgment calls.
  Split the work that way.
- Build the simplest thing that works: plain files, one small script, three
  scheduled prompts. No app and no database server.

## The parts

| Part | What it holds | Who updates it |
|---|---|---|
| **Ledger** | Facts: accounts, debts (balance, APR, minimum, due date), money owed, pipeline, upcoming bills, tax reserve | Daily routine, from email, statements, and the books |
| **Bets** | Every prediction, logged before the outcome ("paid by Oct 9", "yes: 60%", "cash on Nov 1: $N"), each with a check date | Routines and owner |
| **Playbook** | The current rules. Each is numbered and has a why, a start date, and evidence | Weekly routine proposes, owner approves |
| **Engine** | One script for net position, 13-week cash, the sweep amount, payoff order, and the scorecard. All the math lives here, never in a prompt | Monthly routine improves it |
| **Reports** | A morning alert (only when something changed) and a Friday one-pager | Routines |

## Three loops (the recursion)

1. **Daily: run the rules.** Read new mail, statements, and the books, then
   update the ledger and run the engine. Draft whatever needs doing: nudges,
   invoices, reminders. Send an alert only if something changed. Nothing gets
   sent or paid without the owner.
2. **Weekly (Friday): improve the rules.** Score the bets that came due and
   update the scoreboard. Propose at most **one** playbook change, with the
   evidence behind it. The owner says yes or no.
3. **Monthly: improve the system.** Check the ledger against the accountant's
   books. Every miss (a deposit it didn't catch, a forecast that was off)
   becomes a fix to how data comes in, to the engine, or to the prompts.
   Delete anything that hasn't changed a decision in 4 weeks.

Loop 2 improves the rules Loop 1 runs. Loop 3 improves the machinery behind
both. Every change is a commit, so the history shows what the system learned
and when, and any change can be undone.

## Scoreboard

- **Net position**, the one number, plus its weekly change compared with a
  "minimum payments only" baseline.
- **Effective day rate:** fees divided by all the days a job really took
  (prep, scout, travel, post).
- **Win rate:** quotes won divided by quotes sent, over the last 10.
- **Days to cash:** from wrap to money in the bank.
- **Forecast error:** how far last month's cash forecast was from reality.
  This one grades the system itself.

## Playbook v1 (starting rules, each one a test)

1. **Minimums always, on time.** A late payment can trigger a penalty APR,
   which costs more than any other rule here saves.
2. **Friday sweep.** Cash above the buffer goes to the highest-APR card. The
   buffer is the next 30 days of bills, payroll, and tax reserve, plus a safety
   margin.
3. **Price from win rate.** Quote full rate first. If you win more than
   two-thirds of recent quotes, raise the next quote 10%.
4. **Get paid sooner.** Invoice within 24 hours of wrap. New clients pay a 50%
   deposit when they book. Send a reminder the day after the due date and
   call at 10 days late.
5. **No lead goes quiet.** Any warm lead untouched for 5 working days gets a
   drafted nudge.
6. **Footage keeps paying.** Log each delivery's usage terms and end date.
   Draft a renewal quote 30 days before the usage runs out.
7. **Client seasons.** Track each repeat client's cycle (fashion weeks,
   holiday, spring) and pitch 6 weeks before it starts.

## Guardrails

- Drafts only. The owner sends every client email and makes every payment.
- Math happens in code, not in the model.
- The accountant's books are the source of truth for bookkeeping and tax. The
  system flags differences but never overrides them.
- One rule change a week, so it stays clear what caused what.
- No full account numbers stored anywhere. Last 4 digits only.
- Personal financial data never goes in a public repo.
- On days off and travel days, no alerts except a bill coming due.

## Build order

- **Phase 0 (one session):** fill the ledger from statements and email, write
  engine v1 and playbook v1, and log the first bets.
- **Phase 1 (week 1):** turn on the daily routine.
- **Phase 2 (the first Friday after):** first weekly scorecard and first rule
  proposal.
- **Phase 3 (after the next month's books close):** first reconciliation, and
  the first round of the system fixing itself.
- **90-day check:** is net position beating the minimums-only baseline? If
  not, cut the system back to what works.

## Open decisions (owner)

- The target number and date.
- Where private data lives: a private repo, or a private Drive folder with
  only code in this repo.
- Reconnect the QuickBooks connector.
- Approval to schedule the three routines.
