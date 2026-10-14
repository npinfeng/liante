# 系统工程完善验收报告

日期：2026-10-01。项目：本地 `C:\projects\liante`，C209 `F:\npfcode\liante`。本报告针对本轮软件完善，历史训练和此前重构不计作本轮修改。

## 1. 完成状态

软件接入、任务与数据生命周期、权限、日志、追溯、界面、运维工具和文档已实现并部署 C209。真实 Stage2 回归通过。Stage1、Stage3 保持 NOT CONFIGURED；三阶段真实业务验收依赖未来模型接入，不能声明全流程模型生产就绪。

C209 `LianteWebServer` 正在运行，单个后端监听 `127.0.0.1:8000`，认证开启、未排空、队列为空；计划任务自动启动标志维持禁用。存活 200、Stage2 就绪 200；Stage1/Stage3/整体就绪 503 是缺模型的真实状态。

部署前保存原业务库与原代码备份，升级后另保存业务备份：

- `.deploy/backups/software-business-20261001.zip`：迁移前数据库及配置。
- `.deploy/backups/software-code-20261001.zip`：升级前原代码。
- `.deploy/backups/software-final-20261001.zip`：升级后的业务数据及配置。

没有安装依赖、下载权重、清理用户缓存或修改现有认证凭据。临时部署检查脚本已删除；本地 8765 界面测试服务和临时标签页已关闭。

## 2. 修改文件

以下按本轮开始时文件内容基线比较；此前已经存在的未提交改动没有被重置。

共 33 个文件。

| 文件 |
|---|
| `.env.example` |
| `.gitignore` |
| `README.md` |
| `api_v1.py` |
| `backend_server.py` |
| `config.py` |
| `deploy/archive_tasks.py` |
| `deploy/start_c209.ps1` |
| `docs/API.md` |
| `docs/MES_STAGE2_INTEGRATION.md` |
| `docs/MODEL_ADAPTER_CONTRACT.md` |
| `docs/README.md` |
| `docs/RUNNING_AND_TRAINING_CN.md` |
| `docs/openapi.yaml` |
| `gui/app.js` |
| `gui/index.html` |
| `gui/stage2_folder.js` |
| `schemas/inference.py` |
| `serve.py` |
| `services/aoi_stream_service.py` |
| `services/frame_journal.py` |
| `services/inference_service.py` |
| `services/prediction_service.py` |
| `services/stage2_source_store.py` |
| `tasks/inference_store.py` |
| `tasks/qualification_store.py` |
| `tasks/task_index.py` |
| `tasks/task_manager.py` |
| `tests/conftest.py` |
| `tests/test_api_docs.py` |
| `tests/test_reliability.py` |
| `tools/README.md` |
| `tools/generate_api_docs.py` |

## 3. 新增文件

共 32 个文件。

| 文件 |
|---|
| `.github/workflows/software-ci.yml` |
| `api/__init__.py` |
| `api/operations.py` |
| `deploy/README.md` |
| `deploy/backup_restore.py` |
| `deploy/lifecycle.ps1` |
| `deploy/profiles/c209.json` |
| `deploy/profiles/example.json` |
| `deploy/register_service.ps1` |
| `deploy/run_profile.ps1` |
| `deploy/runtime.py` |
| `docs/API_ROUTE_INVENTORY.json` |
| `docs/PRODUCTION_READINESS.md` |
| `docs/SYSTEM_IMPROVEMENT_PLAN.md` |
| `docs/SYSTEM_IMPROVEMENT_REPORT.md` |
| `gui/operations.css` |
| `gui/operations.html` |
| `gui/operations.js` |
| `gui/operations_link.js` |
| `schemas/qualification.py` |
| `services/access_policy.py` |
| `services/capacity.py` |
| `services/idempotency.py` |
| `services/pagination.py` |
| `tasks/review_store.py` |
| `tasks/state_machine.py` |
| `tests/test_system_engineering.py` |
| `tools/api_catalog_legacy.py` |
| `tools/api_response_schemas.py` |
| `tools/check_task_consistency.py` |
| `tools/list_api_routes.py` |
| `tools/validate_runtime.py` |

新模块按 api/、services/、tasks/、schemas/、gui/、deploy/、tools/、tests/ 分层。运行日志、测试证据、升级包分别在 `.build/system-upgrade/`、C209 `.deploy/`，不混入业务源码。

## 4. Stage1

流与产品分离：流内多产品独立建立、查询、关闭；必需视角在产品建立/首帧时固定。view_types/required_views 有界配置，帧记录 view_type、接收/处理时间、序号、产品和校验和。缺视角为 INCOMPLETE，帧失败为 FAILED，流继续接收其他产品；关闭流结算未完成产品。

先提交 journal 再 ACK，队列满 429 加 Retry-After；相同帧重放、不同图片/产品/视角冲突 409。重启从 journal 恢复，覆盖父任务丢失、失败流残留帧、processing 产品；恢复帧优先，新帧暂时退避。确实丢失来源元数据的孤儿帧记录 STORAGE_PARENT_MISSING，不猜测来源或合格结论。原终态保持不变，帧和产品继续得到明确结算。

没有配置真实模型；无模型帧明确 MODEL_NOT_CONFIGURED，无假缺陷和假合格结果。

## 5. Stage2

保留文件夹识别、通道规则、特征提取、分块计算、真实权重和原预测数值。MES 在允许根内登记来源路径，来源版本与设备归属持久化；扫描、提交、执行、结果和日志均可查询。原 /predict、/predict-file、训练接口保留。

公共幂等按认证身份、操作/路径、request_id 和规范化 JSON 保存原响应；相同请求 replayed，不同参数 REQUEST_REPLAY_CONFLICT。Header 与 JSON 请求编号一致性校验；Header 单独提供时传入任务编号。任务回执与任务创建同事务；来源/证据/复核回执与各自对象同事务，响应丢失不会重复创建。旧 Stage2 receipts 仅兼容读取，新回执不重复双写。容量 100000 后明确拒收，不自动复用过期键。

失败、取消、超时任务使用原输入快照 retry 创建新任务并保留 retry_of；文件变化明确失败，新文件应重新扫描提交。结果分页和 CSV 导出有上限。

**真实验收**：87 个模型及配套文件 SHA-256 全部未变；八通道各 623 行、合计 4984 行预测与升级前完整结果哈希一致。MES 实际任务 `1b09121d1f8745f09f8ac17ffc1da3a8`，产品 `SOFTWARE-VERIFY-20261001`，8 个文件完成、4984 条预测、91 条任务日志。同请求返回原任务，改产品返回 409。真实预测 CSV 首文件 623 行，追溯预览 24 行，来源日志查询通过。最终测试后再次确认权重未变。

## 6. Stage3

Qualification 强类型校验有限值、单位/声明范围、字段长度、人工说明、时区时间、配方 ID/版本及实际完成的上游结果。认证绑定 recorded_by/operator/reviewer，客户端不能冒充。AOI 产品独立完成即可登记，无需整个流关闭；Stage2 摘要与实际预测行、产品和配方必须一致。

ACTIVE / SUPERSEDED / REVOKED / EXPIRED 查询、历史、撤销和同事务替换可用；过期按时区比较。分级入队、执行和提交终态前复查有效证据；生命周期与最终提交使用共同治理锁，终态不被迟到回调覆盖。

人工复核不可覆盖，保存认证身份、理由、时间和关联任务/证据。无真实分级结果时 suggested_grade/final_grade 为空，禁止填写伪等级；历史真实等级复核仍需有效证据。Stage3 模型状态仍为 NOT CONFIGURED，分级不可用时明确 MODEL_NOT_CONFIGURED。

## 7. Task

SQLite 为主存，JSON 可修复快照，JSONL 追加审计副本。推理与 AutoML 共用 PENDING、QUEUED、RUNNING、CANCEL_REQUESTED、COMPLETED、FAILED、CANCELLED、TIMEOUT、REVIEW_REQUIRED 状态机；旧训练接口保持小写兼容。终态 CAS 不允许覆盖原结果，终态日志在提交成功后记录，重复/迟到完成不产生假的完成日志。

无用任务内存副本移除，训练结束后从持久化恢复查询；执行队列与日志缓存有界。普通中断任务重启明确失败并给出 retry 路径，AOI journal 独立恢复。只读一致性工具检查 DB/JSON/结果计数/孤立事件与日志，显式 repair-snapshots 仅修复快照。

C209 最终只读检查：17 条任务，status=OK，findings=[]，未执行修复。

## 8. Product Trace

按产品返回任务、AOI 产品、流任务关联、Stage2 实际 EA/Bias 预览、证据版本、复核与时间。服务端分页，设备只看归属自己的数据；空页也按总数进行归属判断。每次最多 100 个文件摘要、总计 100 个预测预览行，每文件最多 3 行，不整批加载预测数组。CSV/JSON 导出继承归属检查与分页限制。

## 9. Frontend

保留中文深色三阶段工作台和训练页，增加管理中心：任务历史筛选/分页、产品追溯表、实际预测摘要及日志链接、证据历史/撤销/替换、人工复核、系统状态及来源日志。Stage1 可配视角、关闭单产品；Stage3 缺模型按钮禁用且无等级。

日志展示阶段、产品、文件、通道、操作、进度和错误；前端缓存最多 500 行，管理页每页 50。移除工作台外部 CDN 依赖，真实数值图表使用本地 canvas 后备绘制。浏览器检查了状态、任务历史、复核和追溯空态，无控制台错误；JavaScript 语法检查通过。

## 10. API

| 来源 | 实际操作数 |
|---|---:|
| Flask 运行时方法/路径 | 60 |
| API.md 端点条目 | 60 |
| OpenAPI operations | 60 |

包含 blueprint、静态 path 路由、不同方法；不计自动 HEAD/OPTIONS。动态路径转换器标准化，operationId 唯一；API/实际路由双向覆盖、角色矩阵与 schema 引用通过测试。每条接口有用途、权限、参数、响应示例、状态、异步/幂等/容量说明，主要响应记录的嵌套字段契约同步到 OpenAPI。

新增业务能力为产品生命周期、证据生命周期/审计、Review、Trace、历史、导出、存活/阶段就绪和管理员运行信息。最终文档源为实际注册表，旧元数据不能单独生成过时数量。

## 11. Tests

| 环境 | Passed | Failed / Errors | Skipped |
|---|---:|---:|---:|
| 本地完整软件测试 | 94 | 0 | 35 |
| C209 完整软件测试 | 94 | 0 | 35 |

35 项跳过原因：保留的旧测试会训练或构造伪模型/推理结果，本轮未启用 allow-model-work。真实 Stage2 的额外验收使用现有真实权重及用户目录，八通道/4984 行基线一致，不通过伪模型替代。

测试覆盖终态竞争、队列预约失败、背压/重复 ACK、journal/DB/JSON/JSONL 故障、父任务/孤儿帧/失败流恢复、证据过期/撤销/原子替换审计、复核无等级、请求回执恢复、Header 幂等、设备 IDOR、磁盘临界、排空、分页/导出、恢复校验与中途安装回滚。开发中发现的夹具目录重名、时区比较、PowerShell 编码问题均已修复并重跑；表格是最终结果。

Python 编译、JavaScript 检查、Windows PowerShell 5 AST 解析、git diff --check 通过；密钥/私钥常见格式扫描无命中，部署包不含凭据。最小 CI 已配置，托管平台尚未执行。

## 12. Production Readiness

READY：Web/认证/环境校验、有界准入/容量、Task 主存/状态机、AOI 软件接入、MES 软件接入、证据与复核软件、追溯/界面、API 文档、受控备份恢复。

PARTIAL：模型卡死的强制资源回收和多机高可用；人工测量正式阈值/配方审批/具名账号；长期保留、备份计划、SQLite 物理收缩与告警责任人；托管 CI 执行。当前有看门狗与终态保护，但线程卡死仍可能占用资源。

BLOCKED：Stage1 真实检测、Stage3 真实分级、三阶段真实业务整体就绪。原因是模型未配置，软件开发没有因此停止。详细状态见 PRODUCTION_READINESS.md。

## 13. 本轮没有做的内容

Stage1 模型训练：未做。

Stage1 权重配置：未做。

Stage3 模型训练：未做。

Stage3 权重配置：未做。

没有 Stage2 重训或权重修改；没有下载模型，没有构造假缺陷、假合格证据或假等级证明系统可用。这些模型事项属于后续授权范围。

## 14. 真正剩余事项与本地访问

1. 后续获得授权时接入并验收真实 Stage1/Stage3 模型、数据、版本和性能。
2. 业务方确定正式光电阈值、配方审批与具名人员认证规则。
3. 按实际生产规模完成进程隔离/多机部署、现场保留与备份/告警计划，并执行托管 CI。

C209 后端已运行。本地后台 SSH 隧道启动被自动审批拦截，工具未给具体原因，未绕过；在本机 PowerShell 手动执行：

```powershell
ssh -N -L 127.0.0.1:18000:127.0.0.1:8000 C209
```

保持该窗口打开，访问 `http://127.0.0.1:18000/gui/index.html`，管理中心为 `/gui/operations.html`，使用原认证。此方式维持 C209 本机监听，不增加公开端口。

验收证据：本地 `.build/system-upgrade/test-all-final.txt`、`test-c209-final.txt`、`software-stage2-weights-20261001.json`、`software-stage2-predictions-20261001.json`、`software-mes-verification.json`、`software-runtime-final.json`、`software-consistency-final.json`、`software-service-final.json`；C209 对应证据在 `.deploy/`。
