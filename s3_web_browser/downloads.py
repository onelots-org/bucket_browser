import sqlite3
from contextlib import closing
from pathlib import Path


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS downloads (key TEXT PRIMARY KEY, count INTEGER NOT NULL)")
    return db


def count_download(path: str, key: str) -> None:
    with closing(connect(path)) as db, db:
        db.execute(
            "INSERT INTO downloads VALUES (?, 1) ON CONFLICT(key) DO UPDATE SET count = count + 1",
            (key,),
        )


def download_counts(path: str) -> dict[str, int]:
    with closing(connect(path)) as db:
        return dict(db.execute("SELECT key, count FROM downloads"))
