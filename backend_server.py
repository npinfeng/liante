import os
import sys
import torch
import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
from contextlib import asynccontextmanager
import torch.nn as nn
import torch.nn.functional as F
from sklearn.preprocessing import StandardScaler

# --- Path Configuration ---
# 获取项目根目录（backend_server.py 所在的目录）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

# --- Models ---
class MLP(nn.Module):
    def __init__(self):
        super(MLP, self).__init__()
        self.fc1 = nn.Linear(4, 64)
        self.fc2 = nn.Linear(64, 16)
        self.fc3 = nn.Linear(16, 2)
        self.fc4 = nn.Linear(2, 2)
    def forward(self, x):
        x = torch.tanh(self.fc1(x))
        x = torch.tanh(self.fc2(x))
        x = torch.tanh(self.fc3(x))
        x = self.fc4(x)
        return x

class ResBlock1D(nn.Module):
    def __init__(self, dim):
        super(ResBlock1D, self).__init__()
        self.fc1 = nn.Linear(dim, dim)
        self.fc2 = nn.Linear(dim, dim)
    def forward(self, x):
        res = x
        x = F.relu(self.fc1(x))
        x = self.fc2(x)
        return F.relu(x + res)

class ResNet1D(nn.Module):
    def __init__(self):
        super(ResNet1D, self).__init__()
        self.in_fc = nn.Linear(4, 64)
        self.res1 = ResBlock1D(64)
        self.res2 = ResBlock1D(64)
        self.res3 = ResBlock1D(64)
        self.out_fc = nn.Linear(64, 2)
    def forward(self, x):
        x = F.relu(self.in_fc(x))
        x = self.res1(x)
        x = self.res2(x)
        x = self.res3(x)
        x = self.out_fc(x)
        return x

class UNet1D(nn.Module):
    def __init__(self):
        super(UNet1D, self).__init__()
        self.enc1 = nn.Conv1d(1, 16, kernel_size=3, padding=1) 
        self.enc2 = nn.Conv1d(16, 32, kernel_size=3, padding=1)
        self.bottleneck = nn.Conv1d(32, 64, kernel_size=3, padding=1)
        self.up1 = nn.ConvTranspose1d(64, 32, kernel_size=2, stride=2) 
        self.dec1 = nn.Conv1d(64, 32, kernel_size=3, padding=1) 
        self.up2 = nn.ConvTranspose1d(32, 16, kernel_size=2, stride=2) 
        self.dec2 = nn.Conv1d(32, 16, kernel_size=3, padding=1) 
        self.out_fc = nn.Linear(16 * 4, 2)
    def forward(self, x):
        x = x.unsqueeze(1)
        e1 = F.relu(self.enc1(x))
        p1 = F.max_pool1d(e1, 2)
        e2 = F.relu(self.enc2(p1))
        p2 = F.max_pool1d(e2, 2)
        b = F.relu(self.bottleneck(p2))
        d1 = self.up1(b)
        c1 = torch.cat([d1, e2], dim=1)
        d1 = F.relu(self.dec1(c1))
        d2 = self.up2(d1)
        c2 = torch.cat([d2, e1], dim=1)
        d2 = F.relu(self.dec2(c2))
        d2_flat = d2.view(d2.size(0), -1)
        out = self.out_fc(d2_flat)
        return out

class TransUNet1D(nn.Module):
    def __init__(self):
        super(TransUNet1D, self).__init__()
        self.enc1 = nn.Conv1d(1, 16, kernel_size=3, padding=1) 
        self.enc2 = nn.Conv1d(16, 32, kernel_size=3, padding=1) 
        self.bottleneck = nn.Conv1d(32, 64, kernel_size=3, padding=1) 
        encoder_layer = nn.TransformerEncoderLayer(d_model=64, nhead=4, dim_feedforward=128, batch_first=True)
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=2)
        self.up1 = nn.ConvTranspose1d(64, 32, kernel_size=2, stride=2) 
        self.dec1 = nn.Conv1d(64, 32, kernel_size=3, padding=1) 
        self.up2 = nn.ConvTranspose1d(32, 16, kernel_size=2, stride=2) 
        self.dec2 = nn.Conv1d(32, 16, kernel_size=3, padding=1) 
        self.out_fc = nn.Linear(16 * 4, 2)
    def forward(self, x):
        x = x.unsqueeze(1)
        e1 = F.relu(self.enc1(x)) 
        p1 = F.max_pool1d(e1, 2) 
        e2 = F.relu(self.enc2(p1)) 
        p2 = F.max_pool1d(e2, 2) 
        b = F.relu(self.bottleneck(p2))
        b_t = b.permute(0, 2, 1)
        t_out = self.transformer(b_t)
        t_out = t_out.permute(0, 2, 1)
        d1 = self.up1(t_out) 
        c1 = torch.cat([d1, e2], dim=1) 
        d1 = F.relu(self.dec1(c1)) 
        d2 = self.up2(d1) 
        c2 = torch.cat([d2, e1], dim=1) 
        d2 = F.relu(self.dec2(c2)) 
        d2_flat = d2.view(d2.size(0), -1) 
        out = self.out_fc(d2_flat)
        return out

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

# --- Global State ---
MODELS_CACHE = {}
SCALERS_CACHE = {}
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# 在服务端动态加载所需的权重和预存路径
SAVED_MODELS_DIR = os.path.join(BASE_DIR, "saved_models")
BASE_DATA_DIR = os.path.join(BASE_DIR, "train_data", "data_v5", "split_by_channel")

def load_or_create_scaler(channel_id: int):
    """提取或创建特征归一化器，免除事先显式存储 joblib 的麻烦"""
    if channel_id in SCALERS_CACHE:
        return SCALERS_CACHE[channel_id]
        
    inputfile = os.path.join(BASE_DATA_DIR, f"channel_{channel_id}_ea.csv")
    if not os.path.exists(inputfile):
        raise FileNotFoundError(f"找不到 Channel {channel_id} 的原始数据表来提取归一化分布规则！")
    
    try:
        data = pd.read_csv(inputfile, encoding='latin1')
    except:
        data = pd.read_csv(inputfile)
        
    data = data.dropna(subset=data.columns[[3,4,5,6,11,12]])
    train_limit = int(len(data) * 0.8)
    
    feature_cols = [3, 4, 5, 6]
    target_cols = [11, 12]
    X_train_raw = data.iloc[:train_limit, feature_cols].values
    y_train_raw = data.iloc[:train_limit, target_cols].values

    scaler_x = StandardScaler().fit(X_train_raw)
    scaler_y = StandardScaler().fit(y_train_raw)
    
    SCALERS_CACHE[channel_id] = (scaler_x, scaler_y)
    return scaler_x, scaler_y

def load_model(model_type: str, channel_id: int):
    """基于请求动态挂载模型并缓存，减少IO时间"""
    cache_key = f"{model_type}_{channel_id}"
    if cache_key in MODELS_CACHE:
        return MODELS_CACHE[cache_key]

    pth_path = os.path.join(SAVED_MODELS_DIR, f"{model_type}_channel_{channel_id}.pth")
    if not os.path.exists(pth_path):
        raise FileNotFoundError(f"未找到指定的权重文件: {pth_path}")

    if model_type == "ResNet1D":
        model = ResNet1D()
    elif model_type == "MLP":
        model = MLP()
    elif model_type == "UNet1D":
        model = UNet1D()
    elif model_type == "TransUNet1D":
        model = TransUNet1D()
    elif model_type == "VGG1D":
        model = VGG1D()
    else:
        raise ValueError(f"未知的架构: {model_type}。支持的架构有: ResNet1D, UNet1D, TransUNet1D, MLP, VGG1D")

    model.load_state_dict(torch.load(pth_path, map_location=DEVICE, weights_only=True))
    model.to(DEVICE)
    model.eval()
    
    MODELS_CACHE[cache_key] = model
    return model

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 在服务启动时自动加载预热所有数据字典，避免第一次请求太慢
    print(f"正在后台预热模型缓存和归一化矩阵...")
    for ch in range(8):
        try:
            load_or_create_scaler(ch)
            print(f"【Cache】Channel {ch} 的特征归一化器已就绪")
        except Exception as e:
            pass
    yield
    # Cleanup on exit
    MODELS_CACHE.clear()

app = FastAPI(title="Local PyTorch API Server", lifespan=lifespan)

# Add CORS middleware to allow requests from the frontend GUI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure the gui directory exists in the project root
GUI_DIR = os.path.join(BASE_DIR, "gui")
os.makedirs(GUI_DIR, exist_ok=True)

# Mount the GUI directory to serve static files (HTML, CSS, JS)
app.mount("/gui", StaticFiles(directory=GUI_DIR), name="gui")

@app.get("/")
def read_root():
    return {"message": "Server is running. Access the GUI at /gui/index.html"}

@app.get("/info")
def get_info():
    """Return available models and channels for the frontend to build the UI dynamically"""
    return {
        "models": ["ResNet1D", "UNet1D", "TransUNet1D", "MLP", "VGG1D"],
        "channels": list(range(8))
    }

class PredictRequest(BaseModel):
    model_type: str  # 填写 "MLP", "ResNet1D", "UNet1D", "TransUNet1D"
    channel: int     # 填写 0 到 7 的频道号
    data: List[List[float]] # 输入的四维数据列表，例如 [[1.573, 4.598, 1.439, 0.919]]

@app.post("/predict")
def predict(req: PredictRequest):
    # 动态调取对应归一化器与模型权重
    try:
        model = load_model(req.model_type, req.channel)
        scaler_x, scaler_y = load_or_create_scaler(req.channel)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        # 归一化输入
        scaled_input = scaler_x.transform(req.data)
        input_tensor = torch.tensor(scaled_input, dtype=torch.float32, device=DEVICE)

        # CPU/GPU推理
        with torch.no_grad():
            preds = model(input_tensor)

        # 反归一化并提取指标
        preds_np = preds.cpu().numpy()
        real_preds = scaler_y.inverse_transform(preds_np)

        # JSON格式按 ea 和 bias 字典形式传给后端
        output = [{"ea": float(row[0]), "bias": float(row[1])} for row in real_preds]
        return {
            "status": "success",
            "model_type": req.model_type,
            "channel": req.channel,
            "predictions": output
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    print("==============================================")
    print("启动 Pure Python 部署后台接口服务...")
    print("默认监听地址: http://127.0.0.1:8000")
    print("支持热挂载 `saved_models` 下生成的 24 个模型")
    print("==============================================")
    
    # 启动轻量级 ASGI 服务器
    uvicorn.run("backend_server:app", host="0.0.0.0", port=8000, reload=True)
