import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ADR_DIR = DOCS / "adr"
DESIGN_DIR = DOCS / "design"


def test_document_categories_are_separate():
    for dirname in ("adr", "design", "requirements", "guides", "records"):
        assert (DOCS / dirname).is_dir()
    assert not list(DOCS.glob("ADR-*.md"))
    assert not (DOCS / "设计决策与变更规则.md").exists()


def test_adr_numbers_are_unique_contiguous_and_indexed():
    pattern = re.compile(r"^ADR-(\d{3})-.+\.md$")
    files = sorted(ADR_DIR.glob("ADR-*.md"))
    matches = [(path, pattern.match(path.name)) for path in files]
    assert all(match for _, match in matches)
    numbers = [int(match.group(1)) for _, match in matches]
    assert numbers == list(range(1, 33))
    assert len(numbers) == len(set(numbers))

    index = (ADR_DIR / "README.md").read_text(encoding="utf-8")
    for path, match in matches:
        adr_id = f"ADR-{match.group(1)}"
        assert path.name in index
        assert path.read_text(encoding="utf-8").startswith(f"# {adr_id}")


def test_design_documents_do_not_embed_adr_bodies():
    embedded_adr = re.compile(r"^## ADR-\d{3}", re.MULTILINE)
    for path in DESIGN_DIR.glob("*.md"):
        assert not embedded_adr.search(path.read_text(encoding="utf-8")), path


def test_all_design_documents_are_indexed():
    index = (DOCS / "README.md").read_text(encoding="utf-8")
    for path in DESIGN_DIR.glob("*.md"):
        assert f"design/{path.name}" in index, path


def test_local_markdown_links_resolve():
    link_pattern = re.compile(r"\[[^]]*\]\(([^)]+)\)")
    broken = []
    for path in DOCS.rglob("*.md"):
        for target in link_pattern.findall(path.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "#")):
                continue
            relative = target.split("#", 1)[0]
            if not relative or not relative.lower().endswith(".md"):
                continue
            if not (path.parent / relative).resolve().exists():
                broken.append((path.relative_to(ROOT), target))
    assert not broken
