import XCTest
import SQLite3
import zlib
@testable import MinisSyncCore

final class WorkspaceTests: XCTestCase {
    func session(_ id: String = "s", title: String = "hello") throws -> WorkspaceRows {
        var rows = WorkspaceRows()
        try rows.add(table: "sessions", envelope: ["t":"SessionV2", "v":1, "d":["session":["id":id,"modelId":"model","title":title,"createdAt":"2026-10-21T12:00:00Z","updatedAt":"2026-10-21T12:00:00Z"],"memoryEnabled":true]])
        return rows
    }
    func database() throws -> OpaquePointer {
        var db: OpaquePointer?; XCTAssertEqual(sqlite3_open(":memory:", &db), SQLITE_OK)
        let database = try XCTUnwrap(db); addTeardownBlock { sqlite3_close(database) }
        for table in WorkspaceRows.tables {
            let columns = WorkspaceRows.fields[table]!.values.sorted().map { $0 + ($0 == "id" ? " TEXT PRIMARY KEY" : "") }.joined(separator: ",")
            XCTAssertEqual(sqlite3_exec(database,"CREATE TABLE \(table) (\(columns))",nil,nil,nil), SQLITE_OK)
        }
        return database
    }
    func testSQLiteApplyIsAuthoritativeEvenForEqualTimestampsAndDeletes() throws {
        let db = try database(), empty = WorkspaceRows(), first = try session(), next = try session(title: "edited on Android")
        try WorkspaceDatabase.apply(db: db, expected: empty, desired: first)
        try WorkspaceDatabase.apply(db: db, expected: first, desired: next)
        XCTAssertTrue(try WorkspaceDatabase.matches(db: db, rows: next))
        try WorkspaceDatabase.apply(db: db, expected: next, desired: empty)
        XCTAssertTrue(try WorkspaceDatabase.matches(db: db, rows: empty))
    }
    func testSQLiteRejectsConcurrentEditAndRollsBackFailure() throws {
        let db = try database(), first = try session(), next = try session(title: "new")
        try WorkspaceDatabase.apply(db: db, expected: WorkspaceRows(), desired: first)
        XCTAssertThrowsError(try WorkspaceDatabase.apply(db: db, expected: first, desired: next) { throw SyncFailure.invalid("disk failure") })
        XCTAssertTrue(try WorkspaceDatabase.matches(db: db, rows: first))
        XCTAssertEqual(sqlite3_exec(db,"UPDATE sessions SET title='local edit'",nil,nil,nil),SQLITE_OK)
        XCTAssertThrowsError(try WorkspaceDatabase.apply(db: db, expected: first, desired: next))
    }
    func testDanglingReferenceAndUnknownWireSchemaAreRefused() throws {
        var rows = WorkspaceRows()
        try rows.add(table: "messages", envelope: ["t":"MessageV2","v":1,"d":["id":"m","sessionId":"missing","role":"user","parts":[],"createdAt":"2026-10-21T12:00:00Z"]])
        XCTAssertThrowsError(try rows.validateReferences())
        XCTAssertThrowsError(try rows.add(table: "sessions", envelope: ["t":"SessionV2","v":1,"d":["id":"flat-old-layout"]]))
    }
    func package(_ root: URL, files: [String: Data]) throws -> WorkspaceDirectory {
        let fm = FileManager.default;try fm.createDirectory(at: root,withIntermediateDirectories:true)
        var entries:[String:Data] = [:],index:[[String:Any]] = []
        for (path, data) in files {
            let sha = SyncDigest.data(data), relative = "blobs/\(sha.prefix(2))/\(sha)"
            entries[relative] = data;index.append(["path":path,"size":data.count,"sha256":sha,"category":"shared_files"])
        }
        entries["files.index.jsonl"] = try index.sorted { ($0["path"] as! String) < ($1["path"] as! String) }.reduce(into: Data()) { result, row in result.append(try JSONSerialization.data(withJSONObject:row));result.append(10) }
        var hashes:[String:String]=[:]
        for (path,data) in entries {
            let url=root.appendingPathComponent(path);try fm.createDirectory(at:url.deletingLastPathComponent(),withIntermediateDirectories:true)
            try data.write(to:url);hashes[path]=SyncDigest.data(data)
        }
        let manifest:[String:Any] = ["format":"minisbak/1","categories":["chats":[:],"shared_files":[:]],"integrity":hashes]
        try JSONSerialization.data(withJSONObject:manifest).write(to:root.appendingPathComponent("manifest.json"))
        return try WorkspaceDirectory(root:root)
    }
    func temporary() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        addTeardownBlock { try? FileManager.default.removeItem(at:root) };return root
    }
    func testFilesUpdateDeleteCreateAndRollbackPreservesExactOriginals() throws {
        let root = try temporary(), live=root.appendingPathComponent("live")
        try FileManager.default.createDirectory(at:live,withIntermediateDirectories:true)
        try Data("old".utf8).write(to:live.appendingPathComponent("中文😀.py"))
        try Data("delete".utf8).write(to:live.appendingPathComponent("delete.txt"))
        let before=try package(root.appendingPathComponent("before"),files:["shared/中文😀.py":Data("old".utf8),"shared/delete.txt":Data("delete".utf8)])
        let next=try package(root.appendingPathComponent("next"),files:["shared/中文😀.py":Data("new".utf8),"shared/add.txt":Data("add".utf8)])
        let txn=try WorkspaceFiles(roots:["shared":live],journal:root.appendingPathComponent("journal"))
        try txn.prepare(expected:before,desired:next);try txn.apply()
        XCTAssertEqual(try String(contentsOf:live.appendingPathComponent("中文😀.py")),"new")
        XCTAssertFalse(FileManager.default.fileExists(atPath:live.appendingPathComponent("delete.txt").path))
        try txn.rollback()
        XCTAssertEqual(try String(contentsOf:live.appendingPathComponent("中文😀.py")),"old")
        XCTAssertEqual(try String(contentsOf:live.appendingPathComponent("delete.txt")),"delete")
        XCTAssertFalse(FileManager.default.fileExists(atPath:live.appendingPathComponent("add.txt").path))
    }
    func testFileRaceDoesNotOverwriteNewDestinationAndJournalCanRecover() throws {
        let root=try temporary(), live=root.appendingPathComponent("live")
        try FileManager.default.createDirectory(at:live,withIntermediateDirectories:true)
        let before=try package(root.appendingPathComponent("before"),files:[:])
        let next=try package(root.appendingPathComponent("next"),files:["shared/new.txt":Data("remote".utf8)])
        let journal=root.appendingPathComponent("journal")
        let txn=try WorkspaceFiles(roots:["shared":live],journal:journal)
        try txn.prepare(expected:before,desired:next)
        try Data("concurrent".utf8).write(to:live.appendingPathComponent("new.txt"))
        XCTAssertThrowsError(try txn.apply())
        try WorkspaceFiles(roots:["shared":live],journal:journal).rollback()
        XCTAssertEqual(try String(contentsOf:live.appendingPathComponent("new.txt")),"concurrent")
    }
    func testSymlinkTraversalAndCorruptBlobRefusedBeforeWrites() throws {
        let root=try temporary(), live=root.appendingPathComponent("live"),outside=root.appendingPathComponent("outside")
        try FileManager.default.createDirectory(at:live,withIntermediateDirectories:true)
        try FileManager.default.createDirectory(at:outside,withIntermediateDirectories:true)
        try FileManager.default.createSymbolicLink(at:live.appendingPathComponent("link"),withDestinationURL:outside)
        let before=try package(root.appendingPathComponent("before"),files:[:])
        let next=try package(root.appendingPathComponent("next"),files:["shared/link/new.txt":Data("remote".utf8)])
        let txn=try WorkspaceFiles(roots:["shared":live],journal:root.appendingPathComponent("journal"))
        XCTAssertThrowsError(try txn.prepare(expected:before,desired:next))
        XCTAssertFalse(WorkspaceDirectory.safe("shared/../escape"))
        let blob=next.root.appendingPathComponent("blobs/\(SyncDigest.data(Data("remote".utf8)).prefix(2))/\(SyncDigest.data(Data("remote".utf8)))")
        try Data("corrupt".utf8).write(to:blob)
        XCTAssertThrowsError(try WorkspaceDirectory(root:next.root))
    }
    func testRawDeflateStreamsAndRejectsTruncationOrExpansion() throws {
        let root=try temporary(), raw=Data(repeating:65,count:2_000_000)
        var stream=z_stream();XCTAssertEqual(deflateInit2_(&stream,Z_DEFAULT_COMPRESSION,Z_DEFLATED,-MAX_WBITS,8,Z_DEFAULT_STRATEGY,ZLIB_VERSION,Int32(MemoryLayout<z_stream>.size)),Z_OK)
        var compressed=Data(count:2_100_000)
        let count=raw.withUnsafeBytes { input in compressed.withUnsafeMutableBytes { output -> Int in
            stream.next_in=UnsafeMutablePointer(mutating:input.bindMemory(to:UInt8.self).baseAddress);stream.avail_in=uInt(input.count)
            stream.next_out=output.bindMemory(to:UInt8.self).baseAddress;stream.avail_out=uInt(output.count)
            XCTAssertEqual(deflate(&stream,Z_FINISH),Z_STREAM_END);return Int(stream.total_out)
        } };deflateEnd(&stream);compressed.count=count
        let source=root.appendingPathComponent("compressed"),dest=root.appendingPathComponent("out")
        try compressed.write(to:source);FileManager.default.createFile(atPath:dest.path,contents:nil)
        func run(_ bytes:Int,_ expected:Int) throws { let i=try FileHandle(forReadingFrom:source),o=try FileHandle(forWritingTo:dest);defer{try? i.close();try? o.close()};try RawDeflate.extract(input:i,compressedBytes:bytes,expectedBytes:expected,output:o) }
        try run(count,raw.count);XCTAssertEqual(try Data(contentsOf:dest),raw)
        XCTAssertThrowsError(try run(count-1,raw.count));XCTAssertThrowsError(try run(count,1))
    }
    func testDiskStageHandlesSeventyMiBTranscriptAndSubsecondDatabaseTimes() throws {
        let root = try temporary(), directory = root.appendingPathComponent("large")
        try FileManager.default.createDirectory(at: directory.appendingPathComponent("data"), withIntermediateDirectories: true)
        let sessionData = try JSONSerialization.data(withJSONObject: ["t":"SessionV2","v":1,"d":["session":["id":"s","modelId":"m","createdAt":"2026-10-21T12:00:00Z","updatedAt":"2026-10-21T12:00:00Z"],"memoryEnabled":true]])
        try sessionData.write(to: directory.appendingPathComponent("data/sessions.jsonl"))
        let messages = directory.appendingPathComponent("data/messages.jsonl")
        FileManager.default.createFile(atPath: messages.path, contents: nil)
        let handle = try FileHandle(forWritingTo: messages)
        let text = String(repeating: "x", count: 70_000)
        for n in 0..<1024 {
            try autoreleasepool {
                let row: [String:Any] = ["t":"MessageV2","v":1,"d":["id":"m\(n)","sessionId":"s","role":"user","parts":[["type":"text","text":text]],"createdAt":"2026-10-21T12:00:00Z","sortOrder":n]]
                try handle.write(contentsOf: JSONSerialization.data(withJSONObject: row));try handle.write(contentsOf: Data([10]))
            }
        }
        try handle.close()
        try Data().write(to: directory.appendingPathComponent("files.index.jsonl"))
        var integrity: [String:String] = [:]
        for path in ["data/sessions.jsonl","data/messages.jsonl","files.index.jsonl"] { integrity[path] = try SyncDigest.file(directory.appendingPathComponent(path)) }
        try JSONSerialization.data(withJSONObject: ["format":"minisbak/1","categories":["chats":[:],"shared_files":[:]],"integrity":integrity]).write(to: directory.appendingPathComponent("manifest.json"))
        let staged = try WorkspaceDirectory(root: directory)
        XCTAssertEqual(try staged.records.count("messages"), 1024)
        XCTAssertTrue(staged.records.rows.isEmpty, "large rows must remain on disk")
        let db = try database(); let first = try session()
        try WorkspaceDatabase.apply(db: db, expected: WorkspaceRows(), desired: first)
        XCTAssertEqual(sqlite3_exec(db,"UPDATE sessions SET created_at=created_at+0.123, updated_at=updated_at+0.456",nil,nil,nil),SQLITE_OK)
        XCTAssertTrue(try WorkspaceDatabase.matches(db: db, rows: first))
    }

}
