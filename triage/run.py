"""End-to-end smoke test: GitHub API -> Pydantic models -> SQLite -> read back.

Run from the project root:
    PYTHONPATH=src uv run python triage/run.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pipeline import build_repo_profile
from store import connect, init_db, list_repos, read_repo_profile, write_repo_profile

OWNER = "NWelde"
REPO = "better-auth"
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "shadow.db")


def main() -> None:
    print(f"[1/4] Fetching CI profile for {OWNER}/{REPO} ...")
    profile = build_repo_profile(OWNER, REPO)
    print(f"      repo id    : {profile.id}")
    print(f"      workflows  : {len(profile.workflows)}")
    print(f"      runs       : {len(profile.runs)}")
    print(f"      total jobs : {sum(len(r.jobs) for r in profile.runs)}")

    conn = connect(DB_PATH)
    init_db(conn)

    print(f"\n[2/4] Writing to {DB_PATH} ...")
    write_repo_profile(conn, profile)
    print("      done.")

    print("\n[3/4] Reading back ...")
    result = read_repo_profile(conn, profile.id)
    print(f"      owner      : {result.owner}")
    print(f"      name       : {result.name}")
    print(f"      workflows  : {len(result.workflows)}")
    print(f"      runs       : {len(result.runs)}")
    print(f"      total jobs : {sum(len(r.jobs) for r in result.runs)}")

    print("\n[4/4] All repos in DB:")
    for row in list_repos(conn):
        print(f"      {row}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
