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
