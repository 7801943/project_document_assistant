# import numpy as np


# # 参考 https://www.doc88.com/p-14161585100.html
# # ==============================================================================
# # --- 节 1: 基本物理和电气常数 ---
# # ==============================================================================
# # 定义计算中需要用到的基本物理常数

# EPSILON_0 = 8.854e-12  # 真空介电常数 (F/m)。用于计算导体间的电场和电容。
# MU_0 = 4 * np.pi * 1e-7      # 真空磁导率 (H/m)。用于计算导体周围的磁场和电感。
# OMEGA = 2 * np.pi * 50       # 电网角频率 (rad/s)。假设电网频率为50Hz，用于计算感抗和容抗。

# # ==============================================================================
# # --- 节 2: 从几何参数计算电容和电感矩阵的函数 ---
# # ==============================================================================

# def get_conductor_coordinates(tower_params):
#     """
#     根据杆塔几何尺寸，计算所有导线（包括相导线和地线）的笛卡尔(x, y)坐标。

#     坐标系约定:
#     - 原点(0, 0)位于地面中心正下方。
#     - x轴沿水平方向。
#     - y轴沿垂直方向，代表对地高度。

#     参数:
#     tower_params (dict): 包含杆塔几何尺寸的字典。

#     返回:
#     dict: 一个字典，键为导体名称(如'1A', 'g1'), 值为其(x, y)坐标元组。
#     """
#     x1, x2, x3, x4 = tower_params['x1'], tower_params['x2'], tower_params['x3'], tower_params['x4']
#     H1, H2, H3 = tower_params['H1'], tower_params['H2'], tower_params['H3']

#     coords = {
#         # 地线坐标 (g1, g2)
#         'g1': (-x1 / 2, H1), 'g2': (x1 / 2, H1),
#         # I回线路 (左侧) 相导线坐标 (1A, 1B, 1C)
#         '1A': (-x2 / 2, H2), '1B': (-x3 / 2, H3), '1C': (-x4 / 2, tower_params['h_bottom']),
#         # II回线路 (右侧) 相导线坐标 (2a, 2b, 2c)
#         '2a': (x2 / 2, H2), '2b': (x3 / 2, H3), '2c': (x4 / 2, tower_params['h_bottom']),
#     }
#     return coords

# def calculate_potential_coefficient_matrix(coords, radii):
#     """
#     计算位移系数矩阵 P (n x n)，也称为麦克斯韦电位系数矩阵。
#     这是计算电容矩阵的基础。矩阵中每个元素 P_ij 代表第 j 根导体带单位电荷时，在第 i 根导体处产生的电位。
#     计算中采用“镜像法”来模拟理想导电地面的影响。

#     - 对角线元素 (P_ii): 描述了导体 i 自身的电位，取决于其对地高度(yi)和半径(ri)。
#       公式: P_ii = (1 / (2*pi*eps0)) * ln(2*yi / ri)
#     - 非对角线元素 (P_ij): 描述了导体 j 对导体 i 的影响，取决于它们之间的直接距离和镜像距离。
#       公式: P_ij = (1 / (2*pi*eps0)) * ln(D_ij' / D_ij)
#       其中 D_ij 是两导体直接距离，D_ij' 是导体i与导体j的镜像之间的距离。
#     """
#     conductors = list(coords.keys())
#     n = len(conductors)
#     P = np.zeros((n, n))
#     for i, cond_i in enumerate(conductors):
#         for j, cond_j in enumerate(conductors):
#             xi, yi = coords[cond_i]
#             xj, yj = coords[cond_j]
#             if i == j: # 对角线元素
#                 P[i, j] = (1 / (2 * np.pi * EPSILON_0)) * np.log(2 * yi / radii[cond_i])
#             else: # 非对角线元素
#                 dist_image = np.sqrt((xj - xi)**2 + (yj + yi)**2)
#                 dist_direct = np.sqrt((xj - xi)**2 + (yj - yi)**2)
#                 P[i, j] = (1 / (2 * np.pi * EPSILON_0)) * np.log(dist_image / dist_direct)
#     return P

# def calculate_inductance_matrix(coords, gmrs, ground_resistivity):
#     """
#     计算电感矩阵 L (n x n)。
#     矩阵中每个元素 L_ij 代表第 j 根导体通有单位电流时，在第 i 根导体处产生的磁链。
#     计算基于卡森(Carson)的近似公式，该公式考虑了电流通过大地返回路径所带来的影响。

#     - 对角线元素 (L_ii): 导体的自感。使用导体的几何均距(GMR)代替物理半径来计及导体内部磁链的影响。
#       公式: L_ii = (mu0 / (2*pi)) * ln(De / GMR_i)
#     - 非对角线元素 (L_ij): 导体间的互感。
#       公式: L_ij = (mu0 / (2*pi)) * ln(De / D_ij)
#     其中 De 是大地等效返回深度，与大地电阻率(rho)和频率(f)有关。
#     """
#     conductors = list(coords.keys())
#     n = len(conductors)
#     L = np.zeros((n, n))
#     # 卡森公式的大地等效返回深度 (m)
#     De = 658.37 * np.sqrt(ground_resistivity / 50)
#     for i, cond_i in enumerate(conductors):
#         for j, cond_j in enumerate(conductors):
#             xi, yi = coords[cond_i]
#             xj, yj = coords[cond_j]
#             if i == j: # 自感
#                 L[i, j] = (MU_0 / (2 * np.pi)) * np.log(De / gmrs[cond_i])
#             else: # 互感
#                 dist_direct = np.sqrt((xj - xi)**2 + (yj - yi)**2)
#                 L[i, j] = (MU_0 / (2 * np.pi)) * np.log(De / dist_direct)
#     return L

# def kron_reduction(full_matrix, phase_indices, ground_indices):
#     """
#     执行克朗(Kron)矩阵约减。
#     这是一个标准的矩阵运算，用于在电气上消除模型中的某些节点（如此处的地线）。
#     地线不是孤立的，它们通过杆塔接地，其电位为零。此方法将地线的影响（屏蔽效应）
#     等效地包含到相导线的参数矩阵中，从而得到一个只包含相导线的、尺寸更小的等效矩阵。
#     """
#     M_pp = full_matrix[np.ix_(phase_indices, phase_indices)]
#     M_pg = full_matrix[np.ix_(phase_indices, ground_indices)]
#     M_gp = full_matrix[np.ix_(ground_indices, phase_indices)]
#     M_gg = full_matrix[np.ix_(ground_indices, ground_indices)]
#     M_gg_inv = np.linalg.inv(M_gg)
#     return M_pp - M_pg @ M_gg_inv @ M_gp

# def calculate_line_matrices(tower_dimensions, conductor_params):
#     """
#     这是一个总控函数，它将物理尺寸和材料特性转化为最终的电气参数矩阵。
#     流程:
#     1. 计算导体坐标。
#     2. 建立包含所有导体（相线+地线）的完整参数矩阵。
#     3. 通过克朗约减消除地线，得到仅含6根相线的6x6等效参数矩阵。
#     4. 从6x6矩阵中提取出计算所需的4个3x3子矩阵。
#     """
#     coords = get_conductor_coordinates(tower_dimensions)
#     phase_conductors = ['1A', '1B', '1C', '2a', '2b', '2c']
#     ground_conductors = ['g1', 'g2']
#     all_conductors = phase_conductors + ground_conductors
#     phase_idx = list(range(len(phase_conductors)))
#     ground_idx = list(range(len(phase_conductors), len(all_conductors)))

#     radii = {name: conductor_params['conductor_radius'] for name in phase_conductors}
#     radii.update({name: conductor_params['ground_wire_radius'] for name in ground_conductors})
#     gmrs = {name: conductor_params['conductor_gmr'] for name in phase_conductors}
#     gmrs.update({name: conductor_params['ground_wire_gmr'] for name in ground_conductors})

#     ordered_coords = {name: coords[name] for name in all_conductors}
#     ordered_radii = {name: radii[name] for name in all_conductors}
#     ordered_gmrs = {name: gmrs[name] for name in all_conductors}

#     # 计算包含地线的8x8原始位移系数和电感矩阵 (单位: m/F, H/m)
#     P_full = calculate_potential_coefficient_matrix(ordered_coords, ordered_radii)
#     L_full = calculate_inductance_matrix(ordered_coords, ordered_gmrs, conductor_params['rho_ground'])

#     # 克朗约减得到6x6相导线等效矩阵
#     P_reduced = kron_reduction(P_full, phase_idx, ground_idx)
#     L_reduced = kron_reduction(L_full, phase_idx, ground_idx)

#     # 电容矩阵是位移系数矩阵的逆: C = inv(P)
#     C_reduced = np.linalg.inv(P_reduced)

#     # 从6x6矩阵中提取出所需的3x3子矩阵
#     C_aa = C_reduced[0:3, 0:3]  # 运行线路(I回)的自电容矩阵
#     C_cc = C_reduced[3:6, 3:6]  # 停电线路(II回)的自电容矩阵
#     C_ac = C_reduced[3:6, 0:3]  # I回对II回的互电容矩阵
#     L_aa = L_reduced[0:3, 0:3]  # 运行线路(I回)的自感矩阵
#     L_cc = L_reduced[3:6, 3:6]  # 停电线路(II回)的自感矩阵
#     L_ac = L_reduced[3:6, 0:3]  # I回对II回的互感矩阵

#     return C_aa, C_ac, C_cc, L_aa, L_ac, L_cc

# # ==============================================================================
# # --- 节 3: 计算四种核心感应量的函数 ---
# # ==============================================================================

# def calculate_electrostatic_induced_voltage(C_cc, C_ac, U_ABC):
#     """
#     计算静电感应电压 (当停电线路开路/悬空时)。
#     物理意义：停电线路各相导体会感应出一定的对地电压，使得流入导线的总电容电流为零。
#     公式: U_es = -inv(C_victim_self) * C_mutual * U_energized
#     """
#     try:
#         C_cc_inv = np.linalg.inv(C_cc)
#         U_es_induced = -C_cc_inv @ C_ac @ U_ABC
#         return U_es_induced
#     except np.linalg.LinAlgError:
#         print("错误: 停电线路的自电容矩阵 C_cc 是奇异矩阵，无法求逆。")
#         return None

# def calculate_electromagnetic_induced_current(L_cc, L_ac, I_ABC):
#     """
#     计算电磁感应电流 (当停电线路两端接地时)。
#     物理意义：停电线路会形成一个闭合回路，其中感应出的电流产生的磁场正好抵消来自运行线路的耦合磁场，
#     使得停电线路上总的感应电动势为零（因为其两端电压为零）。
#     公式: I_em = -inv(L_victim_self) * L_mutual * I_energized
#     """
#     try:
#         L_cc_inv = np.linalg.inv(L_cc)
#         I_em_induced = -L_cc_inv @ L_ac @ I_ABC
#         return I_em_induced
#     except np.linalg.LinAlgError:
#         print("错误: 停电线路的自电感矩阵 L_cc 是奇异矩阵，无法求逆。")
#         return None

# # ==============================================================================
# # --- 节 4: 主程序执行块 ---
# # ==============================================================================
# if __name__ == '__main__':

#     # --- 步骤 1: 定义输入参数 ---
#     # a) 杆塔几何尺寸 (来自图1, 单位: 米)
#     tower_dimensions = {
#         'x1': 20.4, 'x2': 15.0, 'x3': 20.0, 'x4': 16.4,
#         'H1': 29.0, 'H2': 22.0, 'H3': 10.6,
#         'h_bottom': 15.0
#     }

#     # b) 导线和大地参数 (!!! 重要假设, 请根据实际情况修改 !!!)
#     #    这些参数通常需要从导线型号手册、地质勘测报告中获取。
#     conductor_params = {
#         # 论文提到导线型号为 4xLGJ-500, 以下为LGJ-500/45的典型参数
#         'conductor_radius': 30.0 / 2 / 1000,  # 导线物理半径 (m)
#         'conductor_gmr': 12.08 / 1000,       # 导线几何均距 (m), 用于电感计算, 考虑了内部磁链
#         # 假设地线型号为 GJ-50
#         'ground_wire_radius': 9.0 / 2 / 1000, # 地线物理半径 (m)
#         'ground_wire_gmr': 3.51 / 1000,      # 地线几何均距 (m)
#         # 假设大地电阻率 (ohm-m)
#         'rho_ground': 100.0
#     }

#     # c) 运行工况参数
#     voltage_level_kV = 520
#     power_MW = 600
#     line_length_km = 100.0 # 定义线路长度 (公里)，用于计算与长度相关的感应量

#     # --- 步骤 2: 将物理模型转换为电气参数矩阵 ---
#     print("--- 正在根据几何参数计算电容和电感矩阵... ---")
#     C_aa, C_ac, C_cc, L_aa, L_ac, L_cc = calculate_line_matrices(tower_dimensions, conductor_params)

#     # --- 步骤 3: 构建运行线路的电压和电流向量(相量) ---
#     phase_voltage_V = (voltage_level_kV / np.sqrt(3)) * 1000 # 相电压有效值 (V)
#     current_A = (power_MW * 1e6) / (np.sqrt(3) * voltage_level_kV * 1000) # 电流有效值 (A)

#     # 创建复数向量来表示三相电压和电流的幅值与相位
#     phase_A_rad, phase_B_rad, phase_C_rad = np.deg2rad(0), np.deg2rad(-120), np.deg2rad(120)
#     U_ABC = phase_voltage_V * np.array([[np.exp(1j * phase_A_rad)], [np.exp(1j * phase_B_rad)], [np.exp(1j * phase_C_rad)]])
#     I_ABC = current_A * np.array([[np.exp(1j * phase_A_rad)], [np.exp(1j * phase_B_rad)], [np.exp(1j * phase_C_rad)]])

#     # --- 步骤 4: 计算四种核心感应量 ---
#     print("\n\n--- 正在计算四种感应量... ---")

#     # 4.1 静电感应电压 (与长度无关)
#     U_es = calculate_electrostatic_induced_voltage(C_cc, C_ac, U_ABC)

#     # 4.2 电磁感应电流 (与长度无关)
#     I_em = calculate_electromagnetic_induced_current(L_cc, L_ac, I_ABC)

#     # 4.3 电磁感应电压 (与长度成正比)
#     # 公式: U_em = Z_mutual * I_energized = (j*w*L_mutual) * I_energized
#     # L_ac 是单位长度互感(H/m), I_ABC 是总电流(A), 结果是单位长度感应电压(V/m)。
#     # 再乘以总长度(m)得到总的纵向感应电动势。
#     U_em = (1j * OMEGA * L_ac @ I_ABC) * (line_length_km * 1000)

#     # 4.4 静电感应电流 (与长度成正比)
#     # 公式: I_es = Y_shunt * U_es = (j*w*C_shunt) * U_es
#     # C_cc 是单位长度电容(F/m), U_es 是感应电压(V)。
#     # Y_cc @ U_es 是单位长度的电容电流(A/m)，再乘以总长度(m)得到总电流。
#     I_es = (1j * OMEGA * C_cc @ U_es) * (line_length_km * 1000) if U_es is not None else None

#     # --- 步骤 5: 显示格式化的最终结果 ---
#     print("\n--- 最终计算结果 ---")
#     np.set_printoptions(precision=2, suppress=True)

#     # 1. 静电感应电压
#     if U_es is not None:
#         print(f"\n1. 静电感应电压 (停电线路悬空时，导线对地电压):")
#         print(f"   (此结果由电场耦合产生，与线路长度无关)")
#         for i, phase in enumerate(['a', 'b', 'c']):
#             voltage_mag = np.abs(U_es[i][0])
#             voltage_ang = np.angle(U_es[i][0], deg=True)
#             print(f"   - Ues_{phase}: {voltage_mag / 1000:.2f} kV, 相角: {voltage_ang:.2f} 度")

#     # 2. 静电感应电流
#     if I_es is not None:
#         print(f"\n2. 静电感应电流 (停电线路单点接地时，流入大地的电流):")
#         print(f"   (此结果由静电感应电压驱动，与线路长度成正比，当前计算长度: {line_length_km} km)")
#         for i, phase in enumerate(['a', 'b', 'c']):
#             current_mag = np.abs(I_es[i][0])
#             current_ang = np.angle(I_es[i][0], deg=True)
#             print(f"   - Ies_{phase}: {current_mag:.2f} A, 相角: {current_ang:.2f} 度")

#     # 3. 电磁感应电压
#     if U_em is not None:
#         print(f"\n3. 电磁感应电压 (停电线路悬空时，线路首末两端的电位差):")
#         print(f"   (此结果由磁场耦合产生，与线路长度成正比，当前计算长度: {line_length_km} km)")
#         for i, phase in enumerate(['a', 'b', 'c']):
#             voltage_mag = np.abs(U_em[i][0])
#             voltage_ang = np.angle(U_em[i][0], deg=True)
#             print(f"   - Uem_{phase}: {voltage_mag / 1000:.2f} kV, 相角: {voltage_ang:.2f} 度")

#     # 4. 电磁感应电流
#     if I_em is not None:
#         print(f"\n4. 电磁感应电流 (停电线路两端接地时，回路中的电流):")
#         print(f"   (此结果由电磁感应电压驱动，在此简化模型中与线路长度无关)")
#         for i, phase in enumerate(['a', 'b', 'c']):
#             current_mag = np.abs(I_em[i][0])
#             current_ang = np.angle(I_em[i][0], deg=True)
#             print(f"   - Iem_{phase}: {current_mag:.2f} A, 相角: {current_ang:.2f} 度")

# import numpy as np


# # https://www.doc88.com/p-14161585100.html
# # ==============================================================================
# # --- 节 1: 基本物理和电气常数 ---
# # ==============================================================================
# # 定义计算中需要用到的基本物理常数

# EPSILON_0 = 8.854e-12  # 真空介电常数 (F/m)。用于计算导体间的电场和电容。
# MU_0 = 4 * np.pi * 1e-7      # 真空磁导率 (H/m)。用于计算导体周围的磁场和电感。
# OMEGA = 2 * np.pi * 50       # 电网角频率 (rad/s)。假设电网频率为50Hz，用于计算感抗和容抗。

# # ==============================================================================
# # --- 节 2: 参数计算函数 ---
# # ==============================================================================

# def calculate_bundle_parameters(sub_conductor_radius, sub_conductor_gmr, bundle_count, bundle_spacing):
#     """
#     计算分裂导线的等效半径和等效几何均距。
#     """
#     if bundle_count == 1:
#         return sub_conductor_radius, sub_conductor_gmr

#     R = bundle_spacing / (2 * np.sin(np.pi / bundle_count))
#     equivalent_radius = (bundle_count * sub_conductor_radius * (R**(bundle_count - 1)))**(1 / bundle_count)
#     equivalent_gmr = (bundle_count * sub_conductor_gmr * (R**(bundle_count - 1)))**(1 / bundle_count)

#     return equivalent_radius, equivalent_gmr

# def get_conductor_coordinates(tower_params):
#     """
#     根据杆塔几何尺寸，计算所有导线的笛卡尔(x, y)坐标。
#     (已根据正确的坐标系解读进行修正)
#     """
#     x1, x2, x3, x4 = tower_params['x1'], tower_params['x2'], tower_params['x3'], tower_params['x4']
#     H1, H2, H3 = tower_params['H1'], tower_params['H2'], tower_params['H3']
#     h_bottom = tower_params['h_bottom']

#     # H1, H2, H3 是相对于最底层导线的高度。
#     # 最终坐标需要使用对地绝对高度。
#     coords = {
#         'g1': (-x1 / 2, h_bottom + H1), # 地线绝对高度
#         'g2': (x1 / 2,  h_bottom + H1), # 地线绝对高度
#         '1A': (-x2 / 2, h_bottom + H2), # 上层导线绝对高度
#         '2a': (x2 / 2,  h_bottom + H2), # 上层导线绝对高度
#         '1B': (-x3 / 2, h_bottom + H3), # 中层导线绝对高度
#         '2b': (x3 / 2,  h_bottom + H3), # 中层导线绝对高度
#         '1C': (-x4 / 2, h_bottom),      # 最底层导线绝对高度
#         '2c': (x4 / 2,  h_bottom),      # 最底层导线绝对高度
#     }
#     return coords

# def calculate_potential_coefficient_matrix(coords, radii):
#     """
#     计算位移系数矩阵 P (n x n)，也称为麦克斯韦电位系数矩阵。
#     """
#     conductors = list(coords.keys())
#     n = len(conductors)
#     P = np.zeros((n, n))
#     for i, cond_i in enumerate(conductors):
#         for j, cond_j in enumerate(conductors):
#             xi, yi = coords[cond_i]
#             xj, yj = coords[cond_j]
#             if i == j:
#                 P[i, j] = (1 / (2 * np.pi * EPSILON_0)) * np.log(2 * yi / radii[cond_i])
#             else:
#                 dist_image = np.sqrt((xj - xi)**2 + (yj + yi)**2)
#                 dist_direct = np.sqrt((xj - xi)**2 + (yj - yi)**2)
#                 P[i, j] = (1 / (2 * np.pi * EPSILON_0)) * np.log(dist_image / dist_direct)
#     return P

# def calculate_inductance_matrix(coords, gmrs, ground_resistivity):
#     """
#     计算电感矩阵 L (n x n)。
#     """
#     conductors = list(coords.keys())
#     n = len(conductors)
#     L = np.zeros((n, n))
#     De = 658.37 * np.sqrt(ground_resistivity / 50)
#     for i, cond_i in enumerate(conductors):
#         for j, cond_j in enumerate(conductors):
#             xi, yi = coords[cond_i]
#             xj, yj = coords[cond_j]
#             if i == j:
#                 L[i, j] = (MU_0 / (2 * np.pi)) * np.log(De / gmrs[cond_i])
#             else:
#                 dist_direct = np.sqrt((xj - xi)**2 + (yj - yi)**2)
#                 L[i, j] = (MU_0 / (2 * np.pi)) * np.log(De / dist_direct)
#     return L

# def kron_reduction(full_matrix, phase_indices, ground_indices):
#     """
#     执行克朗(Kron)矩阵约减，消除地线的影响。
#     """
#     M_pp = full_matrix[np.ix_(phase_indices, phase_indices)]
#     M_pg = full_matrix[np.ix_(phase_indices, ground_indices)]
#     M_gp = full_matrix[np.ix_(ground_indices, phase_indices)]
#     M_gg = full_matrix[np.ix_(ground_indices, ground_indices)]
#     M_gg_inv = np.linalg.inv(M_gg)
#     return M_pp - M_pg @ M_gg_inv @ M_gp

# def calculate_line_matrices(tower_dimensions, conductor_params):
#     """
#     总控函数，将物理尺寸和材料特性转化为最终的电气参数矩阵。
#     (已增加电阻矩阵的计算)
#     """
#     eq_radius, eq_gmr = calculate_bundle_parameters(
#         conductor_params['sub_conductor_radius'],
#         conductor_params['sub_conductor_gmr'],
#         conductor_params['bundle_count'],
#         conductor_params['bundle_spacing']
#     )

#     coords = get_conductor_coordinates(tower_dimensions)
#     phase_conductors = ['1A', '1B', '1C', '2a', '2b', '2c']
#     ground_conductors = ['g1', 'g2']
#     all_conductors = phase_conductors + ground_conductors
#     phase_idx, ground_idx = list(range(len(phase_conductors))), list(range(len(phase_conductors), len(all_conductors)))

#     radii = {name: eq_radius for name in phase_conductors}
#     radii.update({name: conductor_params['ground_wire_radius'] for name in ground_conductors})
#     gmrs = {name: eq_gmr for name in phase_conductors}
#     gmrs.update({name: conductor_params['ground_wire_gmr'] for name in ground_conductors})

#     ordered_coords = {name: coords[name] for name in all_conductors}
#     ordered_radii = {name: radii[name] for name in all_conductors}
#     ordered_gmrs = {name: gmrs[name] for name in all_conductors}

#     P_full = calculate_potential_coefficient_matrix(ordered_coords, ordered_radii)
#     L_full = calculate_inductance_matrix(ordered_coords, ordered_gmrs, conductor_params['rho_ground'])

#     P_reduced = kron_reduction(P_full, phase_idx, ground_idx)
#     L_reduced = kron_reduction(L_full, phase_idx, ground_idx)

#     C_reduced = np.linalg.inv(P_reduced)

#     # 新增：计算电阻矩阵
#     # 分裂导线的等效电阻 = 子导线电阻 / 分裂数
#     bundle_resistance = conductor_params['sub_conductor_resistance_ac'] / conductor_params['bundle_count']
#     # 假设各相导线电阻相同，且相间电阻为0
#     R_reduced = np.diag([bundle_resistance] * len(phase_conductors))

#     # 提取子矩阵
#     C_aa, C_cc, C_ac = C_reduced[0:3, 0:3], C_reduced[3:6, 3:6], C_reduced[3:6, 0:3]
#     L_aa, L_cc, L_ac = L_reduced[0:3, 0:3], L_reduced[3:6, 3:6], L_reduced[3:6, 0:3]
#     R_cc = R_reduced[3:6, 3:6] # 停电线路的自电阻矩阵

#     return C_aa, C_ac, C_cc, L_aa, L_ac, L_cc, R_cc

# # ==============================================================================
# # --- 节 3: 计算四种核心感应量的函数 ---
# # ==============================================================================

# def calculate_electrostatic_induced_voltage(C_cc, C_ac, U_ABC):
#     """计算静电感应电压 (当停电线路开路/悬空时)。"""
#     try:
#         C_cc_inv = np.linalg.inv(C_cc)
#         return -C_cc_inv @ C_ac @ U_ABC
#     except np.linalg.LinAlgError:
#         print("错误: 停电线路的自电容矩阵 C_cc 是奇异矩阵，无法求逆。")
#         return None

# def calculate_electromagnetic_induced_current(R_cc, L_cc, L_ac, I_ABC):
#     """
#     计算电磁感应电流 (当停电线路两端接地时)。
#     (已更新为使用完整的阻抗 Z = R + jwL)
#     """
#     try:
#         # 构建停电线路自身回路的阻抗矩阵 Z_cc = R_cc + j*w*L_cc
#         Z_cc = R_cc + 1j * OMEGA * L_cc
#         # 构建互阻抗矩阵 Z_ac = j*w*L_ac (互感部分没有电阻项)
#         Z_ac = 1j * OMEGA * L_ac

#         Z_cc_inv = np.linalg.inv(Z_cc)
#         # 公式: I_em = -inv(Z_victim_self) * Z_mutual * I_energized
#         return -Z_cc_inv @ Z_ac @ I_ABC
#     except np.linalg.LinAlgError:
#         print("错误: 停电线路的自阻抗矩阵 Z_cc 是奇异矩阵，无法求逆。")
#         return None

# # ==============================================================================
# # --- 节 4: 主程序执行块 ---
# # ==============================================================================
# if __name__ == '__main__':

#     # --- 步骤 1: 定义输入参数 ---
#     tower_dimensions = {
#         'x1': 20.4, 'x2': 15.0, 'x3': 20.0, 'x4': 16.4,
#         'H1': 29.0, 'H2': 22.0, 'H3': 10.6,
#         'h_bottom': 15.0
#     }


#     conductor_params = {
#         # 子导线参数 (LGJ-500/45)
#         'sub_conductor_radius': 30.0 / 2 / 1000,
#         'sub_conductor_gmr': 12.08 / 1000,
#         'sub_conductor_resistance_ac': 0.06 / 1000, # 子导线交流电阻 (Ohm/m), 0.06 Ohm/km 是一个典型值
#         # 分裂导线参数
#         'bundle_count': 4,
#         'bundle_spacing': 0.45,
#         # 地线参数 (假设 GJ-50)
#         'ground_wire_radius': 15 / 2 / 1000,
#         'ground_wire_gmr': 3.51 / 1000,
#         # 环境参数
#         'rho_ground': 100.0
#     }

#     voltage_level_kV = 500
#     power_MW = 525
#     line_length_km = 100

#     # --- 步骤 2: 计算参数矩阵 ---
#     print("--- 正在根据几何参数和分裂导线参数计算电容、电感和电阻矩阵... ---")
#     C_aa, C_ac, C_cc, L_aa, L_ac, L_cc, R_cc = calculate_line_matrices(tower_dimensions, conductor_params)

#     # --- 步骤 3: 构建运行线路的电压和电流向量 ---
#     phase_voltage_V = (voltage_level_kV / np.sqrt(3)) * 1000
#     current_A = (power_MW * 1e6) / (np.sqrt(3) * voltage_level_kV * 1000)

#     phase_A_rad, phase_B_rad, phase_C_rad = np.deg2rad(0), np.deg2rad(-120), np.deg2rad(120)
#     U_ABC = phase_voltage_V * np.array([[np.exp(1j * phase_A_rad)], [np.exp(1j * phase_B_rad)], [np.exp(1j * phase_C_rad)]])
#     I_ABC = current_A * np.array([[np.exp(1j * phase_A_rad)], [np.exp(1j * phase_B_rad)], [np.exp(1j * phase_C_rad)]])

#     # --- 步骤 4: 计算四种核心感应量 ---
#     print("\n\n--- 正在计算四种感应量... ---")

#     U_es = calculate_electrostatic_induced_voltage(C_cc, C_ac, U_ABC)

#     # 电磁感应电流计算现在需要传入电阻矩阵
#     # 注意：这里的R_cc, L_cc, L_ac都是单位长度值(Ohm/m, H/m), I_ABC是总电流(A)
#     # 计算结果 I_em 是单位长度线路上的感应电流 (A/m)。
#     # 对于一个两端接地的均匀长线路，其上各点电流近似相等，这个值就是最终的电流值。
#     I_em = calculate_electromagnetic_induced_current(R_cc, L_cc, L_ac, I_ABC)

#     U_em = (1j * OMEGA * L_ac @ I_ABC) * (line_length_km * 1000)
#     I_es = (1j * OMEGA * C_cc @ U_es) * (line_length_km * 1000) if U_es is not None else None

#     # --- 步骤 5: 显示格式化的最终结果 ---
#     print("\n--- 最终计算结果 (已包含导线电阻影响) ---")
#     np.set_printoptions(precision=2, suppress=True)

#     if U_es is not None:
#         print(f"\n1. 静电感应电压 (停电线路悬空时，导线对地电压):")
#         print(f"   (此结果由电场耦合产生，与线路长度无关)")
#         for i, phase in enumerate(['a', 'b', 'c']):
#             voltage_mag, voltage_ang = np.abs(U_es[i][0]), np.angle(U_es[i][0], deg=True)
#             print(f"   - Ues_{phase}: {voltage_mag / 1000:.2f} kV, 相角: {voltage_ang:.2f} 度")

#     if I_es is not None:
#         print(f"\n2. 静电感应电流 (停电线路单点接地时，流入大地的电流):")
#         print(f"   (此结果由静电感应电压驱动，与线路长度成正比，当前计算长度: {line_length_km} km)")
#         for i, phase in enumerate(['a', 'b', 'c']):
#             current_mag, current_ang = np.abs(I_es[i][0]), np.angle(I_es[i][0], deg=True)
#             print(f"   - Ies_{phase}: {current_mag:.2f} A, 相角: {current_ang:.2f} 度")

#     if U_em is not None:
#         print(f"\n3. 电磁感应电压 (停电线路悬空时，线路首末两端的电位差):")
#         print(f"   (此结果由磁场耦合产生，与线路长度成正比，当前计算长度: {line_length_km} km)")
#         for i, phase in enumerate(['a', 'b', 'c']):
#             voltage_mag, voltage_ang = np.abs(U_em[i][0]), np.angle(U_em[i][0], deg=True)
#             print(f"   - Uem_{phase}: {voltage_mag / 1000:.2f} kV, 相角: {voltage_ang:.2f} 度")

#     if I_em is not None:
#         print(f"\n4. 电磁感应电流 (停电线路两端接地时，回路中的电流):")
#         print(f"   (此结果基于完整的Z=R+jX阻抗计算，在此简化模型中与线路长度无关)")
#         for i, phase in enumerate(['a', 'b', 'c']):
#             current_mag, current_ang = np.abs(I_em[i][0]), np.angle(I_em[i][0], deg=True)
#             print(f"   - Iem_{phase}: {current_mag:.2f} A, 相角: {current_ang:.2f} 度")

import numpy as np

# ==============================================================================
# --- 节 1: 基本物理和电气常数 ---
# ==============================================================================
EPSILON_0 = 8.854e-12  # 真空介电常数 (F/m)
MU_0 = 4 * np.pi * 1e-7  # 真空磁导率 (H/m)
OMEGA = 2 * np.pi * 50   # 电网角频率 (rad/s)

# ==============================================================================
# --- 节 2: 分裂导线参数 ---
# ==============================================================================

def bundle_equivalent_radius(sub_r, bundle_count, bundle_spacing):
    """分裂导线几何等效半径 (用于电容计算)"""
    if bundle_count == 1:
        return sub_r
    # 计算所有子导线中心间距的几何平均值
    R = bundle_spacing / (2 * np.sin(np.pi / bundle_count))
    return (sub_r * (R ** (bundle_count - 1))) ** (1 / bundle_count)

def bundle_equivalent_gmr(sub_gmr, bundle_count, bundle_spacing):
    """分裂导线等效GMR (用于电感计算)"""
    if bundle_count == 1:
        return sub_gmr
    R = bundle_spacing / (2 * np.sin(np.pi / bundle_count))
    return (sub_gmr * (R ** (bundle_count - 1))) ** (1 / bundle_count)

# ==============================================================================
# --- 节 3: 坐标与矩阵计算 ---
# ==============================================================================

def get_conductor_coordinates(tower_params):
    x1, x2, x3, x4 = tower_params['x1'], tower_params['x2'], tower_params['x3'], tower_params['x4']
    H1, H2, H3 = tower_params['H1'], tower_params['H2'], tower_params['H3']
    h_bottom = tower_params['h_bottom']
    return {
        'g1': (-x1 / 2, h_bottom + H1),
        'g2': (x1 / 2,  h_bottom + H1),
        '1A': (-x2 / 2, h_bottom + H2),
        '2a': (x2 / 2,  h_bottom + H2),
        '1B': (-x3 / 2, h_bottom + H3),
        '2b': (x3 / 2,  h_bottom + H3),
        '1C': (-x4 / 2, h_bottom),
        '2c': (x4 / 2,  h_bottom),
    }

def calculate_potential_coefficient_matrix(coords, radii):
    conductors = list(coords.keys())
    n = len(conductors)
    P = np.zeros((n, n))
    for i, ci in enumerate(conductors):
        for j, cj in enumerate(conductors):
            xi, yi = coords[ci]
            xj, yj = coords[cj]
            if i == j:
                P[i, j] = (1 / (2 * np.pi * EPSILON_0)) * np.log(2 * yi / radii[ci])
            else:
                dist_image = np.sqrt((xj - xi)**2 + (yj + yi)**2)
                dist_direct = np.sqrt((xj - xi)**2 + (yj - yi)**2)
                P[i, j] = (1 / (2 * np.pi * EPSILON_0)) * np.log(dist_image / dist_direct)
    return P

def carson_equivalent_distance(h_i, h_j, d_ij, rho_ground):
    """Carson 校正的等效距离"""
    # Carson 近似公式中的地面修正
    De = 658.37 * np.sqrt(rho_ground / 50.0)
    return np.sqrt(d_ij**2 + (h_i + h_j)**2 + De**2)

def calculate_inductance_matrix(coords, gmrs, rho_ground):
    conductors = list(coords.keys())
    n = len(conductors)
    L = np.zeros((n, n))
    for i, ci in enumerate(conductors):
        for j, cj in enumerate(conductors):
            xi, yi = coords[ci]
            xj, yj = coords[cj]
            if i == j:
                L[i, j] = (MU_0 / (2 * np.pi)) * np.log(carson_equivalent_distance(yi, yi, 0, rho_ground) / gmrs[ci])
            else:
                dist_direct = np.sqrt((xj - xi)**2 + (yj - yi)**2)
                L[i, j] = (MU_0 / (2 * np.pi)) * np.log(carson_equivalent_distance(yi, yj, dist_direct, rho_ground) / dist_direct)
    return L

def kron_reduction(full_matrix, phase_indices, ground_indices):
    M_pp = full_matrix[np.ix_(phase_indices, phase_indices)]
    M_pg = full_matrix[np.ix_(phase_indices, ground_indices)]
    M_gp = full_matrix[np.ix_(ground_indices, phase_indices)]
    M_gg = full_matrix[np.ix_(ground_indices, ground_indices)]
    return M_pp - M_pg @ np.linalg.inv(M_gg) @ M_gp

# ==============================================================================
# --- 节 4: 线路矩阵计算 ---
# ==============================================================================

def calculate_line_matrices(tower_dimensions, conductor_params):
    eq_radius_c = bundle_equivalent_radius(
        conductor_params['sub_conductor_radius'],
        conductor_params['bundle_count'],
        conductor_params['bundle_spacing']
    )
    eq_gmr_l = bundle_equivalent_gmr(
        conductor_params['sub_conductor_gmr'],
        conductor_params['bundle_count'],
        conductor_params['bundle_spacing']
    )

    coords = get_conductor_coordinates(tower_dimensions)
    phase_conductors = ['1A', '1B', '1C', '2a', '2b', '2c']
    ground_conductors = ['g1', 'g2']
    all_conductors = phase_conductors + ground_conductors
    phase_idx = list(range(len(phase_conductors)))
    ground_idx = list(range(len(phase_conductors), len(all_conductors)))

    radii = {name: eq_radius_c for name in phase_conductors}
    radii.update({name: conductor_params['ground_wire_radius'] for name in ground_conductors})
    gmrs = {name: eq_gmr_l for name in phase_conductors}
    gmrs.update({name: conductor_params['ground_wire_gmr'] for name in ground_conductors})

    ordered_coords = {name: coords[name] for name in all_conductors}
    ordered_radii = {name: radii[name] for name in all_conductors}
    ordered_gmrs = {name: gmrs[name] for name in all_conductors}

    P_full = calculate_potential_coefficient_matrix(ordered_coords, ordered_radii)
    L_full = calculate_inductance_matrix(ordered_coords, ordered_gmrs, conductor_params['rho_ground'])

    P_reduced = kron_reduction(P_full, phase_idx, ground_idx)
    L_reduced = kron_reduction(L_full, phase_idx, ground_idx)
    C_reduced = np.linalg.inv(P_reduced)

    bundle_resistance = conductor_params['sub_conductor_resistance_ac'] / conductor_params['bundle_count']
    R_reduced = np.diag([bundle_resistance] * len(phase_conductors))

    C_aa, C_cc, C_ac = C_reduced[0:3, 0:3], C_reduced[3:6, 3:6], C_reduced[3:6, 0:3]
    L_aa, L_cc, L_ac = L_reduced[0:3, 0:3], L_reduced[3:6, 3:6], L_reduced[3:6, 0:3]
    R_cc = R_reduced[3:6, 3:6]

    return C_aa, C_ac, C_cc, L_aa, L_ac, L_cc, R_cc

# ==============================================================================
# --- 节 5: 感应量计算 ---
# ==============================================================================

def calculate_electrostatic_induced_voltage(C_cc, C_ac, U_ABC):
    return -np.linalg.inv(C_cc) @ C_ac @ U_ABC

def calculate_electromagnetic_induced_current(R_cc, L_cc, L_ac, I_ABC, line_length_m):
    Z_cc = R_cc * line_length_m + 1j * OMEGA * L_cc * line_length_m
    Z_ac = 1j * OMEGA * L_ac * line_length_m
    return -np.linalg.inv(Z_cc) @ Z_ac @ I_ABC

# ==============================================================================
# --- 节 6: 主程序 ---
# ==============================================================================

if __name__ == '__main__':
    # https://www.doc88.com/p-14161585100.html
    # tower_dimensions = {
    #     'x1': 20.4, 'x2': 15.0, 'x3': 20.0, 'x4': 16.4,
    #     'H1': 29.0, 'H2': 22.0, 'H3': 10.6,
    #     'h_bottom': 15.0
    # }
    # https://www.docin.com/p-1439352914.html
    tower_dimensions = {
        'x1': 20, 'x2': 21.6, 'x3': 24.4, 'x4': 22.6,
        'H1': 30, 'H2': 23, 'H3': 11,
        'h_bottom': 42 - 15 # 上面的高度包括了
    }
    conductor_params = {
        'sub_conductor_radius': 30.0 / 2 / 1000,
        'sub_conductor_gmr': 12.08 / 1000,
        'sub_conductor_resistance_ac': 0.06 / 1000,
        'bundle_count': 4,
        'bundle_spacing': 0.4,
        'ground_wire_radius': 15 / 2 / 1000,
        'ground_wire_gmr': 3.51 / 1000,
        'rho_ground': 100.0
    }
    #https://www.doc88.com/p-14161585100.html
    # voltage_level_kV = 500
    # power_MW = 525
    # line_length_km = 100
    # https://www.docin.com/p-1439352914.html
    voltage_level_kV = 500
    power_MW = 2200 # 1300A
    line_length_km = 70
    line_length_m = line_length_km * 1000

    C_aa, C_ac, C_cc, L_aa, L_ac, L_cc, R_cc = calculate_line_matrices(tower_dimensions, conductor_params)

    phase_voltage_V = (voltage_level_kV / np.sqrt(3)) * 1000
    current_A = (power_MW * 1e6) / (np.sqrt(3) * voltage_level_kV * 1000)
    U_ABC = phase_voltage_V * np.array([[1], [np.exp(-1j*2*np.pi/3)], [np.exp(1j*2*np.pi/3)]])
    I_ABC = current_A * np.array([[1], [np.exp(-1j*2*np.pi/3)], [np.exp(1j*2*np.pi/3)]])

    U_es = calculate_electrostatic_induced_voltage(C_cc, C_ac, U_ABC)
    I_em = calculate_electromagnetic_induced_current(R_cc, L_cc, L_ac, I_ABC, line_length_m)
    U_em = (1j * OMEGA * L_ac @ I_ABC) * line_length_m
    I_es = (1j * OMEGA * C_cc @ U_es) * line_length_m

    np.set_printoptions(precision=2, suppress=True)
    print("\n--- 最终计算结果 ---")
    for name, arr in [("Ues", U_es), ("Ies", I_es), ("Uem", U_em), ("Iem", I_em)]:
        print(f"\n{name}:")
        for i, ph in enumerate(["a", "b", "c"]):
            mag, ang = np.abs(arr[i][0]), np.angle(arr[i][0], deg=True)
            unit = "kV" if "U" in name else "A"
            scale = 1000 if unit == "kV" else 1
            print(f"  - {ph}: {mag/scale:.2f} {unit}, 相角 {ang:.2f}°")
