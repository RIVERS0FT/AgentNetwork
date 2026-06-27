import os
import sys
import glob
import importlib.util

def generate_mds():
    tools_files = glob.glob("scenes/**/tools.py", recursive=True)
    for tf in tools_files:
        scene_dir = os.path.dirname(tf)
        skills_dir = os.path.join(scene_dir, "skills")
        os.makedirs(skills_dir, exist_ok=True)
        
        # Load the module
        spec = importlib.util.spec_from_file_location("mod", tf)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            print(f"Skipping {tf}: {e}")
            continue
            
        if not hasattr(mod, "ToolRegistry"):
            continue
            
        registry = mod.ToolRegistry
        tools_dict = getattr(registry, "_tools", getattr(registry, "_skills", {}))
        for tool_name, func in tools_dict.items():
            if not tool_name.endswith("_tool"):
                continue
                
            skill_name = tool_name[:-5] # remove _tool
            
            # Check if md already exists
            md_path = os.path.join(skills_dir, f"{skill_name}.md")
            if os.path.exists(md_path):
                continue
                
            params = getattr(registry, "_params", {}).get(tool_name, {})
            req = params.get("required", [])
            opt = params.get("optional", [])
            
            inputs_yaml = ""
            for p in req:
                inputs_yaml += f"  {p}:\n    type: string\n    required: true\n"
            for p in opt:
                inputs_yaml += f"  {p}:\n    type: string\n    required: false\n"
                
            doc = (func.__doc__ or f"执行 {skill_name} 动作").strip().split('\n')[0].strip()
            
            md_content = f"""---
name: {skill_name}
description: "{doc}"
version: 1.0
inputs:
{inputs_yaml}
tools:
  - {tool_name}
---

# Skill: {skill_name}

## 何时使用
当需要执行 {doc} 时使用此技能。

## 执行步骤
1. 调用 `{tool_name}` 工具。
2. 检查返回结果并根据需要反馈。

"""
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(md_content)
            print(f"Generated {md_path}")

if __name__ == '__main__':
    generate_mds()
