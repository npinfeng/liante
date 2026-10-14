"""Historical endpoint descriptions imported by generate_api_docs.

Deprecated as a standalone generator: actual registrations determine the public
catalog. Retained descriptions do not define routes, auth or persistence rules.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1] / "docs"
ENDPOINTS = []


def add(category, method, path, name, purpose, content_type, params, request_example,
        success_status, success_example, failure_status, failure_example, codes, note=""):
    ENDPOINTS.append(locals().copy())


def p(name, type_, required, description, location="body"):
    return dict(name=name, type=type_, required=required, description=description, location=location)


add("系统接口", "GET", "/api/v1/health", "系统健康", "检查 WebServer、三阶段模型、GPU 和任务存储。", "无", [],
    "GET /api/v1/health", 200, {"code": 0, "message": "success", "data": {"web": "ok", "stage1": "not_configured", "stage2": "ok", "stage3": "not_configured", "database": "file_store", "gpu": "available"}},
    500, {"code": "INTERNAL_ERROR", "message": "健康检查失败", "data": {}, "request_id": "..."}, ["INTERNAL_ERROR"], "未配置模型会准确返回状态，HTTP 仍为 200。")
add("系统接口", "GET", "/api/v1/ready", "生产就绪", "三阶段模型就绪且无队列或存储告警时返回 200。", "无", [],
    "GET /api/v1/ready", 200, {"code": 0, "message": "success", "data": {"web": "ok", "stage1": "ok", "stage2": "ok", "stage3": "ok", "alerts": []}},
    503, {"code": "NOT_READY", "message": "模型或容量尚未达到三阶段运行条件", "data": {"stage1": "not_configured", "alerts": []}},
    ["NOT_READY"], "可供负载均衡和运维探针使用；未配置的阶段不得报告生产就绪。")
add("系统接口", "POST", "/api/v1/admin/drain", "服务排空", "管理员停止接收新任务并关闭所有 AOI 流，等待在途任务完成后再停止进程。", "无", [],
    "POST /api/v1/admin/drain", 200, {"code": 0, "message": "success", "data": {"draining": True, "aoi_streams": 0}},
    403, {"code": "FORBIDDEN", "message": "仅管理员可排空服务"}, ["FORBIDDEN"],
    "设置排空后新 POST 返回 503 和 Retry-After；GET 健康与任务查询仍可用。")
add("系统接口", "GET", "/api/v1/models", "模型状态", "列出各阶段和 0–7 通道模型状态。", "无", [],
    "GET /api/v1/models", 200, {"code": 0, "message": "success", "data": [{"stage": "stage2", "channel": 0, "model_name": "MLP", "model_version": "artifact", "model_path": "saved_models/channel_0", "loaded": False, "status": "ok", "recipe_version": "unconfigured"}]},
    500, {"code": "INTERNAL_ERROR", "message": "查询失败", "data": {}, "request_id": "..."}, ["INTERNAL_ERROR"], "loaded 表示当前进程是否缓存了模型。")
add("系统接口", "GET", "/health", "旧健康接口", "保留旧预测服务健康信息。", "无", [],
    "GET /health", 200, {"status": "ok", "device": "cuda", "available_channels": [0, 1]},
    500, {"status": "error", "error": "Internal Server Error"}, [], "兼容旧客户端；新客户端使用 /api/v1/health。")
add("系统接口", "GET", "/info", "旧模型概览", "返回旧 MLP 模型类型和通道。", "无", [],
    "GET /info", 200, {"models": ["MLP"], "channels": [0, 1, 2, 3, 4, 5, 6, 7], "trained_channels": [0, 1]},
    500, {"status": "error", "error": "Internal Server Error"}, [], "兼容旧客户端。")

add("任务接口", "POST", "/api/v1/tasks", "创建任务", "创建带 product_id 的可追踪任务。", "application/json",
    [p("stage", "string: stage1|stage2|stage3", True, "业务阶段"), p("product_id", "string", True, "产品追溯编号"), p("request_id", "string", False, "调用方请求编号；缺省自动生成")],
    {"stage": "stage2", "product_id": "P-001"}, 201,
    {"code": 0, "message": "success", "data": {"task_id": "...", "product_id": "P-001", "stage": "stage2", "status": "PENDING", "progress": 0}},
    400, {"code": "INVALID_ARGUMENT", "message": "stage 必须是 stage1、stage2 或 stage3", "data": {}, "request_id": "..."}, ["INVALID_ARGUMENT"],
    "推理 POST 也会自动创建任务；此接口适用于外部系统预先建立追踪记录。")
add("任务接口", "GET", "/api/v1/tasks/{task_id}", "任务状态", "查询阶段、文件、通道、操作、进度和错误。", "无",
    [p("task_id", "string", True, "任务编号", "path")], "GET /api/v1/tasks/abc", 200,
    {"code": 0, "message": "success", "data": {"task_id": "abc", "stage": "stage2", "status": "RUNNING", "current_file": "sample.csv", "file_index": 1, "file_total": 2, "current_channel": "CH0", "channel_index": 1, "channel_total": 2, "operation": "inference", "progress": 25}},
    404, {"code": "TASK_NOT_FOUND", "message": "任务不存在", "data": {}, "request_id": "..."}, ["TASK_NOT_FOUND"], "任务按 JSON 文件持久化；重启中断的任务标为 FAILED。")
add("任务接口", "GET", "/api/v1/tasks/{task_id}/result", "任务结果", "获取完成结果或未完成状态。", "无",
    [p("task_id", "string", True, "任务编号", "path")], "GET /api/v1/tasks/abc/result", 200,
    {"code": 0, "message": "success", "data": {"task_id": "abc", "status": "COMPLETED", "result": {"files": []}}},
    409, {"code": "MODEL_NOT_CONFIGURED", "message": "模型未配置", "data": {"task_id": "abc"}, "request_id": "..."},
    ["TASK_NOT_FOUND", "TASK_FAILED", "MODEL_NOT_CONFIGURED", "FILE_NOT_FOUND", "INFERENCE_FAILED"], "未完成返回 202 和当前状态；失败返回 409 及实际错误码。")
add("任务接口", "GET", "/api/v1/tasks/{task_id}/predictions", "分页预测结果", "按文件序号、通道和偏移读取 Stage2 已落盘的预测行。", "无",
    [p("task_id", "string", True, "Stage2 任务编号", "path"), p("file_index", "integer", False, "从 1 开始的文件序号", "query"),
     p("channel", "integer", False, "0–7", "query"), p("limit", "integer", False, "1–1000", "query"),
     p("offset", "integer", False, "非负偏移", "query")],
    "GET /api/v1/tasks/abc/predictions?file_index=1&channel=0&limit=100&offset=0", 200,
    {"code": 0, "message": "success", "data": {"items": [{"row": 2, "ea": 1.0, "bias": 2.0}], "total": 1, "limit": 100, "offset": 0}},
    404, {"code": "TASK_NOT_FOUND", "message": "Stage2 任务不存在", "data": {}},
    ["TASK_NOT_FOUND", "INVALID_ARGUMENT"], "大任务的任务 JSON 只保留摘要和前 10 行；完整结果在 SQLite 分页读取。")
add("日志接口", "GET", "/api/v1/tasks/{task_id}/logs", "任务日志", "按任务查询结构化日志，支持阶段和分页。", "无",
    [p("task_id", "string", True, "任务编号", "path"), p("stage", "string", False, "阶段筛选", "query"), p("limit", "integer", False, "1–1000，默认 200", "query"), p("offset", "integer", False, "非负偏移，默认 0", "query")],
    "GET /api/v1/tasks/abc/logs?stage=stage2&limit=200", 200,
    {"code": 0, "message": "success", "data": {"items": [{"timestamp": "2026-09-28T00:00:00Z", "level": "INFO", "task_id": "abc", "stage": "stage2", "file_name": "sample.csv", "channel": "CH0", "operation": "inference", "status": "RUNNING", "message": "开始推理"}], "total": 1, "limit": 200, "offset": 0}},
    404, {"code": "TASK_NOT_FOUND", "message": "任务不存在", "data": {}, "request_id": "..."}, ["TASK_NOT_FOUND", "INVALID_ARGUMENT"],
    "前端每 1.5 秒短轮询；生产响应隐藏 traceback，服务器日志保留。")

add("AOI接口", "POST", "/api/v1/inference/aoi", "旧单帧 AOI 接口", "旧客户端上传单张图片或引用服务器图片；新产线接入使用 AOI 流。", "multipart/form-data 或 application/json",
    [p("product_id", "string", True, "产品编号"), p("image", "binary", False, "上传图片；与 image_path 二选一"), p("image_path", "string", False, "服务器可读图片路径；与 image 二选一"), p("request_id", "string", False, "请求编号")],
    "multipart: product_id=P-001, image=@aoi.png", 202,
    {"code": 0, "message": "success", "data": {"task_id": "abc", "status": "RUNNING"}},
    503, {"code": "MODEL_NOT_CONFIGURED", "message": "stage1 真实模型适配器未配置", "data": {"task_id": "abc"}, "request_id": "..."},
    ["INVALID_ARGUMENT", "INVALID_FILE_FORMAT", "FILE_NOT_FOUND", "MODEL_NOT_CONFIGURED", "MODEL_LOAD_FAILED", "INFERENCE_FAILED"],
    "兼容旧客户端；不用于产线连续检测。模型未配置不会生成伪造缺陷。")
add("AOI流接口", "POST", "/api/v1/aoi/streams", "建立 AOI 流", "按产品和设备编号建立连续帧流，返回可追踪任务 ID。", "application/json",
    [p("product_id", "string", True, "产品追溯编号"), p("source_id", "string", True, "AOI 设备编号，1–128 字符"), p("request_id", "string", False, "调用方请求编号")],
    {"product_id": "P-001", "source_id": "AOI-LINE-01"}, 201,
    {"code": 0, "message": "success", "data": {"task_id": "abc", "status": "RUNNING", "stream": {"queue_capacity": 8, "accepted": 0, "processed": 0, "rejected": 0}}},
    429, {"code": "STREAM_CAPACITY_REACHED", "message": "AOI 流数量已达上限，请稍后重试", "data": {}, "request_id": "..."},
    ["INVALID_ARGUMENT", "STREAM_CAPACITY_REACHED"], "流数量由 AOI_MAX_STREAMS 限制；429 带 Retry-After。")
add("AOI流接口", "POST", "/api/v1/aoi/streams/{task_id}/frames", "连续推送 AOI 帧", "设备按序号连续推送 PNG/JPEG/BMP/TIFF 帧。", "multipart/form-data",
    [p("task_id", "string", True, "流任务编号", "path"), p("sequence", "integer", True, "从 1 连续递增；429 时重试同序号"), p("product_id", "string", False, "当前帧的产品追溯编号；省略时使用建流默认值"), p("image", "binary", True, "图片帧；默认最大 2 MiB")],
    "multipart: sequence=1, image=@frame-1.jpg", 202,
    {"code": 0, "message": "success", "data": {"task_id": "abc", "sequence": 1, "status": "QUEUED", "stream": {"queue_depth": 1}}},
    429, {"code": "STREAM_BACKPRESSURE", "message": "AOI 队列已满，请按 Retry-After 重试同一帧序号", "data": {"task_id": "abc"}, "request_id": "..."},
    ["INVALID_ARGUMENT", "INVALID_FRAME", "INVALID_FILE_FORMAT", "FRAME_SEQUENCE_ERROR", "STREAM_BACKPRESSURE", "STREAM_CLOSED", "STREAM_NOT_FOUND"],
    "同一设备流可连续处理不同产品，逐帧记录 product_id。有界队列满时拒收并计数，不丢弃已接收帧；429 带 Retry-After。模型未配置的帧在任务结果中明确标为失败。")
add("AOI流接口", "GET", "/api/v1/aoi/streams/{task_id}", "AOI 流状态", "查询接收、处理、失败、拒收、队列和处理速率。", "无",
    [p("task_id", "string", True, "流任务编号", "path")], "GET /api/v1/aoi/streams/abc", 200,
    {"code": 0, "message": "success", "data": {"task_id": "abc", "status": "RUNNING", "stream": {"accepted": 10, "processed": 8, "failed": 0, "rejected": 2, "queue_depth": 1, "in_flight": True}}},
    404, {"code": "STREAM_NOT_FOUND", "message": "AOI 流不存在", "data": {}, "request_id": "..."}, ["STREAM_NOT_FOUND"],
    "任务状态及结构化日志也可通过通用任务接口查询。")
add("AOI流接口", "GET", "/api/v1/aoi/streams/{task_id}/latest-frame", "最新 AOI 帧", "读取当前流最近接收的原始帧，供监控页面预览。", "无",
    [p("task_id", "string", True, "流任务编号", "path")], "GET /api/v1/aoi/streams/abc/latest-frame", 200,
    "<image bytes>", 404, {"code": "FRAME_NOT_FOUND", "message": "尚未接收图片帧", "data": {"task_id": "abc"}},
    ["FRAME_NOT_FOUND", "STREAM_CLOSED", "STREAM_NOT_FOUND"], "返回图片二进制及 X-Frame-Sequence；仅流活跃时可读。")
add("AOI流接口", "POST", "/api/v1/aoi/streams/{task_id}/close", "停止 AOI 流", "停止接收新帧，并处理完已接收队列。", "无",
    [p("task_id", "string", True, "流任务编号", "path")], "POST /api/v1/aoi/streams/abc/close", 202,
    {"code": 0, "message": "success", "data": {"task_id": "abc", "status": "DRAINING"}},
    409, {"code": "STREAM_CLOSED", "message": "AOI 流已停止", "data": {"task_id": "abc"}},
    ["STREAM_NOT_FOUND", "STREAM_CLOSED"], "排空完成后任务状态为 COMPLETED；任一帧失败时为 FAILED，结果保留统计和最近 20 帧记录。")
add("EA/Bias参数预测接口", "POST", "/api/v1/inference/ea-bias", "批量 EA/Bias 参数推荐", "对有序文件列表及每文件的有序通道执行真实 Stage2 推理。", "application/json",
    [p("product_id", "string", True, "产品编号"), p("request_id", "string", False, "请求编号"), p("files", "array<object>", True, "1–100 个文件对象"), p("files[].file_path", "string", True, "服务器路径"), p("files[].channels", "array<integer>", True, "不重复的 0–7 通道"), p("files[].sheet_name", "string|integer", False, "XLSX 工作表，默认 0"), p("files[].feature_columns", "array<string|integer>", False, "恰好 4 列；缺省沿用旧特征选择")],
    {"product_id": "P-001", "files": [{"file_path": "D:/data/sample.csv", "channels": [0, 1]}]}, 202,
    {"code": 0, "message": "success", "data": {"task_id": "abc", "request_id": "req", "status": "PENDING"}},
    400, {"code": "INVALID_ARGUMENT", "message": "channels 必须是不重复的 0 到 7", "data": {}, "request_id": "..."},
    ["INVALID_ARGUMENT", "FILE_NOT_FOUND", "INVALID_FILE_FORMAT", "MODEL_NOT_CONFIGURED", "INFERENCE_FAILED"],
    "推理异步执行；每文件和通道的进度来自实际循环。结果通过任务结果接口获取。")
add("EA/Bias参数预测接口", "GET", "/api/v1/inference/ea-bias/sources", "输入文件夹数据源", "查询已配置的 Stage2 输入目录及就绪模型通道。", "无", [],
    "GET /api/v1/inference/ea-bias/sources", 200,
    {"code": 0, "message": "success", "data": {"sources": [{"source_id": "input-1", "directory": "F:/npfcode/liante_old/train_data/data_v5/split_by_channel", "exists": True}], "model_channels": [0, 1, 3, 4, 5]}},
    401, {"code": "UNAUTHORIZED", "message": "需要有效身份凭据"}, ["UNAUTHORIZED"],
    "默认来源由 STAGE2_INPUT_DIRS 初始化；MES 可通过同一路径的 POST 登记或修改允许范围内的目录。设备仅列出自己的来源。")
add("EA/Bias参数预测接口", "POST", "/api/v1/inference/ea-bias/sources", "MES 登记输入目录", "登记或修改 C209 可访问的目录，配置持久化并记录变更日志。", "application/json",
    [p("source_id", "string", True, "1–100 字符：字母数字开头，可含 . _ -"), p("directory", "string", True, "服务端绝对目录或 UNC 共享目录；位于 STAGE2_INPUT_DIRS / STAGE2_ALLOWED_DIRS 范围内"),
     p("owner_device_id", "string", False, "管理员可指定已配置的 MES 身份；设备只能使用自己身份"),
     p("expected_revision", "integer", False, "乐观锁；新建可传 0，修改传当前 revision"), p("request_id", "string", False, "变更日志关联编号")],
    {"source_id": "MES-LINE-01", "directory": "F:/npfcode/liante_old/train_data/data_v5/split_by_channel", "expected_revision": 0, "request_id": "config-001"}, 201,
    {"code": 0, "message": "success", "data": {"source_id": "MES-LINE-01", "directory": "F:/npfcode/liante_old/train_data/data_v5/split_by_channel", "revision": 1, "owner_device_id": "MES-01", "exists": True}},
    403, {"code": "PATH_FORBIDDEN", "message": "目录不在 Stage2 允许范围内", "data": {}},
    ["INVALID_ARGUMENT", "PATH_FORBIDDEN", "DIRECTORY_NOT_FOUND", "SOURCE_FORBIDDEN", "SOURCE_VERSION_CONFLICT", "SOURCE_CAPACITY_REACHED", "UNKNOWN_DEVICE"],
    "新增返回 201，修改或相同配置返回 200；版本冲突 409；设备需独立 ID，操作员不可修改来源。共享目录需服务账号权限。")
add("日志接口", "GET", "/api/v1/inference/ea-bias/sources/{source_id}/logs", "MES 来源接入日志", "分页读取目录变更、扫描、预测提交、重复请求及拒绝记录；最新记录在前。", "无",
    [p("source_id", "string", True, "来源编号", "path"), p("limit", "integer", False, "1–1000，默认 100", "query"), p("offset", "integer", False, "非负，默认 0", "query")],
    "GET /api/v1/inference/ea-bias/sources/MES-LINE-01/logs?limit=100", 200,
    {"code": 0, "message": "success", "data": {"items": [{"operation": "folder", "request_id": "req-001", "task_id": "abc", "http_status": 202, "code": 0}], "total": 1, "order": "newest_first"}},
    403, {"code": "SOURCE_FORBIDDEN", "message": "MES 无权访问其他来源", "data": {}},
    ["SOURCE_NOT_FOUND", "SOURCE_FORBIDDEN", "INVALID_ARGUMENT"], "接入日志默认最多 20000 条且保留 30 天；预测内部日志在通用任务 logs 接口。")
add("EA/Bias参数预测接口", "POST", "/api/v1/inference/ea-bias/scan", "自动识别文件夹", "有界扫描 CSV/XLSX，按文件名或子目录自动识别通道。", "application/json",
    [p("source_id", "string", True, "来源于 sources 接口"), p("recursive", "boolean", False, "包含子目录，默认 true")],
    {"source_id": "input-1", "recursive": True}, 200,
    {"code": 0, "message": "success", "data": {"source_id": "input-1", "directory": "F:/data", "recursive": True, "total_files": 1, "ready_files": 1, "missing_model_files": 0, "invalid_files": 0, "ignored_entries": 0, "files": [{"file_name": "channel_0.csv", "file_path": "F:/data/channel_0.csv", "channel": 0, "size_bytes": 128, "mtime_ns": 1000000, "status": "ready", "message": "可预测"}]}},
    413, {"code": "SCAN_LIMIT_EXCEEDED", "message": "文件夹条目超过扫描上限"},
    ["SOURCE_NOT_FOUND", "DIRECTORY_NOT_FOUND", "DIRECTORY_UNREADABLE", "SCAN_BUSY", "SCAN_LIMIT_EXCEEDED"],
    "通道命名为 channel_0–channel_7 / CH0–CH7；最多 100 个文件、10000 个目录条目、8 层子目录；默认修改后 2 秒才接收；不跟随符号链接及重解析点。识别状态为 ready、model_missing、invalid。")
add("EA/Bias参数预测接口", "POST", "/api/v1/inference/ea-bias/folder", "文件夹批量预测", "重新识别文件夹并异步处理全部数据文件，无需提交特征、文件路径或通道。", "application/json",
    [p("source_id", "string", True, "已配置的数据源"), p("recursive", "boolean", False, "默认 true"), p("product_id", "string", False, "产品/批次追溯编号；缺省生成 BATCH 编号"), p("request_id", "string", False, "请求追踪编号")],
    {"source_id": "input-1"}, 202,
    {"code": 0, "message": "success", "data": {"task_id": "abc", "request_id": "req", "status": "PENDING", "product_id": "BATCH-abc", "source": {"total_files": 8, "ready_files": 5, "missing_model_files": 3, "invalid_files": 0, "files": []}}},
    422, {"code": "INVALID_INPUT_FOLDER", "message": "部分文件无法识别，请刷新查看具体原因", "data": {"invalid_files": 1, "files": []}},
    ["QUEUE_FULL", "SOURCE_NOT_FOUND", "INPUT_FOLDER_EMPTY", "INVALID_INPUT_FOLDER", "INPUT_FILE_CHANGED", "BATCH_PARTIAL_FAILED"],
    "先预约有界队列再扫描；读取前后检查文件大小与修改时间。逐文件失败不阻止其他文件；任一失败使整批任务 FAILED，结果接口以 HTTP 409 返回 data.result，包含成功与失败项及 file_index。成功项通过 predictions_endpoint 分页。失败批次不能登记 Stage3 合格证据。MES 必须传 product_id 和 request_id；同身份同 request_id 重试返回 200 及原任务，参数不同返回 409。提交回执保留 30 天，容量满时拒收。")
add("EA/Bias参数预测接口", "POST", "/predict", "旧数组预测", "对一个通道的 N×4 特征数组同步预测。", "application/json",
    [p("channel", "integer", True, "0–7"), p("data", "array<array<number>>", True, "非空 N×4 特征")],
    {"channel": 0, "data": [[1.573, 4.598, 1.439, 0.919]]}, 200,
    {"status": "success", "channel": 0, "predictions": [{"ea": 319.8691, "bias": 690.2004}]},
    404, {"status": "error", "error": "Channel 0 模型 artifact 不完整"}, [], "兼容旧接口；数值算法保持原样。参数错误为 400，推理内部错误为 500。")
add("EA/Bias参数预测接口", "POST", "/predict-file", "旧单文件预测", "读取一个 CSV/XLSX，对指定通道同步预测。", "application/json",
    [p("channel", "integer", True, "0–7"), p("file_path", "string", True, "服务器文件路径"), p("sheet_name", "string|integer", False, "XLSX 工作表，默认 0"), p("feature_columns", "array<string|integer>", False, "恰好 4 列")],
    {"channel": 0, "file_path": "D:/data/sample.csv"}, 200,
    {"status": "success", "channel": 0, "source": {"file_name": "sample.csv", "row_count": 1, "feature_columns": ["a", "b", "c", "d"]}, "predictions": [{"row": 2, "ea": 1.0, "bias": 2.0}]},
    400, {"status": "error", "error": "预测文件不存在"}, [], "兼容旧接口；无任务进度。推理内部错误为 500。")

add("多模态分级接口", "POST", "/api/v1/inference/grading", "合格产品多模态分级", "仅前序检测均合格且调测完成的产品进入 A/B/C/D 等级模型。", "application/json",
    [p("product_id", "string", True, "产品编号"), p("qualification_id", "string", True, "服务端合格证据编号"), p("request_id", "string", False, "请求编号")],
    {"product_id": "P-001", "qualification_id": "registered-evidence-id"}, 202,
    {"code": 0, "message": "success", "data": {"task_id": "abc", "status": "RUNNING"}},
    422, {"code": "QUALIFICATION_REQUIRED", "message": "分级需要该产品的服务端合格证据编号", "data": {}, "request_id": "..."},
    ["INVALID_ARGUMENT", "QUALIFICATION_REQUIRED", "MODEL_NOT_CONFIGURED", "MODEL_LOAD_FAILED", "INVALID_MODEL_OUTPUT", "INFERENCE_FAILED"],
    "调用方自行填写的 qualified 字段不作为分级依据；模型未配置返回 503。")

add("调测接口", "POST", "/automl/train", "旧单通道 AutoML 训练", "异步训练一个 MLP 通道。", "application/json",
    [p("channel", "integer", True, "0–7"), p("n_trials", "integer", False, "默认 30"), p("max_epochs", "integer", False, "默认 500"), p("patience", "integer", False, "默认 40"), p("random_seed", "integer", False, "默认 42"), p("search_space", "object", False, "Optuna 搜索空间")],
    {"channel": 0, "n_trials": 1, "max_epochs": 2}, 202,
    {"task_id": "abc", "status": "queued"}, 400, {"status": "error", "error": "channel 无效"}, [],
    "保留原训练任务机制；训练可覆盖对应通道模型，请在受控环境执行。")
add("调测接口", "POST", "/automl/train-all", "旧多通道 AutoML 训练", "按 channels 顺序训练多个通道。", "application/json",
    [p("channels", "array<integer>", False, "默认 0–7，不重复"), p("n_trials", "integer", False, "每通道默认 30"), p("max_epochs", "integer", False, "默认 500"), p("patience", "integer", False, "默认 40"), p("random_seed", "integer", False, "默认 42"), p("search_space", "object", False, "搜索空间")],
    {"channels": [0, 1], "n_trials": 1, "max_epochs": 2}, 202,
    {"task_id": "abc", "status": "queued", "channels": [0, 1]}, 400,
    {"status": "error", "error": "channels 不能重复"}, [], "GPU 训练默认单任务串行；保留旧 API。")
add("调测接口", "GET", "/automl/tasks/{task_id}", "旧训练状态", "查询训练 trial、进度与消息。", "无",
    [p("task_id", "string", True, "训练任务编号", "path")], "GET /automl/tasks/abc", 200,
    {"task_id": "abc", "status": "training", "channel": 0, "current_trial": 1, "total_trials": 2, "progress": 50},
    404, {"status": "error", "error": "Training task not found"}, [], "与新版推理 Task Store 分开，避免改变已有训练行为。")
add("调测接口", "GET", "/automl/tasks/{task_id}/result", "旧训练结果", "读取完成的训练指标与模型配置。", "无",
    [p("task_id", "string", True, "训练任务编号", "path")], "GET /automl/tasks/abc/result", 200,
    {"channel": 0, "model_config": {"model_type": "MLP"}, "metrics": {"ea": {}, "bias": {}}},
    404, {"status": "error", "error": "Training task not found"}, [], "未完成返回 202；失败返回 500，保留旧兼容行为。")

add("其他已有接口", "GET", "/", "旧服务入口", "返回服务链接与旧状态。", "无", [], "GET /", 200,
    {"status": "ok", "service": "Liante MLP AutoML", "prediction_gui": "/gui/index.html", "automl_gui": "/gui/train.html"},
    500, {"status": "error", "error": "Internal Server Error"}, [], "兼容旧入口。")
add("其他已有接口", "GET", "/gui/index.html", "三阶段演示页面", "浏览器打开中文业务演示页面。", "无", [],
    "GET /gui/index.html", 200, "<!DOCTYPE html>...", 404, "Not Found", [], "静态 HTML；页面调用同源相对路径 API。")
add("其他已有接口", "GET", "/gui/train.html", "AutoML 训练页面", "浏览器打开原有训练界面。", "无", [],
    "GET /gui/train.html", 200, "<!doctype html>...", 404, "Not Found", [], "静态 HTML，继续使用旧训练 API。")

add("AOI流接口", "GET", "/api/v1/aoi/streams/{task_id}/frames", "逐帧结果", "分页查询持久化 AOI 逐帧结果。", "无",
    [p("task_id", "string", True, "AOI 流任务编号", "path"), p("limit", "integer", False, "1–1000", "query"), p("offset", "integer", False, "非负偏移", "query")],
    "GET /api/v1/aoi/streams/abc/frames", 200,
    {"code": 0, "message": "success", "data": {"items": [], "total": 0}},
    404, {"code": "STREAM_NOT_FOUND", "message": "AOI 流不存在", "data": {}}, ["STREAM_NOT_FOUND"])
add("AOI流接口", "GET", "/api/v1/aoi/streams/{task_id}/frames/{sequence}/image", "历史原图", "在保留期限内读取指定原始帧。", "无",
    [p("task_id", "string", True, "AOI 流任务编号", "path"), p("sequence", "integer", True, "帧序号", "path")],
    "GET /api/v1/aoi/streams/abc/frames/1/image", 200, "<image bytes>",
    404, {"code": "FRAME_NOT_FOUND", "message": "帧不存在或已过保留期", "data": {}}, ["FRAME_NOT_FOUND"])
add("多模态分级接口", "POST", "/api/v1/qualifications", "登记合格证据", "管理员验证已完成的 AOI 和 Stage2 任务并保存人工测量、调测及复核证据。", "application/json",
    [p("product_id", "string", True, "产品编号"), p("aoi_task_id", "string", True, "AOI 任务编号"),
     p("stage2_task_id", "string", True, "Stage2 任务编号"), p("optical_features", "object", True, "光学检测证据"),
     p("electrical_features", "object", True, "电学检测证据"), p("tuning_result", "object", True, "调测完成证据"),
     p("manual_review", "object", True, "decision=qualified、reviewer、带时区 measured_at、notes")],
    {"product_id": "P-001", "aoi_task_id": "aoi", "stage2_task_id": "ea", "optical_features": {"qualified": True, "power_dbm": 2.1},
     "electrical_features": {"qualified": True, "voltage_v": 3.3}, "tuning_result": {"status": "complete"},
     "manual_review": {"decision": "qualified", "reviewer": "EMP-001", "measured_at": "2026-09-29T10:00:00+08:00", "notes": "工单 123；光电测量复核通过"}}, 201,
    {"code": 0, "message": "success", "data": {"qualification_id": "..."}},
    422, {"code": "INVALID_QUALIFICATION", "message": "证据无效", "data": {}}, ["INVALID_QUALIFICATION", "FORBIDDEN"],
    "当前人工录入模式仅管理员可登记；服务器核对产品、前序任务状态、完成时间、预测记录和配方版本。")
add("多模态分级接口", "GET", "/api/v1/qualifications/{qualification_id}", "查询合格证据", "读取服务端登记的合格证据。", "无",
    [p("qualification_id", "string", True, "合格证据编号", "path")],
    "GET /api/v1/qualifications/abc", 200, {"code": 0, "message": "success", "data": {"qualification_id": "abc"}},
    404, {"code": "QUALIFICATION_NOT_FOUND", "message": "合格证据不存在", "data": {}}, ["QUALIFICATION_NOT_FOUND"])
add("任务接口", "POST", "/api/v1/tasks/{task_id}/cancel", "取消推理任务", "取消排队或请求运行中的推理任务停止。", "无",
    [p("task_id", "string", True, "任务编号", "path")], "POST /api/v1/tasks/abc/cancel", 200,
    {"code": 0, "message": "success", "data": {"task_id": "abc", "status": "CANCELLED"}},
    409, {"code": "TASK_NOT_CANCELLABLE", "message": "任务无法取消", "data": {}}, ["TASK_NOT_CANCELLABLE"])
add("调测接口", "POST", "/automl/tasks/{task_id}/cancel", "取消训练任务", "取消排队或请求运行中的训练任务停止。", "无",
    [p("task_id", "string", True, "训练任务编号", "path")], "POST /automl/tasks/abc/cancel", 200,
    {"task_id": "abc", "status": "cancelled"}, 409,
    {"status": "error", "error": "Task not cancellable"}, [])


def _deprecated_generation():
    def pretty(value):
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    
    
    def type_schema(type_):
        if type_.startswith("integer"):
            return {"type": "integer"}
        if type_.startswith("array<array<number>>"):
            return {"type": "array", "items": {"type": "array", "items": {"type": "number"}}}
        if type_.startswith("array<"):
            inner = type_[6:-1]
            return {"type": "array", "items": type_schema(inner)}
        if type_ == "number":
            return {"type": "number"}
        if type_ == "binary":
            return {"type": "string", "format": "binary"}
        if type_ == "object":
            return {"type": "object", "additionalProperties": True}
        return {"type": "string"}
    
    
    md = ["# 联特科技多模态智能检测系统 HTTP API", "", "本文由 `tools/generate_api_docs.py` 与 OpenAPI 文件共同生成。所有相对路径均以当前 WebServer 为基准。新版接口返回 `{code,message,data}`；旧接口保持原响应格式。普通用户响应不包含 traceback。", "",
          "## 鉴权与流控", "", "生产环境使用 HTTP Basic（浏览器）或 Bearer 令牌（设备）。`GET /health`、`GET /api/v1/health`、`GET /api/v1/ready` 无需凭据。管理员可登记人工合格证据、训练和排空；操作员可查看任务与提交推理，不能训练或登记证据。设备须使用独立 Bearer 令牌及 `X-Liante-Device-ID` 访问绑定的 AOI 流；通用设备令牌不能登记合格证据。所有凭据仅经 HTTPS 或 SSH 隧道发送。队列满返回 429 和 `Retry-After`；排空时新的 POST 返回 503。", "",
          "## 接口总览", "", "| 类别 | Method | URL | 名称 |", "|---|---|---|---|"]
    for spec in ENDPOINTS:
        md.append(f"| {spec['category']} | {spec['method']} | `{spec['path']}` | {spec['name']} |")
    for category in dict.fromkeys(spec["category"] for spec in ENDPOINTS):
        md += ["", f"## {category}", ""]
        for spec in [s for s in ENDPOINTS if s["category"] == category]:
            md += [f"### {spec['name']} — `{spec['method']} {spec['path']}`", "",
                   f"用途：{spec['purpose']}", "", f"Content-Type：{spec['content_type']}", "",
                   "| 参数 | 类型 | 必填 | 位置 | 说明 |", "|---|---|---|---|---|"]
            if spec["params"]:
                md.extend(f"| `{x['name']}` | {x['type']} | {'是' if x['required'] else '否'} | {x['location']} | {x['description']} |" for x in spec["params"])
            else:
                md.append("| 无 | — | — | — | 无请求参数 |")
            md += ["", "请求示例：", "", "```text", pretty(spec["request_example"]), "```", "",
                   f"成功响应（HTTP {spec['success_status']}）：", "", "```json", pretty(spec["success_example"]), "```", "",
                   f"失败响应示例（HTTP {spec['failure_status']}）：", "", "```json", pretty(spec["failure_example"]), "```", "",
                   f"HTTP Status Code：{spec['success_status']}、{spec['failure_status']}。",
                   f"业务错误码：{', '.join(spec['codes']) if spec['codes'] else '旧接口无统一业务错误码'}。",
                   f"备注：{spec['note'] or '无'}", ""]
    md += ["## 统一说明", "", "新版常见错误码：`INVALID_ARGUMENT`、`FILE_NOT_FOUND`、`INVALID_FILE_FORMAT`、`TASK_NOT_FOUND`、`MODEL_NOT_CONFIGURED`、`MODEL_LOAD_FAILED`、`INFERENCE_FAILED`、`NOT_ELIGIBLE_FOR_GRADING`、`STREAM_BACKPRESSURE`、`FRAME_SEQUENCE_ERROR`、`TASK_FAILED`、`INTERNAL_ERROR`。三阶段推理任务的状态为 `PENDING`、`RUNNING`、`COMPLETED`、`FAILED`、`REVIEW_REQUIRED`；AOI 流关闭前持续 RUNNING。旧训练任务继续使用原有小写状态。Flask 自动处理 HEAD 与 OPTIONS 预检。", ""]
    (ROOT / "API.md").write_text("\n".join(md), encoding="utf-8")
    
    paths = {}
    for spec in ENDPOINTS:
        op = {"tags": [spec["category"]], "summary": spec["name"], "description": spec["purpose"] + " " + spec["note"],
              "operationId": f"{spec['method'].lower()}_{spec['path'].strip('/').replace('/', '_').replace('{', '').replace('}', '').replace('-', '_').replace('.', '_')}",
              "responses": {str(spec["success_status"]): {"description": "成功", "content": {"application/json" if spec["content_type"] != "无" or not spec["path"].startswith("/gui/") else "text/html": {"example": spec["success_example"]}}},
                            str(spec["failure_status"]): {"description": "失败", "content": {"application/json": {"example": spec["failure_example"]}}}}}
        if spec["path"] not in {"/health", "/api/v1/health", "/api/v1/ready"}:
            op["security"] = [{"basicAuth": []}, {"bearerAuth": []}]
        if spec["method"] == "POST" and spec["path"] != "/api/v1/admin/drain":
            op["responses"].setdefault("503", {"description": "服务排空或模型不可用；请查看业务错误码及 Retry-After"})
        if spec["path"] in {"/api/v1/inference/ea-bias", "/api/v1/inference/grading",
                             "/api/v1/inference/ea-bias/scan", "/api/v1/inference/ea-bias/folder", "/api/v1/inference/ea-bias/sources",
                             "/api/v1/aoi/streams", "/api/v1/aoi/streams/{task_id}/frames",
                             "/automl/train", "/automl/train-all"}:
            op["responses"].setdefault("429", {"description": "容量满；按 Retry-After 重试"})
        if spec["path"].endswith("/latest-frame") or spec["path"].endswith("/image"):
            op["responses"]["200"]["content"] = {
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
                "image/png": {"schema": {"type": "string", "format": "binary"}}}
        arguments = [x for x in spec["params"] if x["location"] in ("path", "query")]
        if arguments:
            op["parameters"] = [{"name": x["name"], "in": x["location"], "required": x["required"],
                                 "description": x["description"], "schema": type_schema(x["type"])} for x in arguments]
        body = [x for x in spec["params"] if x["location"] == "body" and "[]." not in x["name"]]
        if body:
            properties = {x["name"]: {**type_schema(x["type"]), "description": x["description"]} for x in body}
            schema = {"type": "object", "properties": properties,
                      "required": [x["name"] for x in body if x["required"]]}
            if spec["path"] == "/api/v1/inference/ea-bias":
                schema["properties"]["files"] = {"type": "array", "minItems": 1, "maxItems": 100,
                    "items": {"type": "object", "required": ["file_path", "channels"], "properties": {
                        "file_path": {"type": "string"}, "channels": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True, "items": {"type": "integer", "minimum": 0, "maximum": 7}},
                        "sheet_name": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
                        "feature_columns": {"type": "array", "minItems": 4, "maxItems": 4}}}}
            if spec["path"] == "/api/v1/qualifications":
                schema["properties"]["manual_review"] = {"type": "object", "required": [
                    "decision", "reviewer", "measured_at", "notes"], "properties": {
                    "decision": {"type": "string", "enum": ["qualified"]},
                    "reviewer": {"type": "string", "minLength": 1},
                    "measured_at": {"type": "string", "format": "date-time"},
                    "notes": {"type": "string", "minLength": 1}}}
                for feature in ("optical_features", "electrical_features"):
                    schema["properties"][feature] = {"type": "object", "required": ["qualified"],
                        "properties": {"qualified": {"type": "boolean", "enum": [True]}},
                        "additionalProperties": {"type": "number"}}
                schema["properties"]["tuning_result"] = {"type": "object", "required": ["status"],
                    "properties": {"status": {"type": "string", "enum": ["complete"]}},
                    "additionalProperties": True}
            content_type = "multipart/form-data" if spec["content_type"].startswith("multipart") else "application/json"
            op["requestBody"] = {"required": True, "content": {content_type: {"schema": schema,
                                  "example": spec["request_example"] if isinstance(spec["request_example"], dict) else None}}}
            if spec["path"] == "/api/v1/inference/aoi":
                op["requestBody"]["content"]["application/json"] = {"schema": {"type": "object", "required": ["product_id", "image_path"],
                    "properties": {"product_id": {"type": "string"}, "image_path": {"type": "string"}, "request_id": {"type": "string"}}}}
        paths.setdefault(spec["path"], {})[spec["method"].lower()] = op
    
    document = {"openapi": "3.0.3", "info": {"title": "联特科技多模态智能检测系统 API", "version": "1.0.0",
                "description": "同一清单生成 API.md 与本 OpenAPI 3 文档。旧接口保持兼容。"},
                "servers": [{"url": "/"}], "paths": paths,
                "components": {"securitySchemes": {
                    "basicAuth": {"type": "http", "scheme": "basic"},
                    "bearerAuth": {"type": "http", "scheme": "bearer"}}}}
    (ROOT / "openapi.yaml").write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"Generated {len(ENDPOINTS)} operations")
    

if __name__ == '__main__':
    raise SystemExit('Use python tools/generate_api_docs.py; historical catalog does not define routes.')
