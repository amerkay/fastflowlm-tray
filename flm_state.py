"""Pure state logic for flm-tray: journal phase, model list, env file, icon state.

Deliberately free of PyQt6 and of any I/O beyond the env file, so it is testable
without a display or the Qt runtime. The Qt shell lives in flm_tray.py.
"""

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import NamedTuple

COLOUR_RUNNING = "#3daf3d"  # green
COLOUR_LOADING = "#d8a03c"  # amber: weights moving into DRAM
COLOUR_IDLE = "#888888"
COLOUR_FAULT = "#d04040"

# (line prefix, substring, phase). The prefix match is what makes this safe:
# flm logs the model's own generated text verbatim into the same stream, so a
# model discussing "Loading model:" would otherwise drive the icon.
PHASE_MARKERS = (
    ("[FLM]", "Loading model:", "loading"),
    ("[FLM]", "Start prefill", "busy"),
    ("[FLM]", "Start generating", "busy"),
    ("[\U0001F535", "NPU Lock Released", "ready"),
    ("[\U0001F7E2", "NPU Locked", "busy"),
    ("[FLM]", "WebServer started", "ready"),
)

# `flm list` marks each model ✅ current, ⚠ present but outdated, ⏬ not downloaded.
_MODEL_RE = re.compile(r"^\s*-\s+(\S+)\s+([✅⚠⏬])")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_NPU_SUFFIX_RE = re.compile(r"npu2?$")
_TAG_MATCH_THRESHOLD = 0.62


def phase_for_line(line: str) -> str | None:
    """The phase a journal line implies, or None if it implies nothing."""
    for tag, key, phase in PHASE_MARKERS:
        if line.startswith(tag) and key in line:
            return phase
    return None


def model_dir_for_line(line: str) -> str | None:
    """Final path component of a `[FLM] Loading model: <path>` line, else None."""
    if phase_for_line(line) != "loading":
        return None
    _, _, path = line.partition("Loading model:")
    return Path(path.strip()).name or None


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def resolve_model_tag(dir_name: str, known_tags: list[str]) -> str:
    """Map an on-disk model directory to its user-facing tag.

    The two spellings diverge in ways no rule captures — Qwen3.6-35B-A3B-NPU2 is
    served as qwen3.6-moe:35b-a3b — so this is a similarity match against the
    tags actually installed, falling back to the directory name when nothing is
    close enough to trust.
    """
    target = _NPU_SUFFIX_RE.sub("", _norm(dir_name))
    best, best_score = dir_name, _TAG_MATCH_THRESHOLD
    for tag in known_tags:
        score = SequenceMatcher(None, target, _norm(tag)).ratio()
        if score > best_score:
            best, best_score = tag, score
    return best


def parse_model_list(text: str) -> list[tuple[str, bool]]:
    """[(name, is_outdated)] for downloaded models in `flm list` output.

    The stream interleaves [WARNING]/[FLM] lines with the entries, so this
    matches per line rather than splitting the block.
    """
    return [
        (m.group(1), m.group(2) == "⚠")
        for m in map(_MODEL_RE.match, _ANSI_RE.sub("", text).splitlines())
        if m and m.group(2) in ("✅", "⚠")
    ]


def read_model(path: Path) -> str | None:
    """FLM_MODEL from the env file the systemd unit reads, or None."""
    try:
        for line in Path(path).read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "FLM_MODEL":
                return value.strip() or None
    except OSError:
        pass
    return None


def write_model(path: Path, name: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"FLM_MODEL={name}\n")


def shown_model(active: str, configured: str | None,
                serving: str | None) -> str | None:
    """The model the UI should name.

    While the unit runs, that is whatever is resident — a client asking for
    another model hot-swaps it, so FLM_MODEL then names only what was
    preloaded. Stopped, the configured model is all there is to report.
    """
    return serving if active == "active" and serving else configured


class VisualState(NamedTuple):
    colour: str
    note: str
    animate: bool


def visual_state(load: str, active: str, phase: str | None) -> VisualState:
    """Icon colour, tooltip fragment, and whether to breathe the dot."""
    if load != "loaded":
        return VisualState(COLOUR_FAULT, "not installed", False)
    if active == "active":
        if phase == "loading":
            return VisualState(COLOUR_LOADING, "loading model", False)
        if phase == "busy":
            return VisualState(COLOUR_RUNNING, "generating", True)
        return VisualState(COLOUR_RUNNING, "ready", False)
    if active in ("activating", "deactivating"):
        return VisualState(COLOUR_LOADING, active, False)
    if active == "failed":
        return VisualState(COLOUR_FAULT, "failed", False)
    return VisualState(COLOUR_IDLE, active, False)


def should_idle_stop(active: str, phase: str | None,
                     idle_seconds: float, limit: float) -> bool:
    """Whether to stop a server nobody is using.

    Gated on phase, not the clock alone: a long generation logs nothing between
    "Start generating" and its output, so elapsed time would cut it off.
    """
    return (
        active == "active"
        and phase not in ("busy", "loading")
        and idle_seconds > limit
    )
