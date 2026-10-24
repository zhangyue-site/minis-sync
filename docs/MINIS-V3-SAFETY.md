# Minis sync: retention, baseline recovery and deployed source

The server safety repair protects existing data while restoring missing legacy baselines. It does not publish a new APK or change the iOS build state.

- [Deployed v3 backend and operating instructions](../server/v3/README.md)
- [119 custom Android source snapshot](../android/119/README.md)
- [Isolated regression suite](../tests/v3/test_safety.py)

## Repair behavior

- Retention shares commit locks, protects every live reference and operation receipt, and fails closed on missing baselines or schema errors. Old unreferenced files are quarantined with their indexes intact. There is no permanent purge and no claim of reduced disk usage.
- Explicit baseline recovery validates the exact original SHA and package integrity, creates only the missing account file/index entry and leaves heads, versions, operation receipts and deletion records unchanged.
- Only an explicit applied-version ACK authorizes deletion. Uploading is not treated as applying a merged result; preexisting implicit records remain preserved but cannot grant that authority.
- Direct and fallback transfers use one persistent rolling budget. Stale auth replicas reject with retryable 503, and private replica exports replace the previous predictable temporary path.

## Validation on 2026-10-24

86 tests passed on both macOS and the Linux serving host. They cover legacy baseline recovery, head/tombstone preservation, concurrent cleanup versus commits, quarantine reads/account isolation, ACK scope/deletion authority, atomic auth publication, stale replicas, concurrent budget reservations and real HTTP → frontend → subprocess RPC → temporary store merge/deletion/retry behavior.

Production source initially failed four preexisting ACK regressions; restoring explicit application evidence resolved them. Restored missing production-directory references were traced to synthetic acceptance accounts, not the real-account heads. Real-account version/device-base files checked complete. Recovery compared all affected head/version/tombstone/device/operation rows before and after; no changes were made to those rows.

The repair's production rollout keeps the existing data store, uses an additive ACK-proof schema, and retains a source rollback. Private backups, account identifiers, tokens and task bindings are excluded from this document and Git. Source SHA manifests record the Android snapshot without claiming a reproducible rebuild; external build inputs and signing material stay outside Git.

Production read-only checks passed after the code switch: unauthenticated HTTP 401; an existing acceptance account's head HTTP 200; its partial snapshot download HTTP 206; another account's digest HTTP 404. The transfer ledger recorded the download reservation. Retention dry-run and quarantine application both reported 24 protected files, zero candidates, zero moved files, zero deletions and no errors. Head/version/tombstone/receipt rows remained identical across the switch, and legacy missing references reached zero. Auth refresh keeps its five-minute schedule and now logs failures privately.
