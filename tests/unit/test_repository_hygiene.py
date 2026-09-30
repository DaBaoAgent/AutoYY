from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PUBLIC_ROOTS = [
    REPO / "README.md",
    REPO / "SKILL.md",
    REPO / "CONTRIBUTING.md",
    REPO / "SECURITY.md",
    REPO / "CHANGELOG.md",
    REPO / "AUTOYY_ITERATION_REVIEW_PLAN.md",
    REPO / "AUTOYY_PHASE_ACCEPTANCE_REPORT.md",
    REPO / "pyproject.toml",
    REPO / "src",
    REPO / "scripts",
    REPO / "references",
    REPO / "agents",
    REPO / "laorou",
    REPO / ".github",
]
TEXT_SUFFIXES = {".py", ".ps1", ".md", ".toml", ".yaml", ".yml"}


def iter_text_files():
    for root in PUBLIC_ROOTS:
        if not root.exists():
            continue
        if root.is_file():
            yield root
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                yield path


def test_public_code_and_docs_have_no_utf8_bom_or_questionmark_corruption() -> None:
    bad_bom = []
    bad_questions = []
    for path in iter_text_files():
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            bad_bom.append(str(path.relative_to(REPO)))
        text = raw.decode("utf-8")
        if "???" in text:
            bad_questions.append(str(path.relative_to(REPO)))
    assert bad_bom == []
    assert bad_questions == []


def test_public_docs_do_not_contain_machine_specific_private_paths() -> None:
    forbidden = ("xxx13", "C:\\Users\\", "D:\\@kaifa\\", "F:\\16 ", "BaiduSyncdisk")
    hits = []
    for path in iter_text_files():
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in forbidden):
            hits.append(str(path.relative_to(REPO)))
    assert hits == []
