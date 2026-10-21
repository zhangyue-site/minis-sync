# Minis｜iPhone 与安卓互通聊天和项目文件

> 状态：开发候选，尚未完成完整 iOS App 构建与真机双端验收。2026-10-21。
>
> 已实现同步服务范围扩展、iOS 原生适配、持久重试和安卓候选。生产路由与 OTA 未改变，真实用户安装和数据未操作。

## 要交付的行为

同一同步账号在两端交换聊天、会话工作目录（`chats/<session-id>/…`）和共享项目目录（`shared/…`，终端中通常为 `/var/minis/shared`）。聊天保留原 ID、顺序、分组和消息元数据；按已应用的共同基线传播删除；并发编辑保留恢复内容。

当前自动执行范围是应用在前台、AI 任务空闲时。iOS 被挂起或强制退出后不能保证持续后台同步，下次打开根据持久待办继续。系统/Linux 根目录、已安装软件、外部挂载目录不属于这两个持久目录。iCloud 同步与自定义同步不能同时写入同一份聊天，iOS 适配在 iCloud 开启时明确停止。

## 为什么需要改两端及服务端

本地旧仓库主要是 Android 1.14.11 补丁。最新滴答交接指向 106 的 1.14.12/113 修复候选，其 v3 服务已有三方合并、不可变完整版本、账号隔离和幂等操作。其 `scope=chats` 仅应用会话和附件，共享项目文件不在完整应用范围。本轮从该候选复制源码到独立的 `integrations/`，不覆盖其构建目录或生产服务。

官方 iOS 源码固定在 `OpenMinis/OpenMinis` 的 `b4c0661d5631ebab4d1a2e6f3fd4c805d4030a6c`。两端使用 `minisbak/1`，但相同扩展名不能证明所有旧版本互通：安卓曾把会话记录写为扁平结构，iOS 要求嵌套 `session`；新消息也增加了字段。因此适配拒绝不支持的结构或非空字段，保留原包，不使用逐行静默跳过的恢复路径。

## 实现与边界

| 部分 | 本轮改动 | 保护措施 |
|---|---|---|
| 服务 | 新增 `workspace` 范围，允许聊天和 `shared_files` 的删除推断 | 原 `chats` 语义保留；其他分类缺失不当成删除；不完整或跳过文件的 workspace 上传拒绝 |
| iOS 网络 | v3 上传、SHA 校验下载、应用确认；HTTPS，拒绝重定向 | 令牌留 Keychain；账号和服务器分别隔离状态；回执核对操作和原始上传摘要 |
| 重试 | 上传前落盘正文、操作 ID、基线和模式 | 回执丢失重用操作；本机继续编辑时不应用过期结果；应用失败不 ACK |
| 增量 | 对比本机已应用完整包与本次导出的条目摘要，发送 v2 条目增量 | 删除列表明确；超过增量大小或没有可复用基线时上传完整包 |
| iOS 聊天 | 在 `ChatStore` 自有 actor/SQLite 连接上比较现状并事务更新、删除 | 不清空数据库，不采用备份恢复的“较新时间优先”；相同时间的云端合并结果仍能应用；写失败回滚 |
| 项目文件 | 排他创建、原子交换、删除前保留原 inode | 并发新文件不覆盖；被替换/删除的原件留恢复区；SQLite 中的提交标记与文件日志配合恢复中断 |
| 大记录 | 聊天记录落磁盘 SQLite 暂存并逐条比较/应用 | 70MiB 级 JSONL 已测试；单条 8MiB、总聊天元数据 512MiB、包 2GiB、展开 4GiB 为明确拒绝上限 |
| ZIP | 使用实际上游 ZIP64 读写器；压缩条目改为分块 raw DEFLATE | 限制解压输出，校验截断；不一次性读入整个压缩附件 |
| iOS 原生导出 | 单独的 workspaceTransport 模式 | 不带模型凭据、不附加与旧合并器不兼容的救援清单；同步临时导出使用后释放 |
| 安卓 | 在 1.14.12 已有 hook 上扩展 workspace 范围与 shared 物理更新 | 原文件通过 JNI `RENAME_EXCHANGE` 保留；共享文件删除移入恢复区；独立 QA 构建 |
| 安装隔离 | iOS bundle ID、App Group、CloudKit ID 改为 QA 命名空间 | 不加入官方 App 的数据容器；安卓 QA 包不覆盖正式包 |

不能把旧客户端的同步基线直接升级为 workspace 基线。安卓候选采用独立状态命名空间；服务端也要求该设备明确确认过 workspace/all 基线，才能以它推断共享文件删除。

## 已验证

- 62 项服务端 Python 测试在 106 独立目录通过，包含旧聊天模式回归、共享文件删除、离线新增、并发冲突双方 blob、范围确认隔离和部分备份拒绝。大 JSONL 合并进程峰值 RSS 约 40.7MB（Linux 此次结果）。
- 16 项 Swift 测试通过：持久待办与重启、回执丢失、继续编辑、ACK 失败、损坏下载、真实 SQLite 更新/删除/回滚、中文 emoji 路径、文件交换恢复、符号链接拒绝、流式解压、70MiB 级磁盘暂存以及数据库亚秒时间比较。
- 真实 Swift URLSession → SSH 隧道 → 106 隔离 Python 服务通过：完整上传、双方下载/确认、文件删除、新增、幂等重试、条目增量上传及账号隔离。
- 实际上游 ZIP64 写入器/解压器单独编译运行，与 Python ZIP 相互读取，2MB 压缩附件逐字节一致。
- 安卓 Java、四 ABI 原生模块已构建，APK 签名验证通过。Linux/JVM 真实 JNI 的排他发布、原子交换、旧字节保留和 Unicode 路径通过。
- 新增 Swift 核心和 iOS 适配通过 iOS 16 模拟器 SDK 类型检查；适配检查对原 App 类型采用 ABI stub，因此不等于整个 App 编译成功。Xcode 工程结构 `plutil` 通过。

## 尚未验收的部分

1. **完整 iOS App 构建与安装。** 已尝试 Xcode 依赖解析和无签名构建，停留在 Package Graph/二进制依赖解析阶段，未得到 App。上游 iSH、FFmpeg、LAME、rclone 原生依赖尚未准备。当前磁盘在本轮下降到不足 1GB，已停止大型构建并删除本轮下载缓存；需有足够空间的构建位置后继续。
2. **真机双端验证。** 本轮 adb 没有设备；iPhone 12 Pro 被系统列为 unavailable。尚未在真实 Minis UI 中验收新增、删除、同会话并发编辑、附件显示、后台/冷启动恢复及账号切换。APK 构建和核心测试不能代替这一关。
3. **新旧版本真实数据格式。** 还需用用户当前版本的隔离备份做字段往返测试。非空 `editCount`/`thinkingOverride` 等 iOS 原生模型不支持的字段目前明确拒绝，不能宣称兼容所有旧版本。原件保留，不能删字段强行通过。
4. **生产迁移。** 最新既有交接仍记载历史 50 份中缺 6 份 NAS 历史包、需补历史 ID 扫描以防旧离线数据复活；本轮未解决或激活该迁移。当前生产不应直接改指向本轮新测试库。
5. **安卓版工作区全链路。** 本轮新增 shared 物理更新还未在真实 Android Minis/Room/终端中跑完应用与并发写入验收。已有老候选的双安卓通过属于历史证据，不覆盖这次改动。
6. **恢复区与缓存预算。** 临时导出会释放；恢复副本和不可变基线目前保留。需要接入既有 NAS 验证及缓存保留策略后再长期启用自动同步，不能据此承诺手机无限期运行不增长。

## 源码和复验入口

- `ios/MinisSyncCore/`：独立 Swift Package；`swift test --package-path ios/MinisSyncCore`。
- `ios/Overlay/`：原生备份、ChatStore actor 扩展、登录与前台同步界面。
- `scripts/prepare_ios_sync.py --upstream /path/to/pinned/OpenMinis`：只接受固定上游提交；拒绝覆盖未标记的原有修改。
- `integrations/cloud-v3/`：隔离服务候选；可沿用既有登录鉴权后挂载 `minis_cloud_http.handle`，禁止直接将测试 token 服务对公网开放。
- `integrations/android-v3/`：候选 Java/JNI；`scripts/build_android_workspace.py` 复用已有 1.14.12 hook，在单独目录构建 QA 包。
- `tests/cloud-v3-reference/`：从既有候选复制并追加的隔离回归；需要至少 2GiB 可用空间，沿用生产相同低空间保护。
- `tests/test_iphone_http.py`：先构建 Swift `MinisSyncProbe`，再运行；默认本机隔离服务，也可通过 `MINIS_TEST_CLOUD_URL` 指向专用 loopback SSH 隧道。
- `tests/verify_ios_archives.py --upstream /path/to/prepared/OpenMinis`：实际 ZIP 读写器与 Python 互操作。

公开上游入口：[OpenMinis/OpenMinis](https://github.com/OpenMinis/OpenMinis)。个人服务器、任务绑定和原始日志放私有交接，不进入共享说明。
