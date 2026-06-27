import json
import os
import re
from typing import Any, Dict, List

FACT_BOARD_SECTIONS = [
    ("task_progress", "Task Progress"),
    ("completed_actions", "Completed Actions"),
    ("pending_items", "Pending Items"),
    ("key_constraints", "Key Constraints"),
    ("recent_skill_results", "Recent Skill Results"),
]

def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default

class BoundedFactBoard:
    """Small rule-built fact board used to cap long raw histories."""

    def __init__(
        self,
        max_items: int = None,
        max_chars: int = None,
        item_max_chars: int = None,
    ):
        self.max_items = max(1, max_items if max_items is not None else _int_env("AGENT_FACT_BOARD_MAX_ITEMS", 40))
        self.max_chars = max(500, max_chars if max_chars is not None else _int_env("AGENT_FACT_BOARD_MAX_CHARS", 6000))
        self.item_max_chars = max(80, item_max_chars if item_max_chars is not None else _int_env("AGENT_FACT_ITEM_MAX_CHARS", 300))
        self.sections: Dict[str, List[str]] = {key: [] for key, _ in FACT_BOARD_SECTIONS}

    def clear(self):
        for items in self.sections.values():
            items.clear()

    def _stringify(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(value)

    def _shorten(self, text: Any) -> str:
        text = re.sub(r"\s+", " ", self._stringify(text)).strip()
        if len(text) > self.item_max_chars:
            return text[: self.item_max_chars - 3].rstrip() + "..."
        return text

    def add(self, section: str, text: Any):
        if section not in self.sections:
            section = "task_progress"
        text = self._shorten(text)
        if not text:
            return
        items = self.sections[section]
        if text in items:
            items.remove(text)
        items.append(text)
        self._prune()

    def add_action(self, action: Any):
        if hasattr(action, "to_dict"):
            data = action.to_dict()
        elif isinstance(action, dict):
            data = action
        else:
            data = {}
        action_type = data.get("type", data.get("action", "wait"))
        target = data.get("target", "")
        content = data.get("content", "")
        reasoning = data.get("reasoning", "")
        if action_type in ("send_message", "broadcast"):
            self.add("completed_actions", f"{action_type} -> {target}: {content}")
        elif action_type == "execute_skill":
            skill = data.get("skill") or target
            params = data.get("params") or content
            self.add("pending_items", f"execute_skill {skill}: {params}")
        elif action_type in ("analyze", "plan"):
            self.add("task_progress", f"{action_type}: {content or reasoning}")
        elif action_type == "wait":
            self.add("pending_items", f"wait: {reasoning or content}")
        else:
            self.add("task_progress", f"{action_type} {target}: {content or reasoning}")

    def add_inbox_facts(self, inbox: List[Dict]):
        for msg in inbox[-5:]:
            if msg.get("type") != "system":
                continue
            content = msg.get("content", "")
            lower = content.lower()
            if "skill" in lower or "result" in lower or "执行结果" in content or "技能" in content:
                self.add("recent_skill_results", content)
            else:
                self.add("key_constraints", content)

    def add_skill_result(self, skill_name: str, result: Any):
        self.add("recent_skill_results", f"{skill_name}: {result}")

    def _total_items(self) -> int:
        return sum(len(items) for items in self.sections.values())

    def _render(self) -> str:
        parts = ["## Fact Board", "Stable bounded summary for long-running simulation context."]
        for key, title in FACT_BOARD_SECTIONS:
            items = self.sections.get(key, [])
            parts.append(f"\n### {title}")
            if items:
                parts.extend(f"- {item}" for item in items)
            else:
                parts.append("- (none)")
        return "\n".join(parts)

    def _drop_oldest(self) -> bool:
        for key, _ in FACT_BOARD_SECTIONS:
            if self.sections[key]:
                self.sections[key].pop(0)
                return True
        return False

    def _prune(self):
        while self._total_items() > self.max_items and self._drop_oldest():
            pass
        while len(self._render()) > self.max_chars and self._drop_oldest():
            pass

    def to_message_content(self) -> str:
        self._prune()
        return self._render()
