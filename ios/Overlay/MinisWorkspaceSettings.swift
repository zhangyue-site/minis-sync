import SwiftUI
import Security

private final class WorkspaceLoginNoRedirect: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}
@MainActor
final class MinisWorkspaceController: ObservableObject {
    static let shared = MinisWorkspaceController()
    @Published var status = "登录与安卓相同的同步账号，互通聊天和项目文件"
    @Published var busy = false
    @Published var signedIn = false
    @Published var recoveryURL: URL?
    private var engine: SyncEngine?
    private var polling: Task<Void, Never>?
    private let service = "site.zhangyue.minis.workspace-v1"
    private let defaults = UserDefaults.standard
    private init() { signedIn = token() != nil }
    private func token() -> String? {
        var result: CFTypeRef?
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
            kSecAttrAccount as String: "sync", kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess, let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }
    func login(username: String, password: String) async {
        guard !busy else { return }; busy = true; defer { busy = false }
        do {
            var request = URLRequest(url: URL(string: "https://notes.zhangyue.site/api/login")!)
            request.httpMethod = "POST"; request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: ["username": username, "password": password])
            let config = URLSessionConfiguration.ephemeral; config.httpShouldSetCookies = false
            let session = URLSession(configuration: config, delegate: WorkspaceLoginNoRedirect(), delegateQueue: nil); defer { session.invalidateAndCancel() }
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200,
                  let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  object["ok"] as? Bool == true, let token = object["token"] as? String, !token.isEmpty,
                  let canonicalUser = object["username"] as? String else { throw SyncFailure.invalid("登录失败，请核对同步账号和密码") }
            let key: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: "sync"]
            var item = key; item[kSecValueData as String] = Data(token.utf8)
            item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
            // Update preserves the existing credential if insertion fails.
            let code = SecItemUpdate(key as CFDictionary, [kSecValueData as String: Data(token.utf8)] as CFDictionary)
            guard code == errSecSuccess || (code == errSecItemNotFound && SecItemAdd(item as CFDictionary, nil) == errSecSuccess) else { throw SyncFailure.invalid("无法安全保存登录状态") }
            defaults.set(canonicalUser, forKey: "workspace-sync.user"); engine = nil
            signedIn = true; status = "已登录，可开始同步"; startAutomatic()
        } catch { status = error.localizedDescription }
    }
    private func configuredEngine() throws -> SyncEngine {
        if let engine { return engine }
        guard let token = token(), let user = defaults.string(forKey: "workspace-sync.user") else { throw SyncFailure.invalid("请先登录同步账户") }
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        let identity = SyncDigest.data(Data(("https://notes.zhangyue.site\n" + user).utf8))
        let directory = base.appendingPathComponent("MinisWorkspaceSync/" + identity)
        recoveryURL = directory
        let adapter = MinisWorkspaceAdapter(root: directory.appendingPathComponent("native"),
            chatRoot: AIChatViewModel.minisPersistentBase, sharedRoot: AIChatViewModel.minisSharedPersistentDir)
        let engine = try SyncEngine(root: directory.appendingPathComponent("state"),
            transport: HTTPCloudTransport(server: URL(string: "https://notes.zhangyue.site")!, token: token), workspace: adapter)
        self.engine = engine; return engine
    }
    func synchronize() async {
        guard !busy else { return }; busy = true; defer { busy = false }
        do {
            status = "正在同步聊天和项目文件…"
            let head = try await configuredEngine().synchronize()
            status = "已同步到版本 \(head?.version ?? 0)" + ((head?.conflicts ?? 0) > 0 ? "，冲突双方已保留在恢复副本中" : "")
        } catch { status = error.localizedDescription }
    }
    func startAutomatic() {
        polling?.cancel()
        polling = Task {
            while !Task.isCancelled {
                if defaults.bool(forKey: "workspace-sync.enabled"), signedIn, UIApplication.shared.applicationState == .active { await synchronize() }
                try? await Task.sleep(nanoseconds: 60_000_000_000)
            }
        }
    }
    func signOut() {
        guard !busy else { return }
        polling?.cancel(); engine = nil
        SecItemDelete([kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: "sync"] as CFDictionary)
        defaults.set(false, forKey: "workspace-sync.enabled"); signedIn = false; status = "已退出，聊天、文件与恢复副本保留"
    }
}
struct MinisWorkspaceSettings: View {
    @ObservedObject private var controller = MinisWorkspaceController.shared
    @AppStorage("workspace-sync.enabled") private var automatic = false
    @State private var username = ""
    @State private var password = ""
    var body: some View {
        Form {
            Section {
                Label("安卓与 iPhone 互通", systemImage: "arrow.triangle.2.circlepath")
                Text("同步聊天记录、会话工作目录和共享项目目录。两端使用同一个同步账号。")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            if !controller.signedIn {
                Section("同步账号") {
                    TextField("账号", text: $username).textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField("密码", text: $password)
                    Button("登录") { Task { let secret = password; password = ""; await controller.login(username: username, password: secret) } }
                        .disabled(username.isEmpty || password.isEmpty || controller.busy)
                }
            } else {
                Section {
                    Toggle("在前台自动同步", isOn: $automatic)
                    Button("立即同步") { Task { await controller.synchronize() } }.disabled(controller.busy)
                    Text("先关闭 Minis 的 iCloud 同步。AI 任务执行期间等待完成后再同步；应用退出后会在下次打开时继续。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            Section("同步状态") {
                if controller.busy { ProgressView() }
                Text(controller.status).textSelection(.enabled)
            }
            if controller.recoveryURL != nil {
                Section("恢复副本") {
                    NavigationLink("查看并导出恢复备份") { WorkspaceRecoveryView(root: controller.recoveryURL!) }
                    Text("同步前的本机备份与云端版本均保留，可导出后在“备份与恢复”中查看。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            if controller.signedIn { Section { Button("退出同步账号", role: .destructive) { controller.signOut() }.disabled(controller.busy) } }
        }
        .navigationTitle("跨设备同步")
        .task { controller.startAutomatic() }
    }
}

private struct WorkspaceRecoveryView: View {
    let root: URL
    @State private var files: [URL] = []
    var body: some View {
        List {
            ForEach(files, id: \.path) { file in
                ShareLink(item: file) {
                    Label(file.lastPathComponent.hasPrefix("local-") ? "同步前本机备份" : "云端恢复备份", systemImage: "square.and.arrow.up")
                }
            }
            if files.isEmpty { Text("暂无恢复备份") }
        }
        .navigationTitle("恢复备份")
        .task {
            let enumerator = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.contentModificationDateKey])
            files = (enumerator?.allObjects as? [URL] ?? []).filter { $0.pathExtension == "minisbak" && $0.path.contains("/recovery/") }.sorted { $0.lastPathComponent < $1.lastPathComponent }
        }
    }
}
