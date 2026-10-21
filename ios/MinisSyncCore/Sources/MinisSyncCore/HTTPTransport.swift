import Foundation

private final class NoRedirect: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}
public final class HTTPCloudTransport: CloudTransport, @unchecked Sendable {
    private let server: URL
    private let token: String
    private let session: URLSession
    private let delegate = NoRedirect()
    public init(server: URL, token: String, allowLoopbackHTTP: Bool = false) throws {
        let loopback = ["127.0.0.1", "localhost", "[::1]", "::1"].contains(server.host ?? "")
        guard server.user == nil, server.password == nil, server.query == nil, server.fragment == nil,
              server.path.isEmpty || server.path == "/", !token.isEmpty,
              server.scheme == "https" || (allowLoopbackHTTP && loopback && server.scheme == "http") else {
            throw SyncFailure.invalid("同步服务器须为 HTTPS，且不能含账号、路径或查询参数")
        }
        self.server = server; self.token = token
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 60; config.timeoutIntervalForResource = 900
        config.httpShouldSetCookies = false; config.urlCache = nil
        session = URLSession(configuration: config, delegate: delegate, delegateQueue: nil)
    }
    deinit { session.invalidateAndCancel() }
    private func request(_ path: String, query: [String: String] = [:], post: Bool = false) throws -> URLRequest {
        var components = URLComponents(url: server.appendingPathComponent("api/minis/v3/" + path), resolvingAgainstBaseURL: false)!
        components.queryItems = query.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) }
        var request = URLRequest(url: components.url!); request.httpMethod = post ? "POST" : "GET"
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        request.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        if post { request.setValue("0", forHTTPHeaderField: "Content-Length") }
        return request
    }
    private func response(_ data: Data, _ response: URLResponse) throws -> [String: Any] {
        guard let response = response as? HTTPURLResponse else { throw SyncFailure.invalid("同步响应无效") }
        guard (200..<300).contains(response.statusCode) else { throw SyncFailure.http(response.statusCode) }
        guard data.count <= 16 * 1024 * 1024, let object = try JSONSerialization.jsonObject(with: data) as? [String: Any], object["ok"] as? Bool == true else {
            throw SyncFailure.invalid("服务器未确认同步成功")
        }
        return object
    }
    public func head() async throws -> CloudHead? {
        let (data, res) = try await session.data(for: request("head"))
        let object = try response(data, res)
        guard object["protocol"] as? Int == 3 else { throw SyncFailure.invalid("服务器未启用兼容同步协议") }
        if object["head"] is NSNull { return nil }
        guard let head = object["head"] as? [String: Any] else { throw SyncFailure.invalid("缺少云端版本") }
        return try JSONDecoder().decode(CloudHead.self, from: JSONSerialization.data(withJSONObject: head))
    }
    public func commit(device: String, base: String?, operation: String, file: URL, mode: String = "full") async throws {
        guard ["full", "delta"].contains(mode) else { throw SyncFailure.invalid("上传模式无效") }
        var req = try request("commit", query: ["device": device, "base": base ?? "", "op": operation,
            "mode": mode, "scope": "workspace", "name": "iphone.minisbak"], post: true)
        let size = try file.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        guard size > 0, size <= 2_147_483_648 else { throw SyncFailure.invalid("上传大小无效") }
        req.setValue(String(size), forHTTPHeaderField: "Content-Length")
        let (data, res) = try await session.upload(for: req, fromFile: file)
        let receipt = try response(data, res)
        guard receipt["scope"] as? String == "workspace",
              receipt["op_id"] as? String == operation,
              receipt["source_sha"] as? String == (try SyncDigest.file(file)) else {
            throw SyncFailure.invalid("服务器回执与待上传内容不一致")
        }
    }
    public func acknowledge(device: String, sha: String) async throws {
        let (data, res) = try await session.data(for: request("ack", query: ["device": device, "sha": sha, "scope": "workspace"], post: true))
        let object = try response(data, res)
        guard object["applied"] as? Bool == true, object["sha256"] as? String == sha,
              object["applied_scope"] as? String == "workspace" else { throw SyncFailure.invalid("设备应用确认失败") }
    }
    public func download(_ head: CloudHead, to destination: URL) async throws {
        guard SyncDigest.valid(head.sha256), head.bytes > 0, head.bytes <= 2_147_483_648 else { throw SyncFailure.invalid("下载信息无效") }
        let (file, response) = try await session.download(for: request("snapshots/" + head.sha256))
        defer { try? FileManager.default.removeItem(at: file) }
        guard let http = response as? HTTPURLResponse else { throw SyncFailure.invalid("下载响应无效") }
        guard http.statusCode == 200 else { throw SyncFailure.http(http.statusCode) }
        let size = try file.resourceValues(forKeys: [.fileSizeKey]).fileSize
        guard Int64(size ?? -1) == head.bytes, try SyncDigest.file(file) == head.sha256 else { throw SyncFailure.invalid("下载备份校验失败") }
        if let hash = http.value(forHTTPHeaderField: "X-Content-SHA256"), hash != head.sha256 { throw SyncFailure.invalid("下载摘要响应不一致") }
        let fm = FileManager.default
        if fm.fileExists(atPath: destination.path) { try fm.removeItem(at: destination) }
        try fm.moveItem(at: file, to: destination)
    }
}
