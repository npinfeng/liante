# 联特科技工业多模态检测系统 HTTP API

实际 Flask 注册操作数：**62**。API.md 与 OpenAPI 均由实际路由生成；自动 HEAD / OPTIONS 不计入，静态 `/gui/{filename}` 计一项。

## 概述

Stage1 / Stage3 真实模型 NOT CONFIGURED，软件接口保持可接模型状态。Stage2 的模型与数值推理保持原样。所有相对地址以部署 profile 指定的服务为基准。

## 鉴权与角色

HTTP Basic 用户名 admin / operator；设备使用 Bearer、X-Liante-Role: device 与 X-Liante-Device-ID。独立设备不能读取或修改其他来源的流、任务、产品、合格证据和导出。管理员配置/排空/训练；操作员可人工登记与复核。production 缺少管理员认证或启用 debug 时拒绝启动。密钥使用部署秘密配置，经受控 HTTPS 或 SSH 隧道传输。

## 统一请求、响应与错误

新版响应保留 code / message / data，增加 request_id、timestamp，X-Request-ID 响应头便于跟踪。旧预测与训练成功格式兼容原客户端；认证和准入错误使用统一错误格式。错误不向客户端返回 traceback。

```json
{
  "code": "REQUEST_REPLAY_CONFLICT",
  "message": "相同请求编号对应不同参数",
  "data": {},
  "request_id": "R1",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

## 幂等与重试

重要 POST 可使用 body request_id 或 X-Request-ID。键由认证身份 + 实际操作路径 + request_id 构成，JSON 键序归一化。相同参数返回原对象及 replayed=true；不同参数返回 409 REQUEST_REPLAY_CONFLICT。帧以 stream + sequence + product + SHA256 + view_type 判重，关闭后仍能重放已 ACK 帧。任务与请求回执在同一 SQLite 事务关联；未知响应时先重试相同键再查询 task_id。容量、磁盘、排空拒收无新任务时可重试，已有终态任务用 retry 新建并保留 retry_of。回执不自动按时间删除，最多 100000，满后拒收，维护期备份归档。

## 分页、容量与排空

limit/offset：默认依端点，通常 100，limit 1..1000，offset 0..10000000；追溯每类最多 200。返回 items/total/limit/offset。Task/来源日志保留顺序见端点；历史按更新时间倒序。Stage1 流和每流帧队列、Stage2/3 工作与等待队列、训练队列均有界；图片、像素、预测行与结果字节也有上限。DISK_WARNING_FREE_RATIO/BYTES 告警；DISK_CRITICAL_FREE_RATIO/BYTES 拒绝新大写入/任务 507 DISK_CAPACITY_LOW，在途继续。429 / 503 / 507 的可重试容量拒绝附 Retry-After。drain 后保留 GET、close、cancel、revoke。JSONL 每任务上限 32 MiB，达到上限报告镜像降级；SQLite 保留每任务最新 20000、全局最新 200000 事件，需要归档以保存长期审计。

## Task、持久化与恢复

SQLite 是任务、结果行、结构化日志的权威主存，JSON 为可修复快照，JSONL 为追加审计副本。PENDING → QUEUED → RUNNING → COMPLETED / FAILED / CANCELLED / TIMEOUT / REVIEW_REQUIRED，亦允许提前失败或取消。终态 CAS 禁止迟到回调覆盖。普通中断任务重启标 FAILED 并可创建新任务重试；AOI 按 journal 恢复已确认帧，包括父任务缺失、FAILED 存储故障、PROCESSING 产品；不重新开放历史终态任务。

## AOI Stream / Product / Frame / Views

stream_id 兼容现有 task_id；一条流多个产品，各自多帧。视角由流 view_types 配置，required_views 是非空子集，产品可覆盖默认要求。产品 close 不关闭流；流 close 关闭所有未完成产品。状态 RECEIVING / PROCESSING / COMPLETED / FAILED / INCOMPLETE。缺视角或无帧不能 COMPLETED；模型缺失帧 FAILED / MODEL_NOT_CONFIGURED，不伪造 qualified。

## Qualification / Grading / Review

人工光电测量必须为有限值、明确单位、有界结构，配方 ID/版本必须与上游结果一致；不会替用户发明合格阈值。需要真实完成的 AOI 产品和 Stage2；独立产品完成即可登记，无需整流结束。操作员/复核身份绑定认证账号，测量时间带时区。证据 ACTIVE / SUPERSEDED / REVOKED / EXPIRED，在提交、执行和结果保存前重查。替换同事务；撤销/过期阻止再次分级。人工复核保留身份、决定、说明、建议/最终等级。真实分级模型缺失且无结果时等级必须 null，复核不是模型合格判定。

## Export / Product Trace / Admin / Deployment

任务 CSV、预测 CSV 最多每次 1000 行；证据为单个有界 JSON；产品 trace 各类分别分页，不返回全部预测。system/info 仅管理员，版本/数据库/队列/磁盘信息脱敏。配置 profile 运行 start/stop/drain/status/restart；离线备份恢复与版本完整性检查见 RUNNING_AND_TRAINING_CN.md。

## 错误码清单

`CHANNEL_NOT_IDENTIFIED`, `DEVICE_ID_REQUIRED`, `DIRECTORY_NOT_FOUND`, `DIRECTORY_UNREADABLE`, `DISK_CAPACITY_LOW`, `EMPTY_INPUT_FILE`, `FILE_NOT_FOUND`, `FOLDER_EMPTY`, `FOLDER_LIMIT`, `FOLDER_NOT_FOUND`, `FOLDER_RUN_NOT_FOUND`, `FORBIDDEN`, `FRAME_NOT_FOUND`, `FRAME_SEQUENCE_ERROR`, `FRAME_STORAGE_FAILED`, `FRAME_STORAGE_FULL`, `FRAME_TIMEOUT`, `INFERENCE_FAILED`, `INPUT_FILE_WRITING`, `INPUT_FOLDER_EMPTY`, `INTERNAL_ERROR`, `INVALID_ARGUMENT`, `INVALID_DIRECTORY`, `INVALID_FILE_FORMAT`, `INVALID_FRAME`, `INVALID_INPUT_FOLDER`, `INVALID_MODEL_OUTPUT`, `INVALID_VIEW_TYPE`, `MODEL_LOAD_FAILED`, `MODEL_NOT_CONFIGURED`, `NOT_READY`, `ORIGIN_DENIED`, `PATH_FORBIDDEN`, `PRODUCT_CLOSED`, `PRODUCT_CONFLICT`, `PRODUCT_NOT_FOUND`, `QUALIFICATION_EXPIRED`, `QUALIFICATION_NOT_ACTIVE`, `QUALIFICATION_NOT_FOUND`, `QUALIFICATION_REQUIRED`, `QUALIFICATION_REVOKED`, `QUALIFICATION_SUPERSEDED`, `QUALIFICATION_UPSTREAM_INVALID`, `QUEUE_FULL`, `QUEUE_SUBMISSION_FAILED`, `QUEUE_TIMEOUT`, `REQUEST_HISTORY_FULL`, `REQUEST_REPLAY_CONFLICT`, `REVIEW_LINK_INVALID`, `REVIEW_NOT_FOUND`, `SCAN_BUSY`, `SCAN_LIMIT_EXCEEDED`, `SERVICE_DRAINING`, `SOURCE_BUSY`, `SOURCE_CAPACITY_REACHED`, `SOURCE_FORBIDDEN`, `SOURCE_NOT_FOUND`, `SOURCE_VERSION_CONFLICT`, `STORAGE_PARENT_MISSING`, `STREAM_BACKPRESSURE`, `STREAM_CAPACITY_REACHED`, `STREAM_CLOSED`, `STREAM_NOT_FOUND`, `STREAM_RECOVERING`, `SUBMISSION_BUSY`, `TASK_HISTORY_MISSING`, `TASK_NOT_CANCELLABLE`, `TASK_NOT_FOUND`, `TASK_NOT_RETRYABLE`, `TASK_TIMEOUT`, `UNAUTHORIZED`, `UNKNOWN_DEVICE`

## 接口总览

|模块|Method|Path|用途|角色|异步|幂等|
|---|---|---|---|---|---|---|
|其他已有接口|GET|`/`|旧服务入口|admin,operator|False|读取|
|系统接口|POST|`/api/v1/admin/drain`|服务排空|admin|False|request_id|
|System|POST|`/api/v1/aoi/folder-runs`|api_v1.start_aoi_folder_run|admin,operator|False|request_id|
|System|GET|`/api/v1/aoi/folder-runs/{task_id}`|api_v1.aoi_folder_run_status|admin,operator|False|读取|
|AOI流接口|POST|`/api/v1/aoi/streams`|建立 AOI 流|admin,operator,device|False|request_id|
|AOI流接口|GET|`/api/v1/aoi/streams/{task_id}`|AOI 流状态|admin,operator,device|False|读取|
|AOI流接口|POST|`/api/v1/aoi/streams/{task_id}/close`|停止 AOI 流|admin,operator,device|True|request_id|
|AOI流接口|GET|`/api/v1/aoi/streams/{task_id}/frames`|逐帧结果|admin,operator,device|False|读取|
|AOI流接口|POST|`/api/v1/aoi/streams/{task_id}/frames`|连续推送 AOI 帧|admin,operator,device|False|帧 journal|
|AOI流接口|GET|`/api/v1/aoi/streams/{task_id}/frames/{sequence}/image`|历史原图|admin,operator,device|False|读取|
|AOI流接口|GET|`/api/v1/aoi/streams/{task_id}/latest-frame`|最新 AOI 帧|admin,operator,device|False|读取|
|AOI|GET|`/api/v1/aoi/streams/{task_id}/products`|流内产品|admin,operator,device|False|读取|
|AOI|POST|`/api/v1/aoi/streams/{task_id}/products`|建立产品|admin,operator,device|False|request_id|
|AOI|GET|`/api/v1/aoi/streams/{task_id}/products/{product_id}`|产品状态|admin,operator,device|False|读取|
|AOI|POST|`/api/v1/aoi/streams/{task_id}/products/{product_id}/close`|关闭产品|admin,operator,device|False|request_id|
|Export|GET|`/api/v1/exports/products/{product_id}/trace.json`|追溯 JSON|admin,operator,device|False|读取|
|Export|GET|`/api/v1/exports/qualifications/{qualification_id}.json`|证据 JSON|admin,operator,device|False|读取|
|Export|GET|`/api/v1/exports/tasks.csv`|任务 CSV|admin,operator,device|False|读取|
|Export|GET|`/api/v1/exports/tasks/{task_id}/predictions.csv`|预测 CSV|admin,operator,device|False|读取|
|系统接口|GET|`/api/v1/health`|系统健康|public|False|读取|
|AOI接口|POST|`/api/v1/inference/aoi`|旧单帧 AOI 接口|admin,operator|True|request_id|
|EA/Bias参数预测接口|POST|`/api/v1/inference/ea-bias`|批量 EA/Bias 参数推荐|admin,operator|True|request_id|
|EA/Bias参数预测接口|POST|`/api/v1/inference/ea-bias/folder`|文件夹批量预测|admin,operator,device|True|request_id|
|EA/Bias参数预测接口|POST|`/api/v1/inference/ea-bias/scan`|自动识别文件夹|admin,operator,device|False|request_id|
|EA/Bias参数预测接口|GET|`/api/v1/inference/ea-bias/sources`|输入文件夹数据源|admin,operator,device|False|读取|
|EA/Bias参数预测接口|POST|`/api/v1/inference/ea-bias/sources`|MES 登记输入目录|admin,device|False|request_id|
|日志接口|GET|`/api/v1/inference/ea-bias/sources/{source_id}/logs`|MES 来源接入日志|admin,operator,device|False|读取|
|多模态分级接口|POST|`/api/v1/inference/grading`|合格产品多模态分级|admin,operator,device|True|request_id|
|系统|GET|`/api/v1/live`|服务存活|public|False|读取|
|系统接口|GET|`/api/v1/models`|模型状态|admin,operator|False|读取|
|Qualification|GET|`/api/v1/products/{product_id}/qualifications`|产品证据历史|admin,operator,device|False|读取|
|Product Trace|GET|`/api/v1/products/{product_id}/trace`|产品追溯|admin,operator,device|False|读取|
|Qualification|GET|`/api/v1/qualifications`|合格证据列表|admin,operator,device|False|读取|
|多模态分级接口|POST|`/api/v1/qualifications`|登记合格证据|admin,operator|False|request_id|
|多模态分级接口|GET|`/api/v1/qualifications/{qualification_id}`|查询合格证据|admin,operator,device|False|读取|
|Logs|GET|`/api/v1/qualifications/{qualification_id}/history`|证据审计|admin,operator,device|False|读取|
|Qualification|POST|`/api/v1/qualifications/{qualification_id}/replace`|替换证据|admin,operator|False|request_id|
|Qualification|POST|`/api/v1/qualifications/{qualification_id}/revoke`|撤销证据|admin,operator|False|request_id|
|系统接口|GET|`/api/v1/ready`|生产就绪|public|False|读取|
|系统|GET|`/api/v1/ready/{stage}`|阶段就绪|public|False|读取|
|Review|GET|`/api/v1/reviews`|复核历史|admin,operator|False|读取|
|Review|POST|`/api/v1/reviews`|登记人工复核|admin,operator|False|request_id|
|Review|GET|`/api/v1/reviews/{review_id}`|复核详情|admin,operator|False|读取|
|系统|GET|`/api/v1/system/info`|运行信息|admin|False|读取|
|Task|GET|`/api/v1/tasks`|任务历史|admin,operator,device|False|读取|
|任务接口|POST|`/api/v1/tasks`|创建任务|admin,operator|False|request_id|
|任务接口|GET|`/api/v1/tasks/{task_id}`|任务状态|admin,operator,device|False|读取|
|任务接口|POST|`/api/v1/tasks/{task_id}/cancel`|取消推理任务|admin,operator,device|False|request_id|
|日志接口|GET|`/api/v1/tasks/{task_id}/logs`|任务日志|admin,operator,device|False|读取|
|任务接口|GET|`/api/v1/tasks/{task_id}/predictions`|分页预测结果|admin,operator,device|False|读取|
|任务接口|GET|`/api/v1/tasks/{task_id}/result`|任务结果|admin,operator,device|False|读取|
|Task|POST|`/api/v1/tasks/{task_id}/retry`|重试 Stage2|admin,operator,device|True|request_id|
|调测接口|GET|`/automl/tasks/{task_id}`|旧训练状态|admin,operator|False|读取|
|调测接口|POST|`/automl/tasks/{task_id}/cancel`|取消训练任务|admin|False|request_id|
|调测接口|GET|`/automl/tasks/{task_id}/result`|旧训练结果|admin,operator|False|读取|
|调测接口|POST|`/automl/train`|旧单通道 AutoML 训练|admin|True|request_id|
|调测接口|POST|`/automl/train-all`|旧多通道 AutoML 训练|admin|True|request_id|
|Frontend|GET|`/gui/{filename}`|静态页面和资源|admin,operator|False|读取|
|系统接口|GET|`/health`|旧健康接口|public|False|读取|
|系统接口|GET|`/info`|旧模型概览|admin,operator|False|读取|
|EA/Bias参数预测接口|POST|`/predict`|旧数组预测|admin,operator|False|request_id|
|EA/Bias参数预测接口|POST|`/predict-file`|旧单文件预测|admin,operator|False|request_id|

### 旧服务入口 — `GET /`

用途：返回服务链接与旧状态。

角色：admin, operator。异步：False。源码：`backend_server.py:299`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "status": "ok",
  "service": "Liante MLP AutoML",
  "prediction_gui": "/gui/index.html",
  "automl_gui": "/gui/train.html"
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 服务排空 — `POST /api/v1/admin/drain`

用途：管理员停止接收新任务并关闭所有 AOI 流，等待在途任务完成后再停止进程。

角色：admin。异步：False。源码：`backend_server.py:261`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|request_id|body|{"type": "string", "minLength": 1, "maxLength": 200}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```text
POST /api/v1/admin/drain
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "draining": true,
    "aoi_streams": 0
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### api_v1.start_aoi_folder_run — `POST /api/v1/aoi/folder-runs`

用途：api_v1.start_aoi_folder_run

角色：admin, operator。异步：False。源码：`api_v1.py:493`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```json
{}
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### api_v1.aoi_folder_run_status — `GET /api/v1/aoi/folder-runs/{task_id}`

用途：api_v1.aoi_folder_run_status

角色：admin, operator。异步：False。源码：`api_v1.py:512`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/aoi/folder-runs/{task_id}
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 建立 AOI 流 — `POST /api/v1/aoi/streams`

用途：创建设备流，可持续接收多个产品；配置允许视角和默认必需视角。

角色：admin, operator, device。异步：False。源码：`api_v1.py:474`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Response_api_v1_start_aoi_stream`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|source_id|body|{"type": "string", "minLength": 1, "maxLength": 128}|True|见强类型 schema；未知字段拒绝|
|product_id|body|{"type": "string", "minLength": 1, "maxLength": 200}|True|见强类型 schema；未知字段拒绝|
|request_id|body|{"type": "string", "minLength": 1, "maxLength": 200}|False|见强类型 schema；未知字段拒绝|
|view_types|body|{"type": "array", "minItems": 1, "maxItems": 32, "uniqueItems": true, "items": {"type": "string", "minLength": 1, "maxLength": 64}}|False|见强类型 schema；未知字段拒绝|
|required_views|body|{"type": "array", "minItems": 1, "maxItems": 32, "uniqueItems": true, "items": {"type": "string", "minLength": 1, "maxLength": 64}}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{
  "source_id": "AOI-001",
  "product_id": "P-001",
  "view_types": [
    "TOP",
    "SIDE"
  ],
  "required_views": [
    "TOP",
    "SIDE"
  ],
  "request_id": "STREAM-001"
}
```

成功响应结构（HTTP 201，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "abc",
    "status": "RUNNING",
    "stream": {
      "queue_capacity": 8,
      "accepted": 0,
      "processed": 0,
      "rejected": 0
    }
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：201, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### AOI 流状态 — `GET /api/v1/aoi/streams/{task_id}`

用途：查询接收、处理、失败、拒收、队列和处理速率。

角色：admin, operator, device。异步：False。源码：`api_v1.py:538`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_api_v1_aoi_stream_status`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/aoi/streams/abc
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "abc",
    "status": "RUNNING",
    "stream": {
      "accepted": 10,
      "processed": 8,
      "failed": 0,
      "rejected": 2,
      "queue_depth": 1,
      "in_flight": true
    }
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 停止 AOI 流 — `POST /api/v1/aoi/streams/{task_id}/close`

用途：停止接收新帧，并处理完已接收队列。

角色：admin, operator, device。异步：True。源码：`api_v1.py:586`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Response_api_v1_close_aoi_stream`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|request_id|body|{"type": "string", "minLength": 1, "maxLength": 200}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```text
POST /api/v1/aoi/streams/abc/close
```

成功响应结构（HTTP 202，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "0123456789abcdef0123456789abcdef",
    "status": "QUEUED"
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：202, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 逐帧结果 — `GET /api/v1/aoi/streams/{task_id}/frames`

用途：分页查询持久化 AOI 逐帧结果。

角色：admin, operator, device。异步：False。源码：`api_v1.py:551`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|limit|query|{"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}|False|每页 1..1000|
|offset|query|{"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0}|False|从 0 开始的偏移|
|page|query|{"type": "integer", "minimum": 1}|False|页码从 1 开始；与 limit/offset 互斥|
|page_size|query|{"type": "integer", "minimum": 1, "maximum": 1000}|False|分页条数，与 page 配合|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_api_v1_aoi_frame_records`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/aoi/streams/abc/frames
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "total": 0,
    "limit": 100,
    "offset": 0
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 连续推送 AOI 帧 — `POST /api/v1/aoi/streams/{task_id}/frames`

用途：先持久化帧和产品元数据再 ACK；相同序号、产品、校验和、视角重放，冲突 409。

角色：admin, operator, device。异步：False。源码：`api_v1.py:519`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Response_api_v1_push_aoi_frame`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|image|body|{"type": "string", "format": "binary"}|True|见强类型 schema；未知字段拒绝|
|sequence|body|{"type": "integer", "minimum": 1}|False|见强类型 schema；未知字段拒绝|
|frame_seq|body|{"type": "integer", "minimum": 1}|False|见强类型 schema；未知字段拒绝|
|product_id|body|{"type": "string", "minLength": 1, "maxLength": 200}|False|见强类型 schema；未知字段拒绝|
|view_type|body|{"type": "string", "maxLength": 64}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```text
multipart: sequence=1, image=@frame-1.jpg
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "abc",
    "sequence": 1,
    "status": "QUEUED",
    "stream": {
      "queue_depth": 1
    }
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 历史原图 — `GET /api/v1/aoi/streams/{task_id}/frames/{sequence}/image`

用途：在保留期限内读取指定原始帧。

角色：admin, operator, device。异步：False。源码：`api_v1.py:562`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|sequence|path|{"type": "integer", "minimum": 1}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/aoi/streams/abc/frames/1/image
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
"<image bytes>"
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 最新 AOI 帧 — `GET /api/v1/aoi/streams/{task_id}/latest-frame`

用途：读取当前流最近接收的原始帧，供监控页面预览。

角色：admin, operator, device。异步：False。源码：`api_v1.py:575`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/aoi/streams/abc/latest-frame
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
"<image bytes>"
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 流内产品 — `GET /api/v1/aoi/streams/{task_id}/products`

用途：分页查询产品生命周期、帧状态统计和缺失视角。

角色：admin, operator, device。异步：False。源码：`api\operations.py:115`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|limit|query|{"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}|False|每页 1..1000|
|offset|query|{"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0}|False|从 0 开始的偏移|
|page|query|{"type": "integer", "minimum": 1}|False|页码从 1 开始；与 limit/offset 互斥|
|page_size|query|{"type": "integer", "minimum": 1, "maximum": 1000}|False|分页条数，与 page 配合|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_operations_stream_products`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/aoi/streams/{task_id}/products
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "total": 0,
    "limit": 100,
    "offset": 0
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 建立产品 — `POST /api/v1/aoi/streams/{task_id}/products`

用途：在流内建立独立产品并确定 required_views，不能修改已接收产品的要求。

角色：admin, operator, device。异步：False。源码：`api\operations.py:109`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Response_operations_open_product`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|product_id|body|{"type": "string", "minLength": 1, "maxLength": 200}|True|见强类型 schema；未知字段拒绝|
|required_views|body|{"type": "array", "minItems": 1, "maxItems": 32, "uniqueItems": true, "items": {"type": "string", "minLength": 1, "maxLength": 64}}|False|见强类型 schema；未知字段拒绝|
|request_id|body|{"type": "string", "minLength": 1, "maxLength": 200}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{
  "product_id": "填写实际product_id"
}
```

成功响应结构（HTTP 201，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：201, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 产品状态 — `GET /api/v1/aoi/streams/{task_id}/products/{product_id}`

用途：查看流内单个产品，独立于流任务状态。

角色：admin, operator, device。异步：False。源码：`api\operations.py:121`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|product_id|path|{"type": "string", "minLength": 1, "maxLength": 200}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_operations_stream_product`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/aoi/streams/{task_id}/products/{product_id}
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 关闭产品 — `POST /api/v1/aoi/streams/{task_id}/products/{product_id}/close`

用途：停止接收该产品的新帧，流继续接收其他产品；缺视角为 INCOMPLETE，缺模型帧为 FAILED。

角色：admin, operator, device。异步：False。源码：`api\operations.py:126`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|product_id|path|{"type": "string", "minLength": 1, "maxLength": 200}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Response_operations_close_product`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|request_id|body|{"type": "string", "minLength": 1, "maxLength": 200}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{}
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 追溯 JSON — `GET /api/v1/exports/products/{product_id}/trace.json`

用途：导出当前追溯分页，最多每类 200 条。

角色：admin, operator, device。异步：False。源码：`api\operations.py:233`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|product_id|path|{"type": "string", "minLength": 1, "maxLength": 200}|True|资源标识；必须符合归属权限|
|limit|query|{"type": "integer", "minimum": 1, "maximum": 200, "default": 100}|False|每页 1..200|
|offset|query|{"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0}|False|从 0 开始的偏移|
|page|query|{"type": "integer", "minimum": 1}|False|页码从 1 开始；与 limit/offset 互斥|
|page_size|query|{"type": "integer", "minimum": 1, "maximum": 200}|False|分页条数，与 page 配合|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_operations_export_trace`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/exports/products/{product_id}/trace.json
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "total": 0,
    "limit": 100,
    "offset": 0
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 证据 JSON — `GET /api/v1/exports/qualifications/{qualification_id}.json`

用途：导出单个有界证据对象；设备归属检查与查询相同。

角色：admin, operator, device。异步：False。源码：`api\operations.py:228`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|qualification_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_operations_export_qualification`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/exports/qualifications/{qualification_id}.json
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 任务 CSV — `GET /api/v1/exports/tasks.csv`

用途：导出匹配筛选的当前页，最多 1000 条，防护电子表格公式注入。

角色：admin, operator, device。异步：False。源码：`api\operations.py:213`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|limit|query|{"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}|False|每页 1..1000|
|offset|query|{"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0}|False|从 0 开始的偏移|
|page|query|{"type": "integer", "minimum": 1}|False|页码从 1 开始；与 limit/offset 互斥|
|page_size|query|{"type": "integer", "minimum": 1, "maximum": 1000}|False|分页条数，与 page 配合|
|product_id|query|{"type": "string", "maxLength": 200}|False|可选筛选；时间使用带时区 ISO 8601|
|task_id|query|{"type": "string", "maxLength": 200}|False|可选筛选；时间使用带时区 ISO 8601|
|stage|query|{"type": "string", "maxLength": 200}|False|可选筛选；时间使用带时区 ISO 8601|
|status|query|{"type": "string", "maxLength": 200}|False|可选筛选；时间使用带时区 ISO 8601|
|channel|query|{"type": "integer", "minimum": 0, "maximum": 7}|False|可选筛选；时间使用带时区 ISO 8601|
|source_id|query|{"type": "string", "maxLength": 200}|False|可选筛选；时间使用带时区 ISO 8601|
|from_time|query|{"type": "string", "maxLength": 200, "format": "date-time"}|False|可选筛选；时间使用带时区 ISO 8601|
|to_time|query|{"type": "string", "maxLength": 200, "format": "date-time"}|False|可选筛选；时间使用带时区 ISO 8601|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/exports/tasks.csv
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "total": 0,
    "limit": 100,
    "offset": 0
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 预测 CSV — `GET /api/v1/exports/tasks/{task_id}/predictions.csv`

用途：按任务、文件和通道导出最多 1000 条真实已落盘预测行。

角色：admin, operator, device。异步：False。源码：`api\operations.py:219`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|limit|query|{"type": "integer", "minimum": 1, "maximum": 1000, "default": 1000}|False|每页 1..1000|
|offset|query|{"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0}|False|从 0 开始的偏移|
|page|query|{"type": "integer", "minimum": 1}|False|页码从 1 开始；与 limit/offset 互斥|
|page_size|query|{"type": "integer", "minimum": 1, "maximum": 1000}|False|分页条数，与 page 配合|
|file_index|query|{"type": "integer", "minimum": 1}|False|可选筛选；时间使用带时区 ISO 8601|
|channel|query|{"type": "integer", "minimum": 0, "maximum": 7}|False|可选筛选；时间使用带时区 ISO 8601|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/exports/tasks/{task_id}/predictions.csv
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "total": 0,
    "limit": 100,
    "offset": 0
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 系统健康 — `GET /api/v1/health`

用途：检查 WebServer、三阶段模型、GPU 和任务存储。

角色：public。异步：False。源码：`api_v1.py:113`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|

响应字段契约：`Response_api_v1_health`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|无|—|—|—|无请求参数|

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/health
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "web": "ok",
    "stage1": "not_configured",
    "stage2": "model_missing",
    "stage3": "not_configured",
    "database": "sqlite"
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 旧单帧 AOI 接口 — `POST /api/v1/inference/aoi`

用途：旧客户端上传单张图片或引用服务器图片；新产线接入使用 AOI 流。

角色：admin, operator。异步：True。源码：`api_v1.py:407`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Response_api_v1_aoi`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|product_id|body|{"type": "string", "maxLength": 200}|True|见强类型 schema；未知字段拒绝|
|image_path|body|{"type": "string", "maxLength": 1000}|False|见强类型 schema；未知字段拒绝|
|request_id|body|{"type": "string", "maxLength": 200}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```text
multipart: product_id=P-001, image=@aoi.png
```

成功响应结构（HTTP 202，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "0123456789abcdef0123456789abcdef",
    "status": "QUEUED"
  }
}
```

失败响应示例（HTTP 503）：

```json
{
  "code": "MODEL_NOT_CONFIGURED",
  "message": "模型未配置",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：202, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 批量 EA/Bias 参数推荐 — `POST /api/v1/inference/ea-bias`

用途：对有序文件列表及每文件的有序通道执行真实 Stage2 推理。

角色：admin, operator。异步：True。源码：`api_v1.py:376`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Response_api_v1_ea_bias`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|product_id|body|{"minLength": 1, "title": "Product Id", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|request_id|body|{"anyOf": [{"type": "string"}, {"type": "null"}], "default": null, "title": "Request Id"}|False|见强类型 schema；未知字段拒绝|
|files|body|{"items": {"$ref": "#/components/schemas/BatchFile"}, "maxItems": 100, "minItems": 1, "title": "Files", "type": "array"}|True|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{
  "product_id": "P-001",
  "files": [
    {
      "file_path": "D:/data/sample.csv",
      "channels": [
        0,
        1
      ]
    }
  ]
}
```

成功响应结构（HTTP 202，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "0123456789abcdef0123456789abcdef",
    "status": "QUEUED"
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：202, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 文件夹批量预测 — `POST /api/v1/inference/ea-bias/folder`

用途：重新识别文件夹并异步处理全部数据文件，无需提交特征、文件路径或通道。

角色：admin, operator, device。异步：True。源码：`api_v1.py:301`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Response_api_v1_predict_stage2_folder`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|source_id|body|{"maxLength": 100, "minLength": 1, "title": "Source Id", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|recursive|body|{"default": true, "title": "Recursive", "type": "boolean"}|False|见强类型 schema；未知字段拒绝|
|product_id|body|{"anyOf": [{"maxLength": 200, "type": "string"}, {"type": "null"}], "default": null, "title": "Product Id"}|False|见强类型 schema；未知字段拒绝|
|request_id|body|{"anyOf": [{"maxLength": 200, "type": "string"}, {"type": "null"}], "default": null, "title": "Request Id"}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{
  "source_id": "input-1"
}
```

成功响应结构（HTTP 202，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "0123456789abcdef0123456789abcdef",
    "status": "QUEUED"
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：202, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 自动识别文件夹 — `POST /api/v1/inference/ea-bias/scan`

用途：有界扫描 CSV/XLSX，按文件名或子目录自动识别通道。

角色：admin, operator, device。异步：False。源码：`api_v1.py:288`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|source_id|body|{"maxLength": 100, "minLength": 1, "title": "Source Id", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|recursive|body|{"default": true, "title": "Recursive", "type": "boolean"}|False|见强类型 schema；未知字段拒绝|
|product_id|body|{"anyOf": [{"maxLength": 200, "type": "string"}, {"type": "null"}], "default": null, "title": "Product Id"}|False|见强类型 schema；未知字段拒绝|
|request_id|body|{"anyOf": [{"maxLength": 200, "type": "string"}, {"type": "null"}], "default": null, "title": "Request Id"}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{
  "source_id": "input-1",
  "recursive": true
}
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "source_id": "input-1",
    "directory": "F:/data",
    "recursive": true,
    "total_files": 1,
    "ready_files": 1,
    "missing_model_files": 0,
    "invalid_files": 0,
    "ignored_entries": 0,
    "files": [
      {
        "file_name": "channel_0.csv",
        "file_path": "F:/data/channel_0.csv",
        "channel": 0,
        "size_bytes": 128,
        "mtime_ns": 1000000,
        "status": "ready",
        "message": "可预测"
      }
    ]
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 输入文件夹数据源 — `GET /api/v1/inference/ea-bias/sources`

用途：查询已配置的 Stage2 输入目录及就绪模型通道。

角色：admin, operator, device。异步：False。源码：`api_v1.py:214`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_api_v1_stage2_sources`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/inference/ea-bias/sources
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "sources": [
      {
        "source_id": "input-1",
        "directory": "F:/npfcode/liante_old/train_data/data_v5/split_by_channel",
        "exists": true
      }
    ],
    "model_channels": [
      0,
      1,
      3,
      4,
      5
    ]
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### MES 登记输入目录 — `POST /api/v1/inference/ea-bias/sources`

用途：登记或修改 C209 可访问的目录，配置持久化并记录变更日志。

角色：admin, device。异步：False。源码：`api_v1.py:247`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Response_api_v1_configure_stage2_source`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|source_id|body|{"maxLength": 100, "minLength": 1, "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$", "title": "Source Id", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|directory|body|{"maxLength": 1000, "minLength": 1, "title": "Directory", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|owner_device_id|body|{"anyOf": [{"maxLength": 128, "minLength": 1, "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$", "type": "string"}, {"type": "null"}], "default": null, "title": "Owner Device Id"}|False|见强类型 schema；未知字段拒绝|
|expected_revision|body|{"anyOf": [{"minimum": 0, "type": "integer"}, {"type": "null"}], "default": null, "title": "Expected Revision"}|False|见强类型 schema；未知字段拒绝|
|request_id|body|{"anyOf": [{"maxLength": 200, "minLength": 1, "type": "string"}, {"type": "null"}], "default": null, "title": "Request Id"}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{
  "source_id": "MES-LINE-01",
  "directory": "F:/npfcode/liante_old/train_data/data_v5/split_by_channel",
  "expected_revision": 0,
  "request_id": "config-001"
}
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "source_id": "MES-LINE-01",
    "directory": "F:/npfcode/liante_old/train_data/data_v5/split_by_channel",
    "revision": 1,
    "owner_device_id": "MES-01",
    "exists": true
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### MES 来源接入日志 — `GET /api/v1/inference/ea-bias/sources/{source_id}/logs`

用途：分页读取目录变更、扫描、预测提交、重复请求及拒绝记录；最新记录在前。

角色：admin, operator, device。异步：False。源码：`api_v1.py:267`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|source_id|path|{"type": "string"}|True|资源标识；必须符合归属权限|
|limit|query|{"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}|False|每页 1..1000|
|offset|query|{"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0}|False|从 0 开始的偏移|
|page|query|{"type": "integer", "minimum": 1}|False|页码从 1 开始；与 limit/offset 互斥|
|page_size|query|{"type": "integer", "minimum": 1, "maximum": 1000}|False|分页条数，与 page 配合|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_api_v1_stage2_source_logs`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/inference/ea-bias/sources/MES-LINE-01/logs?limit=100
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "total": 0,
    "limit": 100,
    "offset": 0
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 合格产品多模态分级 — `POST /api/v1/inference/grading`

用途：使用服务端 ACTIVE 且当前有效的证据，入队与执行时复查。真实 Stage3 模型 NOT CONFIGURED 时返回 503，不产生等级。

角色：admin, operator, device。异步：True。源码：`api_v1.py:593`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Response_api_v1_grading`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|product_id|body|{"type": "string", "minLength": 1, "maxLength": 200}|True|见强类型 schema；未知字段拒绝|
|qualification_id|body|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|见强类型 schema；未知字段拒绝|
|request_id|body|{"type": "string", "minLength": 1, "maxLength": 200}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{
  "product_id": "P-001",
  "qualification_id": "registered-evidence-id"
}
```

成功响应结构（HTTP 202，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "0123456789abcdef0123456789abcdef",
    "status": "QUEUED"
  }
}
```

失败响应示例（HTTP 503）：

```json
{
  "code": "MODEL_NOT_CONFIGURED",
  "message": "模型未配置",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：202, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 服务存活 — `GET /api/v1/live`

用途：仅检测 Web 进程存活；不依赖任何模型。

角色：public。异步：False。源码：`api\operations.py:43`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|

响应字段契约：`Response_operations_live`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|无|—|—|—|无请求参数|

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/live
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 模型状态 — `GET /api/v1/models`

用途：列出各阶段和 0–7 通道模型状态。

角色：admin, operator。异步：False。源码：`api_v1.py:124`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/models
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": [
    {
      "stage": "stage2",
      "channel": 0,
      "model_name": "MLP",
      "model_version": "artifact",
      "model_path": "saved_models/channel_0",
      "loaded": false,
      "status": "ok",
      "recipe_version": "unconfigured"
    }
  ]
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 产品证据历史 — `GET /api/v1/products/{product_id}/qualifications`

用途：分页读取同一产品的 ACTIVE、SUPERSEDED、REVOKED、EXPIRED 证据。

角色：admin, operator, device。异步：False。源码：`api\operations.py:134`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|product_id|path|{"type": "string", "minLength": 1, "maxLength": 200}|True|资源标识；必须符合归属权限|
|limit|query|{"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}|False|每页 1..1000|
|offset|query|{"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0}|False|从 0 开始的偏移|
|page|query|{"type": "integer", "minimum": 1}|False|页码从 1 开始；与 limit/offset 互斥|
|page_size|query|{"type": "integer", "minimum": 1, "maximum": 1000}|False|分页条数，与 page 配合|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_operations_product_qualifications`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/products/{product_id}/qualifications
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "total": 0,
    "limit": 100,
    "offset": 0
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 产品追溯 — `GET /api/v1/products/{product_id}/trace`

用途：分页返回各阶段任务、流内产品、证据版本、复核关联与时间；不携带大型预测数组。

角色：admin, operator, device。异步：False。源码：`api\operations.py:199`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|product_id|path|{"type": "string", "minLength": 1, "maxLength": 200}|True|资源标识；必须符合归属权限|
|limit|query|{"type": "integer", "minimum": 1, "maximum": 200, "default": 50}|False|每页 1..200|
|offset|query|{"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0}|False|从 0 开始的偏移|
|page|query|{"type": "integer", "minimum": 1}|False|页码从 1 开始；与 limit/offset 互斥|
|page_size|query|{"type": "integer", "minimum": 1, "maximum": 200}|False|分页条数，与 page 配合|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_operations_product_trace`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/products/{product_id}/trace
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "total": 0,
    "limit": 100,
    "offset": 0
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 合格证据列表 — `GET /api/v1/qualifications`

用途：分页读取版本化证据；设备只读取归属自己的记录。

角色：admin, operator, device。异步：False。源码：`api\operations.py:129`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|limit|query|{"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}|False|每页 1..1000|
|offset|query|{"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0}|False|从 0 开始的偏移|
|page|query|{"type": "integer", "minimum": 1}|False|页码从 1 开始；与 limit/offset 互斥|
|page_size|query|{"type": "integer", "minimum": 1, "maximum": 1000}|False|分页条数，与 page 配合|
|product_id|query|{"type": "string", "maxLength": 200}|False|可选筛选；时间使用带时区 ISO 8601|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_operations_qualification_history`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/qualifications
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "total": 0,
    "limit": 100,
    "offset": 0
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 登记合格证据 — `POST /api/v1/qualifications`

用途：认证管理员或操作员登记有界人工测量；验证真实完成的 AOI 产品与 Stage2、产品、时间和配方。复核身份由服务器绑定。

角色：admin, operator。异步：False。源码：`api_v1.py:653`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Response_api_v1_create_qualification`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|product_id|body|{"maxLength": 200, "minLength": 1, "title": "Product Id", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|aoi_task_id|body|{"pattern": "^[a-f0-9]{32}$", "title": "Aoi Task Id", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|stage2_task_id|body|{"pattern": "^[a-f0-9]{32}$", "title": "Stage2 Task Id", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|optical_features|body|{"$ref": "#/components/schemas/Optical"}|True|见强类型 schema；未知字段拒绝|
|electrical_features|body|{"$ref": "#/components/schemas/Electrical"}|True|见强类型 schema；未知字段拒绝|
|tuning_result|body|{"$ref": "#/components/schemas/Tuning"}|True|见强类型 schema；未知字段拒绝|
|tuning_parameters|body|{"anyOf": [{"$ref": "#/components/schemas/TuningParameters"}, {"type": "null"}], "default": null}|False|见强类型 schema；未知字段拒绝|
|manual_review|body|{"$ref": "#/components/schemas/Manual"}|True|见强类型 schema；未知字段拒绝|
|recipe_id|body|{"maxLength": 128, "minLength": 1, "title": "Recipe Id", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|recipe_version|body|{"maxLength": 128, "minLength": 1, "title": "Recipe Version", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|source_id|body|{"anyOf": [{"maxLength": 128, "type": "string"}, {"type": "null"}], "default": null, "title": "Source Id"}|False|见强类型 schema；未知字段拒绝|
|request_id|body|{"anyOf": [{"maxLength": 200, "minLength": 1, "type": "string"}, {"type": "null"}], "default": null, "title": "Request Id"}|False|见强类型 schema；未知字段拒绝|
|notes|body|{"default": "", "maxLength": 2000, "title": "Notes", "type": "string"}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{
  "product_id": "P-001",
  "aoi_task_id": "0123456789abcdef0123456789abcdef",
  "stage2_task_id": "1123456789abcdef0123456789abcdef",
  "recipe_id": "实际配方编号",
  "recipe_version": "实际版本",
  "optical_features": {
    "qualified": true,
    "measurements": [
      {
        "name": "power",
        "value": 2.1,
        "unit": "dBm"
      }
    ]
  },
  "electrical_features": {
    "qualified": true,
    "measurements": [
      {
        "name": "voltage",
        "value": 3.3,
        "unit": "V"
      }
    ]
  },
  "tuning_result": {
    "status": "complete"
  },
  "manual_review": {
    "decision": "qualified",
    "measured_at": "2026-10-01T08:00:00+08:00",
    "notes": "实际仪器、工单和人工确认说明"
  }
}
```

成功响应结构（HTTP 201，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "qualification_id": "..."
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：201, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 查询合格证据 — `GET /api/v1/qualifications/{qualification_id}`

用途：读取服务端登记的合格证据。

角色：admin, operator, device。异步：False。源码：`api_v1.py:670`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|qualification_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_api_v1_get_qualification`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/qualifications/abc
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "qualification_id": "abc"
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 证据审计 — `GET /api/v1/qualifications/{qualification_id}/history`

用途：分页读取创建、过期、撤销、替换和复核事件。

角色：admin, operator, device。异步：False。源码：`api\operations.py:152`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|qualification_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|limit|query|{"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}|False|每页 1..1000|
|offset|query|{"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0}|False|从 0 开始的偏移|
|page|query|{"type": "integer", "minimum": 1}|False|页码从 1 开始；与 limit/offset 互斥|
|page_size|query|{"type": "integer", "minimum": 1, "maximum": 1000}|False|分页条数，与 page 配合|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_operations_evidence_history`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/qualifications/{qualification_id}/history
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "total": 0,
    "limit": 100,
    "offset": 0
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 替换证据 — `POST /api/v1/qualifications/{qualification_id}/replace`

用途：原 ACTIVE 证据与新证据同事务替换；新证据仍须通过全部校验。

角色：admin, operator。异步：False。源码：`api\operations.py:144`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|qualification_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Response_operations_replace`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|product_id|body|{"maxLength": 200, "minLength": 1, "title": "Product Id", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|aoi_task_id|body|{"pattern": "^[a-f0-9]{32}$", "title": "Aoi Task Id", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|stage2_task_id|body|{"pattern": "^[a-f0-9]{32}$", "title": "Stage2 Task Id", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|optical_features|body|{"$ref": "#/components/schemas/Optical"}|True|见强类型 schema；未知字段拒绝|
|electrical_features|body|{"$ref": "#/components/schemas/Electrical"}|True|见强类型 schema；未知字段拒绝|
|tuning_result|body|{"$ref": "#/components/schemas/Tuning"}|True|见强类型 schema；未知字段拒绝|
|tuning_parameters|body|{"anyOf": [{"$ref": "#/components/schemas/TuningParameters"}, {"type": "null"}], "default": null}|False|见强类型 schema；未知字段拒绝|
|manual_review|body|{"$ref": "#/components/schemas/Manual"}|True|见强类型 schema；未知字段拒绝|
|recipe_id|body|{"maxLength": 128, "minLength": 1, "title": "Recipe Id", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|recipe_version|body|{"maxLength": 128, "minLength": 1, "title": "Recipe Version", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|source_id|body|{"anyOf": [{"maxLength": 128, "type": "string"}, {"type": "null"}], "default": null, "title": "Source Id"}|False|见强类型 schema；未知字段拒绝|
|request_id|body|{"anyOf": [{"maxLength": 200, "minLength": 1, "type": "string"}, {"type": "null"}], "default": null, "title": "Request Id"}|False|见强类型 schema；未知字段拒绝|
|notes|body|{"default": "", "maxLength": 2000, "title": "Notes", "type": "string"}|False|见强类型 schema；未知字段拒绝|
|reason|body|{"type": "string", "minLength": 1, "maxLength": 2000}|True|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{
  "product_id": "填写实际product_id",
  "aoi_task_id": "填写实际aoi_task_id",
  "stage2_task_id": "填写实际stage2_task_id",
  "optical_features": "填写实际optical_features",
  "electrical_features": "填写实际electrical_features",
  "tuning_result": "填写实际tuning_result",
  "manual_review": "填写实际manual_review",
  "recipe_id": "填写实际recipe_id",
  "recipe_version": "填写实际recipe_version",
  "reason": "填写实际reason"
}
```

成功响应结构（HTTP 201，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：201, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 撤销证据 — `POST /api/v1/qualifications/{qualification_id}/revoke`

用途：记录身份和原因；已撤销重放，已替换证据不能再次撤销。

角色：admin, operator。异步：False。源码：`api\operations.py:139`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|qualification_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Response_operations_revoke`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|reason|body|{"maxLength": 2000, "minLength": 1, "title": "Reason", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|request_id|body|{"anyOf": [{"maxLength": 200, "minLength": 1, "type": "string"}, {"type": "null"}], "default": null, "title": "Request Id"}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{
  "reason": "填写实际reason"
}
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 生产就绪 — `GET /api/v1/ready`

用途：三阶段模型就绪且无队列或存储告警时返回 200。

角色：public。异步：False。源码：`api_v1.py:117`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|

响应字段契约：`Response_api_v1_ready`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|无|—|—|—|无请求参数|

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/ready
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "ready": true
  }
}
```

失败响应示例（HTTP 503）：

```json
{
  "code": "NOT_READY",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 阶段就绪 — `GET /api/v1/ready/{stage}`

用途：独立检查阶段模型、排空和临界磁盘状态；未就绪 503。

角色：public。异步：False。源码：`api\operations.py:46`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|stage|path|{"type": "string", "enum": ["stage1", "stage2", "stage3"]}|True|资源标识；必须符合归属权限|

响应字段契约：`Response_operations_stage_ready`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/ready/{stage}
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

失败响应示例（HTTP 503）：

```json
{
  "code": "NOT_READY",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 复核历史 — `GET /api/v1/reviews`

用途：按产品分页读取不可覆盖的复核决定。

角色：admin, operator。异步：False。源码：`api\operations.py:161`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|limit|query|{"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}|False|每页 1..1000|
|offset|query|{"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0}|False|从 0 开始的偏移|
|page|query|{"type": "integer", "minimum": 1}|False|页码从 1 开始；与 limit/offset 互斥|
|page_size|query|{"type": "integer", "minimum": 1, "maximum": 1000}|False|分页条数，与 page 配合|
|product_id|query|{"type": "string", "maxLength": 200}|False|可选筛选；时间使用带时区 ISO 8601|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_operations_review_list`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/reviews
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "total": 0,
    "limit": 100,
    "offset": 0
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 登记人工复核 — `POST /api/v1/reviews`

用途：保存认证身份、决定、说明和关联记录。真实分级结果缺失时等级必须为空，复核不生成模型结果。

角色：admin, operator。异步：False。源码：`api\operations.py:158`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Response_operations_create_review`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|product_id|body|{"maxLength": 200, "minLength": 1, "title": "Product Id", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|qualification_id|body|{"anyOf": [{"pattern": "^[a-f0-9]{32}$", "type": "string"}, {"type": "null"}], "default": null, "title": "Qualification Id"}|False|见强类型 schema；未知字段拒绝|
|stage3_task_id|body|{"anyOf": [{"pattern": "^[a-f0-9]{32}$", "type": "string"}, {"type": "null"}], "default": null, "title": "Stage3 Task Id"}|False|见强类型 schema；未知字段拒绝|
|decision|body|{"enum": ["APPROVE", "REJECT"], "title": "Decision", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|comment|body|{"maxLength": 2000, "minLength": 1, "title": "Comment", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|final_grade|body|{"anyOf": [{"enum": ["A", "B", "C", "D"], "type": "string"}, {"type": "null"}], "default": null, "title": "Final Grade"}|False|见强类型 schema；未知字段拒绝|
|request_id|body|{"anyOf": [{"maxLength": 200, "minLength": 1, "type": "string"}, {"type": "null"}], "default": null, "title": "Request Id"}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{
  "product_id": "P-001",
  "qualification_id": null,
  "stage3_task_id": null,
  "decision": "APPROVE",
  "comment": "实际人工复核说明；尚无模型等级",
  "final_grade": null,
  "request_id": "REVIEW-001"
}
```

成功响应结构（HTTP 201，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "review_id": "0123456789abcdef0123456789abcdef",
    "suggested_grade": null,
    "final_grade": null,
    "reviewer": "operator",
    "decision": "APPROVE"
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：201, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 复核详情 — `GET /api/v1/reviews/{review_id}`

用途：读取单次复核记录。

角色：admin, operator。异步：False。源码：`api\operations.py:166`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|review_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_operations_review_get`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/reviews/{review_id}
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 运行信息 — `GET /api/v1/system/info`

用途：管理员读取版本、数据库版本、队列、磁盘和阶段状态；不返回秘密或绝对路径。

角色：admin。异步：False。源码：`api\operations.py:55`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_operations_system_info`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/system/info
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 任务历史 — `GET /api/v1/tasks`

用途：按产品、任务、阶段、状态、通道、来源与时间筛选；服务端分页。

角色：admin, operator, device。异步：False。源码：`api\operations.py:75`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|limit|query|{"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}|False|每页 1..1000|
|offset|query|{"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0}|False|从 0 开始的偏移|
|page|query|{"type": "integer", "minimum": 1}|False|页码从 1 开始；与 limit/offset 互斥|
|page_size|query|{"type": "integer", "minimum": 1, "maximum": 1000}|False|分页条数，与 page 配合|
|product_id|query|{"type": "string", "maxLength": 200}|False|可选筛选；时间使用带时区 ISO 8601|
|task_id|query|{"type": "string", "maxLength": 200}|False|可选筛选；时间使用带时区 ISO 8601|
|stage|query|{"type": "string", "maxLength": 200}|False|可选筛选；时间使用带时区 ISO 8601|
|status|query|{"type": "string", "maxLength": 200}|False|可选筛选；时间使用带时区 ISO 8601|
|channel|query|{"type": "integer", "minimum": 0, "maximum": 7}|False|可选筛选；时间使用带时区 ISO 8601|
|source_id|query|{"type": "string", "maxLength": 200}|False|可选筛选；时间使用带时区 ISO 8601|
|from_time|query|{"type": "string", "maxLength": 200, "format": "date-time"}|False|可选筛选；时间使用带时区 ISO 8601|
|to_time|query|{"type": "string", "maxLength": 200, "format": "date-time"}|False|可选筛选；时间使用带时区 ISO 8601|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_operations_task_history`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/tasks
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "total": 0,
    "limit": 100,
    "offset": 0
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 创建任务 — `POST /api/v1/tasks`

用途：创建 SQLite 权威任务记录，不执行模型；外部预建任务不等于推理成功。

角色：admin, operator。异步：False。源码：`api_v1.py:136`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Response_api_v1_create_task`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|stage|body|{"type": "string", "enum": ["stage1", "stage2", "stage3", "automl"]}|True|见强类型 schema；未知字段拒绝|
|product_id|body|{"type": "string", "minLength": 1, "maxLength": 200}|True|见强类型 schema；未知字段拒绝|
|request_id|body|{"type": "string", "minLength": 1, "maxLength": 200}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{
  "stage": "stage2",
  "product_id": "P-001",
  "request_id": "TASK-001"
}
```

成功响应结构（HTTP 201，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "...",
    "product_id": "P-001",
    "stage": "stage2",
    "status": "PENDING",
    "progress": 0
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：201, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 任务状态 — `GET /api/v1/tasks/{task_id}`

用途：读取 SQLite 任务；summary=1 隐藏 Stage2 的结果。

角色：admin, operator, device。异步：False。源码：`api_v1.py:147`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|summary|query|{"type": "string", "maxLength": 200}|False|可选筛选；时间使用带时区 ISO 8601|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_api_v1_task`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/tasks/abc
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "abc",
    "stage": "stage2",
    "status": "RUNNING",
    "current_file": "sample.csv",
    "file_index": 1,
    "file_total": 2,
    "current_channel": "CH0",
    "channel_index": 1,
    "channel_total": 2,
    "operation": "inference",
    "progress": 25
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 取消推理任务 — `POST /api/v1/tasks/{task_id}/cancel`

用途：取消排队或请求运行中的推理任务停止。

角色：admin, operator, device。异步：False。源码：`api_v1.py:189`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|request_id|body|{"type": "string", "minLength": 1, "maxLength": 200}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```text
POST /api/v1/tasks/abc/cancel
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "abc",
    "status": "CANCELLED"
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 任务日志 — `GET /api/v1/tasks/{task_id}/logs`

用途：SQLite 结构化日志按事件升序分页；异常详情在非调试环境隐藏。

角色：admin, operator, device。异步：False。源码：`api_v1.py:198`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|limit|query|{"type": "integer", "minimum": 1, "maximum": 1000, "default": 200}|False|每页 1..1000|
|offset|query|{"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0}|False|从 0 开始的偏移|
|page|query|{"type": "integer", "minimum": 1}|False|页码从 1 开始；与 limit/offset 互斥|
|page_size|query|{"type": "integer", "minimum": 1, "maximum": 1000}|False|分页条数，与 page 配合|
|stage|query|{"type": "string", "maxLength": 200}|False|可选筛选；时间使用带时区 ISO 8601|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_api_v1_logs`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/tasks/abc/logs?stage=stage2&limit=200
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "total": 0,
    "limit": 100,
    "offset": 0
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 分页预测结果 — `GET /api/v1/tasks/{task_id}/predictions`

用途：按文件序号、通道和偏移读取 Stage2 已落盘的预测行。

角色：admin, operator, device。异步：False。源码：`api_v1.py:172`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|limit|query|{"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}|False|每页 1..1000|
|offset|query|{"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0}|False|从 0 开始的偏移|
|page|query|{"type": "integer", "minimum": 1}|False|页码从 1 开始；与 limit/offset 互斥|
|page_size|query|{"type": "integer", "minimum": 1, "maximum": 1000}|False|分页条数，与 page 配合|
|file_index|query|{"type": "integer", "minimum": 1}|False|可选筛选；时间使用带时区 ISO 8601|
|channel|query|{"type": "integer", "minimum": 0, "maximum": 7}|False|可选筛选；时间使用带时区 ISO 8601|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_api_v1_paged_predictions`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/tasks/abc/predictions?file_index=1&channel=0&limit=100&offset=0
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [],
    "total": 0,
    "limit": 100,
    "offset": 0
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 任务结果 — `GET /api/v1/tasks/{task_id}/result`

用途：获取完成结果或未完成状态。

角色：admin, operator, device。异步：False。源码：`api_v1.py:156`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Response_api_v1_result`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /api/v1/tasks/abc/result
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "abc",
    "status": "COMPLETED",
    "result": {
      "files": []
    }
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 重试 Stage2 — `POST /api/v1/tasks/{task_id}/retry`

用途：失败、取消、超时的任务使用原输入快照创建新任务，保留 retry_of；文件变化会明确失败。

角色：admin, operator, device。异步：True。源码：`api\operations.py:80`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Response_operations_retry_task`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|request_id|body|{"type": "string", "minLength": 1, "maxLength": 200}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{}
```

成功响应结构（HTTP 202，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "0123456789abcdef0123456789abcdef",
    "status": "QUEUED"
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：202, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 旧训练状态 — `GET /automl/tasks/{task_id}`

用途：旧小写训练状态保持兼容，主存使用统一 SQLite 任务状态机。

角色：admin, operator。异步：False。源码：`backend_server.py:411`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /automl/tasks/abc
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "task_id": "abc",
  "status": "training",
  "channel": 0,
  "current_trial": 1,
  "total_trials": 2,
  "progress": 50
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 取消训练任务 — `POST /automl/tasks/{task_id}/cancel`

用途：取消排队或请求运行中的训练任务停止。

角色：admin。异步：False。源码：`backend_server.py:434`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|request_id|body|{"type": "string", "minLength": 1, "maxLength": 200}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```text
POST /automl/tasks/abc/cancel
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "task_id": "abc",
  "status": "cancelled"
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 旧训练结果 — `GET /automl/tasks/{task_id}/result`

用途：读取完成的训练指标与模型配置。

角色：admin, operator。异步：False。源码：`backend_server.py:418`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|task_id|path|{"type": "string", "pattern": "^[a-f0-9]{32}$"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /automl/tasks/abc/result
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "channel": 0,
  "model_config": {
    "model_type": "MLP"
  },
  "metrics": {
    "ea": {},
    "bias": {}
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 旧单通道 AutoML 训练 — `POST /automl/train`

用途：异步训练一个 MLP 通道。

角色：admin。异步：True。源码：`backend_server.py:379`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|channel|body|{"maximum": 7, "minimum": 0, "title": "Channel", "type": "integer"}|True|见强类型 schema；未知字段拒绝|
|n_trials|body|{"default": 30, "maximum": 500, "minimum": 1, "title": "N Trials", "type": "integer"}|False|见强类型 schema；未知字段拒绝|
|max_epochs|body|{"default": 500, "maximum": 10000, "minimum": 1, "title": "Max Epochs", "type": "integer"}|False|见强类型 schema；未知字段拒绝|
|patience|body|{"default": 40, "maximum": 1000, "minimum": 1, "title": "Patience", "type": "integer"}|False|见强类型 schema；未知字段拒绝|
|random_seed|body|{"default": 42, "maximum": 2147483647, "minimum": 0, "title": "Random Seed", "type": "integer"}|False|见强类型 schema；未知字段拒绝|
|search_space|body|{"$ref": "#/components/schemas/SearchSpace"}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{
  "channel": 0,
  "n_trials": 1,
  "max_epochs": 2
}
```

成功响应结构（HTTP 202，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "0123456789abcdef0123456789abcdef",
    "status": "QUEUED"
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：202, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 旧多通道 AutoML 训练 — `POST /automl/train-all`

用途：按 channels 顺序训练多个通道。

角色：admin。异步：True。源码：`backend_server.py:395`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|channels|body|{"items": {"type": "integer"}, "maxItems": 8, "minItems": 1, "title": "Channels", "type": "array"}|False|见强类型 schema；未知字段拒绝|
|n_trials|body|{"default": 30, "maximum": 500, "minimum": 1, "title": "N Trials", "type": "integer"}|False|见强类型 schema；未知字段拒绝|
|max_epochs|body|{"default": 500, "maximum": 10000, "minimum": 1, "title": "Max Epochs", "type": "integer"}|False|见强类型 schema；未知字段拒绝|
|patience|body|{"default": 40, "maximum": 1000, "minimum": 1, "title": "Patience", "type": "integer"}|False|见强类型 schema；未知字段拒绝|
|random_seed|body|{"default": 42, "maximum": 2147483647, "minimum": 0, "title": "Random Seed", "type": "integer"}|False|见强类型 schema；未知字段拒绝|
|search_space|body|{"$ref": "#/components/schemas/SearchSpace"}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{
  "channels": [
    0,
    1
  ],
  "n_trials": 1,
  "max_epochs": 2
}
```

成功响应结构（HTTP 202，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "task_id": "0123456789abcdef0123456789abcdef",
    "status": "QUEUED"
  }
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：202, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 静态页面和资源 — `GET /gui/{filename}`

用途：Flask path 转换器提供 index.html、operations.html、train.html 及本地脚本样式；计作一条实际注册路由。

角色：admin, operator。异步：False。源码：`flask/app.py:278`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|filename|path|{"type": "string"}|True|资源标识；必须符合归属权限|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /gui/{filename}
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 旧健康接口 — `GET /health`

用途：保留旧预测服务健康信息。

角色：public。异步：False。源码：`backend_server.py:308`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|无|—|—|—|无请求参数|

请求示例（参数示意，必须替换真实资源）：

```text
GET /health
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "status": "ok",
  "device": "cuda",
  "available_channels": [
    0,
    1
  ]
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 旧模型概览 — `GET /info`

用途：返回旧 MLP 模型类型和通道。

角色：admin, operator。异步：False。源码：`backend_server.py:316`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。

请求示例（参数示意，必须替换真实资源）：

```text
GET /info
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "models": [
    "MLP"
  ],
  "channels": [
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7
  ],
  "trained_channels": [
    0,
    1
  ]
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 旧数组预测 — `POST /predict`

用途：对一个通道的 N×4 特征数组同步预测。

角色：admin, operator。异步：False。源码：`backend_server.py:324`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|channel|body|{"maximum": 7, "minimum": 0, "title": "Channel", "type": "integer"}|True|见强类型 schema；未知字段拒绝|
|data|body|{"items": {"items": {"type": "number"}, "type": "array"}, "maxItems": 100000, "minItems": 1, "title": "Data", "type": "array"}|True|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{
  "channel": 0,
  "data": [
    [
      1.573,
      4.598,
      1.439,
      0.919
    ]
  ]
}
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "status": "success",
  "channel": 0,
  "predictions": [
    {
      "ea": 319.8691,
      "bias": 690.2004
    }
  ]
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

### 旧单文件预测 — `POST /predict-file`

用途：读取一个 CSV/XLSX，对指定通道同步预测。

角色：admin, operator。异步：False。源码：`backend_server.py:348`。

|参数|位置|类型 / 范围|必填|说明|
|---|---|---|---|---|
|X-Liante-Role|header|{"type": "string", "enum": ["admin", "operator", "device"]}|False|Bearer 身份角色，默认 device；Basic 用户名为角色|
|X-Liante-Device-ID|header|{"type": "string", "maxLength": 128}|False|设备调用必须提供独立已配置设备 ID|
|X-Request-ID|header|{"type": "string", "minLength": 1, "maxLength": 200}|False|幂等键；也可放在 JSON request_id；帧使用序号与校验和幂等|

响应字段契约：`Envelope`。标准顶层包含 code/message/data/request_id/timestamp；详细嵌套字段见下文响应契约与 OpenAPI。
|channel|body|{"maximum": 7, "minimum": 0, "title": "Channel", "type": "integer"}|True|见强类型 schema；未知字段拒绝|
|file_path|body|{"minLength": 1, "title": "File Path", "type": "string"}|True|见强类型 schema；未知字段拒绝|
|sheet_name|body|{"anyOf": [{"type": "string"}, {"type": "integer"}], "default": 0, "title": "Sheet Name"}|False|见强类型 schema；未知字段拒绝|
|feature_columns|body|{"anyOf": [{"items": {"anyOf": [{"type": "string"}, {"type": "integer"}]}, "type": "array"}, {"type": "null"}], "default": null, "title": "Feature Columns"}|False|见强类型 schema；未知字段拒绝|

请求示例（参数示意，必须替换真实资源）：

```json
{
  "channel": 0,
  "file_path": "D:/data/sample.csv"
}
```

成功响应结构（HTTP 200，实际模型与证据必须满足条件）：

```json
{
  "status": "success",
  "channel": 0,
  "source": {
    "file_name": "sample.csv",
    "row_count": 1,
    "feature_columns": [
      "a",
      "b",
      "c",
      "d"
    ]
  },
  "predictions": [
    {
      "row": 2,
      "ea": 1.0,
      "bias": 2.0
    }
  ]
}
```

失败响应示例（HTTP 400）：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "请求未满足接口条件",
  "data": {},
  "request_id": "request-001",
  "timestamp": "2026-10-01T00:00:00Z"
}
```

状态码：200, 400, 401, 403, 404, 409, 422, 429, 500, 503, 507。错误码见统一清单及端点实际业务条件。

容量与 Retry-After：使用统一准入规则；GET 分页有界。POST 收到可重试拒绝时重试同一 request_id / 帧序号。

关联：task_id 查询任务与日志；product_id 查询 trace；qualification_id 查询生命周期/审计。具体字段与嵌套范围见 OpenAPI components/schemas。

## 完整实际注册清单

|METHOD|PATH|ENDPOINT|BLUEPRINT|AUTH|SOURCE|
|---|---|---|---|---|---|
|GET|/|root||admin,operator|backend_server.py:299|
|POST|/api/v1/admin/drain|begin_drain||admin|backend_server.py:261|
|POST|/api/v1/aoi/folder-runs|api_v1.start_aoi_folder_run|api_v1|admin,operator|api_v1.py:493|
|GET|/api/v1/aoi/folder-runs/{task_id}|api_v1.aoi_folder_run_status|api_v1|admin,operator|api_v1.py:512|
|POST|/api/v1/aoi/streams|api_v1.start_aoi_stream|api_v1|admin,operator,device|api_v1.py:474|
|GET|/api/v1/aoi/streams/{task_id}|api_v1.aoi_stream_status|api_v1|admin,operator,device|api_v1.py:538|
|POST|/api/v1/aoi/streams/{task_id}/close|api_v1.close_aoi_stream|api_v1|admin,operator,device|api_v1.py:586|
|GET|/api/v1/aoi/streams/{task_id}/frames|api_v1.aoi_frame_records|api_v1|admin,operator,device|api_v1.py:551|
|POST|/api/v1/aoi/streams/{task_id}/frames|api_v1.push_aoi_frame|api_v1|admin,operator,device|api_v1.py:519|
|GET|/api/v1/aoi/streams/{task_id}/frames/{sequence}/image|api_v1.aoi_frame_image|api_v1|admin,operator,device|api_v1.py:562|
|GET|/api/v1/aoi/streams/{task_id}/latest-frame|api_v1.latest_aoi_frame|api_v1|admin,operator,device|api_v1.py:575|
|GET|/api/v1/aoi/streams/{task_id}/products|operations.stream_products|operations|admin,operator,device|api\operations.py:115|
|POST|/api/v1/aoi/streams/{task_id}/products|operations.open_product|operations|admin,operator,device|api\operations.py:109|
|GET|/api/v1/aoi/streams/{task_id}/products/{product_id}|operations.stream_product|operations|admin,operator,device|api\operations.py:121|
|POST|/api/v1/aoi/streams/{task_id}/products/{product_id}/close|operations.close_product|operations|admin,operator,device|api\operations.py:126|
|GET|/api/v1/exports/products/{product_id}/trace.json|operations.export_trace|operations|admin,operator,device|api\operations.py:233|
|GET|/api/v1/exports/qualifications/{qualification_id}.json|operations.export_qualification|operations|admin,operator,device|api\operations.py:228|
|GET|/api/v1/exports/tasks.csv|operations.export_tasks|operations|admin,operator,device|api\operations.py:213|
|GET|/api/v1/exports/tasks/{task_id}/predictions.csv|operations.export_predictions|operations|admin,operator,device|api\operations.py:219|
|GET|/api/v1/health|api_v1.health|api_v1|public|api_v1.py:113|
|POST|/api/v1/inference/aoi|api_v1.aoi|api_v1|admin,operator|api_v1.py:407|
|POST|/api/v1/inference/ea-bias|api_v1.ea_bias|api_v1|admin,operator|api_v1.py:376|
|POST|/api/v1/inference/ea-bias/folder|api_v1.predict_stage2_folder|api_v1|admin,operator,device|api_v1.py:301|
|POST|/api/v1/inference/ea-bias/scan|api_v1.scan_stage2_folder|api_v1|admin,operator,device|api_v1.py:288|
|GET|/api/v1/inference/ea-bias/sources|api_v1.stage2_sources|api_v1|admin,operator,device|api_v1.py:214|
|POST|/api/v1/inference/ea-bias/sources|api_v1.configure_stage2_source|api_v1|admin,device|api_v1.py:247|
|GET|/api/v1/inference/ea-bias/sources/{source_id}/logs|api_v1.stage2_source_logs|api_v1|admin,operator,device|api_v1.py:267|
|POST|/api/v1/inference/grading|api_v1.grading|api_v1|admin,operator,device|api_v1.py:593|
|GET|/api/v1/live|operations.live|operations|public|api\operations.py:43|
|GET|/api/v1/models|api_v1.models|api_v1|admin,operator|api_v1.py:124|
|GET|/api/v1/products/{product_id}/qualifications|operations.product_qualifications|operations|admin,operator,device|api\operations.py:134|
|GET|/api/v1/products/{product_id}/trace|operations.product_trace|operations|admin,operator,device|api\operations.py:199|
|GET|/api/v1/qualifications|operations.qualification_history|operations|admin,operator,device|api\operations.py:129|
|POST|/api/v1/qualifications|api_v1.create_qualification|api_v1|admin,operator|api_v1.py:653|
|GET|/api/v1/qualifications/{qualification_id}|api_v1.get_qualification|api_v1|admin,operator,device|api_v1.py:670|
|GET|/api/v1/qualifications/{qualification_id}/history|operations.evidence_history|operations|admin,operator,device|api\operations.py:152|
|POST|/api/v1/qualifications/{qualification_id}/replace|operations.replace|operations|admin,operator|api\operations.py:144|
|POST|/api/v1/qualifications/{qualification_id}/revoke|operations.revoke|operations|admin,operator|api\operations.py:139|
|GET|/api/v1/ready|api_v1.ready|api_v1|public|api_v1.py:117|
|GET|/api/v1/ready/{stage}|operations.stage_ready|operations|public|api\operations.py:46|
|GET|/api/v1/reviews|operations.review_list|operations|admin,operator|api\operations.py:161|
|POST|/api/v1/reviews|operations.create_review|operations|admin,operator|api\operations.py:158|
|GET|/api/v1/reviews/{review_id}|operations.review_get|operations|admin,operator|api\operations.py:166|
|GET|/api/v1/system/info|operations.system_info|operations|admin|api\operations.py:55|
|GET|/api/v1/tasks|operations.task_history|operations|admin,operator,device|api\operations.py:75|
|POST|/api/v1/tasks|api_v1.create_task|api_v1|admin,operator|api_v1.py:136|
|GET|/api/v1/tasks/{task_id}|api_v1.task|api_v1|admin,operator,device|api_v1.py:147|
|POST|/api/v1/tasks/{task_id}/cancel|api_v1.cancel_task|api_v1|admin,operator,device|api_v1.py:189|
|GET|/api/v1/tasks/{task_id}/logs|api_v1.logs|api_v1|admin,operator,device|api_v1.py:198|
|GET|/api/v1/tasks/{task_id}/predictions|api_v1.paged_predictions|api_v1|admin,operator,device|api_v1.py:172|
|GET|/api/v1/tasks/{task_id}/result|api_v1.result|api_v1|admin,operator,device|api_v1.py:156|
|POST|/api/v1/tasks/{task_id}/retry|operations.retry_task|operations|admin,operator,device|api\operations.py:80|
|GET|/automl/tasks/{task_id}|automl_task||admin,operator|backend_server.py:411|
|POST|/automl/tasks/{task_id}/cancel|automl_cancel||admin|backend_server.py:434|
|GET|/automl/tasks/{task_id}/result|automl_result||admin,operator|backend_server.py:418|
|POST|/automl/train|automl_train||admin|backend_server.py:379|
|POST|/automl/train-all|automl_train_all||admin|backend_server.py:395|
|GET|/gui/{filename}|static||admin,operator|flask/app.py:278|
|GET|/health|health||public|backend_server.py:308|
|GET|/info|info||admin,operator|backend_server.py:316|
|POST|/predict|predict||admin,operator|backend_server.py:324|
|POST|/predict-file|predict_file||admin,operator|backend_server.py:348|

## 响应字段契约

以下为数据字段类型及嵌套引用，示例仅说明结构，不代表缺失模型已产出结果。

### TaskRecord

```json
{
  "type": "object",
  "properties": {
    "request_id": {
      "type": [
        "string",
        "null"
      ]
    },
    "product_id": {
      "type": [
        "string",
        "null"
      ]
    },
    "stage": {
      "type": [
        "string",
        "null"
      ]
    },
    "stage_name": {
      "type": [
        "string",
        "null"
      ]
    },
    "operation": {
      "type": [
        "string",
        "null"
      ]
    },
    "current_file": {
      "type": [
        "string",
        "null"
      ]
    },
    "source_id": {
      "type": [
        "string",
        "null"
      ]
    },
    "owner_device_id": {
      "type": [
        "string",
        "null"
      ]
    },
    "start_time": {
      "type": [
        "string",
        "null"
      ]
    },
    "update_time": {
      "type": [
        "string",
        "null"
      ]
    },
    "finish_time": {
      "type": [
        "string",
        "null"
      ]
    },
    "error_code": {
      "type": [
        "string",
        "null"
      ]
    },
    "error_message": {
      "type": [
        "string",
        "null"
      ]
    },
    "retry_of": {
      "type": [
        "string",
        "null"
      ]
    },
    "task_id": {
      "type": "string"
    },
    "status": {
      "type": "string",
      "enum": [
        "PENDING",
        "QUEUED",
        "RUNNING",
        "CANCEL_REQUESTED",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "TIMEOUT",
        "REVIEW_REQUIRED"
      ]
    },
    "progress": {
      "type": "integer",
      "minimum": 0,
      "maximum": 100
    },
    "revision": {
      "type": "integer",
      "minimum": 0
    },
    "current_channel": {
      "type": [
        "integer",
        "null"
      ],
      "minimum": 0,
      "maximum": 7
    },
    "result": {
      "type": [
        "object",
        "null"
      ]
    }
  }
}
```

### TaskAcknowledgment

```json
{
  "type": "object",
  "properties": {
    "task_id": {
      "type": "string"
    },
    "request_id": {
      "type": "string"
    },
    "product_id": {
      "type": "string"
    },
    "status": {
      "type": "string"
    },
    "stream": {
      "$ref": "#/components/schemas/StreamSummary"
    },
    "sequence": {
      "type": "integer",
      "minimum": 0
    },
    "view_type": {
      "type": "string"
    },
    "replayed": {
      "type": "boolean"
    }
  }
}
```

### StreamSummary

```json
{
  "type": "object",
  "properties": {
    "stream_id": {
      "type": "string"
    },
    "source_id": {
      "type": "string"
    },
    "accepted": {
      "type": "integer",
      "minimum": 0
    },
    "processed": {
      "type": "integer",
      "minimum": 0
    },
    "failed": {
      "type": "integer",
      "minimum": 0
    },
    "rejected": {
      "type": "integer",
      "minimum": 0
    },
    "queue_depth": {
      "type": "integer",
      "minimum": 0
    },
    "queue_capacity": {
      "type": "integer",
      "minimum": 0
    },
    "last_sequence": {
      "type": "integer",
      "minimum": 0
    },
    "in_flight": {
      "type": "boolean"
    },
    "accepting": {
      "type": "boolean"
    },
    "timed_out": {
      "type": "boolean"
    },
    "processing_fps": {
      "type": "number"
    },
    "recent_frames": {
      "type": "array",
      "maxItems": 20,
      "items": {
        "type": "object"
      }
    }
  }
}
```

### FrameRecord

```json
{
  "type": "object",
  "properties": {
    "product_id": {
      "type": [
        "string",
        "null"
      ]
    },
    "view_type": {
      "type": [
        "string",
        "null"
      ]
    },
    "status": {
      "type": [
        "string",
        "null"
      ]
    },
    "sha256": {
      "type": [
        "string",
        "null"
      ]
    },
    "model_name": {
      "type": [
        "string",
        "null"
      ]
    },
    "model_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "recipe_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "received_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "processed_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "error_code": {
      "type": [
        "string",
        "null"
      ]
    },
    "error_message": {
      "type": [
        "string",
        "null"
      ]
    },
    "sequence": {
      "type": "integer",
      "minimum": 0
    },
    "duration_ms": {
      "type": "integer",
      "minimum": 0
    },
    "output": {
      "type": "object"
    }
  }
}
```

### AOIProduct

```json
{
  "type": "object",
  "properties": {
    "stream_id": {
      "type": "string"
    },
    "product_id": {
      "type": "string"
    },
    "status": {
      "type": "string"
    },
    "created_at": {
      "type": "string"
    },
    "updated_at": {
      "type": "string"
    },
    "required_views": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "received_views": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "missing_views": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "closed": {
      "type": "boolean"
    },
    "reason": {
      "type": [
        "string",
        "null"
      ]
    },
    "frames": {
      "type": "object",
      "additionalProperties": {
        "type": "integer",
        "minimum": 0
      }
    }
  }
}
```

### QualificationRecord

```json
{
  "properties": {
    "product_id": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Product Id",
      "type": "string"
    },
    "aoi_task_id": {
      "pattern": "^[a-f0-9]{32}$",
      "title": "Aoi Task Id",
      "type": "string"
    },
    "stage2_task_id": {
      "pattern": "^[a-f0-9]{32}$",
      "title": "Stage2 Task Id",
      "type": "string"
    },
    "optical_features": {
      "$ref": "#/components/schemas/Optical"
    },
    "electrical_features": {
      "$ref": "#/components/schemas/Electrical"
    },
    "tuning_result": {
      "$ref": "#/components/schemas/Tuning"
    },
    "tuning_parameters": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/TuningParameters"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "manual_review": {
      "$ref": "#/components/schemas/Manual"
    },
    "recipe_id": {
      "maxLength": 128,
      "minLength": 1,
      "title": "Recipe Id",
      "type": "string"
    },
    "recipe_version": {
      "maxLength": 128,
      "minLength": 1,
      "title": "Recipe Version",
      "type": "string"
    },
    "source_id": {
      "anyOf": [
        {
          "maxLength": 128,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Source Id"
    },
    "request_id": {
      "anyOf": [
        {
          "maxLength": 200,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Request Id"
    },
    "notes": {
      "default": "",
      "maxLength": 2000,
      "title": "Notes",
      "type": "string"
    },
    "qualification_id": {
      "type": [
        "string",
        "null"
      ]
    },
    "created_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "updated_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "expires_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "recorded_by": {
      "type": [
        "string",
        "null"
      ]
    },
    "operator": {
      "type": [
        "string",
        "null"
      ]
    },
    "reviewer": {
      "type": [
        "string",
        "null"
      ]
    },
    "reason": {
      "type": [
        "string",
        "null"
      ]
    },
    "superseded_by": {
      "type": [
        "string",
        "null"
      ]
    },
    "owner_device_id": {
      "type": [
        "string",
        "null"
      ]
    },
    "status": {
      "type": "string",
      "enum": [
        "ACTIVE",
        "SUPERSEDED",
        "REVOKED",
        "EXPIRED"
      ]
    },
    "aoi_result": {
      "type": "object",
      "properties": {
        "qualified": {
          "type": "boolean"
        },
        "frame_count": {
          "type": "integer",
          "minimum": 0
        }
      }
    }
  },
  "title": "QualificationRequest",
  "type": "object"
}
```

### QualificationEvent

```json
{
  "type": "object",
  "properties": {
    "qualification_id": {
      "type": [
        "string",
        "null"
      ]
    },
    "actor": {
      "type": [
        "string",
        "null"
      ]
    },
    "operation": {
      "type": [
        "string",
        "null"
      ]
    },
    "reason": {
      "type": [
        "string",
        "null"
      ]
    },
    "timestamp": {
      "type": [
        "string",
        "null"
      ]
    },
    "new_qualification_id": {
      "type": [
        "string",
        "null"
      ]
    },
    "review_id": {
      "type": [
        "string",
        "null"
      ]
    },
    "decision": {
      "type": [
        "string",
        "null"
      ]
    }
  }
}
```

### ReviewRecord

```json
{
  "properties": {
    "product_id": {
      "maxLength": 200,
      "minLength": 1,
      "title": "Product Id",
      "type": "string"
    },
    "qualification_id": {
      "anyOf": [
        {
          "pattern": "^[a-f0-9]{32}$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Qualification Id"
    },
    "stage3_task_id": {
      "anyOf": [
        {
          "pattern": "^[a-f0-9]{32}$",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Stage3 Task Id"
    },
    "decision": {
      "enum": [
        "APPROVE",
        "REJECT"
      ],
      "title": "Decision",
      "type": "string"
    },
    "comment": {
      "maxLength": 2000,
      "minLength": 1,
      "title": "Comment",
      "type": "string"
    },
    "final_grade": {
      "anyOf": [
        {
          "enum": [
            "A",
            "B",
            "C",
            "D"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Final Grade"
    },
    "request_id": {
      "anyOf": [
        {
          "maxLength": 200,
          "minLength": 1,
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Request Id"
    },
    "review_id": {
      "type": "string"
    },
    "reviewer": {
      "type": "string"
    },
    "created_at": {
      "type": "string"
    },
    "suggested_grade": {
      "type": [
        "string",
        "null"
      ],
      "enum": [
        "A",
        "B",
        "C",
        "D",
        null
      ]
    }
  },
  "title": "ReviewRequest",
  "type": "object"
}
```

### TaskEvent

```json
{
  "type": "object",
  "properties": {
    "timestamp": {
      "type": [
        "string",
        "null"
      ]
    },
    "level": {
      "type": [
        "string",
        "null"
      ]
    },
    "request_id": {
      "type": [
        "string",
        "null"
      ]
    },
    "task_id": {
      "type": [
        "string",
        "null"
      ]
    },
    "product_id": {
      "type": [
        "string",
        "null"
      ]
    },
    "stage": {
      "type": [
        "string",
        "null"
      ]
    },
    "stage_name": {
      "type": [
        "string",
        "null"
      ]
    },
    "file_name": {
      "type": [
        "string",
        "null"
      ]
    },
    "file_path": {
      "type": [
        "string",
        "null"
      ]
    },
    "model_name": {
      "type": [
        "string",
        "null"
      ]
    },
    "model_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "recipe_version": {
      "type": [
        "string",
        "null"
      ]
    },
    "operation": {
      "type": [
        "string",
        "null"
      ]
    },
    "status": {
      "type": [
        "string",
        "null"
      ]
    },
    "message": {
      "type": [
        "string",
        "null"
      ]
    },
    "error_code": {
      "type": [
        "string",
        "null"
      ]
    },
    "exception": {
      "type": [
        "string",
        "null"
      ]
    },
    "channel": {
      "type": [
        "integer",
        "null"
      ]
    },
    "progress": {
      "type": [
        "integer",
        "null"
      ]
    },
    "duration_ms": {
      "type": [
        "integer",
        "null"
      ]
    }
  }
}
```

### PredictionRow

```json
{
  "type": "object",
  "properties": {
    "row": {
      "type": "integer",
      "minimum": 0
    },
    "ea": {
      "type": "number"
    },
    "bias": {
      "type": "number"
    }
  }
}
```

### HealthRecord

```json
{
  "type": "object",
  "properties": {
    "web": {
      "type": "string"
    },
    "stage1": {
      "type": "string"
    },
    "stage2": {
      "type": "string"
    },
    "stage3": {
      "type": "string"
    },
    "database": {
      "type": "string"
    },
    "gpu": {
      "type": "string"
    },
    "task_store": {
      "type": "string"
    },
    "log_dir": {
      "type": "string"
    },
    "auth": {
      "type": "string"
    },
    "stage2_channels": {
      "type": "array",
      "items": {
        "type": "integer",
        "minimum": 0
      }
    },
    "alerts": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "draining": {
      "type": "boolean"
    },
    "aoi_streams": {
      "type": "object"
    },
    "inference_queue": {
      "type": "object"
    },
    "training_queue": {
      "type": "object"
    },
    "resources": {
      "type": "object"
    },
    "model_resources": {
      "type": "object"
    }
  }
}
```

### SourceRecord

```json
{
  "type": "object",
  "properties": {
    "source_id": {
      "type": [
        "string",
        "null"
      ]
    },
    "directory": {
      "type": [
        "string",
        "null"
      ]
    },
    "owner_device_id": {
      "type": [
        "string",
        "null"
      ]
    },
    "updated_at": {
      "type": [
        "string",
        "null"
      ]
    },
    "revision": {
      "type": "integer",
      "minimum": 0
    },
    "exists": {
      "type": "boolean"
    }
  }
}
```

### TraceRecord

```json
{
  "type": "object",
  "properties": {
    "product_id": {
      "type": "string"
    },
    "tasks": {
      "type": "object",
      "properties": {
        "items": {
          "type": "array",
          "items": {
            "$ref": "#/components/schemas/TaskRecord"
          }
        },
        "total": {
          "type": "integer",
          "minimum": 0
        },
        "limit": {
          "type": "integer",
          "minimum": 0
        },
        "offset": {
          "type": "integer",
          "minimum": 0
        }
      }
    },
    "aoi_products": {
      "type": "object",
      "properties": {
        "items": {
          "type": "array",
          "items": {
            "$ref": "#/components/schemas/AOIProduct"
          }
        },
        "total": {
          "type": "integer",
          "minimum": 0
        },
        "limit": {
          "type": "integer",
          "minimum": 0
        },
        "offset": {
          "type": "integer",
          "minimum": 0
        }
      }
    },
    "qualifications": {
      "type": "object",
      "properties": {
        "items": {
          "type": "array",
          "items": {
            "$ref": "#/components/schemas/QualificationRecord"
          }
        },
        "total": {
          "type": "integer",
          "minimum": 0
        },
        "limit": {
          "type": "integer",
          "minimum": 0
        },
        "offset": {
          "type": "integer",
          "minimum": 0
        }
      }
    },
    "reviews": {
      "type": "object",
      "properties": {
        "items": {
          "type": "array",
          "items": {
            "$ref": "#/components/schemas/ReviewRecord"
          }
        },
        "total": {
          "type": "integer",
          "minimum": 0
        },
        "limit": {
          "type": "integer",
          "minimum": 0
        },
        "offset": {
          "type": "integer",
          "minimum": 0
        }
      }
    },
    "limit": {
      "type": "integer",
      "minimum": 0
    },
    "offset": {
      "type": "integer",
      "minimum": 0
    },
    "aoi_tasks": {
      "type": "array",
      "items": {
        "$ref": "#/components/schemas/TaskRecord"
      }
    },
    "stage2_results": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "task_id": {
            "type": "string"
          },
          "status": {
            "type": "string"
          },
          "files_truncated": {
            "type": "boolean"
          },
          "files": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "file_index": {
                  "type": "integer",
                  "minimum": 0
                },
                "channel": {
                  "type": "integer",
                  "minimum": 0
                },
                "status": {
                  "type": "string"
                },
                "prediction_count": {
                  "type": "integer",
                  "minimum": 0
                },
                "model_version": {
                  "type": [
                    "string",
                    "null"
                  ]
                },
                "recipe_version": {
                  "type": [
                    "string",
                    "null"
                  ]
                },
                "predictions_endpoint": {
                  "type": [
                    "string",
                    "null"
                  ]
                },
                "preview": {
                  "type": "array",
                  "maxItems": 3,
                  "items": {
                    "$ref": "#/components/schemas/PredictionRow"
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
```
