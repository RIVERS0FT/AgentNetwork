from pathlib import Path

root = Path(__file__).resolve().parents[1]
p = root / "agent_network" / "agent_model.py"
s = p.read_text(encoding="utf-8")
s = s.replace("- position / explicit runtime metadata\n", "- explicit runtime metadata\n", 1)
s = s.replace("from datetime import datetime\n", "", 1)
s = s.replace('''        self.pending_task_descs: List[str] = []
        self._created_at = datetime.now().isoformat(timespec="seconds")

        # Frontend layout position.
        self.x: float = 0.0
        self.y: float = 0.0
        self.speed: float = 1.0
        self._target_x: Optional[float] = None
        self._target_y: Optional[float] = None
''', '''        self.pending_task_descs: List[str] = []
''', 1)
s = s.replace('''            "completed_tasks": len(self.completed_tasks),
            "created_at": self._created_at,
            "x": self.x,
            "y": self.y,
''', '''            "completed_tasks": len(self.completed_tasks),
''', 1)
p.write_text(s, encoding="utf-8", newline="\n")
