# Legacy / Unused Code

这个目录保存当前 **MLP-only AutoML + Flask** 主链路不再使用的代码，避免旧实现继续占据项目根目录或被误认为生产入口。

## compatibility

- `automl_train_all_models.py`：旧“所有模型”入口。
- `train_mlp.py`：旧 MLP 兼容入口。
- `mlp_model.py`：早期模型导入兼容层。
- `test_prediction.py`：早期手工 API 请求脚本。

## old_models

历史 ResNet、UNet、TransUNet、VGG 训练脚本。它们不参与当前 AutoML，也不会被 Flask 服务导入。

## tools

与核心服务无关的一次性数据清洗、排序和格式转换脚本。

## torchserve

历史 TorchServe 配置和已禁用的启动脚本。当前服务只使用 Flask + PyTorch。

这些文件仅供追溯，不纳入当前功能测试，也不保证可以作为独立入口直接运行。需要恢复某项旧功能时，应先迁移到当前模块结构并补充测试，不要直接复制回根目录。

