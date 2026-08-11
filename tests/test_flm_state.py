"""Contract tests for flm_state.

Written from the written spec alone, deliberately without reading the
implementation: these encode the behaviour the tray depends on, not the code
that currently provides it.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import flm_state  # noqa: E402

import pytest  # noqa: E402

# Log tag prefixes. Spelled as escapes so the intent survives an editor that
# mangles emoji; the blue and green circles are distinct phases.
BLUE = "\U0001f535"
GREEN = "\U0001f7e2"


# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------


def test_the_four_status_colours_are_distinct_from_one_another():
    colours = [
        flm_state.COLOUR_RUNNING,
        flm_state.COLOUR_LOADING,
        flm_state.COLOUR_IDLE,
        flm_state.COLOUR_FAULT,
    ]
    assert len(set(colours)) == 4


@pytest.mark.parametrize(
    "name",
    ["COLOUR_RUNNING", "COLOUR_LOADING", "COLOUR_IDLE", "COLOUR_FAULT"],
)
def test_every_status_colour_is_a_hex_string(name):
    value = getattr(flm_state, name)
    assert isinstance(value, str)
    assert value.startswith("#")
    assert len(value) == 7


# --------------------------------------------------------------------------
# phase_for_line
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,expected",
    [
        ("[FLM]  Loading model: /home/kay/.config/flm/models/Llama-3.2-1B-NPU2", "loading"),
        ("[FLM]  Start prefill", "busy"),
        ("[FLM]  Start generating", "busy"),
        ("[FLM]  WebServer started", "ready"),
        (f"[{BLUE}]  NPU Lock Released", "ready"),
        (f"[{GREEN}]  NPU Locked", "busy"),
    ],
)
def test_tagged_server_lines_are_classified_into_their_phase(line, expected):
    assert flm_state.phase_for_line(line) == expected


def test_lock_released_is_ready_and_not_confused_with_the_locked_phase():
    assert flm_state.phase_for_line(f"[{BLUE}]  NPU Lock Released") == "ready"
    assert flm_state.phase_for_line(f"[{GREEN}]  NPU Locked") == "busy"


@pytest.mark.parametrize(
    "line",
    [
        # The server logs the model's own generated text into the same journal
        # stream, so these phrases arrive without the [FLM] tag and mean nothing.
        "Here is a summary: Loading model: is what it does, Start generating...",
        '*   **`flm bench` Default:** " `flm bench` now runs 2 iterations',
        "Started flm.service - FastFlowLM server.",
        "[LOG]  Target: /v1/chat/completions",
        "",
        "   ",
    ],
)
def test_untagged_lines_imply_no_phase_even_when_they_quote_the_keys(line):
    assert flm_state.phase_for_line(line) is None


@pytest.mark.parametrize(
    "line",
    [
        "The model said: Start prefill of the data",
        "docs mention NPU Locked behaviour",
        "note: NPU Lock Released eventually",
        "WebServer started, apparently",
        "[LOG]  Loading model: /some/path",
        "[WARNING]  Start generating soon",
    ],
)
def test_a_key_without_its_own_tag_implies_no_phase(line):
    assert flm_state.phase_for_line(line) is None


@pytest.mark.parametrize(
    "line",
    [
        "[FLM]  Ready",
        "[FLM]  Listening on 127.0.0.1:11434",
        f"[{BLUE}]  something else entirely",
        f"[{GREEN}]  something else entirely",
    ],
)
def test_a_tag_without_a_known_key_implies_no_phase(line):
    assert flm_state.phase_for_line(line) is None


def test_the_tag_must_start_the_line_not_merely_appear_in_it():
    assert flm_state.phase_for_line("prefix [FLM]  Start generating") is None


# --------------------------------------------------------------------------
# model_dir_for_line
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,expected",
    [
        (
            "[FLM]  Loading model: /home/kay/.config/flm/models/Qwen3.6-35B-A3B-NPU2",
            "Qwen3.6-35B-A3B-NPU2",
        ),
        (
            "[FLM]  Loading model: /home/kay/.config/flm/models/Llama-3.2-1B-NPU2",
            "Llama-3.2-1B-NPU2",
        ),
        (
            "[FLM]  Loading model: /opt/models/Whisper-V3-Turbo-NPU2/",
            "Whisper-V3-Turbo-NPU2",
        ),
        (
            "[FLM]  Loading model: /opt/models/Gemma4-E2B-IT-NPU2   ",
            "Gemma4-E2B-IT-NPU2",
        ),
        (
            "[FLM]  Loading model: /opt/models/Gemma4-E2B-IT-NPU2/   ",
            "Gemma4-E2B-IT-NPU2",
        ),
    ],
)
def test_the_loading_line_yields_the_final_path_component(line, expected):
    assert flm_state.model_dir_for_line(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "Here is a summary: Loading model: is what it does, Start generating...",
        "Started flm.service - FastFlowLM server.",
        "[LOG]  Loading model: /home/kay/.config/flm/models/Llama-3.2-1B-NPU2",
        "[FLM]  Start generating",
        "[FLM]  WebServer started",
        f"[{GREEN}]  NPU Locked",
        "",
        "   ",
    ],
)
def test_non_loading_lines_yield_no_model_directory(line):
    assert flm_state.model_dir_for_line(line) is None


def test_a_line_the_classifier_calls_loading_is_the_only_one_with_a_directory():
    line = "[FLM]  Loading model: /home/kay/.config/flm/models/Qwen3.6-35B-A3B-NPU2"
    assert flm_state.phase_for_line(line) == "loading"
    assert flm_state.model_dir_for_line(line) is not None


# --------------------------------------------------------------------------
# resolve_model_tag
# --------------------------------------------------------------------------

KNOWN_TAGS = [
    "llama3.2:1b",
    "whisper-v3:turbo",
    "gemma4-it:e2b",
    "qwen3.6-moe:35b-a3b",
    "qwen3.5:0.8b",
]


@pytest.mark.parametrize(
    "dir_name,expected",
    [
        ("Llama-3.2-1B-NPU2", "llama3.2:1b"),
        ("Whisper-V3-Turbo-NPU2", "whisper-v3:turbo"),
        ("Gemma4-E2B-IT-NPU2", "gemma4-it:e2b"),
        ("Qwen3.6-35B-A3B-NPU2", "qwen3.6-moe:35b-a3b"),
    ],
)
def test_an_on_disk_directory_resolves_to_its_user_facing_tag(dir_name, expected):
    assert flm_state.resolve_model_tag(dir_name, KNOWN_TAGS) == expected


def test_a_directory_matching_nothing_is_returned_unchanged():
    assert (
        flm_state.resolve_model_tag("Totally-Unrelated-XYZ", ["llama3.2:1b"])
        == "Totally-Unrelated-XYZ"
    )


def test_an_empty_tag_list_leaves_the_directory_name_unchanged():
    assert flm_state.resolve_model_tag("Llama-3.2-1B-NPU2", []) == "Llama-3.2-1B-NPU2"


@pytest.mark.parametrize("suffix", ["", "-NPU", "-NPU2"])
def test_the_npu_marker_on_a_directory_does_not_affect_the_match(suffix):
    assert flm_state.resolve_model_tag("Llama-3.2-1B" + suffix, KNOWN_TAGS) == "llama3.2:1b"


def test_matching_ignores_case_and_punctuation_differences():
    assert flm_state.resolve_model_tag("llama_3.2_1b", KNOWN_TAGS) == "llama3.2:1b"


def test_an_exact_tag_resolves_to_itself():
    assert flm_state.resolve_model_tag("llama3.2:1b", KNOWN_TAGS) == "llama3.2:1b"


def test_a_resolved_tag_is_always_one_of_the_known_tags_or_the_input():
    for dir_name in ["Llama-3.2-1B-NPU2", "Totally-Unrelated-XYZ", "Gemma4-E2B-IT-NPU2"]:
        result = flm_state.resolve_model_tag(dir_name, KNOWN_TAGS)
        assert result in KNOWN_TAGS or result == dir_name


# --------------------------------------------------------------------------
# parse_model_list
# --------------------------------------------------------------------------

LIST_SAMPLE = """Models:
  - gemma4-it:e2b ✅
  - llama3.2:1b ✅
[WARNING]  Local model qwen3.5:0.8b version: 0.9.38 < 0.9.45
  - qwen3.5:0.8b ⚠
[FLM]  Re-pulling latest model...
  - whisper-v3:turbo ⏬
  - qwen3.6-moe:35b-a3b ✅
"""


def test_a_realistic_listing_yields_only_downloaded_models_in_order():
    assert flm_state.parse_model_list(LIST_SAMPLE) == [
        ("gemma4-it:e2b", False),
        ("llama3.2:1b", False),
        ("qwen3.5:0.8b", True),
        ("qwen3.6-moe:35b-a3b", False),
    ]


def test_models_that_are_not_downloaded_are_excluded_entirely():
    names = [name for name, _ in flm_state.parse_model_list(LIST_SAMPLE)]
    assert "whisper-v3:turbo" not in names


def test_a_stale_model_is_reported_as_outdated():
    outdated = {name: stale for name, stale in flm_state.parse_model_list(LIST_SAMPLE)}
    assert outdated["qwen3.5:0.8b"] is True
    assert outdated["gemma4-it:e2b"] is False


def test_noise_lines_never_become_entries():
    names = [name for name, _ in flm_state.parse_model_list(LIST_SAMPLE)]
    for noise in ("Models:", "[WARNING]", "[FLM]", "Re-pulling"):
        assert not any(noise in name for name in names)


def test_empty_input_yields_no_models():
    assert flm_state.parse_model_list("") == []


def test_ansi_colour_escapes_are_stripped_from_names():
    text = (
        "Models:\n"
        "  - \x1b[32mgemma4-it:e2b\x1b[0m ✅\n"
        "  - \x1b[33mqwen3.5:0.8b\x1b[0m ⚠\n"
        "\x1b[31m  - whisper-v3:turbo ⏬\x1b[0m\n"
    )
    assert flm_state.parse_model_list(text) == [
        ("gemma4-it:e2b", False),
        ("qwen3.5:0.8b", True),
    ]


def test_parsed_names_carry_no_marker_or_surrounding_whitespace():
    for name, _ in flm_state.parse_model_list(LIST_SAMPLE):
        assert name == name.strip()
        assert "✅" not in name
        assert "⚠" not in name
        assert "⏬" not in name
        assert not name.startswith("-")


# --------------------------------------------------------------------------
# read_model / write_model
# --------------------------------------------------------------------------


def test_a_missing_file_has_no_model(tmp_path):
    assert flm_state.read_model(tmp_path / "nope.env") is None


@pytest.mark.parametrize(
    "content",
    [
        "",
        "\n\n",
        "OTHER_KEY=value\n",
        "FLM_MODEL=\n",
        "FLM_MODEL=   \n",
        "FLM_MODEL=\t\n",
    ],
)
def test_an_absent_or_empty_value_reads_as_no_model(tmp_path, content):
    path = tmp_path / "flm.env"
    path.write_text(content)
    assert flm_state.read_model(path) is None


def test_the_model_key_is_read_past_blank_lines_and_other_keys(tmp_path):
    path = tmp_path / "flm.env"
    path.write_text("\nOTHER=1\n\nFLM_MODEL=llama3.2:1b\nTRAILING=2\n")
    assert flm_state.read_model(path) == "llama3.2:1b"


def test_surrounding_whitespace_is_stripped_from_the_value(tmp_path):
    path = tmp_path / "flm.env"
    path.write_text("FLM_MODEL=   llama3.2:1b   \n")
    assert flm_state.read_model(path) == "llama3.2:1b"


@pytest.mark.parametrize(
    "name",
    ["qwen3.6-moe:35b-a3b", "llama3.2:1b", "whisper-v3:turbo", "gemma4-it:e2b"],
)
def test_a_written_model_name_round_trips_exactly(tmp_path, name):
    path = tmp_path / "flm.env"
    flm_state.write_model(path, name)
    assert flm_state.read_model(path) == name


def test_writing_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "deep" / "nested" / "flm.env"
    flm_state.write_model(path, "llama3.2:1b")
    assert path.exists()
    assert flm_state.read_model(path) == "llama3.2:1b"


def test_writing_replaces_any_previous_model(tmp_path):
    path = tmp_path / "flm.env"
    flm_state.write_model(path, "llama3.2:1b")
    flm_state.write_model(path, "qwen3.6-moe:35b-a3b")
    assert flm_state.read_model(path) == "qwen3.6-moe:35b-a3b"
    assert "llama3.2:1b" not in path.read_text()


def test_writing_over_unrelated_content_still_reads_back_the_new_model(tmp_path):
    path = tmp_path / "flm.env"
    path.write_text("JUNK\nFLM_MODEL=old:1b\nMORE JUNK\n")
    flm_state.write_model(path, "gemma4-it:e2b")
    assert flm_state.read_model(path) == "gemma4-it:e2b"


# --------------------------------------------------------------------------
# shown_model
# --------------------------------------------------------------------------


def test_a_running_server_is_named_by_the_model_it_actually_holds():
    assert flm_state.shown_model("active", "lfm2.5-it:1.2b", "qwen3.6-moe:35b-a3b") == (
        "qwen3.6-moe:35b-a3b"
    )


def test_a_running_server_with_no_load_line_yet_falls_back_to_the_configured_model():
    assert flm_state.shown_model("active", "lfm2.5-it:1.2b", None) == "lfm2.5-it:1.2b"


@pytest.mark.parametrize("active", ["inactive", "failed", "activating", "deactivating"])
def test_a_server_that_is_not_running_is_named_by_what_it_would_preload(active):
    assert flm_state.shown_model(active, "lfm2.5-it:1.2b", "qwen3.6-moe:35b-a3b") == (
        "lfm2.5-it:1.2b"
    )


def test_nothing_configured_and_nothing_resident_names_nothing():
    assert flm_state.shown_model("inactive", None, None) is None


def test_a_resident_model_is_reported_even_with_no_model_configured():
    assert flm_state.shown_model("active", None, "llama3.2:1b") == "llama3.2:1b"


# --------------------------------------------------------------------------
# visual_state
# --------------------------------------------------------------------------


@pytest.mark.parametrize("active", ["active", "inactive", "failed", "activating", "unknown"])
@pytest.mark.parametrize("phase", [None, "loading", "busy", "ready"])
def test_a_missing_install_overrides_every_other_signal(active, phase):
    state = flm_state.visual_state("not-found", active, phase)
    assert tuple(state)[:3] == (flm_state.COLOUR_FAULT, "not installed", False)


def test_a_missing_install_wins_even_while_the_service_is_active():
    state = flm_state.visual_state("not-found", "active", "busy")
    assert state.colour == flm_state.COLOUR_FAULT
    assert state.note == "not installed"
    assert state.animate is False


@pytest.mark.parametrize(
    "active,phase,expected",
    [
        ("active", "loading", ("loading model", False)),
        ("active", "busy", ("generating", True)),
        ("active", "ready", ("ready", False)),
        ("active", None, ("ready", False)),
        ("active", "something-new", ("ready", False)),
    ],
)
def test_an_active_service_reports_its_phase(active, phase, expected):
    state = flm_state.visual_state("loaded", active, phase)
    assert (state.note, state.animate) == expected


@pytest.mark.parametrize("phase", ["busy", "ready", None, "unknown"])
def test_a_running_service_shows_the_running_colour(phase):
    assert flm_state.visual_state("loaded", "active", phase).colour == flm_state.COLOUR_RUNNING


def test_loading_a_model_shows_the_loading_colour():
    assert flm_state.visual_state("loaded", "active", "loading").colour == flm_state.COLOUR_LOADING


@pytest.mark.parametrize("active", ["activating", "deactivating"])
@pytest.mark.parametrize("phase", [None, "busy", "loading", "ready"])
def test_a_transitioning_service_shows_its_own_state_as_the_note(active, phase):
    state = flm_state.visual_state("loaded", active, phase)
    assert (state.colour, state.note, state.animate) == (flm_state.COLOUR_LOADING, active, False)


@pytest.mark.parametrize("phase", [None, "busy", "loading", "ready"])
def test_a_failed_service_is_a_fault(phase):
    state = flm_state.visual_state("loaded", "failed", phase)
    assert (state.colour, state.note, state.animate) == (flm_state.COLOUR_FAULT, "failed", False)


@pytest.mark.parametrize("active", ["inactive", "unknown", "", "reloading"])
def test_any_other_service_state_is_idle_and_named_verbatim(active):
    state = flm_state.visual_state("loaded", active, None)
    assert (state.colour, state.note, state.animate) == (flm_state.COLOUR_IDLE, active, False)


def test_animation_runs_only_while_generating():
    animated = [
        (load, active, phase)
        for load in ("loaded", "not-found")
        for active in ("active", "activating", "deactivating", "failed", "inactive", "unknown")
        for phase in (None, "loading", "busy", "ready")
        if flm_state.visual_state(load, active, phase).animate
    ]
    assert animated == [("loaded", "active", "busy")]


def test_the_visual_state_is_readable_by_index_and_by_name():
    state = flm_state.visual_state("loaded", "active", "busy")
    colour, note, animate = state
    assert (state[0], state[1], state[2]) == (colour, note, animate)
    assert (state.colour, state.note, state.animate) == (colour, note, animate)
    assert len(tuple(state)) == 3


# --------------------------------------------------------------------------
# should_idle_stop
# --------------------------------------------------------------------------


def test_an_idle_service_past_the_limit_is_stopped():
    assert flm_state.should_idle_stop("active", None, 601, 600) is True


@pytest.mark.parametrize("phase", [None, "ready", "unknown"])
def test_any_non_working_phase_permits_an_idle_stop(phase):
    assert flm_state.should_idle_stop("active", phase, 601, 600) is True


@pytest.mark.parametrize("idle_seconds", [0, 599, 600])
def test_the_limit_must_be_exceeded_not_merely_reached(idle_seconds):
    assert flm_state.should_idle_stop("active", None, idle_seconds, 600) is False


@pytest.mark.parametrize("phase", ["busy", "loading"])
@pytest.mark.parametrize("idle_seconds", [601, 86_400, 10**9])
def test_work_in_progress_suppresses_the_idle_stop_at_any_age(phase, idle_seconds):
    # A long generation logs nothing for minutes; a pure clock would kill the
    # request mid-flight.
    assert flm_state.should_idle_stop("active", phase, idle_seconds, 600) is False


@pytest.mark.parametrize(
    "active", ["inactive", "failed", "activating", "deactivating", "unknown", ""]
)
def test_a_service_that_is_not_active_is_never_idle_stopped(active):
    assert flm_state.should_idle_stop(active, None, 10**9, 600) is False


def test_a_zero_limit_stops_as_soon_as_any_idle_time_has_passed():
    assert flm_state.should_idle_stop("active", None, 1, 0) is True
    assert flm_state.should_idle_stop("active", None, 0, 0) is False
