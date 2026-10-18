# Minis｜内置官方开发客户端与跨会话接力

> 🟡 **进行中** · 更新：2026-10-18T13:55+08:00
>
> OpenCode、ZCode 已在 ARM64 Android 内真实运行；完整开发与自动交接仍待通过。交接已改为分区、勾选和验证表格。

## 已完成

- [x] 1.14.9 / versionCode 110 构建及家族签名验证。
- [x] 官方 ARM64 客户端离线打包、资源摘要校验、独立目录安装；凭据不进入 APK。
- [x] Minis 原生 Rootfs / PRoot 在专用 ARM64 Android 模拟器内执行。
- [x] 实际版本：OpenCode `1.18.3`、ZCode CLI `0.16.9`、Node `24.18.1`、Python `3.12.15`。
- [x] 修复 OpenCode 加载方式与 Node ICU 数据路径；添加 `coding-clients` Skill。
- [x] 交接排版规范加入桌面 Skill 和 Minis 内置 Skill；模板见 [HANDOFF-FORMAT.md](HANDOFF-FORMAT.md)。

## 待处理

- [ ] **OpenCode 全新会话：** 首轮自动读取交接 Skill 时被工作区权限拒绝，未完成代码和测试；退出码 0 不代表成功。
- [ ] 全局 Skill 目录注册代码已补入并编译，待重建安装和新会话复验；未放开整个文件系统权限。
- [ ] ZCode 新会话接手同一项目，完成后续功能、测试与滴答写入/独立读回。
- [ ] 最终 APK 验收、代码及交接同步 GitHub 和滴答。

## 验证

| 检查 | 结果 | 证据或限制 |
| --- | --- | --- |
| ARM64 PRoot 真正执行命令 | ✅ 通过 | 返回 `aarch64` 与测试标记 |
| 两个官方客户端版本输出 | ✅ 通过 | 在 Android 内运行，未以服务器结果代替 |
| Node / Python 命令 | ✅ 通过 | Node 24.18.1；Python 3.12.15 |
| 客户端资源排除私人凭据 | ✅ 2 项测试通过 | `tests/test_client_payload.py` |
| 交接冲突、幂等及安全回归 | ✅ 7 项通过 | `tests/test_handoff.py` |
| OpenCode 自主开发及自动交接 | 🔴 未通过 | 外部路径读取被拒绝；没有完成实现 |
| ZCode 原生接力会话 | 🧪 尚未运行 | 等待前一客户端完成 |

## 下一步

1. 重建包含全局 Skill 注册修复的安装包；`ClientBootstrap.java` 已编译，保留用户命令、符号链接及配置。
2. 恢复专用 ARM64 测试模拟器；中断后该设备已断开，先核对现存进程与应用数据。
3. 用普通开发提示启动 OpenCode 全新会话；确认真实文件修改、测试输出和滴答读回。
4. 让 ZCode 新会话接手同一项目；达到同样验收条件后再标记闭环完成。

## 接力入口

- **仓库：** [xiaoletian64/minis-sync](https://github.com/xiaoletian64/minis-sync)（私有），分支 `main`。
- **当前执行：** Codex；负责客户端安装、启动、排版和验收。
- **代码：** [ClientBootstrap.java](../src/site/zhangyue/minis/handoff/ClientBootstrap.java)、[启动脚本](../assets/clients/launch)、[资源打包](../scripts/prepare_clients.py)。
- **验收：** [ClientRuntimeInstrumentation.java](../tests/ClientRuntimeInstrumentation.java)。
- **其他工作区改动：** NAS 模块及测试正在并行修改，未纳入本轮客户端提交。
- **账号：** 测试凭据仅在受控测试环境，临时同步会话需要在验收结束后撤销；不上传账号或原始日志。

## 历史记录

- 1.14.8 原生交接桥、AndroidKeyStore、本地未授权 401、手机到滴答写入及读回已通过。
- 桌面 Codex、OpenCode、ZCode、Cursor、DeepSeek 的新会话交接曾实测通过；不能据此宣称手机客户端也已通过。
- 106 的 x86 Android 容器无法执行 ARM64 PRoot，已切换到 ARM64 模拟器。
- 服务器 Free 渠道曾限流；不自动回退付费。API 模板不等于官方客户端或免费权益。
- 首次源码快照 `6f1863b`；协作者 YangLuSE 已发出 write 邀请。个人运维资料和 APK 未放入 Git。
