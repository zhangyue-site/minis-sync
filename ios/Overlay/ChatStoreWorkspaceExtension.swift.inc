
// Minis workspace sync extension. Kept in this file to use the actor's private
// SQLite connection and invalidate its native session-list cache.
extension ChatStore {
    func recoverWorkspaceFiles(transactionID: String, files: WorkspaceFiles) throws {
        guard let db, UUID(uuidString: transactionID) != nil else { throw SyncFailure.invalid("文件事务标识无效") }
        var statement: OpaquePointer?
        let sql = "SELECT 1 FROM workspace_sync_transactions WHERE id='\(transactionID)'"
        var committed = false
        if sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK { committed = sqlite3_step(statement) == SQLITE_ROW }
        sqlite3_finalize(statement)
        if !committed { try files.rollback() }
    }
    func applyWorkspaceCloud(expected: WorkspaceRows, desired: WorkspaceRows, files: WorkspaceFiles) throws {
        guard let db else { throw SyncFailure.invalid("聊天数据库未打开") }
        for id in try expected.ids("sessions").union(desired.ids("sessions")) {
            if isSessionRunning(id) { throw SyncFailure.localChanged }
        }
        let transactionID = files.journal.lastPathComponent
        guard UUID(uuidString: transactionID) != nil else { throw SyncFailure.invalid("文件事务标识无效") }
        try WorkspaceDatabase.apply(db: db, expected: expected, desired: desired) {
            try files.apply()
            let receiptSQL = "CREATE TABLE IF NOT EXISTS workspace_sync_transactions(id TEXT PRIMARY KEY); INSERT INTO workspace_sync_transactions VALUES('\(transactionID)')"
            guard sqlite3_exec(db, receiptSQL, nil, nil, nil) == SQLITE_OK else { throw SyncFailure.invalid("无法确认文件事务") }
            // Use the native expression so previews hide internal tool parts.
            let sql = "UPDATE messages SET part_flags = \(Self.partFlagsSQLExpr("parts_json"))"
            guard sqlite3_exec(db, sql, nil, nil, nil) == SQLITE_OK else { throw SyncFailure.invalid("消息预览索引更新失败") }
        }
        invalidateSessionListCache()
    }
}
