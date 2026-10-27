# Cloud VM deployment

Why: the forward test must run every day whether or not the laptop is awake
(`docs/design/forward-test-platform.md`, decision 2026-10-25). The recorder already runs unchanged
on Linux; the daily `drc-session` job (Slice 1, not built yet) joins it as a systemd timer.

Status (2026-10-27): **tested.** VM `drc-vm` runs in Oracle London (AD-3, `VM.Standard.A1.Flex`,
2 OCPU / 12 GB / 100 GB, Ubuntu 24.04 aarch64). `setup-vm.sh` ran clean; all five venue checks
returned HTTP 200 from London; the recorder runs as the `drc-record` systemd service.
Access: `ssh -i ~/.ssh/id_ed25519_drc ubuntu@<public ip>` (key and OCI API config live in
`~/.ssh` and `~/.oci` on Rohan's laptop only, never in the repo). Network: one VCN, public
subnet, SSH is the only inbound port. Created with the OCI CLI (`uv tool install oci-cli`).

## What Rohan does (needs a human and a payment card for identity checks)
1. Create a free-tier VM, Ubuntu 22.04/24.04, with an SSH key.
   - Preferred: **Oracle Cloud Always Free** (ARM Ampere, up to 4 cores / 24 GB, 200 GB disk).
     Sign-up sometimes rejects cards and ARM capacity can be unavailable in busy regions; retry or
     pick another region.
   - Fallback: **GCP e2-micro** (free in a few US regions, 1 GB RAM, 30 GB disk; the setup script
     adds swap).
   - Region matters: a venue may block datacenter IPs in some countries. The setup script prints
     an HTTP code per venue; anything other than 200 means pick another region before going on.
2. SSH in and run:
   `git clone https://github.com/arorarohan/dog-research-capital.git && bash dog-research-capital/deploy/setup-vm.sh`
3. Read the connectivity lines, then `sudo systemctl enable --now drc-record`.

## What the VM records (changed 2026-10-27, issue #17)
The service runs `config/recorder-forward.toml`: the Polymarket deadline universe (ex-ante,
chosen by scheduled end and volume to date) plus the Kalshi weather series, into
`data/raw_forward`. No crypto books. Measured on the generic config (`config/recorder.toml`),
which grew ~2.4 GB/day compressed and would have filled the 94 GB disk in ~55 days: channels
without a retention rule are kept forever, and the `max_total_gb` valve only prunes channels
that have a rule. The forward config therefore sets 30-day retention on Polymarket
`price_change` and Kalshi `trades` (starting values, re-measure growth and adjust). The unit
stops with SIGINT so the open hour is flushed.

## Notes
- Data stays on the VM (`data/raw`, gitignored); the recorder's `max_total_gb` safety valve
  applies. Size the disk accordingly (the laptop's 610 MB came from a few hours of sessions).
- The ledger (forward-test evidence) goes to the private repo `arorarohan/drc-ledger`, not
  this one. Never commit market data or secrets.
- A deploy key or fine-grained token for pushing the ledger from the VM is a Slice 1 task.
- No inbound ports are opened; reading results from outside comes with the web app phase.
