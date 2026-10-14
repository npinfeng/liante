# 开发与文档工具

这里放不参与 WebServer 启动和模型推理的脚本。运行服务仍使用项目根目录的 `serve.py`；旧客户端与测试可继续导入 `backend_server.py`、`api_v1.py`。

| 路径 | 用途 | 常用命令 |
|---|---|---|
| `generate_api_docs.py` | 从 Flask 实际注册表生成 `docs/API.md` 和 `docs/openapi.yaml` | `python tools/generate_api_docs.py` |
| `document_generation/build_*.py` | 生成历史技术文档和配图 | `python tools/document_generation/build_engineering_doc.py` |
| `document_generation/build_plan.mjs` | 生成方案幻灯片 | 使用配套的 artifact-tool 环境运行 |
| `document_generation/source_extract.py` | 从参考 ZIP 提取源文件与文本 | `python tools/document_generation/source_extract.py <zip路径> <输出目录>` |

这些生成脚本把项目根目录按脚本位置解析，输出目录仍为项目下的 `output/` 或 `source/`。它们是离线工具，不随线上部署包发布。`docs/CURRENT_PROJECT_ANALYSIS.md` 是实施前的历史分析，当前接口以 `docs/API.md` 和 `docs/openapi.yaml` 为准。

| 工具 | 用途 |
|---|---|
| list_api_routes.py --json / --markdown | 隔离无模型环境导出实际路由、源位置、权限；包含 blueprint/static |
| check_task_consistency.py | 默认只读检查 DB/JSON/结果/孤立日志；repair-snapshots 只修复快照 |
| validate_runtime.py | 环境、权限、数据库、目录和模型能力预检 |
| api_catalog_legacy.py | 旧端点说明元数据；禁止单独生成旧静态数量的文档 |

新软件工具随部署发布；历史 document_generation 工具仍是离线参考。软件 CI 不训练、不下载权重。备份恢复工具在 deploy/backup_restore.py，先排空并停止服务。
