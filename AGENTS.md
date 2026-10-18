# 开发规则

安装了 dida-dev-handoff Skill 的环境先读取该技能；维护者本机正本为 `~/.codex/skills/dida-dev-handoff/SKILL.md`。按稳定任务键更新本项目交接及本人同一滴答任务。没有该技能或账号授权时先保存本地交接，不使用维护者个人账号。

构建宿主布局见 docs/BUILD.md。仓库首次导入前无 Git 历史；此后以实际 git status/log 为准，不虚构历史。协作者不能假设自己获准访问维护者生产服务。

- APK 只用乐天家族主钥匙，证书 SHA256 必须是 `b5aaac0eb0369165d34ee3bae1cddd637eaa2bc3566adf9e3f2dc2f5a1a473e4`。
- 钥匙与凭据不进入代码、交接或公开产物；构建从宿主保险库和环境取得。
- 上游 APK 的 classes.dex 和 classes2.dex 存在同名类，修改后须验证首先加载的 DEX 已包含修复。
- 不卸载或覆盖用户真实 Minis；以 `test.zhangyue.minis` 隔离验收升级与数据保留。
- 不覆盖已发布同版本 APK；发布需符合用户授权及 publish 技能。

交接排版遵循 `docs/HANDOFF-FORMAT.md`：标题、状态行、勾选清单、验证与下一步分区；当前结论置顶，旧状态放历史记录。

共享交接：`docs/HANDOFF.md`。维护者个人交接位于被 Git 忽略的 docs/handoff/，不得上传其中的运维和任务隐私记录。
