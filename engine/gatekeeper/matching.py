"""Path globs and text normalization shared by config, rules and snapshot."""

from __future__ import annotations

import unicodedata
from pathlib import PurePosixPath


def path_matches(path: str, pattern: str) -> bool:
    """gitignore-style glob on a repo-relative POSIX path.

    A pattern with no `/` (other than a trailing one) matches at any depth;
    otherwise it is anchored at the repository root. A pattern also covers
    everything under a folder it names; a trailing `/` matches only that.
    `**` crosses folders, `*` does not.
    Matching is case-sensitive, like git.
    """
    anchored = pattern.startswith("/")
    directory = pattern.endswith("/")
    core = pattern.strip("/")
    if not core:
        return False
    if not anchored and "/" not in core:
        core = "**/" + core
    target = PurePosixPath(path.strip("/"))
    if directory:
        return target.full_match(core + "/**")
    return target.full_match(core) or target.full_match(core + "/**")


def normalize(text: str) -> str:
    """Case-, accent- and whitespace-insensitive form; other characters are kept."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(plain.casefold().split())
