#!/bin/bash
# Rank open enhancement issues by community thumbs-up votes.
#
# A vote is a 👍 on the issue itself or on the "gauge community interest"
# comment. Each person counts once, and the issue author and the maintainer
# are left out. Read-only: it only queries the GitHub API through gh.
#
# Usage: ./feature_votes.sh [limit]      (default: all issues)
#        ./feature_votes.sh --md [limit] (Markdown table)

set -euo pipefail

FORMAT=text
if [[ "${1:-}" == "--md" ]]; then
    FORMAT=md
    shift
fi
LIMIT="${1:-0}"

command -v gh >/dev/null || { echo "gh (GitHub CLI) is required" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "gh is not logged in; run: gh auth login" >&2; exit 1; }

FORMAT="$FORMAT" LIMIT="$LIMIT" python3 - <<'PY'
import json
import os
import subprocess

REPO = "maziggy/bambuddy"
MAINTAINER = "maziggy"
SEARCH = f"repo:{REPO} is:issue state:open label:enhancement -label:contrib"
POLL_MARKER = "I'd like to gauge community interest"
QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 30, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
        number title url author { login }
        reactions(content: THUMBS_UP, first: 100) { nodes { user { login } } }
        comments(first: 100) {
          totalCount
          nodes { body reactions(content: THUMBS_UP, first: 100) { nodes { user { login } } } }
        }
      }
    }
  }
}
"""


def voters(reactions):
    return {r["user"]["login"] for r in reactions["nodes"] if r["user"]}


rows, after, total, truncated = [], None, 0, 0
while True:
    args = ["gh", "api", "graphql", "-f", f"query={QUERY}", "-f", f"q={SEARCH}"]
    if after:
        args += ["-f", f"after={after}"]
    page = json.loads(subprocess.check_output(args))["data"]["search"]
    total = page["issueCount"]
    for issue in page["nodes"]:
        body = voters(issue["reactions"])
        poll, has_poll = set(), False
        for comment in issue["comments"]["nodes"]:
            if POLL_MARKER in (comment["body"] or ""):
                has_poll = True
                poll |= voters(comment["reactions"])
        if issue["comments"]["totalCount"] > 100:
            truncated += 1
        author = (issue["author"] or {}).get("login")
        votes = len((body | poll) - {author, MAINTAINER})
        rows.append((votes, len(body), len(poll) if has_poll else None, issue["number"], issue["title"], issue["url"]))
    if not page["pageInfo"]["hasNextPage"]:
        break
    after = page["pageInfo"]["endCursor"]

rows.sort(key=lambda r: (-r[0], -r[1], r[3]))
limit = int(os.environ["LIMIT"])
shown = rows[:limit] if limit > 0 else rows

print(f"{total} open enhancement issues, {sum(r[2] is not None for r in rows)} with a poll comment, "
      f"{sum(r[0] == 0 for r in rows)} without outside votes")
if truncated:
    print(f"Note: {truncated} issues have more than 100 comments; only the first 100 were checked")
print()
if os.environ["FORMAT"] == "md":
    print("| votes | on issue | on poll | issue |\n|---:|---:|---:|---|")
    for votes, body, poll, number, title, url in shown:
        print(f"| {votes} | {body} | {'-' if poll is None else poll} | [#{number}]({url}) {title.replace('|', '/')} |")
else:
    print(f"{'votes':>5} {'on issue':>8} {'on poll':>7}  issue")
    for votes, body, poll, number, title, _ in shown:
        print(f"{votes:>5} {body:>8} {'-' if poll is None else poll:>7}  #{number} {title}")
PY
