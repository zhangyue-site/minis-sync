# Minis v3 deployed server and safety repairs

This directory captures the versioned production backend and direct HTTP frontend so the deployed source is reviewable together. It is separate from the repository's older legacy `server/` modules.

Run the isolated suite from the repository root:

```sh
python3 -m unittest discover -s tests/v3 -v
```

All fixtures use temporary stores and loopback HTTP servers. They do not connect to production accounts or storage. `minis_cloud_http.py` is the loopback fixture server; the production direct frontend is `front.py` and executes `minis_v3_rpc.py` locally.

## Retention and recovery

`minis_retention.py` defaults to dry-run. `--apply` only **moves** unreferenced files older than seven days into the store's `quarantine/<uid>/` directory on the same filesystem. It never deletes snapshot bytes, index rows, version rows, operation receipts or tombstones. It preserves every referenced version, acknowledged device base, legacy device source, projection and operation receipt. A missing live baseline, malformed receipt or unexpected schema blocks retention for that account.

Retention holds the RPC capacity lock and then the same account lock as commits/ACKs. It calculates references only after obtaining those locks, protecting snapshots created concurrently. Normal indexed reads can restore quarantined bytes on demand after account and SHA validation. There is intentionally **no permanent purge**: quarantine consumes disk space and is a recovery safeguard, not a disk-space reduction. A future purge needs independently verified backup/recovery evidence and a separate design.

`minis_repair_baselines.py --uid ... --sha ... --source ...` is an administrator-only recovery utility. It requires an existing legacy-device reference for the specified account, validates the entire source digest and native package, atomically creates only the missing file, restores its snapshot index if missing, and preserves the version chain and deletion inventory. It never searches another account through a public endpoint. Sources, accounts and recovery evidence belong in private operations records, not Git.

## Explicit application confirmation

The service distinguishes upload receipts from the device having applied a cloud version. Only explicit ACK grants deletion authority. Older production code recorded implicit scoped bases; those rows are retained for history/file protection but are not trusted as application evidence. `device_applied_baselines` is seeded from actual latest device ACK records; new ACKs add history there. The 119 Android client retries ACK for its persisted applied base before uploading.

## Frontend, auth and transfer limits

Both X-Accel downloads (including Range requests), fallback downloads and uploads reserve bytes in a SQLite ledger with `BEGIN IMMEDIATE`. The default cap is 150 GiB over a conservative rolling 31-day window; disconnects are not refunded. Range responses reserve the full package size, intentionally conservative. Errors or invalid policy fail closed. Set `MINIS_FRONT_STATE` and optionally create `transfer-policy.json` there:

```json
{"max_transfer_bytes_31_days":161061273600}
```

This ledger starts when the repair is activated. It cannot reconstruct traffic that previously bypassed accounting and is not a cloud annual-cost guarantee or another host's prepaid traffic balance. It does not buy resources, change bandwidth or use paid cloud APIs.

`sync_notes_db.py` exports the source database read-only to a randomly named root-private temporary file. It verifies the received copy, stores the export start time inside the replica and atomically publishes it with mode 0600. The frontend accepts a replica for at most ten minutes; failed exports do not renew it. Stale/unavailable replicas return retryable HTTP 503 rather than falsely treating credentials as revoked. Unknown or revoked tokens return 401. The existing five-minute refresh cadence is retained.

## Deployment and rollback

Back up source files and both live SQLite databases using SQLite backup before changing production. Deploy this directory as a new release, atomically switch the current code link, publish a verified auth replica, then atomically update the frontend/sync wrapper and restart only the frontend service. Keep application data in its existing store. Run retention dry-run and verify live heads/file references before enabling the quarantine-only cron wrapper. Existing cleanup invocations with `--apply` must never execute the original destructive script.

For rollback, restore the saved code link and frontend files only. Keep the additive ACK-proof table and restored baselines; do not replace live databases with an older backup after new syncs. Keep cleanup disabled/quarantine-only during rollback. Backup and private recovery evidence locations are maintained outside Git.
