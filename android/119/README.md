# Android 1.14.18 / 119 source snapshot

This directory records the custom Java sources, smali/manifest patch scripts and build recipe observed on the build host on 2026-10-24. The published APK advertises versionCode 119, versionName 1.14.18 and SHA-256 `e4b20eee38c41607b8ab561221c2cf62c6875c9f46d1bef5119e918a15076474` (181,966,853 bytes).

This is a source snapshot, not a new Android release or a reproducible-build attestation. The original build recipe expects a prepared upstream APK, smali assembly, native libraries, ABI stubs, Android SDK, runtime asset archives and a separately managed signing key on the build host. Those inputs and all credentials remain outside Git. Set `MINIS_BUILD_ROOT` to the prepared build directory; inspect the original recipe's SDK/upstream paths before building. The snapshot has not been rebuilt in this server-safety change. Existing Android/iOS source roots remain available for their original workflows.

The server safety update preserves the 119 protocol: the client explicitly acknowledges an applied base before committing changes. See `CloudSync.java` and `CloudTransport.java` under `src/site/zhangyue/minis/sync/`.
