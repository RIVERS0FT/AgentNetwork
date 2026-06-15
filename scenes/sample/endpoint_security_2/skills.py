import random
import math

class SkillRegistry:
    _skills = {}

    @classmethod
    def register(cls, func):
        cls._skills[func.__name__] = func
        return func

    @classmethod
    def execute(cls, skill_name, **kwargs):
        if skill_name not in cls._skills:
            return {"status": "error", "result": None, "data": {"error": f"Skill {skill_name} not found"}}
        return cls._skills[skill_name](**kwargs)

# 全局资源状态
_state = {
    "budget": {},  # 角色预算
    "incidents": 100,  # 初始安全事件数
    "production_rate": 99.5,  # 生产线上线率百分比
    "vuln_detection_rate": 50,  # 漏洞检测率
    "compliance_coverage": 0,  # 合规覆盖率
    "penalty_total": 0,  # 处罚总额
    "market_share_fw": 30,  # 防火墙厂商市场份额
    "market_share_eta": 10,  # 加密分析厂商市场份额
    "revenue_eta": 0,  # 加密分析厂商营收
    "customers_eta": 0,  # 加密分析厂商客户数
    "prototype_accuracy": 0,  # 原型准确率
    "new_product_ready": False,  # 新产品就绪
}

@SkillRegistry.register
def incident_response(**kwargs):
    """安全运维中心应急响应，减少安全事件数"""
    effort = kwargs.get("effort", 1)
    reduction = random.randint(5, 15) * effort
    _state["incidents"] = max(0, _state["incidents"] - reduction)
    cost = 10 * effort
    return {"status": "success", "result": f"减少{reduction}个事件，花费{cost}预算", "data": {"incidents_left": _state["incidents"], "cost": cost}}

@SkillRegistry.register
def budget_management(**kwargs):
    """预算管理，优化支出"""
    savings = random.randint(5, 20)
    return {"status": "success", "result": f"节省{savings}%预算", "data": {"savings_percent": savings}}

@SkillRegistry.register
def threat_intel(**kwargs):
    """威胁情报收集，提高检测能力"""
    boost = random.randint(1, 5)
    _state["vuln_detection_rate"] = min(100, _state["vuln_detection_rate"] + boost)
    return {"status": "success", "result": f"威胁情报提升检测率{boost}%", "data": {"new_rate": _state["vuln_detection_rate"]}}

@SkillRegistry.register
def production_optimization(**kwargs):
    """生产优化，保持上线率"""
    rate = _state["production_rate"]
    adjustment = random.uniform(-0.5, 0.5)
    new_rate = min(100, max(90, rate + adjustment))
    _state["production_rate"] = new_rate
    return {"status": "success", "result": f"生产线上线率调整为{new_rate:.1f}%", "data": {"production_rate": new_rate}}

@SkillRegistry.register
def vendor_evaluation(**kwargs):
    """供应商评估，选择合作伙伴"""
    score = random.randint(60, 100)
    return {"status": "success", "result": f"供应商评分{score}", "data": {"score": score}}

@SkillRegistry.register
def vulnerability_assessment(**kwargs):
    """漏洞评估，提升检测率"""
    boost = random.randint(2, 8)
    _state["vuln_detection_rate"] = min(100, _state["vuln_detection_rate"] + boost)
    return {"status": "success", "result": f"漏洞评估提升检测率{boost}%", "data": {"new_rate": _state["vuln_detection_rate"]}}

@SkillRegistry.register
def stability_analysis(**kwargs):
    """稳定性分析，确保不影响电网"""
    stability = random.uniform(0.95, 1.0)
    return {"status": "success", "result": f"稳定性系数{stability:.3f}", "data": {"stability": stability}}

@SkillRegistry.register
def policy_negotiation(**kwargs):
    """政策谈判，推动标准"""
    progress = random.randint(5, 15)
    _state["compliance_coverage"] = min(100, _state["compliance_coverage"] + progress)
    return {"status": "success", "result": f"谈判推进合规覆盖率{progress}%", "data": {"coverage": _state["compliance_coverage"]}}

@SkillRegistry.register
def encryption_audit(**kwargs):
    """加密流量审计方案设计"""
    quality = random.randint(70, 100)
    return {"status": "success", "result": f"审计方案质量评分{quality}", "data": {"quality": quality}}

@SkillRegistry.register
def compliance_check(**kwargs):
    """合规检查，确保符合监管要求"""
    compliance = random.randint(80, 100)
    return {"status": "success", "result": f"合规检查得分{compliance}", "data": {"compliance": compliance}}

@SkillRegistry.register
def budget_planning(**kwargs):
    """预算规划，控制成本"""
    cost = random.randint(50, 150)
    return {"status": "success", "result": f"规划预算{cost}万", "data": {"cost": cost}}

@SkillRegistry.register
def ml_model_training(**kwargs):
    """机器学习模型训练，提升准确率"""
    accuracy = random.uniform(0.7, 0.95)
    _state["prototype_accuracy"] = accuracy
    return {"status": "success", "result": f"模型准确率{accuracy:.2f}", "data": {"accuracy": accuracy}}

@SkillRegistry.register
def traffic_analysis(**kwargs):
    """流量分析，识别异常"""
    detection = random.randint(80, 100)
    return {"status": "success", "result": f"流量异常检测率{detection}%", "data": {"detection_rate": detection}}

@SkillRegistry.register
def prototype_testing(**kwargs):
    """原型测试，验证效果"""
    success = random.random() < 0.8
    return {"status": "success", "result": f"原型测试{'成功' if success else '失败'}", "data": {"success": success}}

@SkillRegistry.register
def product_development(**kwargs):
    """产品开发，推出新产品"""
    progress = random.randint(10, 30)
    if progress >= 25:
        _state["new_product_ready"] = True
    return {"status": "success", "result": f"产品开发进度{progress}%", "data": {"progress": progress, "ready": _state["new_product_ready"]}}

@SkillRegistry.register
def market_analysis(**kwargs):
    """市场分析，了解竞争"""
    share = _state["market_share_fw"]
    return {"status": "success", "result": f"当前市场份额{share}%", "data": {"market_share": share}}

@SkillRegistry.register
def competitive_intel(**kwargs):
    """竞争情报，获取对手信息"""
    intel = random.randint(1, 10)
    return {"status": "success", "result": f"获取竞争情报{intel}条", "data": {"intel_count": intel}}

@SkillRegistry.register
def sales_negotiation(**kwargs):
    """销售谈判，获取客户"""
    customers = random.randint(0, 2)
    revenue = customers * 200
    _state["customers_eta"] += customers
    _state["revenue_eta"] += revenue
    return {"status": "success", "result": f"获得{customers}个客户，营收{revenue}万", "data": {"customers": customers, "revenue": revenue}}

@SkillRegistry.register
def deployment_support(**kwargs):
    """部署支持，确保客户满意"""
    satisfaction = random.randint(80, 100)
    return {"status": "success", "result": f"客户满意度{satisfaction}%", "data": {"satisfaction": satisfaction}}

@SkillRegistry.register
def revenue_forecast(**kwargs):
    """营收预测，规划未来"""
    forecast = _state["revenue_eta"] + random.randint(100, 500)
    return {"status": "success", "result": f"预测营收{forecast}万", "data": {"forecast": forecast}}

@SkillRegistry.register
def standard_setting(**kwargs):
    """标准制定，发布新标准"""
    coverage = random.randint(5, 20)
    _state["compliance_coverage"] = min(100, _state["compliance_coverage"] + coverage)
    return {"status": "success", "result": f"标准制定推进{coverage}%覆盖率", "data": {"coverage": _state["compliance_coverage"]}}

@SkillRegistry.register
def penalty_assessment(**kwargs):
    """处罚评估，对违规行为罚款"""
    penalty = random.randint(50, 200)
    _state["penalty_total"] += penalty
    return {"status": "success", "result": f"处罚{penalty}万", "data": {"penalty": penalty, "total": _state["penalty_total"]}}

@SkillRegistry.register
def coverage_monitoring(**kwargs):
    """覆盖率监控，检查合规进展"""
    coverage = _state["compliance_coverage"]
    return {"status": "success", "result": f"当前合规覆盖率{coverage}%", "data": {"coverage": coverage}}