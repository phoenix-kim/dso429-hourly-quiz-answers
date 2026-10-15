"""Fetch the latest questions, answer every round not yet answered, and write
answers/round-NNNN.json. Committing and pushing is done by the caller
(GitHub Actions workflow or push.sh).

Answer sources, in order:
  1. solver.py  - exact computation for known question templates
  2. Claude API - for anything the solver does not recognise (needs ANTHROPIC_API_KEY)
  3. guess "A"  - last resort so the round is never missing; retried on later runs
                  while the round is still inside its 3-hour edit window
"""

import csv
import io
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from solver import solve

QUESTIONS_URL = "https://raw.githubusercontent.com/FaisalXL/dso429-hourly-quiz/main/questions.csv"
ROOT = Path(__file__).resolve().parent
ANSWERS = ROOT / "answers"
META = ROOT / "meta"
EDIT_WINDOW = timedelta(hours=3)


def fetch_questions():
    # Cache-bust raw.githubusercontent.com's CDN so a new round shows up quickly.
    url = f"{QUESTIONS_URL}?t={int(datetime.now().timestamp())}"
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def ask_claude(question, options):
    """Return a letter from Claude, or None if unavailable."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    prompt = (
        "Answer this multiple-choice business arithmetic question. Compute carefully.\n\n"
        f"{question}\n\n"
        + "\n".join(f"{l}) {o}" for l, o in zip("ABCD", options))
        + "\n\nReply with only the single letter of the correct option."
    )
    try:
        client = anthropic.Anthropic()
        resp = client.beta.messages.create(
            model="claude-opus-5-5",
            max_tokens=16000,
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as e:
        print(f"  Claude API error: {e}", file=sys.stderr)
        return None
    text = "".join(b.text for b in resp.content if b.type == "text")
    m = re.search(r"\b([ABCD])\b", text.strip()[-20:]) or re.search(r"\b([ABCD])\b", text)
    return m.group(1) if m else None


def answer_round(qs):
    answers, methods = {}, {}
    for r in sorted(qs, key=lambda r: int(r["q_index"])):
        key = f"q{int(r['q_index'])}"
        options = [r[f"option_{c}"] for c in "abcd"]
        letter, method = solve(r["question_text"], options)
        if letter is None:
            letter = ask_claude(r["question_text"], options)
            method = "claude" if letter else "guess"
            letter = letter or "A"
            print(f"  {key}: unrecognised -> {method} {letter}: {r['question_text'][:90]}")
        answers[key] = letter
        methods[key] = method
    return answers, methods


def main():
    ANSWERS.mkdir(exist_ok=True)
    META.mkdir(exist_ok=True)
    rows = fetch_questions()
    rounds = defaultdict(list)
    for r in rows:
        rounds[int(r["round_id"])].append(r)

    now = datetime.now(timezone.utc)
    written = []
    for rid in sorted(rounds):
        qs = rounds[rid]
        path = ANSWERS / f"round-{rid:04d}.json"
        meta_path = META / f"round-{rid:04d}.json"
        released = datetime.fromisoformat(qs[0]["released_at"].replace("Z", "+00:00"))

        if path.exists():
            # Re-answer only if earlier answers were guesses and we are still in the edit window.
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            methods = meta.get("methods", {})
            needs_retry = len(methods) < len(qs) or any(m == "guess" for m in methods.values())
            if not (needs_retry and now - released < EDIT_WINDOW):
                continue

        if len(qs) != 25:
            print(f"round {rid}: only {len(qs)} questions so far; answering what is there")
        answers, methods = answer_round(qs)
        out = {"round_id": rid, "answers": answers}
        new = json.dumps(out, indent=2) + "\n"
        if path.exists() and path.read_text() == new:
            continue
        path.write_text(new)
        meta_path.write_text(json.dumps({"round_id": rid, "methods": methods,
                                         "answered_at": now.isoformat()}, indent=2) + "\n")
        written.append(rid)
        print(f"round {rid}: wrote {path.name}")

    print(f"done: {len(written)} round file(s) written" + (f" ({written})" if written else ""))


if __name__ == "__main__":
    main()
