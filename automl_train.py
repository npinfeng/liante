"""
automl_train.py
===============
基于 Optuna 的 AutoML 超参/架构搜索脚本。

功能：
  - 自动从 MLP / ResNet1D / UNet1D / TransUNet1D / VGG1D 中选最优架构
  - 自动搜索：学习率、隐藏层维度、Dropout率、激活函数
  - 每个 Channel 独立优化，保存最优模型到 saved_models/
  - 输出格式与 backend_server.py 完全兼容（直接可用于推理）

依赖安装：
  pip install optuna

运行：
  python automl_train.py
"""

import os
import sys
import time
import warnings
import pandas as pd
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, r2_score

warnings.filterwarnings("ignore")

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
except ImportError:
    print("❌ 未检测到 Optuna，请先安装: pip install optuna")
    sys.exit(1)

# ====================== 路径配置 ======================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "train_data", "data_v5", "split_by_channel")
SAVE_DIR = os.path.join(BASE_DIR, "automl", "saved_models")
os.makedirs(SAVE_DIR, exist_ok=True)

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# ====================== 超参搜索空间 ======================
N_TRIALS = 30        # 每个 Channel 的搜索次数（越大越准但越慢）
N_EPOCHS = 500       # 每次 Trial 的训练轮数
N_CHANNELS = 8       # 共8个 Channel

# ====================== 模型定义（可动态配置维度）======================

class FlexMLP(nn.Module):
    """可配置隐层维度和Dropout的MLP"""
    def __init__(self, hidden_dim: int, dropout: float, activation: str):
        super().__init__()
        act_fn = {"relu": nn.ReLU, "tanh": nn.Tanh, "gelu": nn.GELU}[activation]
        self.net = nn.Sequential(
            nn.Linear(4, hidden_dim), act_fn(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim // 2), act_fn(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim // 2, 16), act_fn(),
            nn.Linear(16, 2)
        )
    def forward(self, x):
        return self.net(x)


class FlexResBlock(nn.Module):
    def __init__(self, dim: int, activation: str):
        super().__init__()
        act_fn = {"relu": nn.ReLU, "tanh": nn.Tanh, "gelu": nn.GELU}[activation]
        self.fc1 = nn.Linear(dim, dim)
        self.fc2 = nn.Linear(dim, dim)
        self.act = act_fn()
    def forward(self, x):
        return self.act(self.fc2(self.act(self.fc1(x))) + x)


class FlexResNet1D(nn.Module):
    """可配置宽度和深度的ResNet"""
    def __init__(self, hidden_dim: int, n_blocks: int, activation: str):
        super().__init__()
        self.in_fc = nn.Linear(4, hidden_dim)
        self.blocks = nn.Sequential(*[FlexResBlock(hidden_dim, activation) for _ in range(n_blocks)])
        self.out_fc = nn.Linear(hidden_dim, 2)
    def forward(self, x):
        return self.out_fc(self.blocks(F.relu(self.in_fc(x))))


class FlexUNet1D(nn.Module):
    """固定拓扑UNet（输入4维，卷积需保持此维度）"""
    def __init__(self):
        super().__init__()
        self.enc1 = nn.Conv1d(1, 16, 3, padding=1)
        self.enc2 = nn.Conv1d(16, 32, 3, padding=1)
        self.bottleneck = nn.Conv1d(32, 64, 3, padding=1)
        self.up1 = nn.ConvTranspose1d(64, 32, 2, stride=2)
        self.dec1 = nn.Conv1d(64, 32, 3, padding=1)
        self.up2 = nn.ConvTranspose1d(32, 16, 2, stride=2)
        self.dec2 = nn.Conv1d(32, 16, 3, padding=1)
        self.out_fc = nn.Linear(16 * 4, 2)
    def forward(self, x):
        x = x.unsqueeze(1)
        e1 = F.relu(self.enc1(x))
        p1 = F.max_pool1d(e1, 2)
        e2 = F.relu(self.enc2(p1))
        p2 = F.max_pool1d(e2, 2)
        b = F.relu(self.bottleneck(p2))
        d1 = F.relu(self.dec1(torch.cat([self.up1(b), e2], dim=1)))
        d2 = F.relu(self.dec2(torch.cat([self.up2(d1), e1], dim=1)))
        return self.out_fc(d2.view(d2.size(0), -1))


class FlexTransUNet1D(nn.Module):
    """固定拓扑TransUNet"""
    def __init__(self):
        super().__init__()
        self.enc1 = nn.Conv1d(1, 16, 3, padding=1)
        self.enc2 = nn.Conv1d(16, 32, 3, padding=1)
        self.bottleneck = nn.Conv1d(32, 64, 3, padding=1)
        enc_layer = nn.TransformerEncoderLayer(d_model=64, nhead=4, dim_feedforward=128, batch_first=True)
        self.transformer = nn.TransformerEncoder(enc_layer, num_layers=2)
        self.up1 = nn.ConvTranspose1d(64, 32, 2, stride=2)
        self.dec1 = nn.Conv1d(64, 32, 3, padding=1)
        self.up2 = nn.ConvTranspose1d(32, 16, 2, stride=2)
        self.dec2 = nn.Conv1d(32, 16, 3, padding=1)
        self.out_fc = nn.Linear(16 * 4, 2)
    def forward(self, x):
        x = x.unsqueeze(1)
        e1 = F.relu(self.enc1(x))
        p1 = F.max_pool1d(e1, 2)
        e2 = F.relu(self.enc2(p1))
        p2 = F.max_pool1d(e2, 2)
        b = F.relu(self.bottleneck(p2))
        t = self.transformer(b.permute(0, 2, 1)).permute(0, 2, 1)
        d1 = F.relu(self.dec1(torch.cat([self.up1(t), e2], dim=1)))
        d2 = F.relu(self.dec2(torch.cat([self.up2(d1), e1], dim=1)))
        return self.out_fc(d2.view(d2.size(0), -1))


class FlexVGG1D(nn.Module):
    """固定拓扑VGG"""
    def __init__(self):
        super().__init__()
        self.block1 = nn.Sequential(
            nn.Conv1d(1, 16, 3, padding=1), nn.ReLU(inplace=True),
            nn.Conv1d(16, 16, 3, padding=1), nn.ReLU(inplace=True),
            nn.MaxPool1d(2, 2)
        )
        self.block2 = nn.Sequential(
            nn.Conv1d(16, 32, 3, padding=1), nn.ReLU(inplace=True),
            nn.Conv1d(32, 32, 3, padding=1), nn.ReLU(inplace=True),
            nn.MaxPool1d(2, 2)
        )
        self.classifier = nn.Sequential(
            nn.Flatten(), nn.Linear(32, 64), nn.ReLU(inplace=True),
            nn.Linear(64, 32), nn.ReLU(inplace=True), nn.Linear(32, 2)
        )
    def forward(self, x):
        return self.classifier(self.block2(self.block1(x.unsqueeze(1))))


# ====================== 模型工厂（由 Trial 参数决定实例）======================

def build_model(trial: optuna.Trial) -> tuple[nn.Module, str]:
    """
    根据 Optuna Trial 建议的参数动态构建模型，
    返回 (model, model_name_for_saving)
    """
    arch = trial.suggest_categorical("arch", ["MLP", "ResNet1D", "UNet1D", "TransUNet1D", "VGG1D"])

    if arch == "MLP":
        hidden_dim = trial.suggest_categorical("mlp_hidden", [32, 64, 128, 256])
        dropout = trial.suggest_float("mlp_dropout", 0.0, 0.4, step=0.1)
        activation = trial.suggest_categorical("mlp_act", ["relu", "tanh", "gelu"])
        model = FlexMLP(hidden_dim, dropout, activation)

    elif arch == "ResNet1D":
        hidden_dim = trial.suggest_categorical("res_hidden", [32, 64, 128])
        n_blocks = trial.suggest_int("res_blocks", 1, 4)
        activation = trial.suggest_categorical("res_act", ["relu", "tanh", "gelu"])
        model = FlexResNet1D(hidden_dim, n_blocks, activation)

    elif arch == "UNet1D":
        model = FlexUNet1D()

    elif arch == "TransUNet1D":
        model = FlexTransUNet1D()

    else:  # VGG1D
        model = FlexVGG1D()

    return model, arch


# ====================== 单次 Trial 训练逻辑 ======================

def run_trial(trial: optuna.Trial, X_train, y_train, X_val, y_val) -> float:
    """训练一次并返回验证集 MSE（Optuna 最小化此值）"""
    lr = trial.suggest_float("lr", 1e-4, 1e-2, log=True)
    weight_decay = trial.suggest_float("weight_decay", 1e-5, 1e-2, log=True)
    optimizer_name = trial.suggest_categorical("optimizer", ["Adam", "AdamW", "SGD"])

    model, _ = build_model(trial)
    model = model.to(DEVICE)

    X_tr = torch.tensor(X_train, dtype=torch.float32, device=DEVICE)
    y_tr = torch.tensor(y_train, dtype=torch.float32, device=DEVICE)
    X_vl = torch.tensor(X_val, dtype=torch.float32, device=DEVICE)
    y_vl = torch.tensor(y_val, dtype=torch.float32, device=DEVICE)

    if optimizer_name == "Adam":
        optimizer = optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    elif optimizer_name == "AdamW":
        optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    else:
        optimizer = optim.SGD(model.parameters(), lr=lr, weight_decay=weight_decay, momentum=0.9)

    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=N_EPOCHS)
    criterion = nn.MSELoss()

    model.train()
    for epoch in range(N_EPOCHS):
        optimizer.zero_grad()
        loss = criterion(model(X_tr), y_tr)
        loss.backward()
        optimizer.step()
        scheduler.step()

        # Optuna 剪枝：如果当前 Trial 明显差于已知最优，提前终止
        if epoch % 50 == 49:
            model.eval()
            with torch.no_grad():
                val_loss = criterion(model(X_vl), y_vl).item()
            model.train()
            trial.report(val_loss, epoch)
            if trial.should_prune():
                raise optuna.exceptions.TrialPruned()

    model.eval()
    with torch.no_grad():
        val_loss = criterion(model(X_vl), y_vl).item()
    return val_loss


# ====================== 单 Channel 完整 AutoML 流程 ======================

def automl_channel(channel_id: int):
    inputfile = os.path.join(DATA_DIR, f"channel_{channel_id}_ea.csv")
    if not os.path.exists(inputfile):
        print(f"  ⚠️  Channel {channel_id}: 数据文件不存在，跳过")
        return

    print(f"\n{'='*55}")
    print(f"  🔍 AutoML 搜索 - Channel {channel_id}  ({N_TRIALS} Trials)")
    print(f"{'='*55}")

    # 读数据
    try:
        data = pd.read_csv(inputfile, encoding='latin1')
    except Exception:
        data = pd.read_csv(inputfile)

    data = data.dropna(subset=data.columns[[3, 4, 5, 6, 11, 12]])
    if len(data) < 10:
        print(f"  ⚠️  Channel {channel_id}: 数据量不足，跳过")
        return

    feature_cols = [3, 4, 5, 6]
    target_cols = [11, 12]
    X_raw = data.iloc[:, feature_cols].values
    y_raw = data.iloc[:, target_cols].values

    split = int(len(data) * 0.8)
    X_train_raw, X_val_raw = X_raw[:split], X_raw[split:]
    y_train_raw, y_val_raw = y_raw[:split], y_raw[split:]

    scaler_x = StandardScaler().fit(X_train_raw)
    scaler_y = StandardScaler().fit(y_train_raw)

    X_train = scaler_x.transform(X_train_raw)
    X_val   = scaler_x.transform(X_val_raw)
    y_train = scaler_y.transform(y_train_raw)
    y_val   = scaler_y.transform(y_val_raw)

    # 创建 Optuna Study（最小化验证集 MSE）
    sampler = optuna.samplers.TPESampler(seed=42)
    pruner  = optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=100)
    study = optuna.create_study(
        direction="minimize",
        sampler=sampler,
        pruner=pruner,
        study_name=f"channel_{channel_id}"
    )

    t0 = time.time()
    study.optimize(
        lambda trial: run_trial(trial, X_train, y_train, X_val, y_val),
        n_trials=N_TRIALS,
        show_progress_bar=False
    )
    elapsed = time.time() - t0

    best = study.best_trial
    best_arch = best.params.get("arch", "Unknown")
    best_lr   = best.params.get("lr", "?")

    # -------- 用最优超参重训整个数据集并保存 --------
    print(f"  ✅ 最优架构: {best_arch} | lr={best_lr:.5f} | Val MSE={best.value:.6f} | 耗时 {elapsed:.1f}s")
    print(f"  📦 正在用完整训练集重训最佳模型并保存...")

    # 重建最优模型
    best_model, arch_name = build_model(best)
    best_model = best_model.to(DEVICE)

    X_full_t = torch.tensor(X_train, dtype=torch.float32, device=DEVICE)
    y_full_t = torch.tensor(y_train, dtype=torch.float32, device=DEVICE)

    optimizer_name = best.params.get("optimizer", "Adam")
    weight_decay   = best.params.get("weight_decay", 1e-4)
    lr_val         = best.params.get("lr", 1e-3)

    if optimizer_name == "Adam":
        opt = optim.Adam(best_model.parameters(), lr=lr_val, weight_decay=weight_decay)
    elif optimizer_name == "AdamW":
        opt = optim.AdamW(best_model.parameters(), lr=lr_val, weight_decay=weight_decay)
    else:
        opt = optim.SGD(best_model.parameters(), lr=lr_val, weight_decay=weight_decay, momentum=0.9)

    scheduler = optim.lr_scheduler.CosineAnnealingLR(opt, T_max=N_EPOCHS * 2)
    criterion = nn.MSELoss()

    best_model.train()
    for _ in range(N_EPOCHS * 2):
        opt.zero_grad()
        loss = criterion(best_model(X_full_t), y_full_t)
        loss.backward()
        opt.step()
        scheduler.step()

    # 验证集最终指标
    best_model.eval()
    X_vl_t = torch.tensor(X_val, dtype=torch.float32, device=DEVICE)
    with torch.no_grad():
        preds_scaled = best_model(X_vl_t).cpu().numpy()

    preds_real  = scaler_y.inverse_transform(preds_scaled)
    y_val_real  = scaler_y.inverse_transform(y_val)

    mse_ea   = mean_squared_error(y_val_real[:, 0], preds_real[:, 0])
    mse_bias = mean_squared_error(y_val_real[:, 1], preds_real[:, 1])
    r2_ea    = r2_score(y_val_real[:, 0], preds_real[:, 0])
    r2_bias  = r2_score(y_val_real[:, 1], preds_real[:, 1])

    print(f"  📊 最终指标: MSE(ea)={mse_ea:.2f}, R²(ea)={r2_ea:.4f} | MSE(bias)={mse_bias:.2f}, R²(bias)={r2_bias:.4f}")

    # 保存权重 —— 使用与 backend_server.py 兼容的命名规则
    save_path = os.path.join(SAVE_DIR, f"{arch_name}_channel_{channel_id}.pth")
    torch.save({k: v.cpu() for k, v in best_model.state_dict().items()}, save_path)
    print(f"  💾 模型已保存: {save_path}")

    return {
        "channel": channel_id,
        "arch": arch_name,
        "best_lr": lr_val,
        "val_mse": best.value,
        "mse_ea": mse_ea,
        "mse_bias": mse_bias,
        "r2_ea": r2_ea,
        "r2_bias": r2_bias,
    }


# ====================== 主入口 ======================

def main():
    print("=" * 55)
    print("  🤖 Liante AutoML 训练器（基于 Optuna TPE）")
    print(f"  设备: {DEVICE} | Trials/Channel: {N_TRIALS}")
    print("=" * 55)

    summary = []
    for ch in range(N_CHANNELS):
        result = automl_channel(ch)
        if result:
            summary.append(result)

    if summary:
        print(f"\n{'='*55}")
        print("  📋 全局 AutoML 搜索汇总")
        print(f"{'='*55}")
        df = pd.DataFrame(summary)
        df = df.round(4)
        print(df.to_string(index=False))

        report_path = os.path.join(BASE_DIR, "automl_report.csv")
        df.to_csv(report_path, index=False, encoding="utf-8-sig")
        print(f"\n  📄 完整报告已保存至: {report_path}")

    print("\n✅ AutoML 搜索完成！模型已保存至 saved_models/，可直接被 backend_server.py 调用。")


if __name__ == "__main__":
    main()
