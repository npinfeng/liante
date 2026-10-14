# 部署与维护

## 配置与启动

`profiles/example.json` 是通用模板，`profiles/c209.json` 是 C209 实例。项目路径、Python、服务名、绑定地址和端口在 profile 中；凭据放在 profile 指定的受控文件中，不提交 Git、不打印、不放入普通备份。环境变量不自动从 `.env` 加载。

```powershell
python tools/validate_runtime.py
powershell.exe -NoProfile -File deploy/run_profile.ps1 -Profile deploy/profiles/example.json
```

生产环境必须 `APP_ENV=production`、`DEBUG=false`，配置管理员认证。未配置允许输入根时相应文件输入功能拒绝访问。模型缺失作为能力未就绪报告，允许 Web 服务启动。服务使用 Waitress、单进程及项目 PID 标记。

## C209

在 C209 的 PowerShell 中执行：

```powershell
Set-Location F:\npfcode\liante
.\deploy\lifecycle.ps1 -Action status -Profile .\deploy\profiles\c209.json
.\deploy\lifecycle.ps1 -Action start -Profile .\deploy\profiles\c209.json
```

`-Action` 还支持 `drain`、`stop`、`restart`。排空拒收新业务并等待在途任务，超时会报错并保留服务；停止仅操作指定服务及该项目 PID。计划任务按需启用后启动，再禁用自动启动，避免意外重启。注册新机器的服务可使用 `register_service.ps1 -Profile ... -Credential (Get-Credential)` 指定服务账号。

C209 后端维持 `127.0.0.1:8000`。本地浏览器通过 SSH 隧道访问：

```powershell
ssh -N -L 18000:127.0.0.1:8000 C209
```

打开 `http://127.0.0.1:18000/gui/index.html` 或管理中心 `/gui/operations.html`。现有登录凭据沿用，SSH 隧道不更改鉴权。

## 备份与恢复

维护窗口先排空、停止，再运行：

```powershell
python deploy/backup_restore.py backup --project F:\npfcode\liante --archive F:\npfcode\liante\.deploy\backups\business.zip --port 8000
python deploy/backup_restore.py restore --project F:\npfcode\liante --archive F:\npfcode\liante\.deploy\backups\business.zip --port 8000 --replace-existing
```

覆盖任务/预测/日志索引、Qualification/Review、MES 来源、AOI 帧/产品/流数据库及非敏感实例配置。使用 SQLite backup API 取得一致快照；备份和恢复都要求服务离线。默认不包含模型、原始数据或凭据。凭据由原密钥存储独立保管。

恢复检查 PID、端口、应用/清单/数据库版本、完整性、SHA-256、磁盘容量和路径白名单。全部验证后安装；原文件保留到 `.restore-backups/`，安装中异常自动回滚已替换文件。恢复后执行运行验证、一致性检查，再启动。数据库版本改变时应配合对应版本代码恢复。

```powershell
python tools/check_task_consistency.py --output output --logs logs
```

此工具默认只读；`--repair-snapshots` 仅从 SQLite 重建 JSON，不修改业务状态或预测结果。历史任务归档工具 `archive_tasks.py` 默认预览，`--apply` 才执行，引用中的证据任务会保留。SQLite 物理文件收缩应在维护窗口进行，不能用删除数据库解决容量问题。

## 健康

`/api/v1/live` 检查进程，`/api/v1/ready/stage2` 检查八通道 Stage2，`/api/v1/ready` 检查三阶段整体。Stage1/Stage3 未配置时整体 503 是预期状态。`/api/v1/system/info` 仅管理员访问，不返回密钥和绝对路径。
