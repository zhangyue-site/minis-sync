import Foundation
import UIKit

actor MinisWorkspaceAdapter: LocalWorkspace {
    private let root: URL
    private var exports = Set<URL>()
    private let liveRoots: [String: URL]
    init(root: URL, chatRoot: URL, sharedRoot: URL) {
        self.root = root; self.liveRoots = ["chats": chatRoot, "shared": sharedRoot]
    }
    private func checkWriters() async throws {
        let cloudEnabled = await MainActor.run {
            UserDefaults.standard.bool(forKey: "cloudSync.enabled") || UserDefaults.standard.bool(forKey: "cloudSync.v2.enabled")
        }
        guard !cloudEnabled else { throw SyncFailure.invalid("请先在 Minis 设置中关闭 iCloud 同步，再使用安卓互通") }
        for id in await ChatStore.shared.allSessionIds() {
            if ChatStore.shared.isSessionRunning(id) { throw SyncFailure.invalid("请等待当前 AI 任务结束后同步") }
        }
    }
    private func unpack(_ package: URL) throws -> (WorkspaceDirectory, URL) {
        let work = root.appendingPathComponent("unpack-" + UUID().uuidString)
        let handle = try FileHandle(forReadingFrom: package)
        let entries: [BackupZipExtractor.Entry]
        do { entries = try BackupZipExtractor.centralDirectory(handle: handle); try handle.close() }
        catch { try? handle.close(); throw error }
        guard entries.count <= 100_000, Set(entries.map(\.name)).count == entries.count,
              entries.reduce(Int64(0), { $0 + Int64($1.uncompressedSize) }) <= 4_294_967_296 else { throw SyncFailure.invalid("备份展开大小或条目数超限") }
        let manifests = entries.map(\.name).filter { $0 == "manifest.json" || $0.hasSuffix("/manifest.json") }
        guard manifests.count == 1 else { throw SyncFailure.invalid("备份清单缺失或重复") }
        try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
        do {
            try BackupZipExtractor.extract(package, to: work)
            let packageRoot = work.appendingPathComponent(manifests[0]).deletingLastPathComponent()
            return (try WorkspaceDirectory(root: packageRoot), work)
        } catch { try? FileManager.default.removeItem(at: work); throw error }
    }
    private func recoverFiles() async throws {
        let transactions = root.appendingPathComponent("file-transactions")
        guard FileManager.default.fileExists(atPath: transactions.path) else { return }
        let recovery = root.appendingPathComponent("file-recovery")
        try FileManager.default.createDirectory(at: recovery, withIntermediateDirectories: true)
        for old in try FileManager.default.contentsOfDirectory(at: transactions, includingPropertiesForKeys: nil) {
            if FileManager.default.fileExists(atPath: old.appendingPathComponent("journal.json").path) {
                let files = try WorkspaceFiles(roots: liveRoots, journal: old)
                try await ChatStore.shared.recoverWorkspaceFiles(transactionID: old.lastPathComponent, files: files)
            }
            try FileManager.default.moveItem(at: old, to: recovery.appendingPathComponent(old.lastPathComponent))
        }
    }
    func snapshot() async throws -> LocalSnapshot {
        try await checkWriters()
        try await recoverFiles()
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let summary = try await BackupExporter().export(options: .init(categories: [.chats, .sharedFiles],
            includeCredentials: false, snapshotAt: .distantFuture, workspaceTransport: true))
        var retained = false
        defer { if !retained { try? FileManager.default.removeItem(at: summary.packageURL) } }
        guard summary.skippedFiles == 0 else { throw SyncFailure.invalid("有文件未能备份，已停止同步以防误删") }
        let (package, temporary) = try unpack(summary.packageURL)
        defer { try? FileManager.default.removeItem(at: temporary) }
        retained = true; exports.insert(summary.packageURL)
        return LocalSnapshot(url: summary.packageURL, fingerprint: package.fingerprint)
    }
    func discard(_ snapshot: LocalSnapshot) async {
        if exports.remove(snapshot.url) != nil { try? FileManager.default.removeItem(at: snapshot.url) }
    }
    func prepareUpload(snapshot: LocalSnapshot, baseline: URL?, baseSHA: String?, to destination: URL) async throws -> String {
        guard let baseline, let baseSHA else {
            try FileManager.default.copyItem(at: snapshot.url, to: destination); return "full"
        }
        let (before, beforeWork) = try unpack(baseline)
        defer { try? FileManager.default.removeItem(at: beforeWork) }
        let (next, nextWork) = try unpack(snapshot.url)
        defer { try? FileManager.default.removeItem(at: nextWork) }
        // Prefix changes cannot reuse ZIP entry names. A complete upload is safe.
        let beforeHandle = try FileHandle(forReadingFrom: baseline)
        let names = try BackupZipExtractor.centralDirectory(handle: beforeHandle).map(\.name)
        try beforeHandle.close()
        guard names.contains("manifest.json") else {
            try FileManager.default.copyItem(at: snapshot.url, to: destination); return "full"
        }
        let old = try JSONSerialization.jsonObject(with: Data(contentsOf: before.root.appendingPathComponent("manifest.json"))) as! [String: Any]
        let new = try JSONSerialization.jsonObject(with: Data(contentsOf: next.root.appendingPathComponent("manifest.json"))) as! [String: Any]
        let oldHashes = old["integrity"] as! [String: String], newHashes = new["integrity"] as! [String: String]
        let writer = try BackupZipWriter(url: destination)
        _ = try writer.addData(JSONSerialization.data(withJSONObject: ["v":2, "base":baseSHA,
            "deleted":Array(Set(oldHashes.keys).subtracting(newHashes.keys)).sorted()]), name: "__delta__.json")
        _ = try writer.addFile(at: next.root.appendingPathComponent("manifest.json"), name: "manifest.json")
        for path in newHashes.keys.sorted() where oldHashes[path] != newHashes[path] {
            _ = try writer.addFile(at: next.root.appendingPathComponent(path), name: path)
        }
        try writer.close()
        let size = try destination.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        let fullSize = try snapshot.url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        if size > 256 * 1024 * 1024 || size >= fullSize {
            try FileManager.default.removeItem(at: destination)
            try FileManager.default.copyItem(at: snapshot.url, to: destination); return "full"
        }
        return "delta"
    }
    func apply(remote: URL, expected: LocalSnapshot) async throws -> String {
        try await checkWriters()
        let (before, beforeWork) = try unpack(expected.url)
        defer { try? FileManager.default.removeItem(at: beforeWork) }
        let (next, nextWork) = try unpack(remote)
        defer { try? FileManager.default.removeItem(at: nextWork) }
        let transactionRoot = root.appendingPathComponent("file-transactions")
        try FileManager.default.createDirectory(at: transactionRoot, withIntermediateDirectories: true)
        let journal = transactionRoot.appendingPathComponent(UUID().uuidString)
        let files = try WorkspaceFiles(roots: liveRoots, journal: journal)
        try files.prepare(expected: before, desired: next)
        do {
            try await BackupActivityLock.shared.withLock(.restore) {
                try await ChatStore.shared.applyWorkspaceCloud(expected: before.records, desired: next.records, files: files)
            }
            // Keep exact displaced bytes in recovery, outside exported directories.
            let recovery = root.appendingPathComponent("file-recovery")
            try FileManager.default.createDirectory(at: recovery, withIntermediateDirectories: true)
            try FileManager.default.moveItem(at: journal, to: recovery.appendingPathComponent(journal.lastPathComponent))
        } catch {
            try? await recoverFiles()
            throw error
        }
        let actual = try await snapshot()
        defer { if exports.remove(actual.url) != nil { try? FileManager.default.removeItem(at: actual.url) } }
        guard actual.fingerprint == next.fingerprint else { throw SyncFailure.invalid("原生读回与云端不一致，已保留双方备份，尚未确认同步") }
        await MainActor.run { NotificationCenter.default.post(name: .cloudSyncDidFetchChanges, object: nil) }
        return actual.fingerprint
    }
}
