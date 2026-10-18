# Minis 同步版

Minis Android 同步修复与开发交接扩展项目。当前交付版本 **1.14.8 / 109**。

此仓库保存 Android 补丁源码、APK 重打包脚本、服务器同步模块及验收夹具，并非完整的上游 Android Gradle 工程。构建依赖指定的上游 APK、smali 工具和 Android SDK，见 [构建说明](docs/BUILD.md)。

## 已实现

- ZIP 条目增量同步，基线丢失或增量失败时回退完整上传。
- SMB 安全备份：临时上传、完整回读 SHA256 校验、验证后替换。
- NAS 历史归档与云端保留预算相关模块。
- Minis 启动时通过原生仓库导入开发交接 Skill，并注入默认交接要求。
- 应用私有回环桥，复用已登录同步账号；服务器限定账号访问交接和模型代理。
- 滴答交接支持稳定任务绑定、冲突检测、保留其他会话内容和写后读回。

## 验收及剩余工作

1.14.8 已验证冷启动注册 Skill、AndroidKeyStore 解密、本地错误令牌拒绝、Android 到服务端到滴答的写入及独立读回。交接相关 7 项 Python 回归通过。

**尚未完成：** Minis 中官方 OpenCode/ZCode 客户端内置及模型自主开发新会话。Free API 模板目前禁用；代理模板、HTTP 200 不代表官方免费权益或客户端已可用。完整接手入口见 [当前交接](docs/HANDOFF.md)。

## 测试

```sh
python3 -m unittest discover -s tests -p test_handoff.py -v
python3 tests/test_delta.py
```

其他服务集成测试需要额外的宿主依赖和隔离数据目录；Android 测试需要 Android SDK、指定上游 APK 和独立测试设备。不得向真实用户数据库或真实手机执行夹具。

## 协作边界

- 共享源码及通用交接，不上传密钥、OAuth 凭据、用户备份、生产数据库、个人费用记录、原始日志或 APK。
- 签名钥匙和部署凭据由维护者在受控环境提供，不能提交到 Git。
- 上游 APK、依赖和素材保留各自权利；本仓库不额外授予其分发许可。
