# Stage2 MES 目录与预测接入

Stage1 和 Stage2 共用一个 HTTP 服务端口。C209 当前运行在 **127.0.0.1:8000**；这是 C209 本机地址。端口由启动参数指定，接口无需新增独立 TCP 服务。

## 1. 目录与身份

MES 提交的是 **C209 服务账号可读取的绝对目录**，可以是 C209 本地目录或 UNC 共享目录；MES 电脑上的 `D:\data` 不会自动成为 C209 的同名目录。当前默认输入根为 `F:\npfcode\liante_old\train_data\data_v5\split_by_channel`。

管理员在 `.secrets/c209.json` 配置 `STAGE2_ALLOWED_DIRS`，多个根目录用分号分隔。`STAGE2_INPUT_DIRS` 的默认根也被允许。MES 可选择这些根目录下的子目录，不能扩大允许范围。目录需先存在，系统不创建或删除 MES 数据目录。共享路径使用 UNC，服务账号必须已有访问权限；计划任务账号可能没有交互用户的映射盘。

为每个 MES 配置独立的 `LIANTE_DEVICE_TOKENS`，其值是 JSON **字符串**，例如 `{"MES-01":"替换为独立随机密钥"}`。修改环境配置后重启服务。MES 请求头：

```http
Authorization: Bearer <MES-01 的密钥>
X-Liante-Device-ID: MES-01
Content-Type: application/json
```

也可用 HTTP Basic，用户名 `device:MES-01`，密码为同一密钥。管理员可以登记来源并指定已配置的 `owner_device_id`；MES 登记来源时自动绑定自己的身份。共享 device 密钥无法调用本节接口。密钥不会写入接入日志。

C209 已为本次接入配置独立身份 `MES-STAGE2`，凭据保存在 `F:\npfcode\liante\.secrets\c209.json` 的 `LIANTE_DEVICE_TOKENS` 字段，由管理员提供给 MES。下文的身份示例可替换为 `MES-STAGE2`；示例密钥不是实际凭据。

现有来源 `MES-LINE-01` 已绑定该身份，目录为上述默认输入根。2026-10-01 实测此身份提交 8 个文件并完成 4984 条预测，相同请求重试返回原任务；接入日志、任务日志和结果分页均已验证。这是升级前验证；本轮完整测试与基线比较见 SYSTEM_IMPROVEMENT_REPORT.md。

## 2. 登记或更改目录

`POST /api/v1/inference/ea-bias/sources`

```json
{
  "source_id": "MES-LINE-01",
  "directory": "F:\\npfcode\\liante_old\\train_data\\data_v5\\split_by_channel",
  "expected_revision": 0,
  "request_id": "config-20261001-001"
}
```

新增返回 HTTP 201；修改或重复相同配置返回 200。响应 `data` 包含 `source_id`、`directory`、`owner_device_id`、`revision`、`updated_at`、`exists`。最多 100 个来源。

修改时将 `directory` 换为目标目录，并传当前 `revision` 作为 `expected_revision`。版本不匹配返回 409 `SOURCE_VERSION_CONFLICT`；相同目录重复提交不增加版本。`GET .../sources` 获取最新配置，设备只看到自己的来源。配置保存在 `output/stage2_inputs.sqlite3`，重启后继续使用；更新默认环境目录不会覆盖已经登记的来源。

## 3. 扫描和提交预测

可先 `POST /api/v1/inference/ea-bias/scan`，请求 `{"source_id":"MES-LINE-01","recursive":true}`。响应包含每个文件的自动识别通道、大小、状态与原因。CSV/XLSX 文件名或子目录需含唯一 `channel_0`–`channel_7` / `CH0`–`CH7`；四列特征文件直接读取，七列以上文件读取第 4–7 列。

正式提交：`POST /api/v1/inference/ea-bias/folder`

```json
{
  "source_id": "MES-LINE-01",
  "recursive": true,
  "product_id": "LOT-20261001-001",
  "request_id": "prediction-20261001-001"
}
```

MES 必须提供非空 `product_id` 和 `request_id`。首次接收返回 202，`data` 含 `task_id`、`request_id`、`status`、`product_id`、`source`（实际扫描清单与配置版本）。此请求处理提交时该目录中所有识别到的文件；当前不持续监视新增文件。混合产品应按产品拆分目录及请求，避免一个产品编号关联多个产品的数据。

相同身份、相同 `request_id`、相同请求参数重试返回 **200、原 task_id、当前 status 和 replayed=true**，不重复扫描或预测；即使来源目录已修改，仍返回原任务。参数不同返回 409 `IDEMPOTENCY_CONFLICT`。回执默认保留 30 天；该期限内不要重复使用编号，过期后新提交可产生新任务。回执容量由 `STAGE2_SOURCE_MAX_EVENTS` 控制，达到上限拒收新回执，不淘汰期限内回执。

## 4. 状态、结果与日志

| 用途 | GET 接口 |
| --- | --- |
| 状态和进度 | `/api/v1/tasks/{task_id}?summary=1` |
| 文件结果摘要 | `/api/v1/tasks/{task_id}/result` |
| 逐行预测 | `/api/v1/tasks/{task_id}/predictions?file_index=1&channel=0&limit=100&offset=0` |
| 执行日志 | `/api/v1/tasks/{task_id}/logs?limit=200&offset=0` |
| MES 接入日志 | `/api/v1/inference/ea-bias/sources/{source_id}/logs?limit=100&offset=0` |

轮询建议间隔 1–2 秒。结果未完成返回 202；完成返回 200。任一文件失败时任务为 FAILED，结果返回 **409 且 data.result 保留成功和失败项**，不能把批次判为全部成功。每个成功项提供 `predictions_endpoint`，按此地址分页获取完整逐行结果。

接入日志包含时间、身份、来源、请求编号、目录版本、变更前后路径、扫描计数、task_id、HTTP 状态及错误码，按最新在前分页返回。参数错误、拥塞拒收、重复提交也有记录。执行日志包含接收、读取、预处理、模型推理、写入结果、完成或失败等过程。MES 只能读取自己身份对应的来源和任务；旧任务保留提交时的身份归属。

接入日志默认 **30 天且最多 20000 条**，由 `STAGE2_SOURCE_RETENTION_DAYS`、`STAGE2_SOURCE_MAX_EVENTS` 配置；写入时清理并回收空闲页。此限制不改变原有任务历史的保留策略。来源清单和日志可在 Stage2 页面查看，外部变更后刷新页面。

## 5. 重试与容量

| 状态/错误 | MES 处理 |
| --- | --- |
| 429 QUEUE_FULL / SCAN_BUSY / SUBMISSION_BUSY | 按 Retry-After 退避，预测重试相同 request_id |
| 429 REQUEST_HISTORY_FULL / SOURCE_CAPACITY_REACHED | 管理员处理容量；避免持续快速重试 |
| 503 SERVICE_DRAINING | 按 Retry-After 等待服务恢复 |
| 403 PATH_FORBIDDEN | 管理员配置允许根，不提交其他系统目录 |
| 403 SOURCE_FORBIDDEN / DEVICE_ID_REQUIRED | 检查来源归属或独立 MES 身份 |
| 404 DIRECTORY_NOT_FOUND | 检查 C209 路径、共享目录及服务账号权限 |
| 422 INPUT_FOLDER_EMPTY / INVALID_INPUT_FOLDER | 修正文件；重新扫描 |
| 409 REQUEST_REPLAY_CONFLICT / SOURCE_VERSION_CONFLICT | 修正请求编号或读取新版本；避免盲目重试 |

提交先预约推理队列再扫描；最多 100 数据文件、10000 目录条目、8 层目录，拒绝扫描链接/重解析点。文件默认修改后 2 秒才接收，读取前后检查大小和修改时间；建议写临时文件后原子重命名为最终 CSV/XLSX。

## 6. 跨机器连接与启动

C209 当前服务只监听本机，其他机器不能直接请求 `http://10.254.7.25:8000`。跨机器部署可复用项目 HTTPS 反向代理，保持后端本机监听；在受控测试网络直接访问时，可停止当前服务后从 **C209 的 PowerShell** 启动并明确监听地址：

```powershell
Set-Location F:\npfcode\liante
.\deploy\start_c209.ps1 -Port 8000 -ListenAddress 10.254.7.25
```

随后 MES 的基址为 `http://10.254.7.25:8000`，同时配置网络及防火墙访问范围。不要与当前服务同时占用 8000。正式传输凭据使用 HTTPS。没有 Origin 的 MES 服务端调用不需要添加浏览器 CORS 来源；来自浏览器的跨域调用还需配置 `LIANTE_ALLOWED_ORIGINS`。

## 7. 公共幂等与管理

新提交使用 Task DB 的公共回执，按认证身份、HTTP 操作/路径、request_id、规范化 JSON 区分；同请求返回原对象及 replayed，不重复预测；不同参数返回 409 REQUEST_REPLAY_CONFLICT。历史 Stage2 receipts 仅兼容读取，不再双写。公共回执上限 100000，无自动到期复用，来源日志 30 天保留不等于新幂等键有效期。

来源回执与来源配置同事务，任务回执与任务创建同事务。失败任务需要新执行时 POST /api/v1/tasks/{task_id}/retry，使用原输入快照并保留 retry_of；输入变化应重新扫描提交。

管理中心 /gui/operations.html 提供历史、追溯、来源日志和受控导出。日志及管理分页支持 page/page_size，从 1 开始、最多 1000（追溯 200），与 limit/offset 互斥。本轮测试和真实 Stage2 基线比较以 SYSTEM_IMPROVEMENT_REPORT.md 为准。
