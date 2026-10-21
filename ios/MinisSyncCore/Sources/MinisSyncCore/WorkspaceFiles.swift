import Foundation
import Darwin

/// Every replaced/deleted inode is retained. Atomic swap/exclusive rename never
/// destroys a concurrently-created destination. A journal precedes every move.
public final class WorkspaceFiles: @unchecked Sendable {
    struct Operation: Codable {
        var path: String
        var oldHash: String?
        var newHash: String?
        var kind: String
        var slot: String
    }
    private let roots: [String: URL]
    public let journal: URL
    private var operations: [Operation] = []
    private let fm = FileManager.default
    public init(roots: [String: URL], journal: URL) throws {
        self.roots = roots; self.journal = journal
        try fm.createDirectory(at: journal, withIntermediateDirectories: true)
    }
    private func target(_ logical: String, createParent: Bool) throws -> URL {
        guard WorkspaceDirectory.safe(logical) else { throw SyncFailure.invalid("项目路径无效") }
        let parts = logical.split(separator: "/").map(String.init)
        guard let root = roots[parts[0]], parts.count > 1 else { throw SyncFailure.invalid("项目路径不在同步目录") }
        let base = root.standardizedFileURL.resolvingSymlinksInPath()
        if createParent { try fm.createDirectory(at: base, withIntermediateDirectories: true) }
        let url = parts.dropFirst().reduce(base) { $0.appendingPathComponent($1) }
        // Reject even an internal symlink; shell writers must not redirect a sync.
        var current = base
        for component in parts.dropFirst() {
            current.appendPathComponent(component)
            if (try? current.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) == true { throw SyncFailure.invalid("项目路径含符号链接") }
        }
        guard url.resolvingSymlinksInPath().path.hasPrefix(base.path + "/") else { throw SyncFailure.invalid("项目路径越界") }
        if createParent { try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true) }
        return url
    }
    private func move(_ source: URL, _ destination: URL, swap: Bool = false) throws {
        let status = source.path.withCString { src in destination.path.withCString { dst in
            renameatx_np(AT_FDCWD, src, AT_FDCWD, dst, swap ? UInt32(RENAME_SWAP) : UInt32(RENAME_EXCL))
        } }
        guard status == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
    }
    public func prepare(expected: WorkspaceDirectory, desired: WorkspaceDirectory) throws {
        guard !fm.fileExists(atPath: journal.appendingPathComponent("journal.json").path) else { throw SyncFailure.invalid("有未恢复的文件事务") }
        for path in Set(expected.files.keys).union(desired.files.keys).sorted() {
            let before = expected.files[path], next = desired.files[path]
            if before?.directory == true || next?.directory == true {
                // Directory/file transitions need explicit handling rather than
                // replacing an entire tree whose children may have changed.
                if before != nil && next != nil && before?.directory != next?.directory { throw SyncFailure.invalid("目录与文件类型冲突：\(path)") }
                if before?.directory != next?.directory {
                    operations.append(Operation(path: path, oldHash: nil, newHash: nil,
                        kind: next?.directory == true ? "dir_create" : "dir_delete", slot: UUID().uuidString))
                }
                continue
            }
            if before?.hash == next?.hash { continue }
            let live = try target(path, createParent: true)
            let exists = fm.fileExists(atPath: live.path)
            if exists {
                guard let old = before?.hash, try SyncDigest.file(live) == old else { throw SyncFailure.localChanged }
            } else if before != nil { throw SyncFailure.localChanged }
            let slot = UUID().uuidString
            if let next, let hash = next.hash {
                let source = desired.root.appendingPathComponent("blobs/\(hash.prefix(2))/\(hash)")
                let staged = journal.appendingPathComponent(slot)
                try fm.copyItem(at: source, to: staged)
                guard try SyncDigest.file(staged) == hash else { throw SyncFailure.invalid("项目文件暂存校验失败") }
            }
            operations.append(Operation(path: path, oldHash: before?.hash, newHash: next?.hash,
                kind: next == nil ? "delete" : before == nil ? "create" : "replace", slot: slot))
        }
        // Create parents before files; remove empty directories after children.
        operations.sort { a, b in
            let rank: (Operation) -> Int = { $0.kind == "dir_create" ? 0 : $0.kind == "dir_delete" ? 2 : 1 }
            if rank(a) != rank(b) { return rank(a) < rank(b) }
            return a.kind == "dir_delete" ? a.path.count > b.path.count : a.path < b.path
        }
        try JSONEncoder().encode(operations).write(to: journal.appendingPathComponent("journal.json"), options: .atomic)
    }
    public func apply() throws {
        for operation in operations {
            let live = try target(operation.path, createParent: true), slot = journal.appendingPathComponent(operation.slot)
            if let old = operation.oldHash { guard try SyncDigest.file(live) == old else { throw SyncFailure.localChanged } }
            switch operation.kind {
            case "dir_create":
                try fm.createDirectory(at: live, withIntermediateDirectories: true)
            case "dir_delete":
                if fm.fileExists(atPath: live.path), rmdir(live.path) != 0 { throw SyncFailure.localChanged }
            case "replace":
                try move(slot, live, swap: true)
                guard try SyncDigest.file(slot) == operation.oldHash else { throw SyncFailure.localChanged }
            case "create": try move(slot, live)
            case "delete":
                try move(live, slot)
                guard try SyncDigest.file(slot) == operation.oldHash else { throw SyncFailure.localChanged }
            default: throw SyncFailure.invalid("文件事务类型无效")
            }
        }
    }
    /// Keep divergent live edits and recovery bytes instead of clobbering them.
    public func rollback() throws {
        if operations.isEmpty, fm.fileExists(atPath: journal.appendingPathComponent("journal.json").path) {
            operations = try JSONDecoder().decode([Operation].self, from: Data(contentsOf: journal.appendingPathComponent("journal.json")))
        }
        for operation in operations.reversed() {
            guard UUID(uuidString: operation.slot) != nil else { throw SyncFailure.invalid("文件事务路径无效") }
            let live = try target(operation.path, createParent: true), slot = journal.appendingPathComponent(operation.slot)
            if operation.kind == "dir_delete" {
                try fm.createDirectory(at: live, withIntermediateDirectories: true)
            } else if operation.kind == "dir_create" {
                if fm.fileExists(atPath: live.path) { _ = rmdir(live.path) }
            } else if operation.kind == "delete" {
                if fm.fileExists(atPath: slot.path), !fm.fileExists(atPath: live.path) { try move(slot, live) }
            } else if operation.kind == "replace" {
                if fm.fileExists(atPath: slot.path), try SyncDigest.file(slot) != operation.newHash,
                   fm.fileExists(atPath: live.path), try SyncDigest.file(live) == operation.newHash { try move(slot, live, swap: true) }
            } else if operation.kind == "create", !fm.fileExists(atPath: slot.path),
                      fm.fileExists(atPath: live.path), try SyncDigest.file(live) == operation.newHash { try move(live, slot) }
        }
    }
}
