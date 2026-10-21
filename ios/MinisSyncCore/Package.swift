// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "MinisSyncCore", platforms: [.iOS(.v16), .macOS(.v13)],
    products: [.library(name: "MinisSyncCore", targets: ["MinisSyncCore"])],
    targets: [.executableTarget(name: "MinisSyncProbe", dependencies: ["MinisSyncCore"]), .target(name: "MinisSyncCore"), .testTarget(name: "MinisSyncCoreTests", dependencies: ["MinisSyncCore"])])
