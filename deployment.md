# FeeTrack — deployment

FeeTrack is a Django 5.2 app with server-rendered pages and PostgreSQL in
production (SQLite locally). Payments, SMS/WhatsApp and Tally are mocked
behind the same interfaces the real services would use.

## Run it locally

Requires Python 3.12+ (tested on 3.13).

```bash
git clone <repo-url> feetrack && cd feetrack
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

cp .env.example .env                 # local settings; .env is git-ignored
python manage.py migrate
DEMO_PASSWORD=choose-a-password python manage.py seed_demo
python manage.py runserver
```

Open http://127.0.0.1:8000.

- `seed_demo` creates 4 centres, 16 batches and 164 students, starting from
  the three students in the brief. It replays every day from course start
  (1 Jun 2026) to 2 Nov 2026 through the real payment and reminder code, so
  receipts, reminder history and ageing are genuine output, not typed in.
- `seed_demo --reset` wipes and reseeds. `--if-empty` seeds only a blank database.
- If `DEMO_PASSWORD` is not set, a random staff password is generated and printed.

Run the tests:

```bash
python -m pytest
```

Optional:

```bash
python manage.py daily_jobs          # send due reminders + rescore dropout risk
python manage.py train_risk_model    # retrain risk/model.json (needs numpy, scikit-learn)
```

To use PostgreSQL locally, set `DATABASE_URL=postgres://user:pass@localhost:5432/feetrack` in `.env`.

## How it is deployed

Hosted on Render using the blueprint in `render.yaml`:

- **Web service** (Python): the build installs `requirements.txt` and runs `collectstatic`.
  The start command runs `migrate`, then `seed_demo --if-empty`, then gunicorn.
  Static files are served by WhiteNoise.
- **Database**: Render PostgreSQL, wired in through `DATABASE_URL`.
- **Daily jobs**: `python manage.py daily_jobs` is meant to run once a day as a Render cron job
  (included in `render.yaml`, commented out because cron jobs are a paid Render feature).
  In the demo, staff run the same job from **Demo controls**.

To deploy your own copy: Render dashboard → New → Blueprint → choose the repository →
enter `DEMO_PASSWORD` and `PUBLIC_BASE_URL` when prompted.

## Environment variables

Names only. Values live in Render's dashboard or a local `.env` that is never committed.

| Name | Required | Purpose |
|---|---|---|
| `SECRET_KEY` | yes (production) | Django signing key. The app refuses to start without it when `DEBUG` is off. |
| `DEBUG` | no | `1` for local development only. Off by default. |
| `ALLOWED_HOSTS` | yes (production) | Comma-separated host names. |
| `CSRF_TRUSTED_ORIGINS` | yes (production) | Comma-separated origins with scheme. |
| `DATABASE_URL` | yes (production) | PostgreSQL connection URL. Defaults to local SQLite. |
| `GATEWAY_WEBHOOK_SECRET` | yes (production) | HMAC secret that payment-gateway webhooks must be signed with. |
| `DEMO_MODE` | no | `1` shows OTPs on screen instead of sending SMS, and enables Demo controls (moving the simulated date). |
| `DEMO_PASSWORD` | for seeding | Password given to the demo staff accounts by `seed_demo`. |
| `PUBLIC_BASE_URL` | no | Absolute base URL used in pay links inside reminder messages. |
| `SECURE_SSL_REDIRECT` | no | Defaults to on when `DEBUG` is off. |
| `PYTHON_VERSION` | Render only | Python version for the Render build. |

## Demo logins for the reviewer

The demo date is fixed at **Mon 2 Nov 2026**, the date the brief's sample data implies.

**Parents** (Parent sign in). In demo mode the OTP is shown on screen.

| Mobile | Child | Shows |
|---|---|---|
| 9876500587 | Ishita Sharma, JEE 2027 Evening | ₹15,000 part paid, 23 days overdue (Hindi) |
| 9876500412 | Aarav Meena, JEE 2027 Morning | Paid up to date (English) |
| 9876500219 | Mohit Gurjar, NEET 2027 Morning | ₹40,000 due 15 Nov (Hindi, consent screen first) |

**Staff** (Staff sign in). All use the password set in `DEMO_PASSWORD`, which is given in the submission form.

| Username | Role | Sees |
|---|---|---|
| `accounts` | Accounts executive, Centre 2 | Ledger, pay links, refunds, unmatched queue, uploads, Tally export (Centre 2 only) |
| `accounts.c1` | Accounts executive, Centre 1 | Same, Centre 1 only (shows centre isolation) |
| `counsellor` | Counsellor, Centre 2 | Dropout-risk call tasks only, no fee screens |
| `director` | Director | Everything, all centres |

### A five-minute walkthrough

1. Sign in as Ishita's parent. Pay the ₹15,000 on the test checkout. You get a GST receipt and the message that reminders have stopped.
2. Sign in as `accounts`, open Ishita's ledger. The payment, receipt and cancelled reminders are all there.
3. On Aarav's ledger, choose **Start refund**. The amount is worked out from the published policy and cannot be typed over.
4. Open **Unmatched payments**. Payments that could not be tied to an instalment wait here for a person to confirm.
5. Sign in as `counsellor`. Each flag shows the factors behind its score and can be marked as a false flag.
6. In **Demo controls**, move to the next day. Due reminders go out, and any for a cleared balance are skipped.
