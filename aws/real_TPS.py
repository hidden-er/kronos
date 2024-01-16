import sympy as sp

# 定义符号
x, m, q = sp.symbols('x m q')

# P1 的计算公式
P1 = (1/3 * x + 1/3 * x**2 + 1/3 * x**3)

# P2 的计算公式
P2 = ((1/3 * x * (m - 2) + 1/3 * x**2 * (m - 3) + 1/3 * x**3 * (m - 4))
      / ((1/3 * x + 1/3 * x**2 + 1/3 * x**3) * m))

# P3 的计算公式
P3 = (((1 - q) + q * P1 * (1 - P2))
      / ((1 - q) + q * P1))

# 创建一个函数来计算 P1, P2, P3
def calculate_probabilities(x_value, m_value, q_value):
    P1_value = P1.subs(x, x_value)
    P2_value = P2.subs({x: x_value, m: m_value})
    P3_value = P3.subs({x: x_value, m: m_value, q: q_value})
    print(P3_value)
    return P1_value, P2_value, P3_value

# 设定 x, m, q 的值
x_value = 0.80  # 片间单个输入分片合法比例
m_value = 32   # 分片数
q_value = 0.10  # 片间交易比例

# 计算 P1, P2, P3
print(calculate_probabilities(x_value, m_value, q_value))
