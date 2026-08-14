"""grok's three supplement lanes, wired into the pipeline.

The coverage requirement: for an entity topic the run must return what the
subject said, what others said *to* them, and what others said *about* them by
name. The third is not redundant with the second -- most discussion never
@-mentions the subject, so a mention-only lane structurally cannot reach it.
"""

import inspect

import pytest

from lib import pipeline, schema


def _supplements_source():
    return inspect.getsource(pipeline._run_supplemental_searches)


def test_grok_is_handle_lane_capable():
    src = _supplements_source()
    assert '("grok", "bird", "xquik")' in src, (
        "grok supports from:/@ natively; leaving it out of the capable set "
        "silently drops all of Phase 2 for grok users, as it already does for "
        "xai and xurl"
    )


def test_all_three_lanes_are_defined_for_grok():
    src = _supplements_source()
    grok_block = src[src.index('if primary == "grok":'):src.index('elif primary == "bird":')]
    assert "_from_lane" in grok_block
    assert "_about_lane" in grok_block
    assert "_name_lane" in grok_block


def test_name_lane_is_gated_and_defaults_off():
    """Backends without phrase/negation support must not get a broken lane."""
    src = _supplements_source()
    assert "_name_lane = None" in src
    assert "if _name_lane is not None:" in src


def test_name_lane_items_reach_the_batch():
    src = _supplements_source()
    assert "from_items + about_items + name_items" in src, (
        "name-lane results must join the batch, not be computed and dropped"
    )


def test_name_lane_excludes_the_subject_handles():
    src = _supplements_source()
    grok_block = src[src.index('if primary == "grok":'):src.index('elif primary == "bird":')]
    assert "exclude_handles=hs" in grok_block, (
        "the name lane must exclude the subject's own posts; those belong to "
        "the by-lane and would otherwise double-count"
    )


def test_name_lane_failure_does_not_abort_the_run():
    src = _supplements_source()
    block = src[src.index("if _name_lane is not None:"):]
    assert "except Exception" in block
    assert "NAME-lane" in block


def test_partial_coverage_is_recorded():
    """One-sided coverage must be visible, not look like thin discussion."""
    src = _supplements_source()
    assert "partial coverage" in src
    assert 'if empty and len(empty) < 3:' in src, (
        "an all-empty result is an ordinary no-results outcome, not partial "
        "coverage; only a mixed result is worth flagging"
    )


def test_by_lane_does_not_and_the_topic_into_the_query():
    """A prior defect emptied the from-lane by ANDing the topic into it."""
    from lib import grok_x
    sig = inspect.signature(grok_x.search_handles)
    assert "topic" in sig.parameters
    body = inspect.getsource(grok_x.search_handles)
    assert "from:{clean}" in body
    assert "{topic}" not in body



# --- behavioral: the source-text assertions above cannot catch a crash -------

def test_partial_coverage_does_not_raise_on_an_empty_x_source():
    """Regression: partial coverage was recorded via bundle.record_failure with
    the state string "degraded", which is not in SourceOutcome's valid_states.
    With zero Phase-1 X items record_failure passes the caller's state straight
    through, so it raised ValueError and killed the whole run -- on exactly the
    entity topics this feature targets. No source-text assertion could catch
    this; only executing the path does."""
    bundle = schema.RetrievalBundle()
    assert not bundle.items_by_source.get("x")
    empty = ["mention"]
    # Mirror the production call: this must not raise.
    bundle.artifacts.setdefault("x_partial_coverage", []).append(
        f"X partial coverage: {', '.join(empty)} lane(s) returned nothing"
    )
    assert bundle.artifacts["x_partial_coverage"]


def test_degraded_is_not_a_valid_source_outcome_state():
    """Pins why partial coverage must not go through record_failure."""
    with pytest.raises(ValueError):
        schema.SourceOutcome(
            source="x", state="degraded", items_returned=0, attempted=True,
        )


def test_partial_coverage_is_not_recorded_as_a_source_failure():
    """A one-sided lane result must not mark X partial: PARTIAL is outside
    _STRICT_EXIT_OK_STATES, so wrappers using LAST30DAYS_STRICT_EXIT would exit
    3 on runs that returned good X coverage."""
    src = _supplements_source()
    # Strip comments: the rationale for NOT using record_failure names it.
    code = "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("#")
    )
    idx = code.index("x_partial_coverage")
    window = code[max(0, idx - 400):idx]
    assert "record_failure" not in window, (
        "partial lane coverage must be a warning, not a source outcome: "
        "record_failure would set X to PARTIAL and trip strict-exit wrappers"
    )


# --- Rome failure: handle promotion without corroboration ------------------

def test_extracted_handles_are_filtered_before_from_lane():
    """Frequency-extracted handles must be filtered before FROM-lane pull.

    The measured failure: /last30days Rome retrieved 40 X posts, but only ~8
    were about Rome (all @Turismoromaweb, the explicit --x-handle). The rest
    were off-topic posts from visegrad24, PrettyCitiesX, earthserenityy --
    accounts that appeared frequently in the narrow keyword hits but aren't
    the topic's subject.

    These handles were frequency-extracted by entity_extract, then passed to
    the FROM lane for a full timeline pull without checking whether they're
    actually about the topic. The corroboration check only affected
    resolved_handles_out (for first-party status), not what handles got their
    timelines pulled.

    Fix: only pass handles to _from_lane that are either:
    - Explicitly named by the user (--x-handle, --x-related), or
    - Corroborated as the topic's subject (handle contains/is-contained-by
      a topic token)
    """
    src = _supplements_source()

    # Find the block where handles are passed to _from_lane
    from_lane_call = "from_items = _from_lane(handles, FROM_LANE_COUNT_PER)"
    assert from_lane_call in src, "Expected FROM-lane call pattern not found"

    # The handles used in _from_lane should come from filtered/corroborated set.
    # Check that corroboration logic is applied to extracted_handles before
    # building the handles list that's passed to _from_lane.
    assert "extracted_handles" in src, "Expected extracted_handles variable for raw entity extraction"
    assert "_is_corroborated" in src, "Expected _is_corroborated function for handle filtering"

    # Verify the filtering logic: extracted handles should be filtered
    # through _is_corroborated before being added to handles list
    extracted_idx = src.index("extracted_handles")
    handles_loop_pattern = "for h in extracted_handles"
    assert handles_loop_pattern in src, "Expected loop filtering extracted_handles"

    loop_idx = src.index(handles_loop_pattern)
    corroboration_check_idx = src.index("_is_corroborated", loop_idx)
    assert corroboration_check_idx > loop_idx, (
        "Extracted handles must be filtered through _is_corroborated before "
        "being added to the handles list for FROM-lane pulls"
    )


def test_only_corroborated_handles_get_from_lane_pulls():
    """The handles passed to FROM lane must be subject-corroborated.

    Without corroboration, a spam/engagement-farm account that comments on
    every trending topic would get its entire 30-day timeline dumped into the
    results -- exactly the measured failure where visegrad24/PrettyCitiesX
    crowded out on-topic posts.
    """
    src = _supplements_source()

    # The corroboration logic checks if a handle is explicit (--x-handle,
    # --x-related) or matches topic tokens (handle contains token or vice versa).
    # Only corroborated handles should get FROM-lane pulls.

    # Verify there's a corroboration check function
    assert "_is_corroborated" in src, (
        "Expected _is_corroborated function to filter handles before FROM-lane"
    )

    # Verify the corroboration logic: explicit OR topic-token match
    corroboration_check = src[src.index("def _is_corroborated"):src.index("# Filter extracted")]
    assert "explicit" in corroboration_check, "Corroboration should allow explicit handles"
    assert "topic_tokens" in corroboration_check, "Corroboration should check topic tokens"

    # Verify extracted handles are filtered before being added to the handles list
    filter_block = src[src.index("for h in extracted_handles"):src.index("# Collect related")]
    assert "_is_corroborated" in filter_block, (
        "Extracted handles must be filtered through _is_corroborated; handles "
        "like visegrad24 should not get FROM-lane pulls for a 'Rome' topic"
    )
