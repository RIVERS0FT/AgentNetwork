"""
Agent LLM 大脑 — 让每个 Agent 拥有独立决策能力

观察 → 推理 → 决策 → 行动

每个 Agent 有:
- 角色 persona（system prompt）
- 独立收件箱
- LLM 驱动的决策循环
- 可用的工具/动作集

支持后端: Anthropic / OpenAI / DeepSeek（复用 llm_parser 的配置）
"""

import json
import re
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from .llm_parser import get_api_config


@dataclass
class Action:
    """Agent 决策后的动作"""
    type: str  # "send_message" | "search" | "analyze" | "wait" | "broadcast" | "plan" | "move_to"
    target: str = ""       # 消息目标 agent_id
    content: str = ""      # 消息/动作内容
    reasoning: str = ""    # Agent 的推理过程
    raw_response: str = "" # LLM 原始响应
    target_x: float = -1   # move_to 目标 X
    target_y: float = -1   # move_to 目标 Y

    def to_dict(self):
        d = {
            "type": self.type, "target": self.target,
            "content": self.content, "reasoning": self.reasoning,
        }
        if self.type == "move_to":
            d["target_x"] = self.target_x
            d["target_y"] = self.target_y
        return d


ROLE_SYSTEM_PROMPTS = {
    "scout": """你是一个侦察兵 Agent。你的职责是收集情报、侦察环境和报告发现。

你可以使用以下动作：
- search(topic): 搜索特定主题的情报
- analyze(data): 分析收集到的数据
- send_message(target_name, content): 向目标 Agent 发送消息
- wait: 等待下一轮

行为准则：
- 主动搜索情报，不要等待命令
- 发现重要信息立即报告给指挥官
- 用简洁准确的语言描述发现
- 如果已经搜索过某个主题，去搜索新的""",

    "commander": """你是一个指挥官 Agent。你的职责是分析情报、制定计划和下达指令。

你可以使用以下动作：
- send_message(target_name, content): 向特定 Agent 下达指令
- broadcast(content): 向所有 Agent 广播消息
- analyze(data): 分析情报数据
- plan(objective): 制定行动计划
- wait: 等待更多情报

行为准则：
- 等待侦察兵提供足够情报再做决策
- 下达清晰具体的指令
- 综合多方情报做出判断
- 在信息不足时主动要求更多侦察""",

    "analyst": """你是一个分析员 Agent。你的职责是分析数据、评估情报和生成报告。

你可以使用以下动作：
- analyze(data): 深入分析数据
- send_message(target_name, content): 向目标 Agent 发送分析结果
- search(topic): 搜索补充信息
- wait: 等待更多数据

行为准则：
- 收到数据后先分析再回复
- 给出数据驱动的评估
- 发现异常及时报告
- 用结构化方式呈现分析结果""",

    "support": """你是一个支援 Agent。你的职责是提供后勤支持和信息协调。

你可以使用以下动作：
- send_message(target_name, content): 向目标 Agent 发送消息
- search(topic): 查询信息
- wait: 等待请求

行为准则：
- 响应其他 Agent 的支援请求
- 协调信息流通
- 主动提供帮助""",

    "observer": """你是一个观察员 Agent。你的职责是监视环境、发现异常和预警。

你可以使用以下动作：
- search(topic): 搜索特定信号
- send_message(target_name, content): 发送预警消息
- analyze(data): 分析监测数据
- wait: 持续监控

行为准则：
- 发现异常立即预警
- 持续监测关键指标
- 及时上报威胁""",
}


class Brain:
    """
    Agent 的 LLM 大脑

    每轮决策:
    1. 收集上下文（角色、目标、当前状态）
    2. 收集观察（收件箱消息）
    3. 构造 prompt → 调用 LLM
    4. 解析响应 → Action
    """

    def __init__(self, role: str, name: str, goals: List[str] = None, config: Dict = None):
        self.role = role
        self.name = name
        self.goals = goals or ["完成指派的任务"]
        self.config = config or get_api_config()
        self.memory: List[str] = []  # 短期记忆（最近几轮的事件）
        self.turn = 0

    def decide(self, inbox: List[Dict], context: Dict = None) -> Action:
        """
        给定当前状态，做出决策

        Args:
            inbox: 收件箱消息列表 [{"from": "agent_name", "content": "..."}]
            context: 环境上下文 {"round": N, "known_agents": [...], "world_state": "..."}

        Returns:
            Action 决策动作
        """
        self.turn += 1
        api_key = self.config.get("api_key", "")
        if not api_key:
            return self._fallback_decision(inbox, context)

        prompt = self._build_prompt(inbox, context)
        response_text = self._call_llm(prompt, api_key)
        action = self._parse_response(response_text)

        # 存入记忆
        self.memory.append(f"[Round {self.turn}] Decided: {action.type} → {action.content[:80]}")
        if len(self.memory) > 20:
            self.memory.pop(0)

        action.raw_response = response_text
        return action

    def _build_prompt(self, inbox: List[Dict], context: Dict = None) -> str:
        """构建发给 LLM 的 prompt"""
        context = context or {}
        system = ROLE_SYSTEM_PROMPTS.get(self.role, ROLE_SYSTEM_PROMPTS["scout"])

        # 已知的其他 Agent
        known = context.get("known_agents", [])
        known_list = "\n".join(f"  - {a.get('name', a.get('agent_id', '?'))} ({a.get('role', '?')})"
                              for a in known) if known else "  暂无"

        # 收件箱
        inbox_text = "（空）"
        if inbox:
            inbox_text = "\n".join(
                f"  [{msg.get('from', '?')}]: {msg.get('content', '')}"
                for msg in inbox[-5:]  # 最近5条
            )

        # 记忆
        memory_text = "\n".join(f"  {m}" for m in self.memory[-6:]) if self.memory else "  （开始）"

        # 目标
        goals_text = "\n".join(f"  {i+1}. {g}" for i, g in enumerate(self.goals))

        prompt = f"""{system}

## 当前状态
回合: {self.turn}
你的名字: {self.name}
你的角色: {self.role}

## 你的目标
{goals_text}

## 已知的其它 Agent
{known_list}

## 最近的记忆
{memory_text}

## 收件箱（最新消息）
{inbox_text}

## 指令
基于以上信息，决定你这一轮要做什么。用以下 JSON 格式回复（只输出 JSON，不要其他内容）:

```json
{{
  "reasoning": "你的推理过程（一句话）",
  "action": "send_message|broadcast|search|analyze|plan|wait",
  "target": "目标 Agent 名字（send_message 时需要）",
  "content": "消息内容或动作参数"
}}
```

注意:
- 不要重复已经做过的事情
- 如果收件箱有发给你的消息，回复它
- 优先完成你的目标
- 用中文回复"""
        return prompt

    def _call_llm(self, prompt: str, api_key: str) -> str:
        """调用 LLM API"""
        provider = self.config.get("provider", "auto")
        model = self.config.get("model", "")
        api_base = self.config.get("api_base", "")

        # 复用 llm_parser 的 API 调用逻辑
        if api_key.startswith("sk-ant-") and provider != "openai":
            return self._call_anthropic(prompt, api_key, model)
        else:
            return self._call_openai_compat(prompt, api_key, model, api_base)

    def _call_anthropic(self, prompt: str, api_key: str, model: str = "") -> str:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        model = model or "claude-sonnet-4-6"
        # Split system and user
        parts = prompt.split("## 当前状态")
        system = parts[0].strip() if len(parts) > 1 else prompt[:500]
        user = prompt if len(parts) <= 1 else "## 当前状态" + parts[1]

        message = client.messages.create(
            model=model, max_tokens=512, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return message.content[0].text

    def _call_openai_compat(self, prompt: str, api_key: str, model: str = "", api_base: str = "") -> str:
        import requests
        model = model or "deepseek-chat"
        api_base = api_base or "https://api.deepseek.com/v1"
        url = f"{api_base.rstrip('/')}/chat/completions"

        resp = requests.post(url, headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }, json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 512, "temperature": 0.7,
        }, timeout=30)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def _parse_response(self, text: str) -> Action:
        """从 LLM 响应中解析 Action"""
        # 提取 JSON
        json_match = re.search(r'\{[\s\S]*"action"[\s\S]*\}', text)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                return Action(
                    type=data.get("action", "wait"),
                    target=data.get("target", ""),
                    content=data.get("content", ""),
                    reasoning=data.get("reasoning", ""),
                )
            except json.JSONDecodeError:
                pass

        # 回退：从文本中猜测意图
        text_lower = text.lower()
        if "发送" in text or "send" in text_lower:
            return Action(type="send_message", content=text[:100], reasoning="从文本提取")
        if "搜索" in text or "search" in text_lower:
            return Action(type="search", content="目标区域", reasoning="从文本提取")
        if "等待" in text or "wait" in text_lower:
            return Action(type="wait", reasoning="从文本提取")

        return Action(type="wait", content="", reasoning=f"无法解析: {text[:50]}")

    def _fallback_decision(self, inbox: List[Dict], context: Dict = None) -> Action:
        """无 LLM 时的规则回退决策"""
        context = context or {}

        # 如果有新消息，回复它
        if inbox:
            last_msg = inbox[-1]
            sender = last_msg.get("from", "")
            content = last_msg.get("content", "")

            if self.role == "commander":
                if "情报" in str(content) or "报告" in str(content):
                    return Action(type="analyze", content=content,
                                  reasoning="收到情报，进行分析")
                return Action(type="send_message", target=sender,
                              content="收到，继续执行任务。",
                              reasoning=f"回复 {sender} 的消息")

            if self.role == "scout":
                if "搜索" in str(content) or "侦察" in str(content):
                    return Action(type="search", content="目标区域",
                                  reasoning="收到侦察指令，开始搜索")
                return Action(type="send_message", target=sender,
                              content="正在执行侦察任务。",
                              reasoning=f"回复 {sender}")

            if self.role == "analyst":
                if "分析" in str(content) or "数据" in str(content):
                    return Action(type="analyze", content=content,
                                  reasoning="收到分析请求")
                return Action(type="wait", reasoning="等待分析请求")

            return Action(type="send_message", target=sender,
                          content="收到。", reasoning=f"回复 {sender}")

        # 无消息时，按角色主动行动
        if self.role == "scout":
            return Action(type="search", content="敌军位置",
                          reasoning="主动侦察")
        elif self.role == "commander":
            return Action(type="wait", content="",
                          reasoning="等待侦察情报")
        elif self.role == "analyst":
            return Action(type="wait", content="",
                          reasoning="等待数据输入")
        else:
            return Action(type="wait", content="", reasoning="待命")


def create_brain(role: str, name: str, goals: List[str] = None) -> Brain:
    """工厂函数：创建指定角色的 Brain"""
    if goals is None:
        default_goals = {
            "scout": ["搜索敌军情报", "侦察目标区域地形", "及时报告发现"],
            "commander": ["收集各方情报", "制定作战方案", "下达清晰指令"],
            "analyst": ["分析收到的数据", "评估威胁等级", "提供决策建议"],
            "support": ["协调信息流通", "响应支援请求"],
            "observer": ["持续监测环境", "发现异常立即预警"],
        }
        goals = default_goals.get(role, ["完成指派任务"])
    return Brain(role=role, name=name, goals=goals)
