"""Static checks for versioned process documents and skill contracts."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ["AGENTS.md", "README.md", "START_HERE.md", "docs/WORKFLOW.md", "docs/ONBOARDING.md", "docs/SECURITY.md", ".github/workflows/ci.yml"]
EXPECTED = ["hq-router", "grill-me", "wayfinder", "specify", "architect", "build-slice", "verify", "review", "reflect", "release"]


def check():
    errors = []
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            errors.append("Missing: " + name)
    for name in EXPECTED:
        path = ROOT / ".agents" / "skills" / name / "SKILL.md"
        if not path.is_file():
            errors.append("Missing skill: " + name)
            continue
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            errors.append("Missing YAML frontmatter: " + name)
        if not re.search(r"(?m)^name:\s*" + re.escape(name) + r"\s*$", text):
            errors.append("Invalid skill name: " + name)
        if not re.search(r"(?m)^description:\s*.+", text):
            errors.append("Missing description: " + name)
    return errors


if __name__ == "__main__":
    problems = check()
    for problem in problems:
        print("ERROR:", problem)
    if problems:
        raise SystemExit(1)
    print("PASS: required process files and 10 skills checked")
