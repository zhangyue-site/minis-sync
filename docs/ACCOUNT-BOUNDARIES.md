# 账户数据边界

`server/account_access.py`保护单一维护者的共享私人资源：vault、quota、hub（含设备/会话/控制台/密钥）、qr、旧sync和camera。GET及POST在任何资源读取或执行前校验登录与服务器owner配置。普通账号即使传入admin角色、用户ID或设备认证标记也不能获得owner权限；配置缺失时拒绝。

每个账号自己的`/api/me`、Minis snapshots/delta及播放器进度保留原有按认证UID分区的流程；不使用客户端传来的userId决定归属。密钥二次验证不接受客户端自行声明device成功，现有密码验证保留。

修改notes_site时必须保留GET/POST的account_access.enforce入口及owner_features已有边界。测试：`python3 -m unittest discover -s tests -p test_account_access.py`。生产HTTP验收还应覆盖匿名/普通账号/过期与撤销会话/真实owner，以及同设备切换账号、跨账号摘要下载和清单访问。

本模块不会把共享私人资源变成多租户资源，也不会自动分发密钥。新增私人端点必须纳入权限边界并实测；正常登录或下载站Basic不能替代owner权限。
