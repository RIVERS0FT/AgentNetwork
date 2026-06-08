# -*- coding: utf-8 -*-
"""
自动化剧本生成 Skill 核心驱动脚本 (多文件/复杂范式/复合拓扑升级版)
模型配置: 依托于平台/聚合托管的 DeepSeek-V4 (Anthropic SDK 兼容端点)
"""

import os
import json
import anthropic 

# =====================================================================
# 1. 升级版系统核心提示词 (注入给大模型的 SYSTEM 角色)
# =====================================================================
SYSTEM_PROMPT = """
你是一个顶级的“多Agent分布式网络仿真”剧本编译器。你的任务是根据用户的输入想法，自动生成一个高度复杂的仿真剧本。该剧本包含10个左右的角色节点。

【核心铁律】
1. 绝对禁止自然语言废话：所有输出必须符合即插即用的槽位设计，严格遵循后续给出的 JSON 结构。严禁在 JSON 代码块之外输出任何解释性文字。
2. 互动范式多样化（重点）：不要仅限制于单一博弈。你必须将互动场景细化并混合。互动连线和角色行为必须明确区分：
   - INTERNAL_COLLABORATION (机构/公司内部行政与技术协助)
   - EXTERNAL_NEGOTIATION (外部多方利益拉扯与商务谈判)
   - ZERO_SUM_GAME (纯粹零和博弈、资源抢夺或生存对抗)
3. 丰富多元的复合拓扑网络：严禁生成单一的环状结构（RING）。你需要根据剧本的业务逻辑，在以下标准拓扑中进行组合或嵌套选择：
   - STAR (中心化，如一CEO多下属，或一核心平台多供应商)
   - TREE (层级制，如监管局->大集团->子公司)
   - MESH (稠密网状，错综复杂的外部自由谈判桌)
   - RING (环状传递)
   你必须在剧本中声明总体拓扑类型（global_topology_type），并通过子网（sub_networks）的形式，把角色分入不同的拓扑层。
4. 异构模型底层：根据模型的特长和角色特征，每个角色必须明确它运行时依赖的底层基座模型（model_backbone），必须在 ['openclaw', 'claudecode'] 中二选一。例如内部协助侧、技术蓝图侧节点可倾向于 openclaw，涉及深度工程、对赌合同决策侧可倾向于 claudecode。
5. 工具集与环境依赖落地：每个角色容器必须挂载具体的工具集（skillset）。这些工具需要有具体的网络端点（endpoint）和实现描述，同时在配置中声明该容器需要预装的 Python 依赖包（pip_packages）。
    你是一个顶级的“复杂社会技术系统与网络博弈”剧本架构师。你的任务是根据用户的输入想法，自动生成一个包含10个左右角色槽位、聚焦于“网络化互动、谈判博弈、利益冲突与资源流动”的结构化仿真剧本。
6. 绝对禁止自然语言废话：所有输出必须符合即插即用的槽位设计，严格遵循后续给出的 JSON 结构。严禁在 JSON 代码块之外输出任何解释性文字、前缀（如“好的，这是为您生成的剧本”）或后缀。
7. 动机可量化收敛：角色的核心目标必须具体且带有时限或量化指标（例如：预算结余>50%），严禁日常社交闲聊。
"""

# =====================================================================
# 2. 全新多文件图纸 JSON Schema 强约束结构
# =====================================================================
RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "multi_file_agent_simulation_schema",
        "strict": True, 
        "schema": {
            "type": "object",
            "properties": {
                # 模块一：角色、背景与范式声明 (对应 meta_and_roles.json)
                "meta_and_roles": {
                    "type": "object",
                    "properties": {
                        "scenario_metadata": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "剧本名称"},
                                "global_rules": {"type": "string", "description": "物理世界规则与仿真时限约束"}
                            },
                            "required": ["title", "global_rules"],
                            "additionalProperties": False
                        },
                        "roles": {
                            "type": "object",
                            "description": "剧本中包含的10个左右角色，Key为角色的标准ID（如 DEV_A1_CEO）",
                            "additionalProperties": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "description": "角色人类可读名称"},
                                    "model_backbone": {"type": "string", "enum": ["openclaw", "claudecode"], "description": "指定的底层模型基座"},
                                    "identity": {"type": "string", "description": "角色的身份特征与组织背景说明"},
                                    "core_goal": {"type": "string", "description": "量化的具体利益导向或终极指标"},
                                    "primary_interaction_paradigm": {"type": "string", "enum": ["INTERNAL_COLLABORATION", "EXTERNAL_NEGOTIATION", "ZERO_SUM_GAME"], "description": "角色主导的互动范式"}
                                },
                                "required": ["name", "model_backbone", "identity", "core_goal", "primary_interaction_paradigm"],
                                "additionalProperties": False
                            }
                        }
                    },
                    "required": ["scenario_metadata", "roles"],
                    "additionalProperties": False
                },
                # 模块二：实例容器运行配置与挂载工具集 (对应 instances_and_skills.json)
                "instances_and_skills": {
                    "type": "object",
                    "properties": {
                        "container_instances": {
                            "type": "object",
                            "description": "实例配置，Key必须与模块一中的角色ID严格一一对应",
                            "additionalProperties": {
                                "type": "object",
                                "properties": {
                                    "runtime_engine": {"type": "string", "description": "运行引擎核心名称"},
                                    "docker_image": {"type": "string", "description": "推荐的底层 Docker 基础镜像名称"},
                                    "pip_packages": {
                                        "type": "array",
                                        "description": "容器拉起时需要自动 pip install 的包列表，带版本号",
                                        "items": {"type": "string"}
                                    },
                                    "skill_bindings": {
                                        "type": "array",
                                        "description": "为该角色发包、赋能的工具和外部接口清单",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "skill_name": {"type": "string", "description": "技能/公共函数名"},
                                                "endpoint": {"type": "string", "description": "对应的服务API端点网址"},
                                                "description": {"type": "string", "description": "该技能的作用及触发逻辑说明"}
                                            },
                                            "required": ["skill_name", "endpoint", "description"],
                                            "additionalProperties": False
                                        }
                                    }
                                },
                                "required": ["runtime_engine", "docker_image", "pip_packages", "skill_bindings"],
                                "additionalProperties": False
                            }
                        }
                    },
                    "required": ["container_instances"],
                    "additionalProperties": False
                },
                # 模块三：丰富拓扑结构关系网络 (对应 network_topology.json)
                "network_topology": {
                    "type": "object",
                    "properties": {
                        "global_topology_type": {"type": "string", "enum": ["STAR", "MESH", "TREE", "RING", "HYBRID_MESH"], "description": "全局宏观拓扑结构类型"},
                        "sub_networks": {
                            "type": "array",
                            "description": "划分的复合拓扑子网络列表，前端根据此配置铺设网络和UI样式",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "sub_id": {"type": "string", "description": "子网唯一ID"},
                                    "topology_type": {"type": "string", "enum": ["STAR", "MESH", "TREE", "RING"], "description": "此局部子网的拓扑类型"},
                                    "description": {"type": "string", "description": "该层网络的业务关联或物理含义"},
                                    "nodes": {
                                        "type": "array",
                                        "description": "包含在此子网中的角色ID数组",
                                        "items": {"type": "string"}
                                    },
                                    "edges": {
                                        "type": "array",
                                        "description": "节点间的连线拓扑",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "source": {"type": "string", "description": "源角色ID"},
                                                "target": {"type": "string", "description": "目标角色ID"},
                                                "paradigm": {"type": "string", "enum": ["COLLABORATION", "NEGOTIATION", "GAME"], "description": "连线代表的互动本质类型"},
                                                "channel_id": {"type": "string", "description": "物理或网络虚拟通道名称，如 vlan_bridge_102"}
                                            },
                                            "required": ["source", "target", "paradigm", "channel_id"],
                                            "additionalProperties": False
                                        }
                                    }
                                },
                                "required": ["sub_id", "topology_type", "description", "nodes", "edges"],
                                "additionalProperties": False
                            }
                        }
                    },
                    "required": ["global_topology_type", "sub_networks"],
                    "additionalProperties": False
                }
            },
            "required": ["meta_and_roles", "instances_and_skills", "network_topology"],
            "additionalProperties": False
        }
    }
}

# =====================================================================
# 3. 核心接口调用与多文件自动拆分分发逻辑
# =====================================================================
def generate_and_dispatch_scenarios(user_idea: str, output_directory: str) -> None:
    """
    根据粗糙想法，调用 DeepSeek-V4 生成全套混合剧本数据，
    并在目标目录下自动生成独立的三个剧本文件：
    1. meta_and_roles.json
    2. instances_and_skills.json
    3. network_topology.json
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("未设置 ANTHROPIC_API_KEY 环境变量。")

    client = anthropic.Anthropic(
        api_key=api_key,
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic")
    )

    model_name = "deepseek-v4-pro"

    # 注入 JSON Schema 约束
    schema_instruction = f"\n你必须严格遵循以下全局 JSON Schema 输出，不得附加任何自然语言包裹：\n{json.dumps(RESPONSE_SCHEMA['json_schema']['schema'], ensure_ascii=False, indent=2)}\n"

    print(">> 正在发起大模型调用，编译剧本网络...")
    response = client.messages.create(
        model=model_name,
        max_tokens=16384,
        temperature=0.7,
        system=SYSTEM_PROMPT + schema_instruction,
        messages=[
            {"role": "user", "content": f"请基于以下原始灵感编译一套包含丰富拓扑、多模型异构、复杂互动范式（协作/谈判/博弈）的中文复合剧本：{user_idea}"}
        ]
    )

    # 安全解析响应文本，剔除思考链干扰
    final_text = ""
    for block in response.content:
        if hasattr(block, 'text'):
            final_text += block.text

    final_text = final_text.strip()
    if final_text.startswith("```json"):
        final_text = final_text[7:]
    if final_text.endswith("```"):
        final_text = final_text[:-3]
    final_text = final_text.strip()

    # 载入统一的大 JSON 字典
    full_blueprint = json.loads(final_text)

    # 自动化多文件分发写入控制器
    os.makedirs(output_directory, exist_ok=True)
    print(f">> 大模型返回数据解析成功。启动多文件（Multi-file Bundle）分发落盘至: {output_directory}")

    # 文件 1 分发：角色背景与互动范式定义
    file1_path = os.path.join(output_directory, "meta_and_roles.json")
    with open(file1_path, "w", encoding="utf-8") as f1:
        json.dump(full_blueprint["meta_and_roles"], f1, ensure_ascii=False, indent=2)
    print(f"   [落盘成功] -> {file1_path}")

    # 文件 2 分发：运行实例与挂载的工具包/环境依赖包
    file2_path = os.path.join(output_directory, "instances_and_skills.json")
    with open(file2_path, "w", encoding="utf-8") as f2:
        json.dump(full_blueprint["instances_and_skills"], f2, ensure_ascii=False, indent=2)
    print(f"   [落盘成功] -> {file2_path}")

    # 文件 3 分发：丰富子网拓扑结构网络
    file3_path = os.path.join(output_directory, "network_topology.json")
    with open(file3_path, "w", encoding="utf-8") as f3:
        json.dump(full_blueprint["network_topology"], f3, ensure_ascii=False, indent=2)
    print(f"   [落盘成功] -> {file3_path}")

    # 快速结构验证打印
    print("\n======== 剧本包自动化编译完成，下游就绪 ========")
    print(f"1. 声明节点总数: {len(full_blueprint['meta_and_roles']['roles'])}")
    print(f"2. 配置容器总数: {len(full_blueprint['instances_and_skills']['container_instances'])}")
    print(f"3. 宏观拓扑结构: {full_blueprint['network_topology']['global_topology_type']}")
    print(f"4. 包含子网络数: {len(full_blueprint['network_topology']['sub_networks'])}")

# =====================================================================
# 4. 本地独立测试入口
# =====================================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="多文件/多范式 Agent 仿真网络沙盒编译器")
    parser.add_argument("--idea", "-i", type=str, 
                        default="一个大型新能源项目的并网审批。包含公司内部技术侧配合、与外部设备供应商的价格谈判、以及与其它开发商就电网有限配额的零和博弈。",
                        help="剧本概念的想法输入")
    parser.add_argument("--dir", "-d", type=str, default="./scenarios/energy_project_v1",
                        help="多文件包输出的目标目录夹")
    args = parser.parse_args()

    try:
        generate_and_dispatch_scenarios(args.idea, args.dir)
    except Exception as e:
        print(f"剧本包编译失败，错误详情: {e}")