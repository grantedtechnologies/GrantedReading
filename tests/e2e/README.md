# Granted Reading E2E (Playwright)

Browser tests against a local Flask app, a dedicated MySQL database (`granted_test`), Mailpit, and (for the live tier) Azure + Runpod + Ollama.

## Prerequisites

Always running for the **fast** tier:

1. MySQL — `granteddb` (local) or a DB whose name contains `test` (e.g. `granted_test`); schema from `granted.sql`. Put the real `LOCAL_DATABASE_PASSWORD` in `.env.test`.
2. Redis
3. [Mailpit](https://github.com/axllent/mailpit) installed (`brew install mailpit`). The suite auto-starts it on `localhost:1025` (SMTP) / `8025` (API) when needed; you can also run `mailpit` yourself.
4. `.env.test` filled in from `.env.test.example`

Also for the **live** tier:

5. Azure Foundry credentials + Runpod API key  
6. `ngrok-endpoint` forwarding to your local app (Runpod callbacks)  
7. Ollama with `JUDGE_MODEL` pulled  
8. `MOCK_AI=false` (or unset)

## Safety

Session start **aborts** if:

- `LOCAL_DATABASE_NAME` is not `granteddb` and does not contain `test`
- `STRIPE_SECRET_KEY` does not start with `sk_test_`

`MOCK_AI=true` refuses to start when Railway/production env vars are set.

## Install

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
playwright install chromium   # required once per Playwright upgrade
cp .env.test.example .env.test
# edit .env.test (Stripe sk_test_…, DB password if needed)
```

If you see `Executable doesn't exist` / Chromium missing, re-run `playwright install chromium` inside the venv.


## Commands

Fast tier (MOCK_AI, default):

```bash
pytest tests/e2e -m "not live and not stripe_cli" -rs --tracing=retain-on-failure --screenshot=only-on-failure
```

On failure the runner prints a banner with **test name**, **input**, **expected**, and **actual** (via the `case` fixture). Skips/aborts also print a clear reason (e.g. missing `.env.test`).

Full run output (including long tracebacks) is written to:

- `e2e_logs/latest.txt` — always the most recent run  
- `e2e_logs/e2e_<timestamp>_<runid>.txt` — timestamped copy  

Open that file after a run if the terminal truncates.

Live tier:

```bash
MOCK_AI=false pytest tests/e2e/test_generation_live.py -m live --tracing=retain-on-failure
```

Single test:

```bash
pytest tests/e2e/test_login.py::test_valid_login_lands_on_app -vv
```

Sweeper (stale `e2e+*@granted.test` users older than 24h):

```bash
python scripts/sweep_e2e_data.py
python scripts/sweep_e2e_data.py --hours 48 --dry-run
```

Optional: point at an already-running app with `BASE_URL=http://127.0.0.1:5000`.

## Coverage (fast tier)

| Area | Happy path | Empty / invalid |
|------|------------|-----------------|
| Login | Valid login, logout + auth gate | Empty fields (HTML required), wrong password |
| Forgot password | Mailpit reset link → new password → sign in | Empty email; weak / mismatched reset password |
| Signup | Standard + Mailpit verify; Pro + signed Stripe webhook | Empty name/school/state-city; weak/mismatched password; pilot without code |
| Classes | Class + **3 students** each with a **vocab list**; edit notes; delete student | Empty class name/subject; empty student name; vocab missing name/words |
| Generation | Full generate → preview → download → history; **Update PDF** (text); **Regenerate Illustration** (image) | Missing grade / reading level / interests; empty title+story on update; regen without image feedback |
| History feedback | Column layout → rate + comment → Saved; update feedback; rating-only | (empty text with rating is allowed) |

## Markers

| Marker | Meaning |
|--------|---------|
| `live` | Real Azure LLM + Runpod |
| `stripe_cli` | Real Stripe CLI forwarded webhooks |

## App hooks used by tests

- `MOCK_AI=true` → fixture LLM + fixture PNG; posts a signed fake Runpod webhook
- `EMAIL_BACKEND=smtp` → Mailpit instead of Mailtrap HTTPS
- `POST /webhooks/runpod` → signature + `processed_webhook_events` dedup (production image gen still polls)
