# Proactive Training Notifications

This guide covers the Intervals.icu OAuth connection, activity webhook, durable notification queue, Telegram feedback, polling fallback, deployment, and troubleshooting.

## Architecture

```text
Intervals.icu ACTIVITY_ANALYZED webhook
  -> HTTPS NGINX host on Home Assistant
  -> intervals-webhook.service on the desktop (192.168.0.2:8010)
  -> activity_events in Supabase (unique activity_id)
  -> training-coach-api worker
  -> Telegram summary with RPE and note buttons
```

The webhook receiver is a separate FastAPI app. It does not expose the coach's chat or sync routes. NGINX proxies only the exact OAuth and webhook paths. The existing polling worker remains the backup path.

## Intervals.icu App Setup

1. Create or open the approved app under Intervals.icu Settings / Apps / Manage App.
2. Register this exact OAuth Redirect URL:

   `https://intervals-webhook.err-hass.duckdns.org/oauth/callback`

3. Configure the webhook to POST to:

   `https://intervals-webhook.err-hass.duckdns.org/webhooks/intervals/activity`

4. Select `ACTIVITY_ANALYZED` and grant the OAuth scope `ACTIVITY:READ`.
5. Set an app webhook secret and put the same value in `INTERVALS_WEBHOOK_SECRET` in `training-coach-agent/.env`.
6. Set the app's client ID and client secret in `INTERVALS_OAUTH_CLIENT_ID` and `INTERVALS_OAUTH_CLIENT_SECRET` in that file.
7. Visit `https://intervals-webhook.err-hass.duckdns.org/oauth/start`, approve `ACTIVITY:READ`, and copy the athlete ID shown by the success page into `INTERVALS_WEBHOOK_ATHLETE_ID`.
8. Restart the webhook receiver after changing its environment.

The callback validates a random, single-use `state` value that expires after five minutes. State is held in process memory, so run exactly one Uvicorn worker and restart the flow if the service restarts before the callback. It exchanges the authorization code server-side, validates the granted scope, displays only the athlete ID, and discards the returned access token. The OAuth callback is excluded from NGINX access logging, and the webhook service runs Uvicorn with access logging disabled so query codes and payload secrets are not logged.

The personal API-key identity (`INTERVALS_ATHLETE_ID`) can differ from the OAuth athlete ID in webhook payloads. The receiver prefers `INTERVALS_WEBHOOK_ATHLETE_ID` and falls back to `INTERVALS_ATHLETE_ID` only when the override is unset.

Activity webhook events are not delivered for Strava-imported activities. Intervals.icu may delay `ACTIVITY_ANALYZED` by about 60 seconds to consolidate events.

## Environment

Keep these values in `training-coach-agent/.env`; do not paste secrets into chat, logs, or this document:

| Variable | Purpose |
|---|---|
| `INTERVALS_WEBHOOK_SECRET` | Shared secret configured in Intervals.icu's app webhook settings; sent in the JSON body |
| `INTERVALS_OAUTH_CLIENT_ID` | Approved Intervals.icu OAuth app client ID |
| `INTERVALS_OAUTH_CLIENT_SECRET` | OAuth app secret used only for server-side code exchange |
| `INTERVALS_WEBHOOK_ATHLETE_ID` | Athlete ID returned by OAuth; used to filter webhook events |
| `INTERVALS_ATHLETE_ID` | Personal API-key athlete ID used by the sync/polling client and webhook fallback |
| `INTERVALS_API_KEY` | Personal API key for the existing sync and activity writeback client |
| `SUPABASE_POOLER_DB_URI` or `SUPABASE_DB_URI` | Database connection used by the webhook receiver and coach |
| `TELEGRAM_BOT_TOKEN` | Bot token used by the notification worker |
| `TELEGRAM_ALLOWED_USER_IDS` | Must contain exactly one positive ID for the notification worker |

The webhook body is an Intervals.icu envelope with a top-level `secret` and an `events` array. Do not send the secret in an Authorization header. The receiver accepts only `ACTIVITY_ANALYZED` events matching the configured webhook athlete and an activity ID.

## Database

The schema is in `sql/proactive_notifications.sql`. It creates:

- `activity_events`: unique event ledger, worker state, attempt count, summary, Telegram message metadata, and activity note.
- `pending_input`: one expiring note prompt per private Telegram chat.

Apply the migration once to a new Supabase database before enabling the worker. It uses `IF NOT EXISTS` for repeat-safe setup. RPE remains canonical in `training_metrics`; the event table is notification state, not a duplicate activity metrics table.

## Deployment

The webhook-only receiver is `intervals-webhook.service`; it binds to `192.168.0.2:8010`. Its NGINX include is `deploy/intervals-webhook.conf`, deployed on Home Assistant at `/share/nginx_proxy/intervals-webhook.conf`. The existing NGINX add-on must have `customize.active: true` and include `nginx_proxy/*.conf`.

After updating the service unit or local configuration:

```bash
systemctl --user daemon-reload
systemctl --user restart intervals-webhook.service
systemctl --user is-active intervals-webhook.service
curl --fail http://192.168.0.2:8010/health
```

After changing the NGINX include, restart the Home Assistant NGINX add-on (`core_nginx_proxy`) and check the public host. The webhook virtual host returns 404 for all paths other than its exact webhook, OAuth start, and OAuth callback routes.

The coach API worker starts only when `TELEGRAM_BOT_TOKEN` and exactly one allowed Telegram user ID are configured. It shares the notification database schema but uses its own DB connection.

## Event Lifecycle

1. The receiver validates the configured webhook secret with a constant-time comparison.
2. It ignores events for other athletes, unsupported event types, and malformed activity IDs.
3. It inserts eligible activity IDs using `ON CONFLICT DO NOTHING` and commits before returning 200. A repeated ID is acknowledged as a duplicate.
4. The worker claims rows with `FOR UPDATE SKIP LOCKED`, preventing concurrent claims. Rows left in `processing` for five minutes can be reclaimed after a crash.
5. The worker reads activity metrics, sends a deterministic Telegram summary, and stores the message ID. It does not call an LLM for the automatic summary.
6. Workouts shorter than five minutes are marked done without a message.
7. Delivery uses at-least-once semantics: if Telegram accepts a message but the database completion write fails, a retry can send a duplicate.

Failures retry with exponential delay capped at one hour, up to eight claims. After the final attempt the event is `failed` and needs operator review; inspect `last_error` before manually requeueing.

## Telegram Feedback

- RPE buttons accept 1 through 10 and update the exact activity in Intervals.icu and `training_metrics`.
- After RPE is saved, the note button remains available.
- Choosing `Añadir nota` stores a pending activity for that private chat for 30 minutes. The next text message is saved to the activity event and posted to that exact Intervals.icu activity.
- Callback actions require the configured allowlisted Telegram ID and a private chat. Normal bot access retains its existing allowlist behavior.
- If local note storage succeeds but posting to Intervals.icu fails, the bot confirms local storage and reports the sync failure.

## Polling Fallback

Every 15 minutes, the API worker syncs the latest two days from Intervals.icu and discovers activities that are at least five minutes long, have no RPE, and have no event row. This does not require an Intervals.icu webhook or OAuth app. Webhook delivery is faster; polling protects against missed webhook delivery.

## Validation

Focused tests:

```bash
training-coach-agent/.venv/bin/python -m pytest \
  tests/test_intervals_webhook.py \
  tests/test_training_coach_primitives.py \
  tests/test_telegram.py -q
```

The receiver smoke test can send an empty event list to verify the secret and public route without queueing an activity. Never use a real activity payload for a smoke test unless a Telegram notification is intended.

## Troubleshooting

| Symptom | Check |
|---|---|
| OAuth start returns 503 | Confirm OAuth client ID is present in the service environment and restart the receiver |
| OAuth callback says state invalid | Start again from `/oauth/start`; state is single-use and expires after five minutes |
| OAuth callback returns 403 | Ensure the app grant includes `ACTIVITY:READ` |
| Webhook returns 401 | Compare the Intervals.icu app webhook secret with `INTERVALS_WEBHOOK_SECRET`; do not use a Bearer header |
| Webhook accepts but ignores all events | Compare the payload athlete ID with `INTERVALS_WEBHOOK_ATHLETE_ID`; check event type is `ACTIVITY_ANALYZED` |
| Webhook returns 503 on insert | Check Supabase connectivity and confirm the migration is applied |
| No Telegram notification | Check the API worker, Telegram allowlist, activity duration, existing RPE, queue status, and `last_error` |
| Webhook unavailable but polling works | Check HA NGINX DNS/TLS and the desktop listener at `192.168.0.2:8010` |

Useful local checks:

```bash
systemctl --user status intervals-webhook.service
systemctl --user status training-coach-api.service
systemctl --user status training-coach-telegram.service
curl --fail http://192.168.0.2:8010/health
```

Do not enable shell tracing (`set -x`) while handling credentials. Rotate any credential that has been exposed and update the corresponding local environment before restarting services.