"""Validate private paths before creating or reading operational files."""
from pathlib import Path

from .brain import ROOT

DIRECTORIES = ["runtime", "private", "secrets", "logs", "exports", "projects", "knowledge", "inbox", "backups", "approvals"]


def private_home(value, root=ROOT):
    configured = Path(value).expanduser()
    if not configured.is_absolute() or configured.is_symlink():
        raise ValueError("AIHQ_HOME must be an absolute private directory, not a symlink.")
    home, root = configured.resolve(), Path(root).resolve()
    if home == Path.home().resolve() or home.is_relative_to(root) or root.is_relative_to(home):
        raise ValueError("Private data must be outside the repository and separate from the home directory.")
    for name in DIRECTORIES:
        if (home / name).is_symlink():
            raise ValueError("Private subdirectories must not be symlinks.")
    return home


def private_path(home, relative):
    home = Path(home).resolve()
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Private paths must remain inside their workspace.")
    target = home
    for part in relative.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError("Private paths must not contain symlinks.")
    if not target.resolve().is_relative_to(home):
        raise ValueError("Private path escaped its workspace.")
    return target
