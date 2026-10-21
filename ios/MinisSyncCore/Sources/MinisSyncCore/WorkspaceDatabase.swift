import Foundation
import SQLite3

public enum WorkspaceSQLValue: Equatable, Sendable, Codable {
    case null, text(String), number(Double)
}
/// Native SQLite projection. Only these explicit wire fields may write columns.
/// Data lives in a bounded staging object; large transcripts fail before writes.
public struct WorkspaceRows: @unchecked Sendable {
    public static let tables = ["folders", "sessions", "messages", "compact_markers"]
    public static let fields: [String: [String: String]] = [
        "folders": ["id":"id", "name":"name", "icon":"icon", "color":"color", "origin":"origin", "sortIndex":"sort_index", "pinnedAt":"pinned_at", "desc":"description", "createdAt":"created_at", "updatedAt":"updated_at"],
        "sessions": ["id":"id", "title":"title", "modelId":"model_id", "createdAt":"created_at", "updatedAt":"updated_at", "category":"category", "modelBinding":"model_binding", "source":"source", "memoryEnabled":"memory_enabled", "pinnedAt":"pinned_at", "folderId":"folder_id", "parentSessionId":"parent_session_id", "parentToolUseId":"parent_tool_use_id"],
        "messages": ["id":"id", "sessionId":"session_id", "role":"role", "parts":"parts_json", "createdAt":"created_at", "tokenUsage":"token_usage", "sortOrder":"sort_order", "reasoningContent":"reasoning_content", "streamInterruptCount":"stream_interrupt_count", "modelId":"model_id", "modelDisplayName":"model_display_name", "providerType":"provider_type", "providerInstanceId":"provider_instance_id", "reasoningEchoJSON":"reasoning_echo", "errorInfo":"error_info"],
        "compact_markers": ["id":"id", "sessionId":"session_id", "summary":"summary", "firstKeptSortOrder":"first_kept_sort_order", "compactedCount":"compacted_count", "createdAt":"created_at", "uiBoundarySortOrder":"ui_boundary_sort_order", "boundaryMessageId":"boundary_message_id", "firstKeptMessageId":"first_kept_message_id", "lastCompactedMessageId":"last_compacted_message_id", "version":"version"]]
    public var rows: [String: [String: [String: WorkspaceSQLValue]]] = [:]
    private var disk: WorkspaceRowDisk?
    public init() {}
    public init(stagingOnDisk: Bool) throws {
        if stagingOnDisk { disk = try WorkspaceRowDisk() }
    }
    public func seal() throws { try disk?.seal() }
    public func count(_ table: String) throws -> Int { try disk?.count(table) ?? rows[table, default: [:]].count }
    public func row(_ table: String, _ id: String) throws -> [String: WorkspaceSQLValue]? { try disk?.row(table, id) ?? rows[table]?[id] }
    public func contains(_ table: String, _ id: String) throws -> Bool { try row(table, id) != nil }
    public func forEach(_ table: String, _ body: (String, [String: WorkspaceSQLValue]) throws -> Void) throws {
        if let disk { try disk.forEach(table, body) }
        else { for id in rows[table, default: [:]].keys.sorted() { try body(id, rows[table]![id]!) } }
    }
    public func ids(_ table: String) throws -> Set<String> {
        var ids = Set<String>(); try forEach(table) { id, _ in ids.insert(id) }; return ids
    }
    public mutating func add(table: String, envelope: [String: Any]) throws {
        guard let mapping = Self.fields[table], envelope["v"] as? Int == 1,
              let type = envelope["t"] as? String,
              type == ["folders":"FolderV2", "sessions":"SessionV2", "messages":"MessageV2", "compact_markers":"CompactMarkerV2"][table],
              var record = envelope["d"] as? [String: Any] else { throw SyncFailure.invalid("不支持的聊天记录格式") }
        if table == "sessions" {
            guard var session = record["session"] as? [String: Any] else { throw SyncFailure.invalid("会话结构不兼容") }
            session["memoryEnabled"] = record["memoryEnabled"] ?? true
            session["modelBinding"] = record["modelBinding"]
            record = session
        }
        if record["desc"] == nil, let description = record.removeValue(forKey: "description") { record["desc"] = description }
        guard let id = record["id"] as? String, !id.isEmpty, !(try contains(table, id)) else { throw SyncFailure.invalid("聊天 ID 缺失或重复") }
        var values: [String: WorkspaceSQLValue] = [:]
        for (wire, column) in mapping {
            let value = record[wire]
            if value == nil || value is NSNull {
                if ["stream_interrupt_count", "sort_order", "sort_index", "compacted_count", "first_kept_sort_order"].contains(column) { values[column] = .number(0) }
                else if column == "version" { values[column] = .number(1) }
                else { values[column] = .null }
            } else if ["created_at", "updated_at", "pinned_at"].contains(column) {
                values[column] = .number(try Self.timestamp(value!))
            } else if let number = value as? NSNumber { values[column] = .number(number.doubleValue) }
            else if value is [String: Any] || value is [Any] {
                values[column] = .text(String(decoding: try JSONSerialization.data(withJSONObject: value!, options: [.sortedKeys, .withoutEscapingSlashes]), as: UTF8.self))
            } else if let text = value as? String { values[column] = .text(text) }
            else { throw SyncFailure.invalid("聊天字段类型不兼容") }
        }
        // Refuse substantive fields unavailable on iOS rather than silently
        // erase them on the next native export. Original cloud package is retained.
        let transient = Set(["lastMessage", "lastSyncedAt", "remoteDeviceId", "remoteDeviceName"])
        for key in record.keys where mapping[key] == nil && !transient.contains(key) {
            if key == "editCount", (record[key] as? NSNumber)?.intValue == 0 { continue }
            if !(record[key] is NSNull) { throw SyncFailure.invalid("当前 iOS 尚不支持字段：\(key)") }
        }
        if let disk { try disk.add(table, id, values) }
        else { rows[table, default: [:]][id] = values }
    }
    static func timestamp(_ value: Any) throws -> Double {
        if let n = value as? NSNumber { return floor(n.doubleValue / 1000) } // Android legacy epoch millis.
        guard let s = value as? String else { throw SyncFailure.invalid("时间字段无效") }
        let format = ISO8601DateFormatter()
        if let date = format.date(from: s) { return floor(date.timeIntervalSince1970) }
        format.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = format.date(from: s) else { throw SyncFailure.invalid("时间字段无效") }
        return floor(date.timeIntervalSince1970)
    }
    public func validateReferences() throws {
        for table in ["messages", "compact_markers"] {
            try forEach(table) { _, row in
                guard case .text(let parent) = row["session_id"], try contains("sessions", parent) else { throw SyncFailure.invalid("消息缺少所属会话") }
            }
        }
        try forEach("sessions") { _, row in
            for (field, parentTable) in [("folder_id", "folders"), ("parent_session_id", "sessions")] {
                if case .text(let parent) = row[field], !parent.isEmpty, !(try contains(parentTable, parent)) { throw SyncFailure.invalid("会话缺少父记录") }
            }
        }
    }
}
public enum WorkspaceDatabase {
    private static let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
    static func execute(_ db: OpaquePointer, _ sql: String, _ values: [WorkspaceSQLValue] = []) throws {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK, let statement else { throw SyncFailure.invalid("数据库语句不兼容") }
        defer { sqlite3_finalize(statement) }
        for (i, value) in values.enumerated() {
            let index = Int32(i + 1)
            switch value {
            case .null: sqlite3_bind_null(statement, index)
            case .text(let text): sqlite3_bind_text(statement, index, text, -1, transient)
            case .number(let number): sqlite3_bind_double(statement, index, number)
            }
        }
        guard sqlite3_step(statement) == SQLITE_DONE else { throw SyncFailure.invalid("数据库写入失败，事务已撤回") }
    }
    static func matchesTable(_ db: OpaquePointer, table: String, fields: [String], expected: WorkspaceRows) throws -> Bool {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, "SELECT " + fields.joined(separator: ",") + " FROM " + table, -1, &statement, nil) == SQLITE_OK, let statement else { throw SyncFailure.invalid("数据库结构不兼容") }
        defer { sqlite3_finalize(statement) }
        var count = 0
        while true {
            let step = sqlite3_step(statement)
            if step == SQLITE_DONE { break }
            guard step == SQLITE_ROW else { throw SyncFailure.invalid("数据库读取失败") }
            var row: [String: WorkspaceSQLValue] = [:]
            for (i, field) in fields.enumerated() {
                let index = Int32(i)
                switch sqlite3_column_type(statement, index) {
                case SQLITE_NULL: row[field] = .null
                case SQLITE_INTEGER, SQLITE_FLOAT: let number = sqlite3_column_double(statement, index)
                    row[field] = .number(["created_at", "updated_at", "pinned_at"].contains(field) ? floor(number) : number)
                default:
                    let text = String(cString: sqlite3_column_text(statement, index))
                    if ["parts_json", "token_usage"].contains(field), let data = text.data(using: .utf8),
                       let json = try? JSONSerialization.jsonObject(with: data), let normalized = try? JSONSerialization.data(withJSONObject: json, options: [.sortedKeys, .withoutEscapingSlashes]) {
                        row[field] = .text(String(decoding: normalized, as: UTF8.self))
                    } else { row[field] = .text(text) }
                }
            }
            guard case .text(let id) = row["id"] else { throw SyncFailure.invalid("数据库记录缺少 ID") }
            guard try expected.row(table, id) == row else { return false }; count += 1
        }
        return try count == expected.count(table)
    }
    public static func matches(db: OpaquePointer, rows: WorkspaceRows) throws -> Bool {
        for table in WorkspaceRows.tables {
            guard try matchesTable(db, table: table, fields: WorkspaceRows.fields[table]!.values.sorted(), expected: rows) else { return false }
        }
        return true
    }
    /// Called inside ChatStore's actor, on its own connection. No await occurs
    /// between the live comparison and commit. Files are prepared before this call.
    public static func apply(db: OpaquePointer, expected: WorkspaceRows, desired: WorkspaceRows,
                             beforeCommit: () throws -> Void = {}) throws {
        try desired.validateReferences()
        try execute(db, "BEGIN IMMEDIATE")
        do {
            try execute(db, "PRAGMA defer_foreign_keys=ON")
            for table in WorkspaceRows.tables {
                let fields = WorkspaceRows.fields[table]!.values.sorted()
                guard try matchesTable(db, table: table, fields: fields, expected: expected) else { throw SyncFailure.localChanged }
            }
            for table in WorkspaceRows.tables {
                try desired.forEach(table) { id, row in
                    if try expected.row(table, id) == row { return }
                    let fields = row.keys.sorted(), placeholders = Array(repeating: "?", count: row.count).joined(separator: ",")
                    let updates = fields.filter { $0 != "id" }.map { $0 + "=excluded." + $0 }.joined(separator: ",")
                    try execute(db, "INSERT INTO " + table + " (" + fields.joined(separator: ",") + ") VALUES (" + placeholders + ") ON CONFLICT(id) DO UPDATE SET " + updates, fields.map { row[$0]! })
                }
            }
            for table in WorkspaceRows.tables.reversed() {
                try expected.forEach(table) { id, _ in
                    if !(try desired.contains(table, id)) { try execute(db, "DELETE FROM " + table + " WHERE id=?", [.text(id)]) }
                }
            }
            try beforeCommit()
            try execute(db, "COMMIT")
        } catch { try? execute(db, "ROLLBACK"); throw error }
    }
}

private final class WorkspaceRowDisk: @unchecked Sendable {
    private let root: URL
    private var db: OpaquePointer?
    private var sealed = false
    init() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("minis-rows-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        guard sqlite3_open_v2(root.appendingPathComponent("records.sqlite").path, &db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK, let db else { throw SyncFailure.invalid("无法创建聊天暂存索引") }
        try WorkspaceDatabase.execute(db, "PRAGMA cache_size=-2048")
        try WorkspaceDatabase.execute(db, "PRAGMA temp_store=FILE")
        try WorkspaceDatabase.execute(db, "CREATE TABLE records(table_name TEXT,id TEXT,data TEXT,PRIMARY KEY(table_name,id)) WITHOUT ROWID")
        try WorkspaceDatabase.execute(db, "BEGIN")
    }
    deinit { if let db { sqlite3_close(db) }; try? FileManager.default.removeItem(at: root) }
    func seal() throws { if !sealed, let db { try WorkspaceDatabase.execute(db, "COMMIT"); sealed = true } }
    func add(_ table: String, _ id: String, _ row: [String: WorkspaceSQLValue]) throws {
        guard let db, !sealed else { throw SyncFailure.invalid("聊天暂存索引已关闭") }
        let data = String(decoding: try JSONEncoder().encode(row), as: UTF8.self)
        try WorkspaceDatabase.execute(db, "INSERT INTO records VALUES(?,?,?)", [.text(table), .text(id), .text(data)])
    }
    func query(_ sql: String, _ values: [String], _ body: (OpaquePointer) throws -> Void) throws {
        guard let db else { throw SyncFailure.invalid("聊天暂存索引不可用") }
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK, let statement else { throw SyncFailure.invalid("聊天暂存查询失败") }
        defer { sqlite3_finalize(statement) }
        for (i, value) in values.enumerated() { sqlite3_bind_text(statement, Int32(i+1), value, -1, unsafeBitCast(-1, to: sqlite3_destructor_type.self)) }
        while true { let step = sqlite3_step(statement); if step == SQLITE_DONE { break }; guard step == SQLITE_ROW else { throw SyncFailure.invalid("聊天暂存读取失败") }; try body(statement) }
    }
    func count(_ table: String) throws -> Int {
        var result = 0; try query("SELECT count(*) FROM records WHERE table_name=?", [table]) { result = Int(sqlite3_column_int64($0, 0)) }; return result
    }
    func row(_ table: String, _ id: String) throws -> [String: WorkspaceSQLValue]? {
        var result: [String: WorkspaceSQLValue]?
        try query("SELECT data FROM records WHERE table_name=? AND id=?", [table, id]) {
            let data = Data(bytes: sqlite3_column_text($0, 0), count: Int(sqlite3_column_bytes($0, 0)))
            result = try JSONDecoder().decode([String: WorkspaceSQLValue].self, from: data)
        }; return result
    }
    func forEach(_ table: String, _ body: (String, [String: WorkspaceSQLValue]) throws -> Void) throws {
        try query("SELECT id,data FROM records WHERE table_name=? ORDER BY id", [table]) {
            let id = String(cString: sqlite3_column_text($0, 0))
            let data = Data(bytes: sqlite3_column_text($0, 1), count: Int(sqlite3_column_bytes($0, 1)))
            try body(id, JSONDecoder().decode([String: WorkspaceSQLValue].self, from: data))
        }
    }
}
