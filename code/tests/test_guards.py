"""Table C (TESTING_PLAN.md sec 4): guard interaction tests."""

from router import rules
from tests.conftest import make_bundle


# --- C1-C2: G1 does not demote safety_critical rule verdicts ---------------

def test_c1_hr8_not_demoted_by_g1_on_personal():
    bundle = make_bundle(conversation_type="personal", behavior={"reported_count": 0})
    action, message_type, notes = rules.apply_guards(
        "mute", "scam", bundle, "rule:HR8", safety_critical=True
    )
    assert action == "mute"
    assert notes == []


def test_c2_hr2_not_demoted_by_g1_on_personal():
    bundle = make_bundle(conversation_type="personal", behavior={"reported_count": 0})
    action, message_type, notes = rules.apply_guards(
        "mute", "scam", bundle, "rule:HR2", safety_critical=True
    )
    assert action == "mute"
    assert notes == []


# --- C3: G1 DOES demote a non-safety-critical rule mute on personal --------

def test_c3_non_safety_critical_rule_is_demoted_by_g1():
    """The case G1 exists for: an ambiguous content-based rule shouldn't be
    able to mute a personal sender with zero behavioral evidence. Uses
    safety_critical=False explicitly -- a hypothetical future rule that
    forgets to mark itself safety_critical fails SAFE into this path, not
    into an accidental bypass."""
    bundle = make_bundle(conversation_type="personal", behavior={"reported_count": 0})
    action, message_type, notes = rules.apply_guards(
        "mute", "spam", bundle, "rule:HR_HYPOTHETICAL", safety_critical=False
    )
    assert action == "digest"
    assert len(notes) == 1 and "G1" in notes[0]


def test_c3b_g1_does_not_touch_when_reported_count_positive():
    bundle = make_bundle(conversation_type="personal", behavior={"reported_count": 2})
    action, message_type, notes = rules.apply_guards(
        "mute", "spam", bundle, "rule:HR_HYPOTHETICAL", safety_critical=False
    )
    assert action == "mute"  # behavioral evidence present -- G1 doesn't apply
    assert notes == []


# --- C4-C7: G2 DND demotion ---------------------------------------------

def test_c4_urgent_type_exempt_from_dnd_demotion():
    bundle = make_bundle(conversation_type="group", content={"in_dnd_window": True})
    action, _, notes = rules.apply_guards("notify", "urgent", bundle, "llm")
    assert action == "notify"
    assert notes == []


def test_c5_non_urgent_group_demoted_during_dnd():
    bundle = make_bundle(conversation_type="group", content={"in_dnd_window": True})
    action, _, notes = rules.apply_guards("notify", "event", bundle, "llm")
    assert action == "digest"
    assert len(notes) == 1 and "G2" in notes[0]


def test_c6_personal_exempt_from_dnd_demotion_regardless_of_type():
    bundle = make_bundle(conversation_type="personal", content={"in_dnd_window": True})
    action, _, notes = rules.apply_guards("notify", "personal", bundle, "llm")
    assert action == "notify"
    assert notes == []


def test_c7_no_op_when_not_in_dnd_window():
    bundle = make_bundle(conversation_type="group", content={"in_dnd_window": False})
    action, _, notes = rules.apply_guards("notify", "event", bundle, "llm")
    assert action == "notify"
    assert notes == []


# --- C8: OPEN POLICY QUESTION, not silently resolved -----------------------

def test_c8_direct_mention_during_dnd_currently_demoted_OPEN_QUESTION():
    """Documents CURRENT behavior, not an endorsed-correct behavior. A
    direct @mention (HR5, typed 'personal' when not itself time-critical)
    arriving during the recipient's DND window gets demoted to digest by G2,
    because neither of G2's two exemptions apply: message_type isn't
    'urgent' and conversation_type is 'group', not 'personal'.

    This mirrors the very first design conversation about this project: a
    muted family group should still be able to surface a direct mention.
    Whether DND should work the same way is an open product call -- see
    TESTING_PLAN.md Table C, row C8. If the answer becomes "yes, mentions
    pierce DND", this test's expected action flips to 'notify' and G2 needs
    a `mentions_recipient` exemption clause; until then, this test exists so
    that outcome doesn't change silently."""
    bundle = make_bundle(
        conversation_type="group",
        content={"in_dnd_window": True, "mentions_recipient": True},
    )
    action, message_type, notes = rules.apply_guards("notify", "personal", bundle, "rule:HR5")
    assert action == "digest"  # current behavior -- flagged as open, not settled
    assert len(notes) == 1 and "G2" in notes[0]
