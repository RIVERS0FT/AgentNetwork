"""
内置技能包 — 对应架构文档 第五节：Skill体系

Skill
├── IntelligenceCollectionSkill
├── ReportGenerationSkill
├── DataAnalysisSkill
├── StrategyPlanningSkill
└── RiskAssessmentSkill
"""

from .intelligence_collection import IntelligenceCollectionSkill
from .strategy_planning import StrategyPlanningSkill

__all__ = ["IntelligenceCollectionSkill", "StrategyPlanningSkill"]
