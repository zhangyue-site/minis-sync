import XCTest
@testable import MinisSyncCore

private actor Workspace: LocalWorkspace {
    let root: URL
    var value = "first"
    var applications = 0
    var failApply = false
    init(_ root: URL) { self.root = root }
    func edit(_ text: String) { value = text }
    func setFailure(_ value: Bool) { failApply = value }
    func snapshot() throws -> LocalSnapshot {
        let url = root.appendingPathComponent(UUID().uuidString)
        try Data(value.utf8).write(to: url)
        return LocalSnapshot(url: url, fingerprint: value)
    }
    func apply(remote: URL, expected: LocalSnapshot) throws -> String {
        guard !failApply else { throw SyncFailure.invalid("injected apply failure") }
        guard expected.fingerprint == value else { throw SyncFailure.localChanged }
        value = try String(contentsOf: remote); applications += 1
        return value
    }
}
private actor Cloud: CloudTransport {
    var content = Data()
    var version = 0
    var operations: [String: String] = [:]
    var received: [String] = []
    var acks = 0
    var loseReceipt = false
    var failAck = false
    var corruptDownload = false
    func injectLostReceipt() { loseReceipt = true }
    func injectAckFailure() { failAck = true }
    func injectBadDownload() { corruptDownload = true }
    func replace(_ value: String) { content = Data(value.utf8); version += 1 }
    func head() -> CloudHead? {
        version == 0 ? nil : CloudHead(sha256: SyncDigest.data(content), bytes: Int64(content.count), version: version)
    }
    func commit(device: String, base: String?, operation: String, file: URL, mode: String) throws {
        let data = try Data(contentsOf: file); let digest = SyncDigest.data(data)
        received.append(operation)
        if let prior = operations[operation] { guard prior == digest else { throw SyncFailure.invalid("reused operation") }; return }
        operations[operation] = digest; content = data; version += 1
        if loseReceipt { loseReceipt = false; throw SyncFailure.http(503) }
    }
    func download(_ head: CloudHead, to: URL) throws { try (corruptDownload ? Data("bad".utf8) : content).write(to: to) }
    func acknowledge(device: String, sha: String) throws {
        if failAck { failAck = false; throw SyncFailure.http(503) }; acks += 1
    }
}
final class SyncEngineTests: XCTestCase {
    private func setupEngine() throws -> (URL, Workspace, Cloud, SyncEngine) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        let workspace = Workspace(root); let cloud = Cloud()
        return (root, workspace, cloud, try SyncEngine(root: root.appendingPathComponent("state"), transport: cloud, workspace: workspace))
    }
    func testInitialApplyAndUnchangedDoesNotCommit() async throws {
        let (_, local, cloud, engine) = try setupEngine()
        _ = try await engine.synchronize(); _ = try await engine.synchronize()
        let version = await cloud.version; let apps = await local.applications; let ack = await cloud.acks
        XCTAssertEqual(version, 1); XCTAssertEqual(apps, 1); XCTAssertEqual(ack, 1)
    }
    func testReceiptLossAndRestartUsesSameOperation() async throws {
        let (root, local, cloud, engine) = try setupEngine()
        await cloud.injectLostReceipt()
        do { _ = try await engine.synchronize(); XCTFail() } catch {}
        let restarted = try SyncEngine(root: root.appendingPathComponent("state"), transport: cloud, workspace: local)
        _ = try await restarted.synchronize()
        let received = await cloud.received; let version = await cloud.version
        XCTAssertEqual(received.count, 2); XCTAssertEqual(Set(received).count, 1); XCTAssertEqual(version, 1)
    }
    func testNewLocalEditAfterLostReceiptIsNotOverwritten() async throws {
        let (_, local, cloud, engine) = try setupEngine()
        await cloud.injectLostReceipt()
        do { _ = try await engine.synchronize(); XCTFail() } catch {}
        await local.edit("second")
        do { _ = try await engine.synchronize(); XCTFail() } catch {}
        let value = await local.value; XCTAssertEqual(value, "second")
        _ = try await engine.synchronize()
        let content = await cloud.content; XCTAssertEqual(String(data: content, encoding: .utf8), "second")
    }
    func testFailedApplyDoesNotAcknowledge() async throws {
        let (_, local, cloud, engine) = try setupEngine()
        await local.setFailure(true)
        do { _ = try await engine.synchronize(); XCTFail() } catch {}
        let ack = await cloud.acks; XCTAssertEqual(ack, 0)
        await local.setFailure(false); _ = try await engine.synchronize()
    }
    func testAckFailureRecoveredWithoutDuplicateCommit() async throws {
        let (_, _, cloud, engine) = try setupEngine()
        await cloud.injectAckFailure()
        do { _ = try await engine.synchronize(); XCTFail() } catch {}
        _ = try await engine.synchronize()
        let version = await cloud.version; let acks = await cloud.acks
        XCTAssertEqual(version, 1); XCTAssertEqual(acks, 1)
    }
    func testCorruptedDownloadNotApplied() async throws {
        let (_, local, cloud, engine) = try setupEngine()
        await cloud.injectBadDownload()
        do { _ = try await engine.synchronize(); XCTFail() } catch {}
        let apps = await local.applications; let acks = await cloud.acks
        XCTAssertEqual(apps, 0); XCTAssertEqual(acks, 0)
    }
    func testCorruptedStateAndMissingBaselineFailClosed() async throws {
        let (root, _, _, engine) = try setupEngine()
        _ = try await engine.synchronize()
        let state = root.appendingPathComponent("state")
        for file in try FileManager.default.contentsOfDirectory(at: state, includingPropertiesForKeys: nil) where file.lastPathComponent.count == 73 { try FileManager.default.removeItem(at: file) }
        do { _ = try await engine.synchronize(); XCTFail() } catch {}
        try Data("bad".utf8).write(to: state.appendingPathComponent("state.json"))
        do { _ = try await engine.synchronize(); XCTFail() } catch {}
    }
    func testHTTPRejectsInsecureAndCredentialBearingURLs() throws {
        for url in ["http://example.com", "https://user:pass@example.com", "https://example.com/path", "https://example.com?token=x"] {
            XCTAssertThrowsError(try HTTPCloudTransport(server: URL(string: url)!, token: "x"))
        }
        XCTAssertNoThrow(try HTTPCloudTransport(server: URL(string: "http://127.0.0.1:1234")!, token: "x", allowLoopbackHTTP: true))
    }
}
