import pandas as pd
import os

# ================= 配置 =================
train_data_file = r'F:\npfcode\liante\train_data\data_v5\训练数据1.csv'
merged_data_file = r'F:\npfcode\liante\predict_data_result\merged_all_channels.csv'
output_file = r'F:\npfcode\liante\predict_data_result\merged_ordered_by_train_data.csv'
# =======================================

print("=== 按训练数据1的SN顺序重新整理Merged数据 ===\n")

# 1. 读取训练数据1
print("1. 读取训练数据1...")
try:
    train_df = pd.read_csv(train_data_file)
    print(f"✓ 训练数据1读取成功: {len(train_df)} 行")
    print(f"✓ 唯一SN数量: {train_df['sn'].nunique()}")
    
    # 获取训练数据中SN的顺序（保持原始出现顺序）
    sn_order = train_df['sn'].unique().tolist()
    print(f"✓ 提取到 {len(sn_order)} 个唯一SN的顺序")
    
except Exception as e:
    print(f"✗ 训练数据1读取失败: {e}")
    exit()

# 2. 读取merged数据
print(f"\n2. 读取merged数据...")
try:
    merged_df = pd.read_csv(merged_data_file)
    print(f"✓ Merged数据读取成功: {len(merged_df)} 行")
    print(f"✓ 唯一SN数量: {merged_df['sn'].nunique()}")
    print(f"✓ Channel范围: {sorted(merged_df['test_channel'].unique().tolist())}")
    
except Exception as e:
    print(f"✗ Merged数据读取失败: {e}")
    exit()

# 3. 按训练数据1的SN顺序重新排序
print(f"\n3. 按训练数据1的SN顺序重新排序...")

# 创建SN的排序映射（基于训练数据1中的顺序）
sn_order_map = {sn: i for i, sn in enumerate(sn_order)}

# 为merged数据添加排序键
merged_df['sn_order'] = merged_df['sn'].map(sn_order_map)

# 筛选出在训练数据1中存在的SN
valid_mask = merged_df['sn_order'].notna()
ordered_df = merged_df[valid_mask].copy()

if len(ordered_df) < len(merged_df):
    missing_count = len(merged_df) - len(ordered_df)
    print(f"⚠ 警告: 有 {missing_count} 行数据的SN在训练数据1中不存在，已被过滤")
    
    # 显示被过滤的SN
    missing_sns = merged_df[~valid_mask]['sn'].unique()
    print(f"被过滤的SN: {missing_sns[:10]}{'...' if len(missing_sns) > 10 else ''}")

# 按照SN顺序和channel顺序排序
ordered_df = ordered_df.sort_values(['sn_order', 'test_channel'])
ordered_df = ordered_df.drop('sn_order', axis=1)  # 删除临时排序列
ordered_df = ordered_df.reset_index(drop=True)

print(f"✓ 排序完成: {len(ordered_df)} 行")

# 4. 验证排序结果
print(f"\n4. 验证排序结果...")
first_10_sns = ordered_df['sn'].unique()[:10]
expected_10_sns = sn_order[:10]

print("前10个SN对比:")
print(f"训练数据1顺序: {expected_10_sns}")
print(f"排序后顺序:   {first_10_sns}")
print(f"顺序一致: {list(first_10_sns) == expected_10_sns}")

# 5. 统计信息
print(f"\n5. 统计信息...")
sn_stats = ordered_df.groupby('sn')['test_channel'].count()
print(f"每个SN的平均Channel数: {sn_stats.mean():.2f}")
print(f"Channel数量分布:")
print(sn_stats.value_counts().sort_index())

# 显示前几行预览
print(f"\n6. 数据预览（前10行）:")
preview_cols = ['sn', 'test_channel', 'SN', 'ea', 'bias', 'ea_pred', 'bias_pred']
available_cols = [col for col in preview_cols if col in ordered_df.columns]
print(ordered_df[available_cols].head(10))

# 6. 保存结果
print(f"\n{'='*60}")
print("=== 保存结果 ===")
try:
    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    ordered_df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"✓ 成功保存至: {output_file}")
    print(f"✓ 文件大小: {os.path.getsize(output_file) / 1024:.2f} KB")
    
    # 验证保存的文件
    verify_df = pd.read_csv(output_file)
    print(f"✓ 验证: 保存文件包含 {len(verify_df)} 行数据")
    
except Exception as e:
    print(f"✗ 保存失败: {e}")

print(f"\n{'='*60}")
print("=== 重新排序完成！ ===")
print(f"数据已按照训练数据1中的SN顺序重新排列")
print(f"{'='*60}")
