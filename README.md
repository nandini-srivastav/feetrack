# FeeTrack

Fee tracking and a parent portal for a four-centre JEE/NEET coaching institute in Jaipur.

Parents see what they owe and when, in Hindi or English, and pay by link. Reminders go out
on their own and stop the moment an instalment is paid. Accounts staff get one view of dues
by centre and batch, refunds that follow the published policy, and a Tally export.
A dropout-risk flag gives counsellors a call list, and does nothing else.

Setup, environment variables and demo logins: **[deployment.md](deployment.md)**.

## Layout

| Path | What lives there |
|---|---|
| `fees/rules.py` | Pure fee rules with no database access: instalment split, allocation, overdue and ageing, GST split, pro-rata refund |
| `fees/services.py` | Applying payments (row-locked, gap-free receipts), gateway events, unmatched queue, refunds |
| `reminders/` | Reminder schedule per instalment, daily dispatch that re-checks the balance before every send |
| `risk/` | Feature building, logistic-regression scoring from `model.json`, counsellor tasks, training script |
| `parents/` | OTP sign-in, consent, dues / receipts / progress screens, test checkout and signed webhook |
| `office/` | Staff screens, all scoped by role and centre |
| `tests/` | Rules, payments, reminders, risk and web-layer tests (`python -m pytest`) |
