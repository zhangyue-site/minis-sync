import Foundation
import MinisSyncCore

@main struct Probe {
    static func main() async {
        do {
            let args = CommandLine.arguments
            guard args.count >= 6 else { throw SyncFailure.invalid("usage: command server token-file device path [base operation]") }
            let token = try String(contentsOfFile: args[3]).trimmingCharacters(in: .whitespacesAndNewlines)
            let transport = try HTTPCloudTransport(server: URL(string: args[2])!, token: token, allowLoopbackHTTP: true)
            if args[1] == "commit" {
                guard args.count == 8 || args.count == 9 else { throw SyncFailure.invalid("missing commit parameters") }
                try await transport.commit(device: args[4], base: args[6] == "-" ? nil : args[6], operation: args[7], file: URL(fileURLWithPath: args[5]), mode: args.count == 9 ? args[8] : "full")
            } else if args[1] == "fetch" {
                guard let head = try await transport.head() else { throw SyncFailure.invalid("empty head") }
                try await transport.download(head, to: URL(fileURLWithPath: args[5]))
                try await transport.acknowledge(device: args[4], sha: head.sha256)
            } else { throw SyncFailure.invalid("unknown command") }
            let head = try await transport.head()
            print(String(decoding: try JSONEncoder().encode(head), as: UTF8.self))
        } catch { fputs(error.localizedDescription + "\n", stderr); exit(1) }
    }
}
