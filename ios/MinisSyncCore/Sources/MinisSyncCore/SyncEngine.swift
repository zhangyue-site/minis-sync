import Foundation
import CryptoKit

public enum SyncFailure: Error, LocalizedError {
    case invalid(String), http(Int), localChanged
    public var errorDescription: String? {
        switch self {
        case .invalid(let message): return message
        case .http(let status): return "同步请求失败（HTTP \(status)）"
        case .localChanged: return "同步期间本机内容发生变化，已保留待同步内容，请重试"
        }
    }
}
public struct CloudHead: Codable, Equatable, Sendable {
    public let sha256: String
    public let bytes: Int64
    public let version: Int
    public let conflicts: Int
    public init(sha256: String, bytes: Int64, version: Int, conflicts: Int = 0) {
        self.sha256 = sha256; self.bytes = bytes; self.version = version; self.conflicts = conflicts
    }
}
public struct LocalSnapshot: Sendable {
    public let url: URL
    public let fingerprint: String
    public init(url: URL, fingerprint: String) { self.url = url; self.fingerprint = fingerprint }
}
/// apply must compare live content to expected, apply the authoritative cloud
/// result (including deletions), retain recovery content and fail on partial work.
/// Return the semantic fingerprint of the actual applied content, not its ZIP hash.
public protocol LocalWorkspace: Sendable {
    func snapshot() async throws -> LocalSnapshot
    func apply(remote: URL, expected: LocalSnapshot) async throws -> String
    func prepareUpload(snapshot: LocalSnapshot, baseline: URL?, baseSHA: String?, to: URL) async throws -> String
    func discard(_ snapshot: LocalSnapshot) async
}
public extension LocalWorkspace {
    func prepareUpload(snapshot: LocalSnapshot, baseline: URL?, baseSHA: String?, to: URL) async throws -> String {
        try FileManager.default.copyItem(at: snapshot.url, to: to); return "full"
    }
    func discard(_ snapshot: LocalSnapshot) async {}
}
public protocol CloudTransport: Sendable {
    func head() async throws -> CloudHead?
    func commit(device: String, base: String?, operation: String, file: URL, mode: String) async throws
    func download(_ head: CloudHead, to: URL) async throws
    func acknowledge(device: String, sha: String) async throws
}
public enum SyncDigest {
    public static func file(_ url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url); defer { try? handle.close() }
        var hash = SHA256()
        while let data = try handle.read(upToCount: 1_048_576), !data.isEmpty { hash.update(data: data) }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }
    public static func data(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    public static func valid(_ value: String) -> Bool { value.count == 64 && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) } }
}
/// One instance per account+server. State roots must never be shared across
/// accounts. Persistent operation identity makes a lost receipt safe to retry.
public actor SyncEngine {
    struct Pending: Codable { var operation: String; var base: String?; var hash: String; var fingerprint: String; var mode: String }
    struct State: Codable {
        var device = UUID().uuidString
        var base: String?
        var appliedFingerprint: String?
        var pending: Pending?
        var needsAcknowledgement = false
    }
    private let root: URL
    private let remote: any CloudTransport
    private let local: any LocalWorkspace
    private var running = false
    private let fm = FileManager.default
    public init(root: URL, transport: any CloudTransport, workspace: any LocalWorkspace) throws {
        self.root = root; self.remote = transport; self.local = workspace
        try fm.createDirectory(at: root, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    }
    private func save(_ state: State) throws {
        let data = try JSONEncoder().encode(state)
        try data.write(to: root.appendingPathComponent("state.json"), options: [.atomic, .completeFileProtectionUnlessOpen])
    }
    private func read() throws -> State {
        let url = root.appendingPathComponent("state.json")
        guard fm.fileExists(atPath: url.path) else { return State() }
        // Never treat a corrupt state as a new device: that could revive deletions.
        return try JSONDecoder().decode(State.self, from: Data(contentsOf: url))
    }
    @discardableResult public func synchronize() async throws -> CloudHead? {
        guard !running else { throw SyncFailure.invalid("同步正在进行") }
        running = true; defer { running = false }
        var state = try read()
        if let base = state.base {
            let baseline = root.appendingPathComponent(base + ".minisbak")
            guard SyncDigest.valid(base), fm.fileExists(atPath: baseline.path), try SyncDigest.file(baseline) == base else {
                throw SyncFailure.invalid("同步基线缺失或损坏，已停止以保护删除记录")
            }
            // A persisted local apply is always retried before creating another change.
            if state.needsAcknowledgement {
                try await remote.acknowledge(device: state.device, sha: base)
                state.needsAcknowledgement = false; try save(state)
            }
        }
        let current = try await local.snapshot()
        defer { Task { await local.discard(current) } }
        let body = root.appendingPathComponent("pending.minisbak")
        if state.pending == nil && (state.base == nil || current.fingerprint != state.appliedFingerprint) {
            let staging = root.appendingPathComponent("pending.staging")
            try? fm.removeItem(at: staging)
            let baseline = state.base.map { root.appendingPathComponent($0 + ".minisbak") }
            let mode = try await local.prepareUpload(snapshot: current, baseline: baseline, baseSHA: state.base, to: staging)
            guard ["full", "delta"].contains(mode) else { throw SyncFailure.invalid("上传模式无效") }
            let hash = try SyncDigest.file(staging)
            try? fm.removeItem(at: body)
            try fm.moveItem(at: staging, to: body)
            state.pending = Pending(operation: UUID().uuidString, base: state.base, hash: hash, fingerprint: current.fingerprint, mode: mode)
            try save(state)
        }
        if let pending = state.pending {
            guard fm.fileExists(atPath: body.path), try SyncDigest.file(body) == pending.hash else {
                throw SyncFailure.invalid("待上传内容缺失或损坏，恢复备份已保留")
            }
            try await remote.commit(device: state.device, base: pending.base, operation: pending.operation, file: body, mode: pending.mode)
            // A restarted pending request may have succeeded earlier. Do not apply
            // its old result over new local edits; submit those on the next pass.
            if current.fingerprint != pending.fingerprint {
                state.pending = nil; try save(state)
                throw SyncFailure.localChanged
            }
        }
        // Re-read head rather than using a possibly old idempotency receipt.
        guard let head = try await remote.head() else { throw SyncFailure.invalid("云端未返回可应用版本") }
        guard SyncDigest.valid(head.sha256), head.bytes > 0, head.bytes <= 2_147_483_648 else {
            throw SyncFailure.invalid("云端版本信息无效")
        }
        if head.sha256 == state.base && state.pending == nil && current.fingerprint == state.appliedFingerprint { return head }
        let incoming = root.appendingPathComponent("incoming.minisbak")
        try await remote.download(head, to: incoming)
        guard try SyncDigest.file(incoming) == head.sha256 else { throw SyncFailure.invalid("云端备份摘要不符") }
        let check = try await local.snapshot()
        defer { Task { await local.discard(check) } }
        guard check.fingerprint == current.fingerprint else { throw SyncFailure.localChanged }
        let recovery = root.appendingPathComponent("recovery", isDirectory: true)
        try fm.createDirectory(at: recovery, withIntermediateDirectories: true)
        let before = recovery.appendingPathComponent("local-\(UUID().uuidString).minisbak")
        try fm.copyItem(at: check.url, to: before)
        let retained = recovery.appendingPathComponent("cloud-\(head.sha256).minisbak")
        if !fm.fileExists(atPath: retained.path) { try fm.copyItem(at: incoming, to: retained) }
        let applied = try await local.apply(remote: incoming, expected: check)
        // Baseline is published before state: an interrupted write can only stop
        // with a mismatch, never silently promote an unapplied cloud version.
        let baseline = root.appendingPathComponent(head.sha256 + ".minisbak")
        if !fm.fileExists(atPath: baseline.path) { try fm.copyItem(at: incoming, to: baseline) }
        state.base = head.sha256; state.appliedFingerprint = applied
        state.pending = nil; state.needsAcknowledgement = true; try save(state)
        try await remote.acknowledge(device: state.device, sha: head.sha256)
        state.needsAcknowledgement = false; try save(state)
        try? fm.removeItem(at: body)
        return head
    }
}
