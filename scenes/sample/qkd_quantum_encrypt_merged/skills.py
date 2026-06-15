import random
import math

class SkillRegistry:
    _skills = {}

    @classmethod
    def register(cls, func):
        cls._skills[func.__name__] = func
        return func

    @classmethod
    def get(cls, name):
        return cls._skills.get(name)

_resource_state = {
    'key_pool': 1000,
    'budget_power': 500,
    'budget_rail': 300,
    'budget_city': 200,
    'equipment_inventory': 50,
    'line_capacity': 100,
    'attacker_success': 0,
    'compliance_violations': 0
}

@SkillRegistry.register
def key_request(**kwargs):
    """请求量子密钥分配，消耗密钥池资源"""
    amount = kwargs.get('amount', 10)
    if _resource_state['key_pool'] >= amount:
        _resource_state['key_pool'] -= amount
        return {'status': 'success', 'result': 'allocated', 'data': {'allocated': amount, 'remaining': _resource_state['key_pool']}}
    else:
        return {'status': 'failure', 'result': 'insufficient', 'data': {'available': _resource_state['key_pool']}}

@SkillRegistry.register
def cost_analysis(**kwargs):
    """分析成本，返回当前预算结余百分比"""
    budget = kwargs.get('budget', 100)
    spent = kwargs.get('spent', 50)
    remaining = budget - spent
    percent = remaining / budget * 100
    return {'status': 'success', 'result': 'analyzed', 'data': {'remaining_percent': percent}}

@SkillRegistry.register
def reliability_check(**kwargs):
    """检查网络可靠性，模拟随机故障"""
    failure_prob = kwargs.get('failure_prob', 0.01)
    if random.random() < failure_prob:
        return {'status': 'failure', 'result': 'fault_detected', 'data': {'fault': True}}
    else:
        return {'status': 'success', 'result': 'reliable', 'data': {'fault': False}}

@SkillRegistry.register
def network_plan(**kwargs):
    """规划网络拓扑，返回覆盖率提升"""
    current_coverage = kwargs.get('current_coverage', 50)
    increase = random.randint(5, 15)
    new_coverage = min(current_coverage + increase, 100)
    return {'status': 'success', 'result': 'planned', 'data': {'new_coverage': new_coverage, 'increase': increase}}

@SkillRegistry.register
def cost_optimization(**kwargs):
    """优化成本，返回节省百分比"""
    cost = kwargs.get('cost', 100)
    savings = random.randint(5, 20)
    optimized_cost = cost - savings
    percent = savings / cost * 100
    return {'status': 'success', 'result': 'optimized', 'data': {'savings_percent': percent, 'optimized_cost': optimized_cost}}

@SkillRegistry.register
def coverage_analysis(**kwargs):
    """分析覆盖率，返回当前覆盖率"""
    coverage = kwargs.get('coverage', 60)
    return {'status': 'success', 'result': 'analyzed', 'data': {'coverage': coverage}}

@SkillRegistry.register
def latency_monitor(**kwargs):
    """监控延迟，模拟网络波动"""
    base_latency = kwargs.get('base_latency', 10)
    jitter = random.uniform(-2, 2)
    current_latency = base_latency + jitter
    if current_latency < 0:
        current_latency = 0
    return {'status': 'success', 'result': 'monitored', 'data': {'latency_ms': current_latency}}

@SkillRegistry.register
def fault_detection(**kwargs):
    """检测设备故障，返回故障概率"""
    fault_rate = kwargs.get('fault_rate', 0.01)
    if random.random() < fault_rate:
        return {'status': 'failure', 'result': 'fault_detected', 'data': {'fault': True}}
    else:
        return {'status': 'success', 'result': 'no_fault', 'data': {'fault': False}}

@SkillRegistry.register
def negotiate_lease(**kwargs):
    """谈判线路租赁，返回价格和条款"""
    price_per_unit = kwargs.get('price_per_unit', 10)
    units = kwargs.get('units', 1)
    discount = random.uniform(0.9, 1.0)
    total = price_per_unit * units * discount
    return {'status': 'success', 'result': 'negotiated', 'data': {'total_price': total, 'discount': discount}}

@SkillRegistry.register
def node_deploy(**kwargs):
    """部署QKD节点，消耗预算和库存"""
    cost = kwargs.get('cost', 50)
    if _resource_state['budget_city'] >= cost and _resource_state['equipment_inventory'] >= 1:
        _resource_state['budget_city'] -= cost
        _resource_state['equipment_inventory'] -= 1
        return {'status': 'success', 'result': 'deployed', 'data': {'remaining_budget': _resource_state['budget_city'], 'remaining_inventory': _resource_state['equipment_inventory']}}
    else:
        return {'status': 'failure', 'result': 'insufficient_resources', 'data': {'budget': _resource_state['budget_city'], 'inventory': _resource_state['equipment_inventory']}}

@SkillRegistry.register
def budget_control(**kwargs):
    """控制预算，返回超支风险"""
    planned = kwargs.get('planned', 100)
    actual = kwargs.get('actual', 90)
    overshoot = actual - planned
    if overshoot > 0:
        return {'status': 'warning', 'result': 'over_budget', 'data': {'overshoot': overshoot}}
    else:
        return {'status': 'success', 'result': 'under_budget', 'data': {'savings': -overshoot}}

@SkillRegistry.register
def success_rate_eval(**kwargs):
    """评估密钥分发成功率"""
    success_count = kwargs.get('success_count', 95)
    total = kwargs.get('total', 100)
    rate = success_count / total * 100
    return {'status': 'success', 'result': 'evaluated', 'data': {'success_rate': rate}}

@SkillRegistry.register
def quote_price(**kwargs):
    """报价，考虑成本和利润"""
    cost = kwargs.get('cost', 100)
    margin = random.uniform(1.2, 1.5)
    price = cost * margin
    return {'status': 'success', 'result': 'quoted', 'data': {'price': price, 'margin': margin}}

@SkillRegistry.register
def market_share_calc(**kwargs):
    """计算市场份额"""
    sales = kwargs.get('sales', 40)
    total_market = kwargs.get('total_market', 100)
    share = sales / total_market * 100
    return {'status': 'success', 'result': 'calculated', 'data': {'market_share': share}}

@SkillRegistry.register
def after_sale_cost(**kwargs):
    """计算售后成本"""
    revenue = kwargs.get('revenue', 1000)
    cost = random.randint(50, 150)
    percent = cost / revenue * 100
    return {'status': 'success', 'result': 'calculated', 'data': {'after_sale_cost': cost, 'percent_of_revenue': percent}}

@SkillRegistry.register
def protocol_optimize(**kwargs):
    """优化协议，提升密钥生成效率"""
    current_efficiency = kwargs.get('current_efficiency', 0.8)
    improvement = random.uniform(0.05, 0.15)
    new_efficiency = min(current_efficiency + improvement, 1.0)
    return {'status': 'success', 'result': 'optimized', 'data': {'new_efficiency': new_efficiency, 'improvement': improvement}}

@SkillRegistry.register
def security_assessment(**kwargs):
    """安全评估，检测漏洞"""
    vulnerability_score = random.random()
    if vulnerability_score < 0.2:
        return {'status': 'warning', 'result': 'vulnerability_found', 'data': {'score': vulnerability_score}}
    else:
        return {'status': 'success', 'result': 'secure', 'data': {'score': vulnerability_score}}

@SkillRegistry.register
def efficiency_boost(**kwargs):
    """提升效率，返回新效率值"""
    base = kwargs.get('base', 0.8)
    boost = random.uniform(0.02, 0.1)
    new = min(base + boost, 1.0)
    return {'status': 'success', 'result': 'boosted', 'data': {'new_efficiency': new}}

@SkillRegistry.register
def lease_line(**kwargs):
    """租赁线路，消耗线路容量"""
    capacity = kwargs.get('capacity', 10)
    if _resource_state['line_capacity'] >= capacity:
        _resource_state['line_capacity'] -= capacity
        return {'status': 'success', 'result': 'leased', 'data': {'remaining_capacity': _resource_state['line_capacity']}}
    else:
        return {'status': 'failure', 'result': 'insufficient_capacity', 'data': {'available': _resource_state['line_capacity']}}

@SkillRegistry.register
def utilization_report(**kwargs):
    """报告利用率"""
    used = kwargs.get('used', 80)
    total = kwargs.get('total', 100)
    utilization = used / total * 100
    return {'status': 'success', 'result': 'reported', 'data': {'utilization': utilization}}

@SkillRegistry.register
def customer_survey(**kwargs):
    """客户满意度调查"""
    satisfaction = random.randint(80, 100)
    return {'status': 'success', 'result': 'surveyed', 'data': {'satisfaction': satisfaction}}

@SkillRegistry.register
def compliance_audit(**kwargs):
    """合规审计，检查违规"""
    if random.random() < 0.1:
        _resource_state['compliance_violations'] += 1
        return {'status': 'failure', 'result': 'violation_found', 'data': {'violations': _resource_state['compliance_violations']}}
    else:
        return {'status': 'success', 'result': 'compliant', 'data': {'violations': _resource_state['compliance_violations']}}

@SkillRegistry.register
def standard_enforce(**kwargs):
    """强制执行标准，返回处罚"""
    if _resource_state['compliance_violations'] > 0:
        penalty = _resource_state['compliance_violations'] * 10
        return {'status': 'warning', 'result': 'penalty_applied', 'data': {'penalty': penalty}}
    else:
        return {'status': 'success', 'result': 'no_action', 'data': {}}

@SkillRegistry.register
def penalty_assess(**kwargs):
    """评估罚款金额"""
    severity = kwargs.get('severity', 1)
    fine = severity * 100
    return {'status': 'success', 'result': 'assessed', 'data': {'fine': fine}}

@SkillRegistry.register
def eavesdrop(**kwargs):
    """窃听尝试，基于概率成功"""
    success_prob = kwargs.get('success_prob', 0.05)
    if random.random() < success_prob:
        _resource_state['attacker_success'] += 1
        return {'status': 'success', 'result': 'eavesdropped', 'data': {'key_stolen': 1}}
    else:
        return {'status': 'failure', 'result': 'detected', 'data': {}}

@SkillRegistry.register
def interfere(**kwargs):
    """干扰网络，造成中断"""
    if random.random() < 0.3:
        return {'status': 'success', 'result': 'interfered', 'data': {'disruption': True}}
    else:
        return {'status': 'failure', 'result': 'failed', 'data': {}}

@SkillRegistry.register
def theft_attempt(**kwargs):
    """窃取密钥，消耗攻击资源"""
    if _resource_state['key_pool'] > 0:
        stolen = min(5, _resource_state['key_pool'])
        _resource_state['key_pool'] -= stolen
        _resource_state['attacker_success'] += stolen
        return {'status': 'success', 'result': 'stolen', 'data': {'stolen': stolen, 'remaining_pool': _resource_state['key_pool']}}
    else:
        return {'status': 'failure', 'result': 'empty_pool', 'data': {}}