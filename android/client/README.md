# Android 活跃候选源码

基于不可变的 `android/119`，增加双向增量下载、首次账号恢复、私有技能与供应商配置分类应用。当前121/1.14.20已发布，覆盖升级、实际客户端OTA及边界见仓库README与发布记录。线上行为及待验收项见仓库README。

`scripts/prepare_verified_base.py` 只接受已核验119 APK摘要，验证两份DEX钩子后重组。构建依赖外部上游APK、SDK、smali、运行资源与独立签名材料，不包含在仓库中。JNI源码未改时按 `native-manifest.json` 校验四ABI既有适配库，不能据此声称重新编译NDK。

私有API Key随账号备份恢复；设备OAuth、回环供应商和桥接令牌不移植。已有技能文件不覆盖，冲突保留；多账号切换目前拒绝同步以防串号。完整工作区隔离、端到端加密、Mac/iOS客户端及官方额度接入待完成。

Android隔离验收探针位于 `tests/android`，仅允许操作 `test.zhangyue.minis`，不可对真实用户安装执行夹具。
