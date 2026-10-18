# Minis｜内置官方开发客户端与跨会话接力

> 🟡 **进行中** · 更新：2026-10-18T14:23+08:00
>
> OpenCode 已在 Minis 的 ARM64 Android / Alpine / PRoot 内完成真实新会话：代码、3 项测试及滴答写入/独立读回。ZCode 正接手同一项目。

## 已完成

- [x] 1.14.9 / versionCode 110 构建及家族签名验证。
- [x] 官方 ARM64 客户端离线打包、资源摘要校验、独立目录安装；凭据不进入 APK。
- [x] Minis 原生 Rootfs / PRoot 在专用 ARM64 Android 模拟器内执行。
- [x] 实际版本：OpenCode `1.18.3`、ZCode CLI `0.16.9`、Node `24.18.1`、Python `3.12.15`。
- [x] 修复 OpenCode 加载方式与 Node ICU 数据路径；添加 `coding-clients` Skill。
- [x] 交接排版规范加入桌面 Skill 和 Minis 内置 Skill；模板见 [HANDOFF-FORMAT.md](HANDOFF-FORMAT.md)。旧安装补充格式参考、保留个人内容的升级代码已编译并签名，待 Android 升级实测。

## 待处理

- [x] OpenCode 全新会话 `ses_eb259c181ffeS7M1DmKm8ZZRaS` 实现函数、3 项测试通过并主动同步；桥返回 `verified=true`，106 独立读回确认同一任务区段存在。
- [x] 标准全局 Skill 入口由 APK 实际创建，权限读取通过；没有放开整个文件系统。
- [x] 恢复服务端已鉴权的 owner_features 路由，修复同步入口 `not_found`；保留同期 NAS 服务改动。
- [ ] ZCode 新会话接手同一项目：首轮报“尚未选择模型”，桌面凭据存在设备相关加密，已移除测试副本并改走 Android CLI 官方登录。微信登录页面等待用户完成拼图验证；之后新增功能与 2 项测试并自动更新同一滴答任务。
- [ ] 最终 APK 验收、撤销临时同步会话、代码及交接同步 GitHub。

## 验证

| 检查 | 结果 | 证据或限制 |
| --- | --- | --- |
| ARM64 PRoot 真正执行命令 | ✅ 通过 | 返回 `aarch64` 与测试标记 |
| 两个官方客户端版本输出 | ✅ 通过 | 在 Android 内运行，未以服务器结果代替 |
| Node / Python 命令 | ✅ 通过 | Node 24.18.1；Python 3.12.15 |
| 客户端资源排除私人凭据 | ✅ 2 项测试通过 | `tests/test_client_payload.py` |
| 交接冲突、幂等及安全回归 | ✅ 7 项通过 | `tests/test_handoff.py` |
| 真实 POST 分发鉴权回归 | ✅ 3 项通过 | `tests/test_handoff_dispatch.py`，生产已鉴权读接口复核通过 |
| OpenCode 自主开发及自动交接 | ✅ 通过 | 新会话实现函数、3 tests OK、verified=true、106 独立读回 |
| ZCode 原生接力会话 | 🧪 执行中 | 同一项目与滴答任务 |

## 下一步

1. 用户完成官方微信登录拼图后，检查 Android CLI 登录结果并重新启动 ZCode 全新会话：是否读取旧交接、保留原代码与 3 项测试、增加功能并达到 5 项测试通过。
2. 独立读回滴答，确认同一任务/稳定键更新，保留 OpenCode 及其他区段。
3. 核验最终正式 APK 签名与摘要、撤销临时同步会话，提交并推送本轮客户端文件。

## 接力入口

- **仓库：** [xiaoletian64/minis-sync](https://github.com/xiaoletian64/minis-sync)（私有），分支 `main`。
- **当前执行：** Codex；负责客户端安装、启动、排版和验收。
- **代码：** [ClientBootstrap.java](../src/site/zhangyue/minis/handoff/ClientBootstrap.java)、[启动脚本](../assets/clients/launch)、[资源打包](../scripts/prepare_clients.py)。
- **验收：** [ClientRuntimeInstrumentation.java](../tests/ClientRuntimeInstrumentation.java)、[命令入口](../scripts/verify_native_clients.py)。
- **待安装升级：** 最新格式升级包已生成，待登录流程结束再安装，避免中断官方授权。
- **其他工作区改动：** NAS 模块及测试正在并行修改，未纳入本轮客户端提交。
- **账号：** 测试凭据仅在受控测试环境，临时同步会话需要在验收结束后撤销；不上传账号或原始日志。

## 历史记录

- 1.14.8 原生交接桥、AndroidKeyStore、本地未授权 401、手机到滴答写入及读回已通过。
- 桌面 Codex、OpenCode、ZCode、Cursor、DeepSeek 的新会话交接曾实测通过；不能据此宣称手机客户端也已通过。
- 106 的 x86 Android 容器无法执行 ARM64 PRoot，已切换到 ARM64 模拟器。
- 服务器 Free 渠道曾限流；不自动回退付费。API 模板不等于官方客户端或免费权益。
- 首次源码快照 `6f1863b`；协作者 YangLuSE 已发出 write 邀请。个人运维资料和 APK 未放入 Git。
