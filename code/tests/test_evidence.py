"""Tests for deterministic evidence retrieval and ranking. `evidence_message_ids`
is a graded output column and validate.py enforces that cited IDs share
lineage with the message, so ranking/lineage bugs are a direct scoring risk."""

from router import config, evidence
from router.textsim import jaccard, tokenize
from tests.conftest import FakeCtx, make_bundle, make_event, make_history_row


def _ctx_with(rows, events=None):
    return FakeCtx(history_rows=rows, events=events or {})


# --- Lineage ---------------------------------------------------------------

def test_only_same_user_history_is_considered():
    """Another user's history must never become this user's evidence."""
    rows = [
        make_history_row("h_mine", "u_test", "water tanker delayed", sender_user_id="u_sender"),
        make_history_row("h_theirs", "u_other", "water tanker delayed", sender_user_id="u_sender"),
    ]
    bundle = make_bundle(user_id="u_test", sender_user_id="u_sender", message_text="water tanker delayed")
    items = evidence.rank_evidence(bundle, _ctx_with(rows))
    ids = [i.message_id for i in items]
    assert "h_mine" in ids
    assert "h_theirs" not in ids


def test_sender_lineage_outranks_group_lineage():
    """Same-sender history is stronger evidence than merely same-group."""
    rows = [
        make_history_row("h_sender", "u_test", "unrelated text here", sender_user_id="u_sender", group_id="grp_1"),
        make_history_row("h_group", "u_test", "unrelated text here", sender_user_id="u_other", group_id="grp_1"),
    ]
    bundle = make_bundle(
        user_id="u_test", sender_user_id="u_sender", group_id="grp_1",
        conversation_type="group", message_text="unrelated text here",
    )
    items = evidence.rank_evidence(bundle, _ctx_with(rows))
    assert items[0].message_id == "h_sender"
    assert items[0].lineage == "sender"


def test_no_lineage_yields_no_evidence():
    rows = [make_history_row("h1", "u_test", "something", sender_user_id="u_unrelated")]
    bundle = make_bundle(user_id="u_test", sender_user_id="u_sender", message_text="hello")
    items = evidence.rank_evidence(bundle, _ctx_with(rows))
    assert items == []
    assert evidence.evidence_ids_field(items) == "none"


# --- Ranking --------------------------------------------------------------

def test_higher_similarity_ranks_higher_within_same_lineage():
    rows = [
        make_history_row("h_exact", "u_test", "maintenance closes at 5 PM today", sender_user_id="u_sender"),
        make_history_row("h_loose", "u_test", "completely different subject matter", sender_user_id="u_sender"),
    ]
    bundle = make_bundle(user_id="u_test", sender_user_id="u_sender",
                          message_text="maintenance closes at 5 PM today")
    items = evidence.rank_evidence(bundle, _ctx_with(rows))
    assert items[0].message_id == "h_exact"
    assert items[0].similarity > items[1].similarity


def test_ranking_is_deterministic_and_stable():
    """Ties break on message_id, so repeated calls cannot reorder -- output
    determinism depends on this."""
    rows = [
        make_history_row(f"h{i}", "u_test", "identical text for all", sender_user_id="u_sender")
        for i in range(5)
    ]
    bundle = make_bundle(user_id="u_test", sender_user_id="u_sender", message_text="identical text for all")
    first = [i.message_id for i in evidence.rank_evidence(bundle, _ctx_with(rows))]
    second = [i.message_id for i in evidence.rank_evidence(bundle, _ctx_with(rows))]
    assert first == second
    assert first == sorted(first)  # tie-break is by id


def test_evidence_is_capped_at_max():
    rows = [
        make_history_row(f"h{i:02d}", "u_test", "similar promotional text", sender_user_id="u_sender")
        for i in range(12)
    ]
    bundle = make_bundle(user_id="u_test", sender_user_id="u_sender", message_text="similar promotional text")
    items = evidence.rank_evidence(bundle, _ctx_with(rows))
    assert len(items) <= config.MAX_EVIDENCE
    assert len(evidence.evidence_ids_field(items).split(";")) <= config.MAX_EVIDENCE


# --- Event outcomes -------------------------------------------------------

def test_event_summary_reflects_actual_user_reaction():
    rows = [
        make_history_row("h_reported", "u_test", "same text", sender_user_id="u_sender"),
        make_history_row("h_replied", "u_test", "same text", sender_user_id="u_sender"),
    ]
    events = {
        ("u_test", "h_reported"): make_event("u_test", "h_reported", reported="1"),
        ("u_test", "h_replied"): make_event("u_test", "h_replied", replied="1"),
    }
    bundle = make_bundle(user_id="u_test", sender_user_id="u_sender", message_text="same text")
    by_id = {i.message_id: i for i in evidence.rank_evidence(bundle, _ctx_with(rows, events))}
    assert "reported" in by_id["h_reported"].event_summary
    assert "replied" in by_id["h_replied"].event_summary


def test_history_without_an_event_is_still_usable_evidence():
    rows = [make_history_row("h1", "u_test", "same text", sender_user_id="u_sender")]
    bundle = make_bundle(user_id="u_test", sender_user_id="u_sender", message_text="same text")
    items = evidence.rank_evidence(bundle, _ctx_with(rows))
    assert len(items) == 1
    assert "no recorded reaction" in items[0].event_summary


# --- candidate_id_set (what validate.py enforces citations against) -------

def test_candidate_set_is_superset_of_ranked_top_n():
    rows = [
        make_history_row(f"h{i:02d}", "u_test", "similar text sample", sender_user_id="u_sender")
        for i in range(9)
    ]
    ctx = _ctx_with(rows)
    bundle = make_bundle(user_id="u_test", sender_user_id="u_sender", message_text="similar text sample")
    ranked = {i.message_id for i in evidence.rank_evidence(bundle, ctx)}
    pool = evidence.candidate_id_set(bundle, ctx)
    assert ranked <= pool
    assert len(pool) == 9  # full pool, not truncated to MAX_EVIDENCE


# --- textsim ---------------------------------------------------------------

def test_jaccard_identical_and_disjoint():
    assert jaccard("water tanker delayed today", "water tanker delayed today") == 1.0
    assert jaccard("water tanker", "quarterly earnings") == 0.0


def test_jaccard_ignores_stopwords_and_is_symmetric():
    a, b = "the water is in the tank", "water tank"
    assert jaccard(a, b) == jaccard(b, a)
    assert jaccard(a, b) > 0.5  # stopwords stripped, so overlap dominates


def test_tokenize_drops_short_tokens_and_stopwords():
    tokens = tokenize("The a is of Water Tanker 12 delayed")
    assert "the" not in tokens and "is" not in tokens and "of" not in tokens
    assert "water" in tokens and "tanker" in tokens
    assert "12" not in tokens  # len <= 2 dropped


def test_empty_text_similarity_is_zero_not_error():
    assert jaccard("", "anything here") == 0.0
    assert jaccard("anything here", "") == 0.0
