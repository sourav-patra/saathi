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

```bash
mkdir -p /etc/saathi/secrets && chmod 700 /etc/saathi/secrets
openssl rand -hex 32 > /etc/saathi/secrets/sender_hmac_key
openssl rand -hex 32 > /etc/saathi/secrets/approval_secret
chmod 600 /etc/saathi/secrets/*
```

The three edge files hold the values the messaging platforms give you (the app secret, and a verify token you choose); write each with `printf %s` into its own file. A trailing newline is ignored. The `kek` file is created with the worker unit, which fixes its exact format (32 bytes); keep a copy offline, because without it the stored household keys cannot be opened.

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

## 2. Household record (people and approvers)

Create the household, its elder, its approvers, the elder's channel identity and the household keys with `saathi-admin` (run as the owner role, on your own machine). The elder's number and the approvers' Telegram ids go into those rows, never into `.env`. Messages from a sender with no registered identity get no reply and are counted in the weekly digest.

## 3. Channels

### Caregiver bot (Telegram)

1. Create a bot with @BotFather and put its token in `SAATHI_TG_CAREGIVER_TOKEN`. This token is never given to the edge.
2. Register each approver's Telegram id as an approver row of the household.

### WhatsApp Business Cloud API (elder side)

1. Create a Meta developer app with the WhatsApp product, and add the helper number. A new SIM works; once registered, that number can't run the normal WhatsApp app.
2. Copy the **Phone number ID**, a **permanent access token** (System User) and the **App secret** into `.env`. Choose any string for `SAATHI_WA_VERIFY_TOKEN`.
3. Set the webhook URL to `https://<SAATHI_DOMAIN>/wa/<SAATHI_WA_WEBHOOK_PATH>` with your verify token, and subscribe to the `messages` field.
4. Register the elder's number as an identity of the elder with `saathi-admin`.

## 4. Smoke test

1. **Health.** `curl -i https://<SAATHI_DOMAIN>/health` answers 200 with `ok` once the core is running, and 503 with no body when it is not.
2. **Elder text.** The elder sends a text to the helper number. The edge stores it before it answers the platform; the worker then handles it.
3. **Unknown sender.** A number that is not registered gets no reply.
4. **Radar.** Send the text "seene mein dard" from the elder's number. Both approvers should get an alert that repeats until someone taps Acknowledge.
5. **Traces.** Open Langfuse at `http://localhost:3000` (via an SSH tunnel or Tailscale).

## Later phases

- Phase 1: commerce MCP OAuth logins.
- Phase 2: Raspberry Pi bridge and spare phone (ADB over Tailscale).

Each phase's setup is documented when that phase lands.

Skipped or not checked: the steps above were not run on a server in this pass. The worker and alert services, and their compose entries, arrive with their own units.
