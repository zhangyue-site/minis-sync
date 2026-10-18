# 当前开发交接

现状：交接内置和 Android → 服务端 → 滴答链路已实测；模型客户端内置及 Minis 自主开发会话未完成。证据日期：2026-10-18。

仓库： https://github.com/xiaoletian64/minis-sync （私有），默认分支 main；首次源码快照 6f1863b。已向 YangLuSE 发出 write 协作邀请，待对方接受。此次提交前7项交接回归和10项增量测试通过；未上传APK或私有运维资料。

上传期间发现 NAS 模块及相关测试存在并行未提交改动，未混入首次快照；接手时先查看 git status/diff，不能把这些工作区变化视为已推送。

## 已完成与证据

- 1.14.8 / versionCode 109 构建、家族签名验证通过。
- 隔离 Android 包真实冷启动注册 Skill；KeyStore 解密、本地未授权 401、端到端写入和独立读回通过。
- `tests/test_handoff.py` 7 项通过：历史内容/字段保留、更新幂等、陈旧版本冲突拒绝、注入和路径拒绝、账号限制、免费渠道不回退付费及输出上限。
- 已在独立桌面测试环境验证 Codex、OpenCode、ZCode、Cursor、DeepSeek Harness 的新会话实现、测试与滴答交接；OpenCode 接手 ZCode 工作区也完成后续功能和同一任务更新。
- 个人验收日志及任务绑定留在维护者环境，本仓库不包含这些私有记录。

## 剩余与下一步

1. 查看 `src/site/zhangyue/minis/handoff/HandoffBootstrap.java`、`assets/handoff/` 和 `server/provider_rpc.py`。当前是交接桥和代理模板，不能报告为两个官方客户端已内置。
2. 在 Minis ARM64 Alpine 环境实际接入官方客户端，先验证启动，再用真正的新开发会话完成文件修改、测试与滴答写入/读回。
3. 官方 OpenCode 免费渠道在服务器侧验收时被限流，未通过；不自动回退付费。模板目前禁用。
4. 部署 `server/owner_features.py` 时，须把鉴权后的路由分发接入宿主 HTTP Handler；以当时服务端文件为基准合并，避免覆盖其他部署。
5. 共享给协作者时使用其本人测试账号和隔离滴答任务，不复用维护者生产令牌。

## 文件入口

- `build.py`：1.14.8 重打包；`build_1_14_7.py`：前版本构建参考。
- `src/site/zhangyue/minis/sync/`：增量传输及 SMB 安全备份。
- `server/`：同步、保留、归档、交接及模型代理模块。
- `tests/HandoffInstrumentation.java`：原生交接仪器验收，必须使用隔离包及临时令牌。
- `assets/handoff/dida-dev-handoff.zip`：面向手机的交接 Skill，不含模型/滴答凭据。

本次上传前源码目录无 Git 历史，首次提交是当前文件快照，不代表从头重建或所有已有代码均由本次上传任务编写。未提交的个人运维记录已被忽略。后续接手先核对 Git 差异、此交接及实际代码，不根据旧结论直接宣称部署完成。
