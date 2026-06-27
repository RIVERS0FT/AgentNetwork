import random

class ToolRegistry:
    skills = {}

    @classmethod
    def register(cls, name, func):
        cls.skills[name] = func

    @classmethod
    def execute(cls, name, **kwargs):
        if name in cls.skills:
            return cls.skills[name](**kwargs)
        return {"status": "error", "result": "Skill not found", "data": {}}

# 全局资源状态追踪
global_state = {
    "market_share_A": 25.0,
    "market_share_B": 25.0,
    "market_share_new": 0.0,
    "profit_A": 100.0,
    "profit_B": 100.0,
    "customer_satisfaction_A": 80.0,
    "customer_satisfaction_B": 80.0,
    "spectrum_allocated": {"A": 30, "B": 30, "new": 0},
    "network_coverage_A": 60.0,
    "network_cost_A": 50.0,
    "network_cost_B": 50.0,
    "contracts": [],
    "complaints": 0
}

def make_strategic_decision_tool(**kwargs):
    """
    CEO制定战略决策，如调整价格或投资。
    参数：decision_type (str): 'price' 或 'investment', value (float)
    返回：更新后的市场份额和利润
    """
    decision_type = kwargs.get("decision_type", "price")
    value = kwargs.get("value", 0.0)
    if decision_type == "price":
        # 降价可能提升市场份额但降低利润
        share_change = random.uniform(0.5, 2.0) * (1 - value/100)
        profit_change = -value * 0.5
        global_state["market_share_A"] += share_change
        global_state["profit_A"] += profit_change
    elif decision_type == "investment":
        # 投资增加成本但可能提升市场份额
        global_state["profit_A"] -= value
        share_change = random.uniform(0.1, 0.5) * value
        global_state["market_share_A"] += share_change
    return {"status": "success", "result": "Decision applied", "data": {"market_share": global_state["market_share_A"], "profit": global_state["profit_A"]}}

ToolRegistry.register("make_strategic_decision_tool", make_strategic_decision_tool)

def approve_budget_tool(**kwargs):
    """
    审批预算，参数：amount (float)
    返回：预算是否批准
    """
    amount = kwargs.get("amount", 0.0)
    if global_state["profit_A"] - amount > 50:
        global_state["profit_A"] -= amount
        return {"status": "success", "result": "Budget approved", "data": {"remaining_profit": global_state["profit_A"]}}
    else:
        return {"status": "failed", "result": "Insufficient profit", "data": {"profit": global_state["profit_A"]}}

ToolRegistry.register("approve_budget_tool", approve_budget_tool)

def plan_network_deployment_tool(**kwargs):
    """
    规划网络部署，参数：coverage_target (float) 目标覆盖率
    返回：部署方案及成本
    """
    target = kwargs.get("coverage_target", 80.0)
    current = global_state["network_coverage_A"]
    if target > current:
        cost = (target - current) * random.uniform(1.0, 2.0)
        global_state["network_cost_A"] += cost
        global_state["network_coverage_A"] = target
        return {"status": "success", "result": "Network plan created", "data": {"coverage": target, "cost": cost}}
    else:
        return {"status": "success", "result": "No deployment needed", "data": {"coverage": current, "cost": 0}}

ToolRegistry.register("plan_network_deployment_tool", plan_network_deployment_tool)

def calculate_cost_tool(**kwargs):
    """
    计算网络部署成本，参数：coverage_increase (float)
    返回：成本估算
    """
    increase = kwargs.get("coverage_increase", 10.0)
    cost = increase * random.uniform(1.0, 2.0)
    return {"status": "success", "result": "Cost calculated", "data": {"cost": cost}}

ToolRegistry.register("calculate_cost_tool", calculate_cost_tool)

def run_marketing_campaign_tool(**kwargs):
    """
    执行市场营销活动，参数：budget (float)
    返回：客户满意度变化和新增用户
    """
    budget = kwargs.get("budget", 10.0)
    satisfaction_boost = random.uniform(0.5, 2.0) * budget / 10
    new_users = random.randint(1000, 5000) * budget / 10
    global_state["customer_satisfaction_A"] += satisfaction_boost
    # 假设新增用户影响市场份额
    global_state["market_share_A"] += new_users / 100000
    return {"status": "success", "result": "Campaign executed", "data": {"satisfaction": global_state["customer_satisfaction_A"], "new_users": new_users}}

ToolRegistry.register("run_marketing_campaign_tool", run_marketing_campaign_tool)

def analyze_customer_satisfaction_tool(**kwargs):
    """
    分析客户满意度，无参数
    返回：当前满意度
    """
    return {"status": "success", "result": "Analysis complete", "data": {"satisfaction": global_state["customer_satisfaction_A"]}}

ToolRegistry.register("analyze_customer_satisfaction_tool", analyze_customer_satisfaction_tool)

def negotiate_partnership_tool(**kwargs):
    """
    与合作伙伴谈判，参数：partner (str), terms (dict)
    返回：谈判结果
    """
    partner = kwargs.get("partner", "")
    terms = kwargs.get("terms", {})
    success_prob = random.random()
    if success_prob > 0.5:
        global_state["contracts"].append(partner)
        return {"status": "success", "result": "Partnership agreed", "data": {"partner": partner, "terms": terms}}
    else:
        return {"status": "failed", "result": "Negotiation failed", "data": {}}

ToolRegistry.register("negotiate_partnership_tool", negotiate_partnership_tool)

def set_competitive_price_tool(**kwargs):
    """
    制定竞争性定价，参数：price (float)
    返回：市场份额变化
    """
    price = kwargs.get("price", 50.0)
    # 简单模型：价格越低，市场份额提升
    share_change = (100 - price) * random.uniform(0.1, 0.3)
    global_state["market_share_B"] += share_change
    return {"status": "success", "result": "Price set", "data": {"market_share": global_state["market_share_B"]}}

ToolRegistry.register("set_competitive_price_tool", set_competitive_price_tool)

def develop_new_service_tool(**kwargs):
    """
    开发新服务，参数：service_name (str), cost (float)
    返回：服务是否成功推出
    """
    service_name = kwargs.get("service_name", "")
    cost = kwargs.get("cost", 10.0)
    if global_state["profit_B"] - cost > 30:
        global_state["profit_B"] -= cost
        return {"status": "success", "result": "New service developed", "data": {"service": service_name, "cost": cost}}
    else:
        return {"status": "failed", "result": "Insufficient funds", "data": {}}

ToolRegistry.register("develop_new_service_tool", develop_new_service_tool)

def optimize_network_cost_tool(**kwargs):
    """
    优化网络成本，参数：无
    返回：优化后的成本
    """
    reduction = random.uniform(1.0, 5.0)
    global_state["network_cost_B"] -= reduction
    return {"status": "success", "result": "Cost optimized", "data": {"cost": global_state["network_cost_B"]}}

ToolRegistry.register("optimize_network_cost_tool", optimize_network_cost_tool)

def allocate_spectrum_tool(**kwargs):
    """
    分配频谱，参数：entity (str), amount (float)
    返回：分配结果
    """
    entity = kwargs.get("entity", "")
    amount = kwargs.get("amount", 10.0)
    if entity in global_state["spectrum_allocated"]:
        global_state["spectrum_allocated"][entity] += amount
        return {"status": "success", "result": "Spectrum allocated", "data": {"entity": entity, "total": global_state["spectrum_allocated"][entity]}}
    else:
        return {"status": "failed", "result": "Entity not found", "data": {}}

ToolRegistry.register("allocate_spectrum_tool", allocate_spectrum_tool)

def enforce_compliance_tool(**kwargs):
    """
    执行合规检查，参数：entity (str)
    返回：是否合规
    """
    entity = kwargs.get("entity", "")
    # 随机合规性
    compliant = random.random() > 0.2
    if not compliant:
        global_state["complaints"] += 1
    return {"status": "success", "result": "Compliance check done", "data": {"entity": entity, "compliant": compliant}}

ToolRegistry.register("enforce_compliance_tool", enforce_compliance_tool)

def bid_for_contract_tool(**kwargs):
    """
    投标获取合同，参数：bidder (str), amount (float)
    返回：是否中标
    """
    bidder = kwargs.get("bidder", "")
    amount = kwargs.get("amount", 100.0)
    # 简单竞标模型
    competitors = ["SUPPLIER_1", "SUPPLIER_2"]
    if bidder not in competitors:
        return {"status": "failed", "result": "Invalid bidder", "data": {}}
    win_prob = 0.5
    if random.random() < win_prob:
        global_state["contracts"].append(bidder)
        return {"status": "success", "result": "Contract won", "data": {"bidder": bidder, "amount": amount}}
    else:
        return {"status": "failed", "result": "Contract lost", "data": {}}

ToolRegistry.register("bid_for_contract_tool", bid_for_contract_tool)

def deliver_equipment_tool(**kwargs):
    """
    交付设备，参数：contract_id (str)
    返回：交付状态
    """
    contract_id = kwargs.get("contract_id", "")
    if contract_id in global_state["contracts"]:
        return {"status": "success", "result": "Equipment delivered", "data": {"contract": contract_id}}
    else:
        return {"status": "failed", "result": "No such contract", "data": {}}

ToolRegistry.register("deliver_equipment_tool", deliver_equipment_tool)

def deliver_materials_tool(**kwargs):
    """
    交付材料，参数：contract_id (str)
    返回：交付状态
    """
    contract_id = kwargs.get("contract_id", "")
    if contract_id in global_state["contracts"]:
        return {"status": "success", "result": "Materials delivered", "data": {"contract": contract_id}}
    else:
        return {"status": "failed", "result": "No such contract", "data": {}}

ToolRegistry.register("deliver_materials_tool", deliver_materials_tool)

def survey_customers_tool(**kwargs):
    """
    进行客户满意度调查，参数：无
    返回：满意度数据
    """
    satisfaction = (global_state["customer_satisfaction_A"] + global_state["customer_satisfaction_B"]) / 2
    return {"status": "success", "result": "Survey completed", "data": {"average_satisfaction": satisfaction}}

ToolRegistry.register("survey_customers_tool", survey_customers_tool)

def file_complaint_tool(**kwargs):
    """
    提交投诉，参数：target (str), reason (str)
    返回：投诉记录
    """
    target = kwargs.get("target", "")
    reason = kwargs.get("reason", "")
    global_state["complaints"] += 1
    return {"status": "success", "result": "Complaint filed", "data": {"target": target, "reason": reason}}

ToolRegistry.register("file_complaint_tool", file_complaint_tool)

def lease_network_tool(**kwargs):
    """
    租用网络资源，参数：provider (str), capacity (float)
    返回：租用结果
    """
    provider = kwargs.get("provider", "")
    capacity = kwargs.get("capacity", 10.0)
    if provider in ["TEL_A_CEO", "TEL_B_CEO"]:
        # 假设租用成功
        return {"status": "success", "result": "Network leased", "data": {"provider": provider, "capacity": capacity}}
    else:
        return {"status": "failed", "result": "Provider not available", "data": {}}

ToolRegistry.register("lease_network_tool", lease_network_tool)

def launch_service_tool(**kwargs):
    """
    推出新服务，参数：service_name (str), cost (float)
    返回：服务推出结果
    """
    service_name = kwargs.get("service_name", "")
    cost = kwargs.get("cost", 10.0)
    # 新进入者初始利润为0，假设有外部资金
    global_state["market_share_new"] += random.uniform(0.1, 0.5)
    return {"status": "success", "result": "Service launched", "data": {"service": service_name, "market_share": global_state["market_share_new"]}}

ToolRegistry.register("launch_service_tool", launch_service_tool)
