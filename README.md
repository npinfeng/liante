# Liante MLP AutoML

Liante 现在是一个 **仅优化 MLP** 的多输出回归系统。模型类型固定为 MLP；Optuna 的职责是为每个 Channel 独立寻找隐藏层数量、每层宽度以及训练超参数。系统使用 Flask 提供异步训练、状态查询和预测接口。

## 核心链路

```text
Channel CSV
  → 顺序划分 Train / Validation / Test（70% / 15% / 15%）
  → 仅在 Train 上拟合 StandardScaler
  → Optuna 搜索 DynamicMLP + 训练超参数
  → Early Stopping + Optuna Pruning
  → 使用最佳 epoch 在 Train + Validation 上重训练
  → 独立 Test 评估
  → 保存模型、配置、Scaler、指标和训练元数据
  → Flask 动态重建 DynamicMLP
  → 前端或 API 预测 ea / bias
```

旧数据读取规则保持不变：特征使用 CSV 的第 4–7 列（位置 3–6），目标 `ea`、`bias` 使用第 12–13 列（位置 11–12）。旧代码一直按行顺序切分；在没有数据字典证明可随机打乱的情况下，新实现继续采用顺序切分以避免潜在时间泄漏。

## 目录

```text
liante/
├── backend_server.py           # Flask API
├── config.py                   # 可移植路径配置
├── models/
│   └── mlp.py                  # 唯一权威 DynamicMLP
├── automl/
│   ├── mlp_trainer.py          # Optuna、重训练、评估、artifact 保存
│   └── search_space.py         # MLP-only 搜索空间
├── training/
│   ├── data_loader.py          # 无泄漏数据划分和标准化
│   └── trainer.py              # DataLoader、Early Stopping、Pruning
├── services/
│   ├── training_service.py
│   └── prediction_service.py   # artifact 加载和 Channel 缓存
├── tasks/
│   └── task_manager.py         # ThreadPoolExecutor 异步任务
├── schemas/
│   ├── training.py
│   └── prediction.py
├── gui/
│   ├── index.html              # 预测
│   └── train.html              # 简单/高级 AutoML
├── legacy/                     # 当前链路不使用的历史代码
├── saved_models/
│   └── channel_0/ ... channel_7/
└── tests/
```

项目根目录只保留当前系统需要的入口和配置。旧多模型脚本、废弃兼容入口、一次性数据工具和 TorchServe 文件统一归档在 `legacy/`；它们不会被新训练/API 链路导入，详情见 `legacy/README.md`。

## 安装与启动

```bash
pip install -r requirements.txt
python backend_server.py
```

服务默认监听 `http://127.0.0.1:8000`。预测页面为 `/gui/index.html`，AutoML 页面为 `/gui/train.html`。

路径均以项目目录为基准，也可设置环境变量：

- `LIANTE_TRAIN_DATA_DIR`：默认 `train_data/data_v5/split_by_channel`
- `LIANTE_MODEL_DIR`：默认 `saved_models`

## AutoML 搜索空间

- `n_layers`: 1–5
- 每层 `hidden_i`: 32 / 64 / 128 / 256 / 512，互相独立
- `activation`: relu / gelu / silu / tanh
- `dropout`: 0.0–0.5
- `batch_norm`: true / false
- `optimizer`: Adam / AdamW / SGD；SGD 额外搜索 momentum
- `learning_rate`: 1e-5–1e-2，对数采样
- `weight_decay`: 1e-6–1e-2，对数采样
- `batch_size`: 16 / 32 / 64 / 128 / 256
- `scheduler`: none / cosine / plateau

默认 Optuna objective 为标准化目标上的 validation MSE，方向为 minimize。Test 不参与任何参数选择。

## API

### 启动单 Channel AutoML

```bash
curl -X POST http://127.0.0.1:8000/automl/train \
  -H "Content-Type: application/json" \
  -d '{"channel":0,"n_trials":30,"max_epochs":500}'
```

响应：`{"task_id":"...","status":"queued"}`。高级模式可传 `search_space`，后端会拒绝未知字段、越界范围和非白名单值。

### 串行训练全部 Channel

```bash
curl -X POST http://127.0.0.1:8000/automl/train-all \
  -H "Content-Type: application/json" \
  -d '{"channels":[0,1,2,3,4,5,6,7],"n_trials":30,"max_epochs":500}'
```

线程池默认 `max_concurrent_jobs=1`，因此不会同时在 GPU 上启动 8 个训练任务。

### 查询状态与结果

```bash
curl http://127.0.0.1:8000/automl/tasks/TASK_ID
curl http://127.0.0.1:8000/automl/tasks/TASK_ID/result
```

状态为 `queued`、`preparing`、`training`、`retraining`、`evaluating`、`saving`、`completed`；异常为 `failed`，不会终止 Flask 服务。

### 预测

```bash
curl -X POST http://127.0.0.1:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"channel":0,"data":[[1.573,4.598,1.439,0.919]]}'
```

模型类型不再由调用方指定。服务根据 Channel 自动加载当前 artifact；重新训练成功后会主动使该 Channel 的缓存失效。

## 模型 artifact

```text
saved_models/channel_0/
├── model.pth
├── config.json
├── scaler_x.joblib
├── scaler_y.joblib
├── metrics.json
└── training_metadata.json
```

`metrics.json` 包含 ea、bias 和 overall 的 MSE、RMSE、MAE、R²。预测端读取配置后构造完全一致的 `DynamicMLP`，再加载权重和两个 Scaler。

## CLI 与测试

```bash
python automl_train.py --channels 0 --n-trials 2 --max-epochs 5 --patience 3
pytest -q
```

CI smoke test 只执行小规模 1–2 trials、2–5 epochs，不会运行完整的 30×500 搜索。
