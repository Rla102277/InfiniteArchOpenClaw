"""Repo-local, file-backed HQ state. No third-party runtime dependencies.

Project documents are portable Git files. Activity is append-only JSONL.
No code execution or cloud API calls occur in this module.
"""
from __future__ import annotations

import json
import re
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT / "projects"
SKILLS = ROOT / ".agents" / "skills"
MAX_TEXT = 10000


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not value or len(value) > 64:
        raise ValueError("Use a shorter name with letters or numbers (1–64 characters).")
    return value


def clean_text(value: Any, max_length: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f"Please enter 1–{max_length} characters of text.")
    return value.strip().replace("\x00", "")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
        tmp = Path(f.name)
    tmp.replace(path)


def _append_event(folder: Path, event: str, message: str) -> None:
    # Events are an append-only, portable activity log in the project repository.
    with (folder / "activity.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time": timestamp(), "event": event, "message": message}) + "\n")


def project_path(project: str) -> Path:
    if slugify(project) != project:
        raise ValueError("Invalid project identifier")
    folder = PROJECTS / project
    if not (folder / "project.json").is_file() or folder.is_symlink():
        raise ValueError("Project not found")
    return folder


def create_project(name: str, idea: str) -> dict:
    name = clean_text(name, 100)
    idea = clean_text(idea)
    slug = slugify(name)
    folder = PROJECTS / slug
    if folder.exists():
        raise ValueError("A project with that name already exists.")
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "features").mkdir()
    (folder / "reviews").mkdir()
    (folder / "evidence").mkdir()
    (folder / "docs").mkdir()
    project = {"id": slug, "name": name, "created": timestamp(), "stage": "discovery", "summary": idea[:180]}
    atomic_json(folder / "project.json", project)
    (folder / "PROJECT.md").write_text(f"# {name}\n\n## Owner's original idea\n\n{idea}\n\n## Problem and intended outcome\n\n_To be discovered in conversation._\n\n## Acceptance criteria\n\n_To be agreed before building._\n\n", encoding="utf-8")
    (folder / "docs" / "DECISIONS.md").write_text("# Decisions\n\nRecord what was decided, why, and by whom.\n", encoding="utf-8")
    (folder / "docs" / "ROADMAP.md").write_text("# Roadmap\n\nNot yet approved. Discovery comes first.\n", encoding="utf-8")
    (folder / "docs" / "HANDOFF.md").write_text("# Handoff\n\nCurrent stage: Discovery.\n\nNext: interview the owner about the problem, users, constraints, and first usable slice.\n", encoding="utf-8")
    _append_event(folder, "project_created", f"Created {name}. Awaiting discovery conversation.")
    return project


def create_feature(project: str, title: str, description: str) -> dict:
    folder = project_path(project)
    title = clean_text(title, 120)
    description = clean_text(description)
    feature_id = "f-" + secrets.token_hex(4)
    record = {"id": feature_id, "title": title, "description": description,
              "stage": "requested", "created": timestamp(), "acceptance": "not_defined"}
    atomic_json(folder / "features" / (feature_id + ".json"), record)
    (folder / "features" / (feature_id + ".md")).write_text(
        f"# {title}\n\n**Status:** Requested, not approved for implementation.\n\n"
        f"## Request\n\n{description}\n\n## Acceptance tests\n\n_To be defined._\n\n"
        "## Evidence\n\n_No evidence yet._\n", encoding="utf-8")
    _append_event(folder, "feature_requested", f"Requested feature: {title}")
    return record


def request_review(project: str, title: str, evidence: str, ref: str) -> dict:
    folder = project_path(project)
    title = clean_text(title, 120)
    evidence = clean_text(evidence, 400)
    evidence_path = Path(evidence)
    if evidence_path.is_absolute() or ".." in evidence_path.parts:
        raise ValueError("Evidence must be a file inside this project.")
    expected = (folder / evidence_path).resolve()
    if not expected.is_relative_to(folder.resolve()) or not expected.is_file() or expected.is_symlink():
        raise ValueError("Evidence file not found inside this project. Attach real verification output first.")
    if expected.stat().st_size == 0:
        raise ValueError("Evidence file is empty.")
    ref = clean_text(ref, 150)
    if not re.fullmatch(r"[a-zA-Z0-9._/#-]+", ref):
        raise ValueError("Ref must be a commit, branch, or PR identifier.")
    review_id = "r-" + secrets.token_hex(4)
    record = {"id": review_id, "title": title, "evidence": evidence,
              "ref": ref, "status": "pending", "requested": timestamp(), "decided": None}
    atomic_json(folder / "reviews" / (review_id + ".json"), record)
    _append_event(folder, "review_requested", f"Owner review requested: {title}")
    return record


def decide_review(project: str, review_id: str, decision: str) -> dict:
    if decision not in {"accepted", "changes_requested"}:
        raise ValueError("Decision must be accepted or changes_requested.")
    folder = project_path(project)
    if not re.fullmatch(r"r-[0-9a-f]{8}", review_id):
        raise ValueError("Invalid review identifier")
    path = folder / "reviews" / (review_id + ".json")
    if not path.is_file() or path.is_symlink():
        raise ValueError("Review not found")
    review = read_json(path)
    if review["status"] != "pending":
        raise ValueError("This review has already been decided.")
    review["status"] = decision
    review["decided"] = timestamp()
    atomic_json(path, review)
    _append_event(folder, "owner_decision", f"Owner marked {review['title']}: {decision}")
    return review


def recent_events(folder: Path, limit: int = 30) -> list[dict]:
    path = folder / "activity.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(events))


def state() -> dict:
    import shutil
    projects = []
    events = []
    pending = []
    for folder in sorted(PROJECTS.iterdir()) if PROJECTS.exists() else []:
        if folder.is_symlink() or not folder.is_dir() or not (folder / "project.json").is_file():
            continue
        try:
            project = read_json(folder / "project.json")
            project["feature_count"] = len(list((folder / "features").glob("*.json")))
            projects.append(project)
            events.extend({**event, "project": project["name"]} for event in recent_events(folder))
            for path in (folder / "reviews").glob("*.json"):
                if path.is_symlink():
                    continue
                r = read_json(path)
                if r.get("status") == "pending":
                    pending.append({**r, "project": project["id"], "project_name": project["name"]})
        except (ValueError, KeyError, OSError, json.JSONDecodeError):
            continue
    events.sort(key=lambda e: e.get("time", ""), reverse=True)
    return {"projects": projects, "events": events[:30], "pending": pending,
            "skills": sorted(p.parent.name for p in SKILLS.glob("*/SKILL.md")),
            "tools": {x: shutil.which(x) is not None for x in ["git", "gh", "hermes", "codex", "python3"]},
            "version": "0.1.0", "deployment": "local-development-only"}
