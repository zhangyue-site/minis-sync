# Minis workspace integration candidates

`cloud-v3/` and `android-v3/` were copied read-only from the maintainer's existing
1.14.12 sync repair candidate on 2026-10-21, then extended here for iPhone workspace
interoperability. These are standalone review/build inputs, not replacements for
the older modules in `server/` or `src/`. Do not combine the two generations by
selectively overwriting production files. See `docs/IOS-SYNC.md` for scope,
validation and uncompleted release gates. No production data or credentials are
included.
