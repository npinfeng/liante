import pandas as pd
import numpy as np
import os
from sklearn.metrics import mean_squared_error, r2_score, precision_score, recall_score

# ================= 配置 =================
INPUT_FILE = r'e:/projects/liante/train_data/训练数据.xlsx'
OUTPUT_CLEAN_FILE = r'e:/projects/liante/test_results/cleaned_test_data.csv'
OUTPUT_STATS_FILE = r'e:/projects/liante/test_results/excel_test_statistics.csv'

# 判定“差距过大”的阈值 (超过此值则删除)
EA_DIFF_THRESHOLD = 50.0   # ea 误差超过 50 则视为异常
BIAS_DIFF_THRESHOLD = 5.0  # bias 误差超过 5 则视为异常

# 判定“召回”的容差范围 (delta)
# 如果预测值在 [真实值-delta, 真实值+delta] 之间，则视为召回成功
# 例如真实值为 80，delta 为 2，则预测在 78-82 之间即为召回
BIAS_RECALL_DELTA = 2.0 
# ========================================

def calculate_precision_recall(y_true, y_pred, delta=None):
    """
    计算指标。
    如果 delta 为 None: 根据中位数将连续值转为二分类，计算精准率和召回率。
    如果 delta 不为 None: 计算在真实值上下 delta 范围内的命中率（作为 Recall 和 Accuracy）。
    """
    if delta is not None:
        # 计算命中率 (Hit Rate)
        hits = np.abs(y_true - y_pred) <= delta
        hit_rate = np.mean(hits)
        # 在这种定义下，我们将命中率同时作为精准率和召回率返回，因为它是对整体准确度的衡量
        return hit_rate, hit_rate

    # 以真实值的中位数为基准
    threshold = np.median(y_true)
    true_labels = (y_true >= threshold).astype(int)
    pred_labels = (y_pred >= threshold).astype(int)
    
    # 这里的 precision 就是用户常说的“准确率”
    precision = precision_score(true_labels, pred_labels, zero_division=0)
    recall = recall_score(true_labels, pred_labels, zero_division=0)
    
    return precision, recall

def main():
    if not os.path.exists(INPUT_FILE):
        print(f"错误: 找不到文件 {INPUT_FILE}")
        return

    print(f"正在读取数据: {INPUT_FILE} ...")
    try:
        df = pd.read_excel(INPUT_FILE, header=1)
    except Exception as e:
        print(f"读取失败: {e}")
        return

    # 检查必要的列是否存在
    required_cols = ['test_channel', 'ea', 'ea_pred', 'bias', 'bias_pred']
    for col in required_cols:
        if col not in df.columns:
            print(f"错误: 缺少必要列 {col}。当前列名: {df.columns.tolist()}")
            return

    # 预处理：删除 NaN 行
    initial_count = len(df)
    df = df.dropna(subset=required_cols).copy()
    print(f"已清理缺失值行，剩余 {len(df)} / {initial_count}")

    # 1. 计算误差并识别“差距过大”的行
    df['diff_ea'] = (df['ea'] - df['ea_pred']).abs()
    df['diff_bias'] = (df['bias'] - df['bias_pred']).abs()

    outliers_mask = (df['diff_ea'] > EA_DIFF_THRESHOLD) | (df['diff_bias'] > BIAS_DIFF_THRESHOLD)
    outliers_count = outliers_mask.sum()
    
    print(f"识别出误差过大的行数: {outliers_count} (阈值: ea > {EA_DIFF_THRESHOLD}, bias > {BIAS_DIFF_THRESHOLD})")

    # 2. 删除差距过大的行
    df_cleaned = df[~outliers_mask].copy()
    print(f"清理完成，剩余有效行数: {len(df_cleaned)}")

    # 3. 按 Channel 划分计算统计量
    all_channel_stats = []
    
    # 获取唯一的 channel 列表并排序
    channels = sorted(df_cleaned['test_channel'].unique())
    
    for ch in channels:
        ch_data = df_cleaned[df_cleaned['test_channel'] == ch]
        if len(ch_data) < 2:
            continue
            
        y_true_ea = ch_data['ea'].values
        y_pred_ea = ch_data['ea_pred'].values
        y_true_bias = ch_data['bias'].values
        y_pred_bias = ch_data['bias_pred'].values

        # 计算指标
        mse_ea = mean_squared_error(y_true_ea, y_pred_ea)
        mse_bias = mean_squared_error(y_true_bias, y_pred_bias)
        r2_ea = r2_score(y_true_ea, y_pred_ea)
        r2_bias = r2_score(y_true_bias, y_pred_bias)
        prec_ea, recall_ea = calculate_precision_recall(y_true_ea, y_pred_ea)
        prec_bias, recall_bias = calculate_precision_recall(y_true_bias, y_pred_bias, delta=BIAS_RECALL_DELTA)

        all_channel_stats.append({
            'Channel': ch,
            'MSE_ea': mse_ea,
            'MSE_bias': mse_bias,
            'R2_ea': r2_ea,
            'R2_bias': r2_bias,
            'Accuracy_ea': prec_ea,
            'Recall_ea': recall_ea,
            'Accuracy_bias': prec_bias,
            'Recall_bias': recall_bias,
            'Count': len(ch_data)
        })

    # 4. 保存结果
    df_stats = pd.DataFrame(all_channel_stats)
    
    # 统一保留两位小数
    df_stats = df_stats.round(2)
    
    os.makedirs(os.path.dirname(OUTPUT_CLEAN_FILE), exist_ok=True)
    
    df_cleaned.to_csv(OUTPUT_CLEAN_FILE, index=False, encoding='utf-8-sig')
    df_stats.to_csv(OUTPUT_STATS_FILE, index=False, encoding='utf-8-sig')

    print("\n--- 分通道统计结果 ---")
    print(df_stats.to_string(index=False))
    print(f"\n[完成] 清理后的数据已保存至: {OUTPUT_CLEAN_FILE}")
    print(f"[完成] 分通道统计量已保存至: {OUTPUT_STATS_FILE}")

if __name__ == "__main__":
    main()
