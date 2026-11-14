# Deploy Saathi on Oracle Cloud Always Free

These steps give one always-on server for the v0.1.0 stack, each ending on a check. [SETUP.md](SETUP.md) installs the application; this page covers the server under it.

Oracle's Always Free page ([Always Free Resources][free], checked 2026-11-13) gives one Arm virtual machine of shape `VM.Standard.A1.Flex` with 2 OCPUs and 12 GB of memory, created in the tenancy's home region. Every image in `infra/docker-compose.yml` has an Arm build or builds on the server.

## 1. Choose the home region

Pick Mumbai (`ap-mumbai-1`) or Hyderabad (`ap-hyderabad-1`) when you sign up. Always Free compute lives in the home region, which cannot be changed later ([Managing Regions](https://docs.oracle.com/en-us/iaas/Content/Identity/regions/managingregions.htm)). Each has one availability domain ([Regions](https://docs.oracle.com/en-us/iaas/Content/General/Concepts/regions.htm)).

Check: the console shows your home region name in the region menu.

## 2. Create the instance

Follow [Creating an Instance](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/launchinginstance.htm) with these values:

1. Image: Platform Images, Canonical Ubuntu 24.04, Arm (aarch64) build.
2. Shape: Virtual machine, Ampere series, `VM.Standard.A1.Flex`, 2 OCPUs, 12 GB.
3. Networking: a public subnet, with "Automatically assign public IPv4 address" on.
4. Boot volume: a custom size of 100 GB. Always Free allows 200 GB of boot and block volumes together.
5. SSH key: upload the public half of a key pair you made yourself. Keep the private half off the server.

If the console answers "out of host capacity", retry later (one availability domain per region).

Check: `ssh -i <private-key-file> ubuntu@<public-ip>` opens a shell, and `uname -m` prints `aarch64` ([Oracle: default user](https://docs.oracle.com/en-us/iaas/Content/GSG/Tasks/testingconnection.htm)).

## 3. Open ports 80 and 443 in two places

Caddy needs port 80 for its certificate and port 443 for the webhook; both layers must allow them.

1. In the console, open your VCN, the public subnet and its Default Security List. Add two ingress rules: source CIDR `0.0.0.0/0`, protocol TCP, destination port 80 for one and 443 for the other. Oracle's walk-through: [Apache on Ubuntu](https://docs.oracle.com/en-us/iaas/developer-tutorials/tutorials/apache-on-ubuntu/01oci-ubuntu-apache-summary.htm).
2. On the instance, add the same two rules to its own firewall and save them. Do this before installing Docker, so the saved file holds only these rules:

```bash
sudo iptables -I INPUT 1 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 1 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

If `netfilter-persistent` is missing, run `sudo apt install iptables-persistent` and save again. Position 1 puts the rules above any reject rule in the image.

Check: `sudo iptables -L INPUT -n --line-numbers` lists both ACCEPT rules before any REJECT line, also after a reboot. The outside check comes in step 6, once Caddy answers.

## 4. Install Docker Engine and the compose plugin

Run the commands from Docker's own page, [Install Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/), section "Install using the apt repository": install `ca-certificates` and `curl`, save Docker's key to `/etc/apt/keyrings/docker.asc`, write `/etc/apt/sources.list.d/docker.sources`, run `sudo apt update`, then:

```bash
sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

To run `docker` without `sudo`, follow [Linux post-installation steps](https://docs.docker.com/engine/install/linux-postinstall/); the `docker` group gives root-level access, so add only yourself.

Check: `docker run hello-world` prints its greeting, and `docker compose version` prints a version.

## 5. Name the server

Use a domain you own or a free dynamic DNS service. Point an A record at the instance's public address. The name becomes `SAATHI_DOMAIN` in `.env`. Update the record if the address changes.

Check: `getent hosts <name>` prints the instance's public address.

## 6. Install and start Saathi

Follow [SETUP.md](SETUP.md), section "1. Server": clone the repository, create `.env`, create the secret files. Then use the start command below in place of the full `up -d --build` shown there.

Set `WHISPER_MODEL`, `WHISPER_MODEL_URL`, `WHISPER_MODEL_SHA256` and `SAATHI_T1_MODEL` even with `ollama` off, or `docker compose config` stops with "required variable ... is missing". `SAATHI_T1_MODEL` can hold any text.

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d --build \
  postgres migrate edge worker egress caddy whisper
```

- The trace stack (Langfuse, ClickHouse, SeaweedFS, Valkey) is behind the compose profile `langfuse`. Leave the profile off. v0.1.0 does not need it.
- Phase 0 sends no text to a model, so `ollama` is left out. No service lists it under `depends_on`, and `src/saathi/channels/worker_main.py` builds no model client.
- `whisper` stays in. Without it a voice note goes through unscanned and approvers get a "Voice note not checked" alert. The first build compiles it and is slow.

Check: `docker compose --env-file .env -f infra/docker-compose.yml ps --all` shows `migrate` exited with code 0 and the other six services running. Run [SETUP.md](SETUP.md) section "4. Smoke test" except step 5 (Langfuse). From your own computer, `curl -i http://<SAATHI_DOMAIN>/` answers with a redirect to `https://` (port 80 open), and `curl -i https://<SAATHI_DOMAIN>/health` answers 200 (port 443 open).

## 7. Keep the instance from being reclaimed

Oracle may reclaim an Always Free instance idle for 7 days: the 95th percentile of CPU under 20%, network under 20%, and memory (Arm shapes) under 20% ([Always Free Resources][free]). Two ways to lower the risk:

1. Upgrade the account to Pay As You Go. Oracle's [FAQ](https://www.oracle.com/cloud/free/faq/) says Always Free resources stay free after it. Neither page says an upgrade exempts the instance from reclaim; users report it, Oracle does not state it. Check: the console's billing page shows the account type as Pay As You Go.
2. Keep real load on the server: daily household use, plus the uptime monitor in [SETUP.md](SETUP.md). Check: in the instance's metrics, CPU, network or memory sits above 20% for most of each day.

Saathi is light, so the second way alone may not pass. Read the instance's metrics in the console after the first days of use; `docker stats --no-stream` shows each service.

## 8. Back up the database

The dump includes the database roles. Run it from the repository folder; `mktemp` creates a new private file each run, and the command fails if the dump fails or ends early:

```bash
(set -o pipefail; f=$(mktemp ~/saathi-$(date +%F)-XXXXXX.sql.gz) || exit 1
docker compose --env-file .env -f infra/docker-compose.yml exec -T postgres \
  sh -c 'pg_dumpall -U "$POSTGRES_USER"' | gzip > "$f" &&
 [ "$(zcat "$f" | awk NF | tail -n 2 | tr '\n' '|')" = "-- PostgreSQL database cluster dump complete|--|" ] &&
 sha256sum "$f" || { rm -f "$f"; false; })
```

`-T` turns off the terminal so the output can be piped ([compose exec](https://docs.docker.com/reference/cli/docker/compose/exec/)). Copy the file to your own computer with `scp -i <private-key-file> ubuntu@<public-ip>:<file> <local-folder>`. Keep the server copy until a restore from your copy has been tested.

Encrypted rows need `/etc/saathi/secrets/kek`; keep the secret files in a password manager, not beside the dump.

Check: the command exits 0 and prints a hash; `sha256sum` on your computer prints the same hash for the copy.

[free]: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

Skipped or not checked: no step was run on an Oracle instance. The iptables steps rest on Oracle's Apache tutorial; Oracle does not document the image's default rules. Restoring from a dump was not tested.
