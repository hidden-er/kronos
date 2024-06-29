import boto3

def extract_unique_public_ips(nested_dict, key_to_extract):
    """
    递归地遍历多层嵌套字典，提取并去重给定键的值。

    :param nested_dict: 要遍历的多层嵌套字典
    :param key_to_extract: 要提取的键（例如，“Public IP”）
    :return: 包含所有找到的唯一值的集合
    """

    def recurse_items(current_item):
        if isinstance(current_item, dict):
            for key, value in current_item.items():
                if key == key_to_extract:
                    unique_ips.add(value)
                else:
                    recurse_items(value)
        elif isinstance(current_item, list):
            for item in current_item:
                recurse_items(item)

    unique_ips = set()
    recurse_items(nested_dict)
    return list(unique_ips)

# 定义一个Python函数来生成并返回上述bash脚本内容
def generate_bash_script(public_ips,node=1):
    """
    生成一个Bash脚本，该脚本使用给定的公共IP地址。

    :param public_ips: 公共IP地址的列表
    :return: Bash脚本的字符串表示
    """
    n = len(public_ips)  # AWS服务器的数量
    #node = 1  # 每个服务器上的节点数

    script_lines = [
        "#!/bin/bash",
        "",
        f"# the number of AWS servers to remove",
        f"N={n}",
        "",
        f"# the number of nodes on each server",
        f"node={node}",
        "",
        f"# public IPs --- This is the public IPs of AWS servers"
    ]

    # 添加公共IP地址
    pub_ips_lines = ["pubIPsVar=("]
    i=0
    for ip in public_ips:
        pub_ips_lines.append(f"[{i}]='{ip}'")
        i+=1
    pub_ips_lines.append(")")

    # 合并脚本行
    script_lines += pub_ips_lines
    script_lines.append("")
    return "\n".join(script_lines)

unique_public_ips= []

region_names = ['eu-west-2','ap-east-1','us-east-1','ap-northeast-1']
for region_name in region_names:
    ec2 = boto3.client('ec2', region_name=region_name)
    response = ec2.describe_instances()
    unique_public_ips.extend(extract_unique_public_ips(response, 'PublicIp'))


# 生成脚本
bash_script = generate_bash_script(unique_public_ips)
print(bash_script)

