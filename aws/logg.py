import os
import re

from collections import defaultdict

# 正则表达式
pattern = re.compile(
    r"shard_id (\d+) node (\d+) stop; total time: (\d+\.\d+); total TPS: (\d+\.\d+); average latency: (\d+\.\d+)")

# 指定文件夹路径
folder_path = 'D:\Documents\WeChat Files\wxid_1wyt3kq7eo1g12\FileStorage\File\\2024-01\log\\8 4 1 5 2000 20000'

matches =  []

# 遍历文件夹
for filename in os.listdir(folder_path):
    file_path = os.path.join(folder_path, filename)

    # 确保是文件且不是目录
    if os.path.isfile(file_path):
        with open(file_path, 'r', encoding='utf-8') as file:
            # 读取文件内容
            content = file.read()

            # 在内容中查找匹配项
            matches+=pattern.findall(content)


#print(matches)

data = matches

# 用于存储每组的TPS和latency值
grouped_data = defaultdict(list)

# 遍历数据并分组
for item in data:
    group_id = item[0]
    tps = float(item[3])
    latency = float(item[4])
    grouped_data[group_id].append((tps, latency))

# 用于存储计算结果
group_averages = {}
total_tps = 0
total_latency = 0
count = 0

# 计算每组的平均TPS和latency
for group_id, values in grouped_data.items():
    avg_tps = sum(tps for tps, _ in values) / len(values)
    avg_latency = sum(latency for _, latency in values) / len(values)
    group_averages[group_id] = (avg_tps, avg_latency)
    total_tps += avg_tps
    total_latency += avg_latency
    count += 1

# 计算所有组的latency的整体平均值
overall_avg_latency = total_latency / count

print("每组的平均TPS和latency：")
for group_id, averages in group_averages.items():
    print(f"组 {group_id}: TPS = {averages[0]:.3f}, Latency = {averages[1]:.3f}")

print(f"\n不同组的TPS累加总和: {total_tps:.3f}")
print(f"所有组的latency平均值: {overall_avg_latency:.3f}")