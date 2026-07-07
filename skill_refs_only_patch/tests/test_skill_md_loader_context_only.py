import pytest

from agent_network.skill_md_loader import LocalSkillRegistry, SkillSpec, parse_skill_md


def test_parse_skill_md_returns_context_metadata(tmp_path):
    skill_file = tmp_path / "submit_report.md"
    skill_file.write_text(
        """---
name: submit_report
description: Submit a structured report
version: 1.2
category: reporting
inputs:
  title:
    type: string
    description: Report title
    required: true
outputs:
  report_id:
    type: string
tools:
  - save_report
---
Follow the reporting SOP.
""",
        encoding="utf-8",
    )

    parsed = parse_skill_md(skill_file)

    assert parsed["name"] == "submit_report"
    assert parsed["description"] == "Submit a structured report"
    assert parsed["version"] == "1.2"
    assert parsed["category"] == "reporting"
    assert parsed["input_schema"]["properties"]["title"]["type"] == "string"
    assert parsed["input_schema"]["required"] == ["title"]
    assert parsed["output_schema"]["properties"]["report_id"]["type"] == "string"
    assert parsed["tools"] == ["save_report"]
    assert "Follow the reporting SOP" in parsed["sop_content"]


def test_local_skill_registry_returns_context_specs_only():
    registry = LocalSkillRegistry(skill_refs=["submit_report"])
    registry.register(
        SkillSpec(
            name="submit_report",
            description="Submit report",
            version="1.0",
            category="reporting",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            tools=["save_report"],
            sop_content="SOP body",
            source_path="skills/submit_report.md",
        )
    )

    specs = registry.context_specs()

    assert len(specs) == 1
    assert specs[0]["name"] == "submit_report"
    assert specs[0]["tools"] == ["save_report"]
    assert specs[0]["sop_content"] == "SOP body"


def test_tool_specs_is_removed_to_prevent_skill_as_tool_regression():
    registry = LocalSkillRegistry()

    with pytest.raises(RuntimeError) as exc:
        registry.tool_specs()

    assert "Markdown Skill must be injected as context" in str(exc.value)
