# Setup (Phase 0)

Run these steps on your own machine and server. Keep everything personal (names, numbers, keys) in `.env` and in the secret files on the server; never put it in the repo.

Two kinds of value exist. Deployment values (URLs, tokens, key files) live in `.env` and the secret files. Household values (the people, the approvers, caps, names) are versioned `household_config` rows in the database, written with `saathi-admin`; no setting in `.env` carries them.

## 1. Server (any always-on ARM64/x86_64 VPS, ≥ 4 vCPU and 16 GB RAM recommended)

1. Install Docker Engine and the compose plugin.
2. Open ports **80** and **443** only. Reach SSH and the Langfuse UI over Tailscale or an SSH tunnel.
3. If your cloud provider reclaims "idle" free-tier instances, follow its guidance to prevent that. The Langfuse/ClickHouse stack usually keeps memory above typical idle thresholds.
4. Point a DNS name at the server (any free dynamic-DNS service works). It becomes `SAATHI_DOMAIN`.

Clone the repo on the server:

```bash
git clone <this-repo-url> saathi && cd saathi
```

Create your `.env` from the template:

```bash
cp .env.example .env && chmod 600 .env
```

Fill in `.env`, generating each secret like this:

```bash
openssl rand -hex 32
```

### Secret files

These secrets are files, not `.env` values. Keep them in one directory on the host (`SAATHI_SECRETS_DIR`, default `/etc/saathi/secrets`), mode 600, each its own random value:

| File | Holds | Mounted into |
|---|---|---|
| `sender_hmac_key` | the key of the sender hash (the edge hashes every sender with it) | edge |
| `wa_app_secret` | the WhatsApp app secret that signs webhook calls | edge |
| `wa_verify_token` | the webhook verification token | edge |
| `tg_elder_secret` | the secret of the elder bot's webhook | edge |
| `approval_secret` | the approval signing secret, at least 32 bytes | worker and approval path, never the edge |
| `kek` | the key-encryption key, exactly 32 bytes | worker only, never the edge |
| `wa_access_token` | the WhatsApp permanent system-user access token | worker only |
| `tg_caregiver_token` | the caregiver bot token from BotFather | worker only |
| `tg_elder_token` | the elder bot token, Phase 1 | worker only |

```bash
mkdir -p /etc/saathi/secrets && chmod 700 /etc/saathi/secrets
openssl rand -hex 32 > /etc/saathi/secrets/sender_hmac_key
openssl rand -hex 32 > /etc/saathi/secrets/approval_secret
chmod 600 /etc/saathi/secrets/*
```

The three edge files and the three token files hold the values the messaging platforms give you (the app secret, the access and bot tokens, and a verify token you choose); write each with `printf %s` into its own file. A trailing newline is ignored. The `kek` file is created with the worker unit, which fixes its exact format (32 bytes); keep a copy offline, because without it the stored household keys cannot be opened.

### Database logins

`SAATHI_DB_URL_EDGE`, `SAATHI_DB_URL_CORE`, `SAATHI_DB_URL_MODEL` and `SAATHI_DB_URL_OWNER` each log in as their own role. The passwords come from the `SAATHI_DB_PASSWORD_*` values, which the database container reads once when it first starts.

The database has no published port and no route out; it sits on the internal network alone. For admin access, run psql inside the container: `docker compose exec postgres psql -U "$POSTGRES_USER" saathi`. To load the local models once, run `docker compose --profile pull run --rm ollama-pull`; that one-off service is the only one that reaches the model registry.

### Webhook paths

`SAATHI_WA_WEBHOOK_PATH` is a random segment (for example the output of `openssl rand -hex 16`). The WhatsApp webhook is `https://<SAATHI_DOMAIN>/wa/<segment>`. The Telegram elder bot of Phase 1 uses `SAATHI_TG_ELDER_WEBHOOK_PATH` the same way, at `/tg/elder/<segment>`. A route is mounted only when its segment and its secret are both set.

Start the stack:

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d --build
```

Watch the logs:

```bash
docker compose --env-file .env -f infra/docker-compose.yml logs -f edge
```

The edge answers `GET /health` with `ok` only while the core heartbeat is fresh and its own write works; otherwise it answers 503 with no body.

### External uptime monitor

1. Configure an external free service to request `https://<SAATHI_DOMAIN>/health` every minute from outside the server. Keep the service choice in private configuration.
2. Configure the service to page both approvers through its own notification channel after 3 failures in a row.
3. Run the outage drill and record the result in receipt RQ-104. Check that both approvers receive the page after 3 consecutive failures.

## 2. Household record (people and approvers)

Create the household, its elder, its approvers, the elder's channel identity and the household keys with `saathi-admin` (run as the owner role, on your own machine). The elder's number and the approvers' Telegram ids go into those rows, never into `.env`. Messages from a sender with no registered identity get no reply and are counted in the weekly digest.

## 3. Channels

### Caregiver bot (Telegram)

1. Create a bot with @BotFather and save its token in the server's `tg_caregiver_token` secret file. Compose supplies it through `SAATHI_TG_CAREGIVER_TOKEN_FILE`; the edge never receives it.
2. Register a privacy policy in BotFather. Telegram's standard bot privacy policy applies by default. The bot must delete user data on request.
3. Register each approver's Telegram id as an approver row of the household.

### WhatsApp Business Cloud API (elder side)

1. Start Meta's app creation flow and create a business portfolio, which is required before the app can use WhatsApp. Use the legal name you will later use for business verification as the portfolio name. Create the app with the "Connect with customers through WhatsApp" use case.
2. Open Step 1, "Try it out". The test number can message at most 5 verified recipients. Add a recipient and verify it with the one-time password (OTP) received in WhatsApp. Generate a temporary access token to send the test message; it expires in about a day. Check that the recipient receives the message.
3. Once the HTTPS server exists, open Step 2, "Production setup". Set the webhook callback URL to `https://<SAATHI_DOMAIN>/wa/<SAATHI_WA_WEBHOOK_PATH>`. Choose a verify token, save it in the server's `wa_verify_token` secret file and enter the same value in the dashboard. Save the app secret in `wa_app_secret`, and subscribe to the `messages` field.
4. Add a phone number that has never been registered on the WhatsApp app and verify it by OTP. Put its Phone number ID in `SAATHI_WA_PHONE_NUMBER_ID` in `.env`. Add a payment method for business-initiated messages.
5. Create the permanent system-user access token only after the server exists. Keep it only in the server's `wa_access_token` secret file. Compose reads these secret files through the corresponding `SAATHI_WA_*_FILE` settings.
6. Publish the app to receive production webhooks. While the app is unpublished, only test webhooks sent from the dashboard are delivered. Check dashboard test delivery before publishing and production delivery after publishing.
7. Complete Step 3, business verification, using legal documents matching the portfolio's legal name.
8. Register the elder's number as an identity of the elder with `saathi-admin`.

The Meta Terms for WhatsApp Business Platform, section 4.7 (AI Providers), bar general-purpose AI assistants. Saathi on WhatsApp handles only its listed tasks and declines open-ended conversation ([receipt PL-32](evidence/receipts/RECEIPT-pl-32-2026-11-13.md)).

## 4. Smoke test

1. **Health.** `curl -i https://<SAATHI_DOMAIN>/health` answers 200 with `ok` once the core is running, and 503 with no body when it is not.
2. **Elder text.** The elder sends a text to the helper number. The edge stores it before it answers the platform; the worker then handles it.
3. **Unknown sender.** A number that is not registered gets no reply.
4. **Radar.** Send the text "seene mein dard" from the elder's number. Both approvers should get an alert that repeats until someone taps Acknowledge.
5. **Traces.** Open Langfuse at `http://localhost:3000` (via an SSH tunnel or Tailscale).

## Container images are never published

The container images are built for the owner's own server only and are never published to any registry. Some
packages in them carry GPL-family licences (listed by name in `scripts/image_base_allowlist.toml`). Publishing an
image would make the owner a distributor of that GPL code. In that case the owner would need to ship the licence
texts, a written offer of the matching source, and a third-party notices file. `scripts/ci_lint.py` fails on any
workflow with a `docker push` step, `push: true` in a build action, or a registry login.
`THIRD-PARTY-NOTICES.md` at the repository root lists the image packages for reference.

## Later phases

- Phase 1: commerce MCP OAuth logins.
- Phase 2: Raspberry Pi bridge and spare phone (ADB over Tailscale).

Each phase's setup is documented when that phase lands.

Skipped or not checked: the steps above were not run on a server in this pass. The worker and alert services, and their compose entries, arrive with their own units.
