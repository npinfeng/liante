# 联特科技工业多模态智能检测系统

通过产品编号关联 AOI 连续流、EA/Bias 推荐、人工合格证据和合格品分级。软件支持模型接入；Stage1、Stage3 保持 not_configured，不生成假缺陷、假合格结论或假等级。C209 Stage2 使用原有八通道真实模型。

## 当前能力

|模块|功能|真实状态|
|---|---|---|
|Stage1|多产品流、视角、产品独立关闭、背压、ACK 持久化、恢复|缺模型，帧明确 MODEL_NOT_CONFIGURED|
|Stage2|文件夹识别、MES 目录/版本/归属、异步预测、分页导出|八通道真实模型，原数值流程和权重保留|
|Stage3|人工证据生命周期、上游复查、人工复核|缺模型，等级为空|
|管理中心|历史、追溯、证据、复核、日志、健康|/gui/operations.html|

SQLite 是任务、预测行和事件的主存。JSON 为可修复快照，JSONL 为追加审计副本。推理与训练共用状态机，终态不被迟到回调覆盖；旧训练接口小写状态兼容。AOI journal 独立保存帧、流和产品元数据。

## 启动

开发环境执行 `python backend_server.py --host 127.0.0.1 --port 8000`。生产使用 Waitress serve.py 和 profile，要求认证、禁止 debug；允许目录根缺失时禁用相关输入。程序不自动读取 .env，详见 [部署维护](deploy/README.md)。

C209 PowerShell：

```powershell
Set-Location F:\npfcode\liante
.\deploy\lifecycle.ps1 -Action start -Profile .\deploy\profiles\c209.json
```

C209 维持 127.0.0.1:8000。本地执行 `ssh -N -L 18000:127.0.0.1:8000 C209`，打开 http://127.0.0.1:18000/gui/index.html。工作台、管理中心、训练页为 /gui/index.html、/gui/operations.html、/gui/train.html，沿用认证。

## 流、产品与容量

建流配置 view_types/required_views；逐帧传 sequence（或 frame_seq）、product_id、view_type。默认四条流、每流八帧等待、单帧 2 MiB，journal 提交后 ACK。同序号/产品/校验和/视角重放，冲突 409；队列满 429 加 Retry-After，同一序号重试。

产品独立建立、查询、关闭；缺视角 INCOMPLETE，缺模型失败 FAILED，流可继续收其他产品。关闭流结算全部产品。恢复先处理已确认帧，新帧收到 STREAM_RECOVERING 时退避。处理为至少一次，外部模型/MES 写入应按流和帧去重。

所有执行队列有界；磁盘 warning/critical 可配置，临界时拒收新大写入，在途任务继续。当前单进程线程执行，卡死模型强制资源回收及多机高可用仍需进程隔离和共享存储。

## Stage2 / MES

自动识别 CSV/XLSX 的 channel_0–channel_7 / CH0–CH7，不要求手工特征或通道。C209 来源为 F:\npfcode\liante_old\train_data\data_v5\split_by_channel。同一 HTTP 端口提供 sources、scan、folder 接口，MES 在允许根内指定目录并绑定独立身份。

提交处理当时扫描文件，不持续监视新增文件；混合产品拆分目录。MES 需 product_id/request_id；公共幂等按认证身份、操作/路径、请求编号、规范化 JSON 校验。冲突为 REQUEST_REPLAY_CONFLICT。新回执集中保存，旧 Stage2 回执只兼容读取。失败任务 retry 使用原输入快照创建新任务，保留 retry_of。

预测分块写 SQLite，只内联受限预览，完整值分页/CSV 读取。来源日志 30 天/20000 条；结构化日志每任务 20000、全库 200000 条；JSONL 每任务 32 MiB 后提示副本降级。日志支持 limit/offset 或 page/page_size，不混用；前端缓存 500 行。

## 证据与复核

人工光电测量校验有限值、单位、声明范围、配方 ID/版本、时区时间及字段上限。身份由认证绑定。AOI 产品必须真实完成且合格，Stage2 完成且实际预测行一致；无需关闭整流。证据 ACTIVE/SUPERSEDED/REVOKED/EXPIRED；替换和审计同事务。分级入队及执行前后复查。

缺模型可登记不可覆盖的复核说明，两个 grade 字段为空；无真实分级结果不能填等级。正式阈值、配方审批及具名账号规则需业务确认。

## 验证和资料

```powershell
python -m pytest -q
python tools/list_api_routes.py --json
python tools/generate_api_docs.py
python tools/check_task_consistency.py --output output --logs logs
```

软件测试不训练、不构造伪模型，35 项旧模型测试跳过，本轮不启用 allow-model-work。一致性默认只读，repair-snapshots 只重建 JSON。备份/恢复要求停止服务，不含权重和凭据。

实际 60 个方法/路径操作，不计自动 HEAD/OPTIONS；[API](docs/API.md)、[OpenAPI](docs/openapi.yaml)、[路由清单](docs/API_ROUTE_INVENTORY.json) 双向一致。

- [实施计划](docs/SYSTEM_IMPROVEMENT_PLAN.md)、[报告](docs/SYSTEM_IMPROVEMENT_REPORT.md)
- [就绪状态](docs/PRODUCTION_READINESS.md)、[MES](docs/MES_STAGE2_INTEGRATION.md)
- [模型契约](docs/MODEL_ADAPTER_CONTRACT.md)、[工具](tools/README.md)

代码按 api/、services/、tasks/、schemas/、gui/、deploy/、tools/、tests/ 分层。Stage2 原 AutoML/MLP 保留，训练不属本轮；[历史训练指南](docs/RUNNING_AND_TRAINING_CN.md) 保留参考。当前状态以实施报告为准。
