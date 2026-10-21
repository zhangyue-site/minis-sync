import Foundation
import Darwin
import CryptoKit

/// Validated native package after ZIP extraction. No data is written live here.
public final class WorkspaceDirectory {
    public let root: URL
    public let records: WorkspaceRows
    public let files: [String: FileRecord]
    public let fingerprint: String
    public struct FileRecord {
        public let category: String
        public let path: String
        public let hash: String?
        public let directory: Bool
        public let size: Int64
    }
    public static func safe(_ path: String) -> Bool {
        !path.isEmpty && !path.hasPrefix("/") && !path.contains("\\") &&
        !path.unicodeScalars.contains { $0.value < 32 } &&
        !path.split(separator: "/", omittingEmptySubsequences: false).contains { $0 == ".." || $0 == "." || $0.isEmpty }
    }
    public static func lines(_ url: URL, _ body: (Data) throws -> Void) throws {
        let file = try FileHandle(forReadingFrom: url); defer { try? file.close() }
        var pending = Data()
        while let chunk = try file.read(upToCount: 65536), !chunk.isEmpty {
            pending.append(chunk)
            while let newline = pending.firstIndex(of: 10) {
                let line = Data(pending[..<newline]); pending.removeSubrange(...newline)
                guard line.count <= 8 * 1024 * 1024 else { throw SyncFailure.invalid("单条记录超过 8MiB") }
                if !line.isEmpty { try autoreleasepool { try body(line) } }
            }
            guard pending.count <= 8 * 1024 * 1024 else { throw SyncFailure.invalid("单条记录超过 8MiB") }
        }
        if !pending.isEmpty { try autoreleasepool { try body(pending) } }
    }
    public init(root: URL) throws {
        let root = root.standardizedFileURL.resolvingSymlinksInPath()
        self.root = root
        let fm = FileManager.default
        guard (try root.appendingPathComponent("manifest.json").resourceValues(forKeys: [.fileSizeKey]).fileSize ?? Int.max) <= 8 * 1024 * 1024 else { throw SyncFailure.invalid("备份清单过大") }
        let manifestData = try Data(contentsOf: root.appendingPathComponent("manifest.json"))
        guard manifestData.count <= 8 * 1024 * 1024,
              let manifest = try JSONSerialization.jsonObject(with: manifestData) as? [String: Any],
              manifest["format"] as? String == "minisbak/1", manifest["encryption"] == nil || manifest["encryption"] is NSNull,
              let integrity = manifest["integrity"] as? [String: String], let categories = manifest["categories"] as? [String: Any],
              categories["chats"] != nil, categories["shared_files"] != nil else {
            throw SyncFailure.invalid("需要未加密的完整聊天与共享文件备份（minisbak/1）")
        }
        // A partial backup must never be submitted as a deletion-bearing snapshot.
        for value in categories.values {
            if let stats = value as? [String: Any], (stats["skipped"] as? Int ?? 0) > 0 { throw SyncFailure.invalid("备份含未读取内容，已停止同步以防误删") }
        }
        if let limits = manifest["limits"] as? [String: Any], (limits["skipped_files"] as? Int ?? 0) > 0 { throw SyncFailure.invalid("备份含跳过文件") }
        func physical(_ url: URL) throws -> String {
            guard let pointer = realpath(url.path, nil) else { throw SyncFailure.invalid("无法解析备份路径") }
            defer { free(pointer) }; return String(cString: pointer)
        }
        let physicalRoot = try physical(root)
        var actual = Set<String>(), total: Int64 = 0
        guard let enumerator = fm.enumerator(at: root, includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey]) else { throw SyncFailure.invalid("无法读取备份") }
        for case let url as URL in enumerator {
            let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
            guard values.isSymbolicLink != true else { throw SyncFailure.invalid("备份包含符号链接") }
            if values.isRegularFile != true { continue }
            let physicalFile = try physical(url)
            guard physicalFile.hasPrefix(physicalRoot + "/") else { throw SyncFailure.invalid("备份路径越界") }
            let path = String(physicalFile.dropFirst(physicalRoot.count + 1))
            guard Self.safe(path) else { throw SyncFailure.invalid("备份路径无效") }
            total += Int64(values.fileSize ?? 0)
            guard total <= 4_294_967_296, actual.count < 100_000 else { throw SyncFailure.invalid("备份展开大小超限") }
            if path == "manifest.json" { continue }
            actual.insert(path)
            guard let sha = integrity[path], SyncDigest.valid(sha.lowercased()), try SyncDigest.file(url) == sha.lowercased() else { throw SyncFailure.invalid("备份内容摘要不符：\(path)") }
        }
        guard actual == Set(integrity.keys) else { throw SyncFailure.invalid("备份完整性索引不完整") }
        var rows = try WorkspaceRows(stagingOnDisk: true), indexed: [String: FileRecord] = [:], metadataBytes = 0
        for path in actual.sorted() {
            if path.hasPrefix("data/") && path.hasSuffix(".jsonl") {
                let name = URL(fileURLWithPath: path).deletingPathExtension().lastPathComponent
                let table = WorkspaceRows.tables.first { name == $0 || name.hasPrefix($0 + "-") }
                guard let table else { continue }
                try Self.lines(root.appendingPathComponent(path)) { line in
                    metadataBytes += line.count
                    // Explicit bounded staging; refuse before any live writes.
                    guard metadataBytes <= 512 * 1024 * 1024 else { throw SyncFailure.invalid("聊天元数据超过 512MiB，原件已保留") }
                    guard let envelope = try JSONSerialization.jsonObject(with: line) as? [String: Any] else { throw SyncFailure.invalid("聊天记录无效") }
                    try rows.add(table: table, envelope: envelope)
                }
            }
        }
        try rows.seal()
        try rows.validateReferences()
        let index = root.appendingPathComponent("files.index.jsonl")
        guard fm.fileExists(atPath: index.path) else { throw SyncFailure.invalid("备份缺少文件索引") }
        try Self.lines(index) { line in
            guard let row = try JSONSerialization.jsonObject(with: line) as? [String: Any] else { throw SyncFailure.invalid("文件索引无效") }
            guard let category = row["category"] as? String, ["chats", "shared_files"].contains(category) else { return }
            guard let path = row["path"] as? String, Self.safe(path), indexed[path] == nil,
                  (category == "chats" && path.hasPrefix("chats/")) || (category == "shared_files" && path.hasPrefix("shared/")) else { throw SyncFailure.invalid("文件逻辑路径无效或重复") }
            if row["skipped"] != nil && !(row["skipped"] is NSNull) { throw SyncFailure.invalid("备份含未下载或跳过的文件") }
            let directory = row["isDirectory"] as? Bool ?? false
            let hash = row["sha256"] as? String
            let size = (row["size"] as? NSNumber)?.int64Value ?? 0
            if !directory {
                guard let hash, SyncDigest.valid(hash), size >= 0 else { throw SyncFailure.invalid("文件摘要缺失") }
                let blob = root.appendingPathComponent("blobs/\(hash.prefix(2))/\(hash)")
                guard try blob.resourceValues(forKeys: [.fileSizeKey]).fileSize == Int(size), try SyncDigest.file(blob) == hash else { throw SyncFailure.invalid("文件内容缺失或损坏") }
            }
            indexed[path] = FileRecord(category: category, path: path, hash: hash, directory: directory, size: size)
        }
        records = rows; files = indexed
        var digest = SHA256()
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        for table in WorkspaceRows.tables {
            try rows.forEach(table) { id, row in
                digest.update(data: Data((table + ":" + id + "\n").utf8))
                digest.update(data: try encoder.encode(row))
            }
        }
        for path in indexed.keys.sorted() {
            let row = indexed[path]!
            digest.update(data: Data((path + ":" + (row.hash ?? "directory") + "\n").utf8))
        }
        fingerprint = digest.finalize().map { String(format: "%02x", $0) }.joined()
    }
}
