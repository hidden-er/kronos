import os
import re

from collections import defaultdict

# 正则表达式
pattern = re.compile(
    r"shard_id (\d+) node (\d+) stop; block_delay: (\d+\.\d+) ; round_delay: (\d+\.\d+) ; time-between-shards: (\d+\.\d+)")


# 指定文件夹路径
folder_path = '../log'

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
    block_delay = float(item[2])
    round_delay = float(item[3])
    #print(block_delay,round_delay)
    grouped_data[group_id].append((block_delay, round_delay))


# 用于存储计算结果
group_averages = {}
total_block_delay = 0
total_round_delay = 0
count = 0

# 计算每组的平均TPS和latency
for group_id, values in grouped_data.items():
    avg_block_delay = sum(block_delay for block_delay, _ in values) / len(values)
    avg_round_delay = sum(round_delay for _, round_delay in values) / len(values)
    group_averages[group_id] = (avg_block_delay, avg_round_delay)
    total_block_delay += avg_block_delay
    total_round_delay += avg_round_delay
    count += 1

# 计算所有组的latency的整体平均值
overall_block_delay = total_block_delay / count
overall_round_delay = total_round_delay / count

print("每组的平均block_delay、round_delay和time-between-shards：")
for group_id, averages in group_averages.items():
    print(f"组 {group_id}: block_delay = {averages[0]:.3f}, round_delay = {averages[1]:.3f} , time-between-shards = {averages[1]-averages[0]:.3f}")

print(f"\n所有组的block_delay平均值: {overall_block_delay:.3f}")
print(f"\n所有组的round_delay平均值: {overall_round_delay:.3f}")
print(f"\n所有组的time-between-shards平均值: {overall_round_delay-overall_block_delay:.3f}")
