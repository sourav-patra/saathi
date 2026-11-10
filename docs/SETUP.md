# Setup (Phase 0)

Note: elder and approver ids move to household config rows (RQ-110, RQ-111a). This page is rewritten after WU-12 and WU-22; until then the steps below describe the earlier environment-variable setup.

Run these steps on your own machine and server. Keep everything personal (names, numbers, keys) in `.env` on the server; never put it in the repo.

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

Start the stack:

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d --build
```

Watch the logs:

```bash
docker compose --env-file .env -f infra/docker-compose.yml logs -f saathi
```

## 2. Telegram bot (caregiver side)

1. Create a bot with @BotFather and put its token in `SAATHI_TG_BOT_TOKEN`.
2. Start the stack, send `/start` to the bot, and it replies with your Telegram id. Put that in `SAATHI_TG_PRIMARY_APPROVER_ID`. Do the same for the backup approver (`SAATHI_TG_BACKUP_APPROVER_IDS=[id]`).
3. Restart the `saathi` service.

## 3. WhatsApp Business Cloud API (elder side)

1. Create a Meta developer app with the WhatsApp product, and add the helper number. A new SIM works; once registered, that number can't run the normal WhatsApp app.
2. Copy the **Phone number ID**, a **permanent access token** (System User) and the **App secret** into `.env`. Choose any string for `SAATHI_WA_VERIFY_TOKEN`.
3. Set the webhook URL to `https://<SAATHI_DOMAIN>/webhooks/whatsapp` with your verify token, and subscribe to the `messages` field.
4. Add the elder's number to `SAATHI_ELDER_PHONES` (E.164). Messages from other numbers are ignored.

## 4. Smoke test

1. **Elder voice note.** The elder sends a voice note to the helper number. You should get a Telegram summary card, and the elder gets an acknowledgement.
2. **Forward intake.** Forward any voice note or text into the Telegram bot. It should be processed the same way.
3. **Radar.** Send the text "seene mein dard" from the elder's number. Both approvers should get a 🚨 alert that repeats until someone taps Acknowledge.
4. **Traces.** Open Langfuse at `http://localhost:3000` (via an SSH tunnel or Tailscale). Each message appears as an `elder_request` trace.

## Later phases

- Phase 1: commerce MCP OAuth logins.
- Phase 2: Raspberry Pi bridge and spare phone (ADB over Tailscale).

Each phase's setup is documented when that phase lands.

Skipped or not checked: the steps below were not run again in this pass.
