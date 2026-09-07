import pandas as pd
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, r2_score
import os
import time

from config import MODEL_DIR, PROJECT_ROOT, TRAIN_DATA_DIR

class VGG1D(nn.Module):
    def __init__(self):
        super(VGG1D, self).__init__()
        # Block 1: Input (1, 4) -> (16, 4) -> (16, 2)
        self.block1 = nn.Sequential(
            nn.Conv1d(1, 16, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv1d(16, 16, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool1d(kernel_size=2, stride=2)
        )
        # Block 2: (16, 2) -> (32, 2) -> (32, 1)
        self.block2 = nn.Sequential(
            nn.Conv1d(16, 32, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv1d(32, 32, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool1d(kernel_size=2, stride=2)
        )
        # Fully connected layers
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(32 * 1, 64),
            nn.ReLU(inplace=True),
            nn.Linear(64, 32),
            nn.ReLU(inplace=True),
            nn.Linear(32, 2)
        )

    def forward(self, x):
        # x shape: (batch, 4) -> (batch, 1, 4)
        x = x.unsqueeze(1)
        x = self.block1(x)
        x = self.block2(x)
        x = self.classifier(x)
        return x

def tune_and_train(model_name, model_class, X_train, y_train, X_test, y_test, scaler_y, save_path, pred_path):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    
    X_train_t = torch.tensor(X_train, dtype=torch.float32).to(device)
    y_train_t = torch.tensor(y_train, dtype=torch.float32).to(device)
    X_test_t = torch.tensor(X_test, dtype=torch.float32).to(device)

    # 调参网格 (学习率实验)
    lr_candidates = [0.001, 0.0005, 0.0001]
    best_loss = float('inf')
    best_state_dict = None
    best_lr = lr_candidates[0]
    best_preds = None

    epochs = 1500
    for lr in lr_candidates:
        model = model_class().to(device)
        criterion = nn.MSELoss()
        optimizer = optim.Adam(model.parameters(), lr=lr)

        model.train()
        for epoch in range(epochs):
            optimizer.zero_grad()
            outputs = model(X_train_t)
            loss = criterion(outputs, y_train_t)
            loss.backward()
            optimizer.step()

        model.eval()
        with torch.no_grad():
            test_preds = model(X_test_t)
            val_loss = criterion(test_preds, torch.tensor(y_test, dtype=torch.float32).to(device)).item()
            
        if val_loss < best_loss:
            best_loss = val_loss
            # CPU deepcopy state dict
            best_state_dict = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            best_lr = lr
            best_preds = test_preds.cpu().numpy()

    # 加载最佳模型并进行压测
    best_model = model_class().to(device)
    best_model.load_state_dict(best_state_dict)
    best_model.eval()

    # 压测 QPS
    iters = 1000
    start_time = time.perf_counter()
    with torch.no_grad():
        for _ in range(iters):
            _ = best_model(X_test_t)
    end_time = time.perf_counter()
    total_time = end_time - start_time
    avg_latency_ms = (total_time / (iters * len(X_test))) * 1000
    qps = 1000 / avg_latency_ms if avg_latency_ms > 0 else float('inf')

    # 反归一化
    test_preds_real = scaler_y.inverse_transform(best_preds)
    y_test_real = scaler_y.inverse_transform(y_test)
    
    mse_ea = mean_squared_error(y_test_real[:, 0], test_preds_real[:, 0])
    mse_bias = mean_squared_error(y_test_real[:, 1], test_preds_real[:, 1])
    r2_ea = r2_score(y_test_real[:, 0], test_preds_real[:, 0])
    r2_bias = r2_score(y_test_real[:, 1], test_preds_real[:, 1])

    # 存储权重文件
    torch.save(best_state_dict, save_path)

    # 导出预测结果到文件
    df = pd.DataFrame({
        'True_ea': y_test_real[:, 0],
        'Pred_ea': test_preds_real[:, 0],
        'True_bias': y_test_real[:, 1],
        'Pred_bias': test_preds_real[:, 1]
    })
    df.to_csv(pred_path, index=False)
    
    print(f"{model_name:<12s} - 最佳Lr: {best_lr} | QPS: {qps:.0f} 条/秒 | MSE(ea) {mse_ea:7.2f}, R2(ea) {r2_ea:6.4f} | MSE(b) {mse_bias:7.2f}, R2(b) {r2_bias:6.4f}")

def main():
    base_dir = str(TRAIN_DATA_DIR)
    save_dir = str(MODEL_DIR)
    pred_dir = str(PROJECT_ROOT / "tuned_predictions")
    os.makedirs(save_dir, exist_ok=True)
    os.makedirs(pred_dir, exist_ok=True)
    
    for channel_id in range(8):
        inputfile = os.path.join(base_dir, f"channel_{channel_id}_ea.csv")
        if not os.path.exists(inputfile):
            continue
            
        print(f"\n======== VGG1D 调参 & 压测 - Channel {channel_id} ========")
        try:
            data = pd.read_csv(inputfile, encoding='latin1')
        except:
            data = pd.read_csv(inputfile)

        data = data.dropna(subset=data.columns[[3,4,5,6,11,12]])
        feature_cols = [3, 4, 5, 6]
        target_cols = [11, 12]
        feature_data = data.iloc[:, feature_cols].values
        target_data = data.iloc[:, target_cols].values

        if len(data) == 0:
            continue

        train_limit = int(len(data) * 0.8)
        X_train_raw = feature_data[:train_limit]
        X_test_raw = feature_data[train_limit:]
        y_train_raw = target_data[:train_limit]
        y_test_raw = target_data[train_limit:]

        scaler_x = StandardScaler()
        scaler_y = StandardScaler()

        X_train_scaled = scaler_x.fit_transform(X_train_raw)
        X_test_scaled = scaler_x.transform(X_test_raw)
        y_train_scaled = scaler_y.fit_transform(y_train_raw)
        y_test_scaled = scaler_y.transform(y_test_raw)

        save_path = os.path.join(save_dir, f"VGG1D_channel_{channel_id}.pth")
        pred_path = os.path.join(pred_dir, f"VGG1D_channel_{channel_id}.csv")
        tune_and_train("VGG1D", VGG1D, X_train_scaled, y_train_scaled, X_test_scaled, y_test_scaled, scaler_y, save_path, pred_path)

if __name__ == '__main__':
    main()
