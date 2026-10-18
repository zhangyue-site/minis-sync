# 构建输入与部署边界

此工程通过 Java 源码与 smali 补丁重打包既有 APK。当前脚本使用维护宿主目录布局；协作者需准备相同布局，或先将脚本中的路径参数化。

- 工作目录：`/opt/minis-fix-20261018`。
- 上游 APK：`/opt/msx107/out/minis-sync-1.14.6.apk`；第二 DEX smali：`/opt/msx107/base/dex1_smali`。
- 工具：`/opt/msx107/smali.jar`、`baksmali.jar`、`lib_jcifs/*.jar`；Android SDK build-tools 35.0.0 与 android-34/android.jar；Java、Python3。
- 签名：受控宿主提供 keystore；密码通过 `MINIS_SIGNING_PASSWORD` 环境变量读取，禁止写进命令历史或提交。
- `python3 build.py` 输出正式包和 `test.zhangyue.minis` 隔离测试包；校验签名与对齐后才验收。不得覆盖用户真实应用。

上游主 DEX 与第二 DEX 存在同名类，修改必须覆盖实际首先加载的类，不能仅验证 classes2.dex。

服务端为已有应用的扩展模块。`notes_site.py` 依赖宿主额外模块（包括 hub_bridge 等），并不是独立的即开即用服务器；生产配置、数据库、账号和依赖需由维护者提供或替换成隔离实现。`handoff_rpc.py` 与 `owner_features.py` 当前含维护环境的固定路径、任务项目和部署标识；在其他环境使用前必须替换并核验账号限制。

本仓库不包含上游 APK、构建产物、keystore、数据库、用户数据或个人操作日志。它们不是可以从 Git 历史恢复的资源。


## 1.14.9 官方客户端资源

`MINIS_BUILD_ROOT` 可指定隔离构建目录。客户端资源为 `assets/clients/runtime.tar.gz`（被 Git 忽略），摘要及来源见同目录 `manifest.json`。先生成资源，再运行 `build.py`；不要用个人配置替代官方内置 provider 配置。

资源准备：

1. 从官方 npm registry 取得 `opencode-linux-arm64-musl@1.18.3`，使用 npm 的完整性校验，解出 `bin/opencode`。
2. 使用官方 Alpine apk-tools 的 `--root`、`--arch aarch64`、`--initdb` 和 `--no-scripts` 安装 Node、Python3、Git、CA 包到隔离的 `runtime-root`。当前输入来自 Alpine v3.23，包签名使用发行版公开 keys 验证；禁止关闭包签名验证。不要在宿主执行 ARM64 安装脚本。
3. 从官方 ZCode Desktop 复制 `resources/glm/zcode.cjs` 和 `resources/config/provider/zcode-builtin.json`，不复制 `~/.zcode`。当前 CLI 版本为 0.16.9；Node 为 24.18.1。
4. 执行：

```sh
python3 scripts/prepare_clients.py --source /path/to/official-artifacts --output assets/clients
MINIS_BUILD_ROOT=/path/to/isolated-build python3 build.py
```

运行库安装到 Alpine 的 `/opt/minis-clients/runtime-v1`，不会升级原有 Alpine 包。启动脚本设置独立动态库和 ICU 数据路径；OpenCode 直接执行官方 ELF，ZCode 通过独立 Node 启动。

客户端凭据不进入资源包。首次使用 ZCode 在终端执行 `zcode login bigmodel --no-browser` 并完成官方账号授权。OpenCode 默认仅选 Free 模型，限流时不切换付费。Minis 同步账号登录与这两个客户端的账号授权是不同状态。

真实终端验收需要 ARM64 Android。x86 Android 容器中的 ARM JNI 翻译不能代替 ARM64 可执行程序支持。验收夹具 `tests/ClientRuntimeInstrumentation.java` 调用实际 Minis Rootfs 与 PRoot runner，使用隔离应用和临时同步账号会话；退出码0仍须结合实际文件、测试与远端读回判断成功。

部署服务端时必须保留鉴权之后的 `owner_features.post` 分发。同步/NAS 功能升级曾覆盖此入口，表现为客户端已登录但交接返回 `not_found`。运行 `python3 -m unittest discover -s tests -p test_handoff_dispatch.py`，部署后用受控账号只读 `/api/minis/handoff` 再确认；源码测试通过不能代替生产路由核验。

可复验的 Android 命令入口：先安装隔离测试 APK 与同签名 instrumentation，再使用 `scripts/verify_native_clients.py --serial <设备> --command-file <私有命令文件> --log <私有日志文件>`。需要同步登录时，添加 `--sync-token-file <Android 上的临时私有文件>`；脚本只传文件路径，不输出令牌。该脚本检查 instrumentation 和命令状态；仍需独立验证实际代码、测试结果与远端交接内容。

真机免 root 验收可用 `--host-sync-token-file <本机私有短时令牌文件>` 代替设备文件。仅测试 instrumentation 接收令牌：通过随机路径、只读一次的 127.0.0.1 HTTP 服务和 adb reverse 传入，随后由 AndroidKeyStore 加密；凭据不进入任何 APK。脚本保留已有端口映射，结束后移除自己建立的转发。验收完成或等待真机授权时撤销服务器上的临时会话。
