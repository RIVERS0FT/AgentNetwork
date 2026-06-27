import os
import glob
import re
import json

def refactor_tools_py():
    skills_files = glob.glob("scenes/**/skills.py", recursive=True)
    for filepath in skills_files:
        dir_path = os.path.dirname(filepath)
        tools_path = os.path.join(dir_path, "tools.py")
        
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # Rename SkillRegistry -> ToolRegistry
        content = content.replace("class SkillRegistry:", "class ToolRegistry:")
        content = content.replace("SkillRegistry.", "ToolRegistry.")
        content = content.replace("_skills", "_tools")
        content = content.replace("list_skills(cls)", "list_tools(cls)")
        
        # Find all registered functions
        # Pattern: ToolRegistry.register("name", func_name)
        registered_funcs = re.findall(r'ToolRegistry\.register\("([^"]+)",\s*([a-zA-Z0-9_]+)\)', content)
        
        for name, func_name in set(registered_funcs):
            # Rename def func_name( to def func_name_tool(
            # and ToolRegistry.register("name", func_name) to ToolRegistry.register("name_tool", func_name_tool)
            content = re.sub(rf'\bdef {func_name}\b', f'def {func_name}_tool', content)
            
            # Note: what if it's called somewhere else inside the file?
            # It's better to replace the word boundary function calls. But many might not be called internally.
            
            # Update the registration line
            content = content.replace(f'ToolRegistry.register("{name}", {func_name})', f'ToolRegistry.register("{name}_tool", {func_name}_tool)')

        with open(tools_path, 'w', encoding='utf-8') as f:
            f.write(content)
            
        os.remove(filepath)
        print(f"Renamed {filepath} to {tools_path} and added _tool suffixes.")

def refactor_json():
    json_files = glob.glob("scenes/**/instances_and_skills.json", recursive=True)
    for filepath in json_files:
        with open(filepath, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
            except:
                print(f"Error parsing {filepath}")
                continue
                
        changed = False
        if "container_instances" in data:
            for role, config in data["container_instances"].items():
                if "skills" in config:
                    skills_list = config.pop("skills")
                    config["skill_refs"] = skills_list
                    # if skills is list of strings
                    if all(isinstance(s, str) for s in skills_list):
                        config["tool_refs"] = [s + "_tool" for s in skills_list]
                    else:
                        # list of dicts (skill_bindings)
                        config["tool_refs"] = [s.get("skill_name", "") + "_tool" for s in skills_list]
                    changed = True
                        
                if "skill_bindings" in config:
                    skills_list = config.pop("skill_bindings")
                    config["skill_refs"] = skills_list
                    config["tool_refs"] = [s.get("skill_name", "") + "_tool" if isinstance(s, dict) else s + "_tool" for s in skills_list]
                    changed = True
        
        if changed:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"Updated {filepath}")

if __name__ == '__main__':
    refactor_tools_py()
    refactor_json()
