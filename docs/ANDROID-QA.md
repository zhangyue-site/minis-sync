# Android隔离验收与只读快照

正式包为`site.zhangyue.minis`，隔离包为`test.zhangyue.minis`。二者的数据目录不同。后续隔离构建必须显示“Minis · 验收测试”，包括应用及activity/alias标签；`scripts/label_qa_manifest.py`拒绝正式包清单，只在build.py的test分支调用。不要把测试包入口当成正式应用，也不要通过卸载或清数据准备升级测试。已发布121正式APK不变，现有旧测试包需停用以免图标混淆。

## 发生数据显示异常时

先停止验收和写入，核对前台包名、正式包/测试包版本与安装时间；保留数据库及备份，不直接恢复旧快照。若停用本轮测试包，使用明确包名并保留它的数据。只读记录和用户界面确认分别保存，不把数据库计数等同于所有内容都完整。

`tests/android/ReadOnlySnapshot.java`是按需使用的同签名instrumentation工具。它以基础Application替代MinisApp并跳过onCreate，避免启动应用同步；不调用恢复、账号设置或缓存清理。复制数据库及WAL/SHM、shared_prefs和私有技能到一个新的external-files快照目录，拒绝覆盖/符号链接，逐文件比对复制前后与副本SHA256；只在副本执行quick_check及固定表计数。失败保留已有证据，不恢复数据。工具首先在自建模拟器验证，然后才能用于明确授权的实体设备。

用既有构建宿主执行`MINIS_PROBE_CLASS=ReadOnlySnapshot python3 tests/android/build_account_probe.py`。工具包名`site.zhangyue.minis.readonlysnapshot`，目标为正式包；targetSdk35，仍需真实系统安装验收。检查实际设备归属、当前用户、签名和空间后，明确指定serial执行`am instrument -w site.zhangyue.minis.readonlysnapshot/test.ReadOnlySnapshot`。它会停止并替换目标应用的运行进程，不应在有活动任务时执行；虽然不写原数据库，复制并非跨文件原子快照，源摘要变化时必须视为失败，不声称一致。

快照包含私人会话、账号凭据和技能，只能拉取到受保护私有目录（目录0700、文件0600），逐文件核对report.json。不得提交Git、上传公共发行页面或发送模型审阅。正式包未更新与数据副本可读不能替代用户确认原界面内容。
