import random

class ToolRegistry:
    _tools = {}

    @classmethod
    def register(cls, func):
        cls._tools[func.__name__] = func
        return func

    @classmethod
    def get(cls, name):
        return cls._tools.get(name)

    @classmethod
    def all_tools(cls):
        return list(cls._tools.keys())

# Resource tracking dictionaries
resource_pool = {"CMCC": 500, "CT": 400, "CU": 300, "total_available": 1200}
transaction_log = []
contracts = []
budget = {"CMCC_COMPUTE": 1000, "CT_CLOUD": 800, "CU_SMART": 600, "PENGLAB": 500, "SCHEDULER": 200, "GPU_VENDOR": 1500}

@ToolRegistry.register
def negotiate_standard(**kwargs):
    """
    协商算力池化标准。角色提出标准版本，其他角色响应。返回是否达成一致。
    """
    actor = kwargs.get('actor', 'unknown')
    target = kwargs.get('target', 'unknown')
    version = kwargs.get('version', 1.0)
    # 随机模拟对方接受概率
    acceptance_prob = random.uniform(0.3, 0.8)
    if random.random() < acceptance_prob:
        result = "accepted"
        data = {"version": version, "agreed_by": target}
    else:
        result = "rejected"
        data = {"version": version, "reason": "标准不兼容"}
    return {"status": "success", "result": result, "data": data}

@ToolRegistry.register
def price_setting(**kwargs):
    """
    设定算力单价（元/PFlops）。返回新价格并记录。
    """
    actor = kwargs.get('actor', 'unknown')
    base_price = kwargs.get('base_price', 100)
    # 根据市场供需调整价格
    demand_factor = random.uniform(0.9, 1.1)
    new_price = round(base_price * demand_factor, 2)
    # 边界校验
    if new_price < 50:
        new_price = 50
    elif new_price > 200:
        new_price = 200
    return {"status": "success", "result": "price_set", "data": {"actor": actor, "new_price": new_price}}

@ToolRegistry.register
def pool_resources(**kwargs):
    """
    将算力资源池化。返回池化后的总资源量。
    """
    actor = kwargs.get('actor', 'unknown')
    amount = kwargs.get('amount', 100)
    if amount > resource_pool.get(actor[:4], 0):
        return {"status": "error", "result": "insufficient_resources", "data": {}}
    resource_pool[actor[:4]] -= amount
    resource_pool["total_available"] += amount
    return {"status": "success", "result": "pooled", "data": {"pooled": amount, "total": resource_pool["total_available"]}}

@ToolRegistry.register
def alliance_strategy(**kwargs):
    """
    与目标角色结成联盟。返回联盟状态。
    """
    actor = kwargs.get('actor', 'unknown')
    target = kwargs.get('target', 'unknown')
    # 随机联盟成功率
    if random.random() < 0.6:
        result = "alliance_formed"
        data = {"allies": [actor, target]}
    else:
        result = "failed"
        data = {"reason": "利益冲突"}
    return {"status": "success", "result": result, "data": data}

@ToolRegistry.register
def cost_optimize(**kwargs):
    """
    优化内部成本，返回节省的预算百分比。
    """
    actor = kwargs.get('actor', 'unknown')
    savings = random.uniform(0.05, 0.15)
    # 更新预算
    if actor in budget:
        budget[actor] *= (1 - savings)
    return {"status": "success", "result": "optimized", "data": {"savings_percent": round(savings*100, 2)}}

@ToolRegistry.register
def collaborate_training(**kwargs):
    """
    与科研机构合作进行模型训练。返回训练进度。
    """
    actor = kwargs.get('actor', 'unknown')
    partner = kwargs.get('partner', 'unknown')
    progress = random.randint(10, 30)
    return {"status": "success", "result": "training_progress", "data": {"progress": progress, "partner": partner}}

@ToolRegistry.register
def data_compliance(**kwargs):
    """
    检查数据合规性。返回合规状态。
    """
    actor = kwargs.get('actor', 'unknown')
    # 随机合规概率
    compliant = random.random() < 0.9
    if compliant:
        return {"status": "success", "result": "compliant", "data": {}}
    else:
        return {"status": "error", "result": "non_compliant", "data": {"issue": "数据加密不足"}}

@ToolRegistry.register
def resource_sharing(**kwargs):
    """
    共享闲置算力资源。返回共享量。
    """
    actor = kwargs.get('actor', 'unknown')
    amount = kwargs.get('amount', 50)
    if amount > resource_pool.get(actor[:4], 0):
        return {"status": "error", "result": "insufficient", "data": {}}
    resource_pool[actor[:4]] -= amount
    return {"status": "success", "result": "shared", "data": {"shared": amount}}

@ToolRegistry.register
def audit_compliance(**kwargs):
    """
    审计交易合规性。返回审计结果。
    """
    actor = kwargs.get('actor', 'unknown')
    target = kwargs.get('target', 'unknown')
    # 随机审计结果
    if random.random() < 0.95:
        return {"status": "success", "result": "pass", "data": {"audited": target}}
    else:
        return {"status": "error", "result": "fail", "data": {"violation": "数据跨境未申报"}}

@ToolRegistry.register
def policy_guide(**kwargs):
    """
    发布政策指导。返回政策影响力。
    """
    actor = kwargs.get('actor', 'unknown')
    impact = random.uniform(0.1, 0.5)
    return {"status": "success", "result": "policy_issued", "data": {"impact": round(impact, 2)}}

@ToolRegistry.register
def approve_pool(**kwargs):
    """
    批准公共算力池建设项目。返回批准状态。
    """
    actor = kwargs.get('actor', 'unknown')
    proposal = kwargs.get('proposal', 'default')
    if random.random() < 0.7:
        return {"status": "success", "result": "approved", "data": {"proposal": proposal}}
    else:
        return {"status": "error", "result": "rejected", "data": {"reason": "预算不足"}}

@ToolRegistry.register
def request_compute(**kwargs):
    """
    申请算力资源。返回分配结果。
    """
    actor = kwargs.get('actor', 'unknown')
    amount = kwargs.get('amount', 100)
    if resource_pool["total_available"] >= amount:
        resource_pool["total_available"] -= amount
        return {"status": "success", "result": "allocated", "data": {"amount": amount}}
    else:
        return {"status": "error", "result": "insufficient", "data": {"available": resource_pool["total_available"]}}

@ToolRegistry.register
def negotiate_price(**kwargs):
    """
    与供应商谈判算力价格。返回最终价格。
    """
    actor = kwargs.get('actor', 'unknown')
    target = kwargs.get('target', 'unknown')
    initial_price = kwargs.get('initial_price', 100)
    discount = random.uniform(0.8, 0.95)
    final_price = round(initial_price * discount, 2)
    return {"status": "success", "result": "price_agreed", "data": {"final_price": final_price}}

@ToolRegistry.register
def evaluate_performance(**kwargs):
    """
    评估算力性能。返回性能评分。
    """
    actor = kwargs.get('actor', 'unknown')
    score = random.randint(70, 100)
    return {"status": "success", "result": "evaluated", "data": {"performance_score": score}}

@ToolRegistry.register
def match_resources(**kwargs):
    """
    撮合算力供需。返回匹配结果。
    """
    actor = kwargs.get('actor', 'unknown')
    demand = kwargs.get('demand', 100)
    supply = resource_pool["total_available"]
    if supply >= demand:
        matched = demand
        resource_pool["total_available"] -= matched
        return {"status": "success", "result": "matched", "data": {"matched": matched}}
    else:
        return {"status": "error", "result": "insufficient_supply", "data": {"supply": supply}}

@ToolRegistry.register
def settle_transaction(**kwargs):
    """
    结算交易，记录佣金。返回交易ID和佣金。
    """
    actor = kwargs.get('actor', 'unknown')
    amount = kwargs.get('amount', 0)
    commission = round(amount * 0.05, 2)
    transaction_id = len(transaction_log) + 1
    transaction_log.append({"id": transaction_id, "actor": actor, "amount": amount, "commission": commission})
    return {"status": "success", "result": "settled", "data": {"transaction_id": transaction_id, "commission": commission}}

@ToolRegistry.register
def monitor_network(**kwargs):
    """
    监控网络状态。返回网络健康度。
    """
    actor = kwargs.get('actor', 'unknown')
    health = random.uniform(0.8, 1.0)
    return {"status": "success", "result": "monitored", "data": {"network_health": round(health, 2)}}

@ToolRegistry.register
def supply_gpu(**kwargs):
    """
    供应GPU设备。返回供货数量和收入。
    """
    actor = kwargs.get('actor', 'unknown')
    quantity = kwargs.get('quantity', 10)
    unit_price = kwargs.get('unit_price', 100)
    total_revenue = quantity * unit_price
    # 更新预算
    if actor in budget:
        budget[actor] += total_revenue
    return {"status": "success", "result": "supplied", "data": {"quantity": quantity, "revenue": total_revenue}}

@ToolRegistry.register
def promote_standard(**kwargs):
    """
    推广自有GPU标准。返回采纳率。
    """
    actor = kwargs.get('actor', 'unknown')
    adoption_rate = random.uniform(0.1, 0.4)
    return {"status": "success", "result": "promoted", "data": {"adoption_rate": round(adoption_rate, 2)}}

@ToolRegistry.register
def bid_contract(**kwargs):
    """
    投标合同。返回是否中标。
    """
    actor = kwargs.get('actor', 'unknown')
    target = kwargs.get('target', 'unknown')
    if random.random() < 0.5:
        contracts.append({"supplier": actor, "buyer": target})
        return {"status": "success", "result": "won", "data": {"contract": {"supplier": actor, "buyer": target}}}
    else:
        return {"status": "error", "result": "lost", "data": {}}

@ToolRegistry.register
def develop_pool_tech(**kwargs):
    """
    开发算力池化技术。返回技术成熟度。
    """
    actor = kwargs.get('actor', 'unknown')
    maturity = random.randint(50, 80)
    return {"status": "success", "result": "tech_developed", "data": {"maturity": maturity}}

@ToolRegistry.register
def test_integration(**kwargs):
    """
    测试系统集成。返回测试结果。
    """
    actor = kwargs.get('actor', 'unknown')
    success = random.random() < 0.8
    if success:
        return {"status": "success", "result": "integration_pass", "data": {}}
    else:
        return {"status": "error", "result": "integration_fail", "data": {"issue": "接口不兼容"}}

@ToolRegistry.register
def develop_api(**kwargs):
    """
    开发API接口。返回完成度。
    """
    actor = kwargs.get('actor', 'unknown')
    progress = random.randint(30, 70)
    return {"status": "success", "result": "api_developed", "data": {"progress": progress}}

@ToolRegistry.register
def data_encrypt(**kwargs):
    """
    数据加密处理。返回加密状态。
    """
    actor = kwargs.get('actor', 'unknown')
    encrypted = random.random() < 0.9
    if encrypted:
        return {"status": "success", "result": "encrypted", "data": {}}
    else:
        return {"status": "error", "result": "encryption_failed", "data": {"reason": "密钥错误"}}
