"""Writes dataset/output.csv with the exact required columns/order."""

import csv

from . import config

COLUMNS = ("message_id", "action", "message_type", "reason", "confidence", "evidence_message_ids")


def write_output(rows: list, path=None) -> None:
    path = path or config.OUTPUT_CSV
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for r in rows:
            writer.writerow(
                [r.message_id, r.action, r.message_type, r.reason, f"{r.confidence:.2f}", r.evidence_message_ids]
            )
