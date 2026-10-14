# Stage1 / Stage3 模型接入契约

本文件规定软件与未来真实模型之间的边界。当前 C209 的这两个阶段没有与任务书匹配的已训练权重；保持 `not_configured`，绝不使用随机权重或伪造等级。

## 加载与健康

通过 `STAGE1_MODEL_PATH`、`STAGE1_ADAPTER` 和 Stage3 同名变量配置。适配器写成 `python.module:ClassName`，类的构造函数接收模型文件路径，须实现：

```python
class Adapter:
    def __init__(self, model_path: str): ...
    def predict(self, payload: dict) -> dict: ...
    def health_check(self) -> bool | str: ...  # True 或 "ok" 才健康
    def get_model_info(self) -> dict: ...       # model_name、model_version、recipe_version
```

服务对加载和调用使用锁，避免同一模型实例并发访问。`/api/v1/models` 报告路径、加载状态及模型元数据；`/api/v1/health` 报告阶段状态。尚未加载但配置完整时为 `configured`，首次加载成功且轻量探测通过后才是 `ok`。`/api/v1/ready` 只有三阶段都为 `ok` 且无容量告警时返回 200。

## Stage1 连续 AOI

设备先调用 `POST /api/v1/aoi/streams`，然后按递增序号调用 `POST /api/v1/aoi/streams/{task_id}/frames`。图片已写入可靠帧日志后才返回 202；收到 429 时重试同一序号。适配器每帧收到：

```json
{
  "product_id": "P-001",
  "stream_id": "task-id",
  "source_id": "CAM-1",
  "frame_sequence": 1,
  "image_path": "F:\\npfcode\\liante\\data\\aoi_stream_work\\task-id\\1.jpg",
  "sha256": "64 位十六进制图片校验值"
}
```

`image_path` 在调用期间有效。适配器必须读取真实图片并返回：

```json
{
  "qualified": false,
  "defects": [
    {"type": "scratch", "bbox": [12, 20, 48, 55], "confidence": 0.94}
  ]
}
```

`qualified` 必须为布尔值，`defects` 必须为数组。每个缺陷须有非空 `type`、非负且右下角不小于左上角的四元 `bbox`，以及 0–1 的 `confidence`。`bbox` 使用原图像素坐标；若模型内部归一化，适配器负责转换。缺陷类别及检测阈值应随实际模型发布的 `model_version` 固定，并由验收数据验证。输出不合约时，帧记为 `INVALID_MODEL_OUTPUT`，不会成为合格证据。逐帧结果包括产品 ID、序号、SHA-256、模型和配方版本、耗时及错误，原图到期清空字节，记录继续保留。

## 人工合格证据与 Stage3 分级

目前光学、电学检测只有人工录入。管理员在页面或 `POST /api/v1/qualifications` 中提交相同产品已完成的 AOI 和 Stage2 任务 ID、有限数值测量、调测完成状态及 `manual_review`（`decision=qualified`、服务器绑定认证身份的 `reviewer`、带时区的 `measured_at`、非空 `notes`）。服务核对前序任务、产品 ID、完成时间、Stage2 实际预测行数及配方版本；AOI 逐帧模型/配方版本不能混用，已报告的 AOI 与 Stage2 配方版本须一致；默认最长七天。该流程保存可追查的人工作业声明，未来接入 MES 后须把人工来源替换为可验证的检测记录与阈值。仅分级请求中的 `qualified: true` 无效。

Stage3 请求只含 `product_id`、`qualification_id`。服务从证据存储取得以下适配器输入：

```json
{
  "product_id": "P-001",
  "qualification_id": "evidence-id",
  "aoi_task_id": "aoi-task-id",
  "stage2_task_id": "stage2-task-id",
  "aoi_result": {"qualified": true, "frame_count": 3},
  "optical_features": {"qualified": true, "power_dbm": 2.1},
  "electrical_features": {"qualified": true, "voltage_v": 3.3},
  "tuning_result": {"status": "complete"},
  "manual_review": {"decision": "qualified", "reviewer": "EMP-001", "measured_at": "2026-09-29T10:00:00+08:00", "notes": "工单 123"},
  "recipe_version": "recipe-v1"
}
```

适配器必须为已合格产品返回：

```json
{
  "grade": "B",
  "score": 0.83,
  "confidence": 0.92,
  "reason": "光学与电学指标位于 B 级区间",
  "rule_version": "grade-rule-v1"
}
```

`grade` 限 A/B/C/D；`score`、`confidence` 限 0–1；`reason`、`rule_version` 必须非空。置信度低于 `STAGE3_MIN_CONFIDENCE`（默认 0.8）时，任务为 `REVIEW_REQUIRED`，自动发布的 `grade` 为 `null`，模型建议存于 `suggested_grade`。模型返回不合约时任务失败，绝不把前序不合格品分成 A/B/C/D。

## 运行边界

Stage1 每设备一条有效流，默认 4 流、每流 8 帧；Stage2 和 Stage3 各自有独立的有界执行队列。超额请求返回 429 与 `Retry-After`。任务和帧看门狗会在超时后给出终态并拒绝迟到结果覆盖；若模型内部线程永久卡住，仍需将适配器放入可杀死的独立进程才能释放计算资源。生产接入前，应以真实权重、输入样本、类别/等级映射及延迟和错误率验收，并在完整模型上线后确认 `/api/v1/ready` 返回 200。

## 软件接入补充（2026-10-01）

本轮未配置 Stage1/Stage3 权重。建流支持 view_types/required_views，适配器输入增加 view_type；产品独立 close，缺视角明确失败/不完整。journal ACK 不依赖父任务 JSON；来源元数据确实丢失时记录 STORAGE_PARENT_MISSING，不能猜测来源或合格状态。

人工证据使用 schemas/qualification.py，recipe_id/recipe_version 必填。测量有单位 measurements 或兼容 power_dbm/voltage_v；recorded_by/operator/reviewer 从认证身份绑定，客户端旧 reviewer 字段不决定身份。ACTIVE 证据才可分级，撤销/替换/过期后禁止使用，执行前后复查。缺真实等级时人工复核 grade 字段为 null。

此前契约中的实名/工号要求需结合后续具名账号实现；当前绑定现有认证角色。未来模型需真实样本、版本、规则、延迟验收；软件故障测试不能证明模型准确率。
