# Liante 在实际电脑上的运行与训练指南

本文面向 Windows 电脑。目标是依次完成：安装环境、检查数据、运行自动测试、做一次小规模真实训练、执行正式训练，并验证模型确实能够加载和预测。

> 建议先只训练一个 Channel。确认数据和完整流程没有问题后，再训练全部 Channel。小规模训练产生的模型只能证明流程能运行，不能代表模型效果已经足够好。

## 1. 准备项目和 Python

建议使用 64 位 Python 3.10 或 3.11。将整个 `liante` 项目复制到目标电脑，例如 `D:\projects\liante`，然后在项目根目录打开 PowerShell：

```powershell
cd D:\projects\liante
```

创建独立虚拟环境：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

如果电脑没有 `py` 命令，但已经安装了 Python，可改用：

```powershell
python -m venv .venv
```

如果 PowerShell 不允许执行 `Activate.ps1`，不必修改系统安全策略，后续直接使用虚拟环境中的解释器即可：

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

后文命令中的 `python` 也相应替换成 `.\.venv\Scripts\python.exe`。

### 检查依赖和 GPU

```powershell
python -c "import flask, joblib, numpy, optuna, pandas, pydantic, sklearn, torch; print('依赖导入成功'); print('PyTorch:', torch.__version__); print('CUDA 可用:', torch.cuda.is_available()); print('训练设备:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

输出“依赖导入成功”即可继续。CUDA 不可用时程序会自动使用 CPU，功能不受影响，只是训练较慢。

如果准备使用 NVIDIA GPU，应根据目标电脑的显卡驱动，使用 [PyTorch 官方安装选择器](https://pytorch.org/get-started/locally/)安装合适的 PyTorch。建议仍先完成一次 CPU 或小规模训练，确认数据链路正确。如果显存不足或 CUDA 环境不稳定，可以在当前 PowerShell 会话中强制使用 CPU：

```powershell
$env:CUDA_VISIBLE_DEVICES = "-1"
```

## 2. 准备训练数据

每个 Channel 对应一个 CSV 文件，程序只识别以下文件名：

```text
channel_0_ea.csv
channel_1_ea.csv
channel_2_ea.csv
channel_3_ea.csv
channel_4_ea.csv
channel_5_ea.csv
channel_6_ea.csv
channel_7_ea.csv
```

只训练某一个 Channel 时，只需要准备对应文件。CSV 必须符合以下规则：

- 第一行是表头。
- 至少有 13 列；程序按列的位置读取数据，而不是按列名读取。
- 第 4、5、6、7 列是 4 个输入特征，对应 Python 列序号 `3, 4, 5, 6`。
- 第 12、13 列是两个训练目标，依次为 `ea` 和 `bias`，对应 Python 列序号 `11, 12`。
- 上述 6 列必须能转换为数值。包含空值或非数值的行会被删除。
- 删除无效行后，代码最低要求 10 行；真实训练通常应准备远多于 10 行的数据。
- 训练文件目前只支持 CSV，不直接支持 XLSX。Excel 数据请先“另存为 CSV”。

程序保持原始行顺序，按 `70% / 15% / 15%` 划分训练集、验证集和测试集。因此，如果数据有时间先后关系，应按时间从早到晚排列；最后 15% 会作为独立测试集，不能提前参与调参。

有两种放置数据的方式。

方式 A：放进项目默认目录：

```text
liante\train_data\data_v5\split_by_channel\channel_0_ea.csv
```

方式 B：数据保留在任意目录，训练时通过 `--data-dir` 指定，例如：

```text
D:\liante_data\channel_0_ea.csv
```

### 在训练前检查一个 Channel

如果数据在 `D:\liante_data`，运行：

```powershell
python -c "from training.data_loader import load_channel_data; d=load_channel_data(0, r'D:\liante_data'); print('文件:', d.source_path); print('划分:', d.sample_counts); print('输入维度:', d.x_train.shape[1]); print('输出维度:', d.y_train.shape[1])"
```

正常输出示例：

```text
文件: D:\liante_data\channel_0_ea.csv
划分: {'train': 700, 'validation': 150, 'test': 150}
输入维度: 4
输出维度: 2
```

把命令中的 `0` 改成其他 Channel 编号，即可逐个检查。若实际行数明显少于 CSV 总行数，通常说明选中的 6 列存在空值、文本或格式错误。

## 3. 先运行代码自带测试

在项目根目录执行：

```powershell
python -m pytest -q
```

只有测试全部通过，再使用真实数据训练。测试会使用自动生成的小数据和临时目录，不会修改你的正式数据与正式模型。

常见错误：

- `ModuleNotFoundError`：当前终端使用的不是安装过依赖的虚拟环境。运行 `python -c "import sys; print(sys.executable)"` 检查解释器路径。
- DLL 加载失败：通常是 Python 环境中混装了不兼容的 NumPy/Pandas/PyTorch 包。删除 `.venv` 后重新创建干净环境，比在旧环境中反复覆盖安装更可靠。

## 4. 用真实数据做小规模冒烟训练

以下命令使用 Channel 0 的真实数据，只搜索 2 次、每次最多训练 5 个 epoch，并把结果写入单独的 `smoke_models` 目录：

```powershell
python automl_train.py --data-dir "D:\liante_data" --save-dir ".\smoke_models" --channels 0 --n-trials 2 --max-epochs 5 --patience 3
```

这一步应当完整经历数据读取、Optuna 搜索、最佳权重恢复、测试集评估和文件保存。运行成功后应出现：

```text
smoke_models\
├── mlp_automl_report.json
└── channel_0\
    ├── model.pth
    ├── config.json
    ├── scaler_x.joblib
    ├── scaler_y.joblib
    ├── metrics.json
    └── training_metadata.json
```

可直接查看测试集指标：

```powershell
Get-Content .\smoke_models\channel_0\metrics.json
```

只要命令正常结束、上述 6 个 artifact 文件齐全，就说明真实数据能够跑通训练链路。因为只有 `2 × 5` 的搜索量，此时指标不好并不代表正式训练会失败。

## 5. 执行正式训练

### 先正式训练一个 Channel

```powershell
python automl_train.py --data-dir "D:\liante_data" --save-dir ".\saved_models" --channels 0 --n-trials 30 --max-epochs 500 --patience 40
```

参数含义：

- `--channels 0`：训练 Channel 0，可以一次写多个编号。
- `--n-trials 30`：Optuna 尝试 30 组网络结构和训练参数。
- `--max-epochs 500`：每次尝试最多训练 500 个 epoch。
- `--patience 40`：验证集连续 40 个 epoch 没有改善时提前停止。
- `--seed 42`：随机种子，默认就是 42，用于提高结果可复现性。
- `--save-dir .\saved_models`：保存最终模型和指标的位置。

确认 Channel 0 的结果和运行时间合理后，再串行训练全部 Channel：

```powershell
python automl_train.py --data-dir "D:\liante_data" --save-dir ".\saved_models" --channels 0 1 2 3 4 5 6 7 --n-trials 30 --max-epochs 500 --patience 40
```

正式训练可能持续较长时间，取决于每个 Channel 的数据量、CPU/GPU 性能、提前停止发生的时间以及搜索到的网络大小。不要一开始就盲目提高 `n-trials` 或 `max-epochs`。训练时可以通过任务管理器或 `nvidia-smi` 观察资源占用。

重新训练同一个 Channel 会更新该 Channel 的现有 artifact。需要保留旧结果时，请更换 `--save-dir` 或先备份原目录。

## 6. 判断是否“真正训练出了可用结果”

训练命令成功只表示模型已经生成。是否可用还要看 `saved_models\channel_N\metrics.json` 中独立测试集的指标：

- `mae`：平均绝对误差，单位与原始 `ea` 或 `bias` 相同，最容易结合业务容差判断。
- `rmse`：比 MAE 更强调少量的大误差，越小越好。
- `r2`：越接近 1 越好；接近 0 表示与“始终预测平均值”相近；小于 0 通常表示泛化效果较差。
- `overall`：对两个输出的指标做统一汇总，其中 R²为 ea 与 bias 的等权平均。由于两个目标的业务含义和量纲可能不同，最终仍应优先分别查看 `ea` 和 `bias`。

还应查看 `training_metadata.json`，确认：

- `sample_counts` 与预期数据量一致；
- `source_file` 指向正确的数据文件；
- `device` 是预期的 `cpu` 或 `cuda`；
- `completed_trials` 大于 0；
- `split_strategy` 为 `sequential_70_15_15`。

不要只凭训练 loss 判断模型好坏。至少应把测试集 MAE/RMSE 与业务允许误差、现有算法或“预测训练集目标均值”的简单基线比较。

## 7. 启动服务并做一次真实预测

如果训练数据和模型使用项目默认目录，直接启动：

```powershell
python backend_server.py --host 127.0.0.1 --port 8000
```

如果数据或模型在其他目录，启动前设置当前 PowerShell 会话的环境变量：

```powershell
$env:LIANTE_TRAIN_DATA_DIR = "D:\liante_data"
$env:LIANTE_MODEL_DIR = "D:\projects\liante\saved_models"
python backend_server.py --host 127.0.0.1 --port 8000
```

浏览器打开：

- 健康检查：`http://127.0.0.1:8000/health`
- 预测页面：`http://127.0.0.1:8000/gui/index.html`
- AutoML 训练页面：`http://127.0.0.1:8000/gui/train.html`

`/health` 的 `available_channels` 应包含刚刚训练完成的 Channel。然后在预测页面选择对应 Channel，输入 4 个与训练数据第 4–7 列含义、单位和顺序完全一致的特征值，确认能够返回 `ea` 和 `bias`。

也可以另开一个 PowerShell 窗口调用 API：

```powershell
$body = @{ channel = 0; data = @(, @(1.573, 4.598, 1.439, 0.919)) } | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/predict" -ContentType "application/json" -Body $body
```

将示例的 4 个数替换为你的真实特征。按 `Ctrl+C` 停止服务。

## 8. 推荐的验收顺序

1. 依赖检查成功，确认实际使用的 Python 解释器。
2. 对每个要训练的 Channel 执行数据加载检查，核对行数和 `4 → 2` 维度。
3. `python -m pytest -q` 全部通过。
4. 使用真实 Channel 0 完成 `2 trials × 5 epochs` 冒烟训练。
5. 检查模型 6 个 artifact 文件与 `metrics.json`。
6. 对 Channel 0 执行正式训练，并根据业务容差判断测试集指标。
7. 启动服务，确认 `/health` 能识别模型，并用真实输入完成预测。
8. 最后再训练其余 Channel。

完成以上 8 步，才能同时证明：代码测试通过、真实数据格式正确、训练流程可运行、模型成功保存、模型可以重新加载预测，并且测试集效果达到你的实际要求。
