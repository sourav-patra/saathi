# Deploy Saathi on Oracle Cloud Always Free

Follow these steps in order to get one always-on server for the v0.1.0 stack. Each step ends on a check you can run. [SETUP.md](SETUP.md) installs the application; this page covers the server under it.

Oracle's Always Free page ([Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), checked 2026-11-13) gives one Arm virtual machine of shape `VM.Standard.A1.Flex` with 2 OCPUs and 12 GB of memory, created in the tenancy's home region. Every image in `infra/docker-compose.yml` has an Arm build or is built from source on the server.

## 1. Choose the home region

Pick Mumbai (`ap-mumbai-1`) or Hyderabad (`ap-hyderabad-1`) when you sign up. Always Free compute must be created in the home region, and Oracle states that the home region cannot be changed after the tenancy exists ([Managing Regions](https://docs.oracle.com/en-us/iaas/Content/Identity/regions/managingregions.htm)). Each has one availability domain ([Regions](https://docs.oracle.com/en-us/iaas/Content/General/Concepts/regions.htm)).

Check: the console shows your home region name in the region menu.

## 2. Create the instance

Follow [Creating an Instance](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/launchinginstance.htm) with these values:

1. Image: Platform Images, Canonical Ubuntu 24.04, Arm (aarch64) build.
2. Shape: Virtual machine, Ampere series, `VM.Standard.A1.Flex`, 2 OCPUs, 12 GB.
3. Networking: a public subnet, with "Automatically assign public IPv4 address" on.
4. Boot volume: a custom size of 100 GB. Always Free allows 200 GB of boot and block volumes together.
5. SSH key: upload the public half of a key pair you made yourself. Keep the private half off the server.

If the console answers "out of host capacity", Oracle says to try another availability domain or wait and retry. With one domain per region, retry later.

Check: `ssh -i <private-key-file> ubuntu@<public-ip>` opens a shell, and `uname -m` prints `aarch64` ([Oracle: default user](https://docs.oracle.com/en-us/iaas/Content/GSG/Tasks/testingconnection.htm)).

## 3. Open ports 80 and 443 in two places

Caddy needs port 80 for its certificate and port 443 for the webhook; both layers must allow them.

1. In the console, open Networking, Virtual Cloud Networks, your VCN, the public subnet, and its Default Security List. Add two ingress rules: source CIDR `0.0.0.0/0`, protocol TCP, destination port 80 for one and 443 for the other. Oracle's walk-through for port 80 is [Apache on Ubuntu](https://docs.oracle.com/en-us/iaas/developer-tutorials/tutorials/apache-on-ubuntu/01oci-ubuntu-apache-summary.htm).
2. On the instance, add the same two rules to its own firewall and save them. Do this before installing Docker, so the saved file holds only these rules:

```bash
sudo iptables -I INPUT 1 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 1 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

If `netfilter-persistent` is missing, run `sudo apt install iptables-persistent` and save again. Inserting at position 1 puts the rules above any reject rule that ships in the image. Oracle's tutorial inserts at position 6; position 1 works on any chain length.

Check: `sudo iptables -L INPUT -n --line-numbers` lists both ACCEPT rules before any REJECT line, and after a reboot the same command still lists them. The outside check comes in step 5, once Caddy answers.

## 4. Install Docker Engine and the compose plugin

Run the commands from Docker's own page, [Install Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/), section "Install using the apt repository": install `ca-certificates` and `curl`, save Docker's key to `/etc/apt/keyrings/docker.asc`, write `/etc/apt/sources.list.d/docker.sources`, run `sudo apt update`, then:

```bash
sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

The sources file picks the Arm build through `dpkg --print-architecture`. To run `docker` without `sudo`, follow [Linux post-installation steps](https://docs.docker.com/engine/install/linux-postinstall/); the `docker` group gives root-level access, so add only yourself.

Check: `docker run hello-world` prints its greeting, and `docker compose version` prints a version.

## 5. Name the server

Use a domain you own or a free dynamic DNS service. Point an A record at the instance's public address. The name becomes `SAATHI_DOMAIN` in `.env`. Update the record if the address changes.

Check: `getent hosts <name>` prints the instance's public address.

## 6. Install and start Saathi

Follow [SETUP.md](SETUP.md), section "1. Server": clone the repository, create `.env`, create the secret files. Then use the start command below in place of the full `up -d --build` shown there.

Compose reads every service when it validates `.env`, so set `WHISPER_MODEL`, `WHISPER_MODEL_URL`, `WHISPER_MODEL_SHA256` and `SAATHI_T1_MODEL` even though you will not start the `ollama` service (without them `docker compose config` stops with "required variable ... is missing"; checked locally). `SAATHI_T1_MODEL` can hold any text.

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d --build \
  postgres migrate edge worker egress caddy whisper
```

- The trace stack (Langfuse, ClickHouse, SeaweedFS, Valkey) is behind the compose profile `langfuse`. Leave the profile off. v0.1.0 does not need it.
- Phase 0 sends no text to a model, so `ollama` is left out. No service lists it under `depends_on`, and `src/saathi/channels/worker_main.py` builds no model client.
- `whisper` stays in. The worker transcribes voice notes with it; without it a voice note is forwarded unscanned and the approvers get a "Voice note not checked" alert. The first build compiles whisper.cpp on the server and is slow (duration not measured).

Check: `docker compose --env-file .env -f infra/docker-compose.yml ps` shows `migrate` as exited with code 0 and the other six services up. Then run the smoke test in [SETUP.md](SETUP.md), section "4. Smoke test", skipping step 5 (Langfuse). `curl -i https://<SAATHI_DOMAIN>/health` from your own computer answers 200 once the core runs, which also proves both firewall layers are open.

## 7. Keep the instance from being reclaimed

Oracle may reclaim an Always Free instance that stays idle for 7 days. Idle means the 95th percentile of CPU, network and memory (Arm shapes) are all under 20% ([Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)). Two ways to avoid it:

1. Upgrade the account to Pay As You Go. Oracle states it does not charge for Always Free resources after the upgrade and charges only for use above the limits.
2. Keep real load on the server: daily household use, plus the uptime monitor in [SETUP.md](SETUP.md).

Saathi's services are light, so the second way alone is unlikely to work. On a 12 GB test machine on 2026-11-13 the services in this runbook used about 0.6 GB at rest and 1.2 GB at peak while transcribing a voice note (speech service 0.98 GB, worker 0.11 GB, the rest under 0.05 GB each), which is under 20% of memory. Upgrade to Pay As You Go (the first way) before the first week ends.

Check: the instance's metrics in the console after a week of use.

## 8. Back up the database

The dump includes the database roles. Run it from the repository folder:

```bash
(umask 077; docker compose --env-file .env -f infra/docker-compose.yml exec -T postgres \
  sh -c 'pg_dumpall -U "$POSTGRES_USER"' | gzip > ~/saathi-$(date +%F).sql.gz)
```

`-T` turns off the terminal so the output can be piped ([compose exec](https://docs.docker.com/reference/cli/docker/compose/exec/)). Copy the file off the server to your own computer with `scp -i <private-key-file> ubuntu@<public-ip>:saathi-<date>.sql.gz <local-folder>`, then delete the server copy.

The key-encryption key in `/etc/saathi/secrets/kek` is needed to read encrypted rows. Keep the secret files in a password manager, not beside the dump.

Check: `gzip -t <file>` exits 0, and `zcat <file> | head` starts with PostgreSQL dump comments.

Skipped or not checked: no step was run on an Oracle instance. The iptables rules and the claim that the image ships a reject rule rest on Oracle's Apache tutorial plus community reports; Oracle's page does not describe the default rules. The restore from a dump was not tested.
