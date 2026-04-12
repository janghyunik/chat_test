import csv
from datetime import datetime
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent / "chat_log.csv"


def log_chat_history(question: str, answer: str, docs: list):
    with LOG_FILE.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().isoformat(),
            question,
            str(answer or "")[:500].replace("\n", " "),
            "|".join([str(d.get("에러명", "")) for d in docs]),
        ])
