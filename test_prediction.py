import requests
import json

# 1. 确定你想调用哪个模型。
# 填入你打包好的模型全名，例如 "ResNet1D_channel_0" 或是 "MLP_channel_5"
MODEL_NAME = "ResNet1D_channel_0" 
url = f"http://127.0.0.1:8080/predictions/{MODEL_NAME}"

# 2. 准备需要预测的新数据
# 这是模型输入特征的4维数组列表（里面可以塞多个样本一起预测，这里塞了2条数据）
test_data = [
    [1.573, 4.598, 1.439, 0.919], 
    [2.100, 3.500, 1.100, 1.200]
]

# 3. 发送请求给 TorchServe 服务
response = requests.post(
    url, 
    data=json.dumps(test_data), 
    headers={"Content-Type": "application/json"}
)

# 4. 打印结果
if response.status_code == 200:
    print("预测成功！返回结果如下：")
    print(response.json())
else:
    print(f"请求失败！状态码：{response.status_code}, 错误信息：{response.text}")