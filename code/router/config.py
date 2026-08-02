"""Paths, model ids, env var names, and shared constants."""

import os
from pathlib import Path

from dotenv import load_dotenv

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CODE_DIR.parent

load_dotenv(REPO_ROOT / ".env")
load_dotenv(CODE_DIR / ".env")
DATASET_DIR = REPO_ROOT / "dataset"
MEDIA_DIR = DATASET_DIR / "media"
CACHE_DIR = CODE_DIR / "cache"
PROMPTS_DIR = CODE_DIR / "prompts"

MESSAGES_CSV = DATASET_DIR / "messages.csv"
SAMPLE_MESSAGES_CSV = DATASET_DIR / "sample_messages.csv"
OUTPUT_CSV = DATASET_DIR / "output.csv"
USERS_CSV = DATASET_DIR / "users.csv"
GROUPS_CSV = DATASET_DIR / "groups.csv"
GROUP_MEMBERS_CSV = DATASET_DIR / "group_members.csv"
BUSINESS_ACCOUNTS_CSV = DATASET_DIR / "business_accounts.csv"
USER_BUSINESS_HISTORY_CSV = DATASET_DIR / "user_business_history.csv"
MESSAGE_HISTORY_CSV = DATASET_DIR / "message_history.csv"
MESSAGE_EVENTS_CSV = DATASET_DIR / "message_events.csv"
IMAGES_CSV = DATASET_DIR / "images.csv"
VOICE_NOTES_CSV = DATASET_DIR / "voice_notes.csv"
DAILY_NOTIFICATION_SUMMARY_CSV = DATASET_DIR / "daily_notification_summary.csv"

TRANSCRIPTS_CACHE = CACHE_DIR / "transcripts.json"
LLM_DECISIONS_CACHE = CACHE_DIR / "llm_decisions.json"
JUDGE_FLAGS_CACHE = CACHE_DIR / "judge_flags.json"
PERCEPTIONS_CACHE = CACHE_DIR / "perceptions.json"

GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"

GEMINI_TRANSCRIBE_MODEL = "gemini-2.5-flash"
CLAUDE_DECIDE_MODEL = "claude-haiku-4-5"
CLAUDE_ESCALATE_MODEL = "claude-sonnet-5"
CLAUDE_JUDGE_MODEL = "claude-haiku-4-5"
TEMPERATURE = 0

ACTIONS = ("notify", "digest", "mute")
MESSAGE_TYPES = (
    "personal",
    "urgent",
    "event",
    "payment",
    "business_update",
    "promotion",
    "greeting",
    "forward",
    "spam",
    "scam",
    "unknown",
)
TYPE_PRECEDENCE = (
    "scam",
    "spam",
    "urgent",
    "payment",
    "event",
    "business_update",
    "promotion",
    "greeting",
    "forward",
    "personal",
    "unknown",
)

# Confidence is now a continuous function of the certainty engine's
# agreement_ratio (see certainty.pick_confidence), not fixed per-state
# bands -- those were originally set to match the solved samples' own
# confidence range, which is fitting to the label, not measuring anything.
# HARD_RULE_CONFIDENCE and UNINFORMED_CONFIDENCE live in certainty.py next
# to the formula they belong to.

MAX_EVIDENCE = 5

RISKY_GROUP_TYPES = {"investment_tips", "finance_help", "marketplace"}
TRUSTED_GROUP_TYPES = {"family", "school_group", "society", "coworker", "extended_family"}


def get_env(name: str) -> str | None:
    return os.environ.get(name)
