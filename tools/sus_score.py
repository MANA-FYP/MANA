"""Score the System Usability Scale answers of the pilot test (#90).

    python3 tools/sus_score.py answers.csv

The file has one row for each participant: a column participant, then q1 to q10, each answer from
1 (strongly disagree) to 5 (strongly agree), in the order of docs/pilot-test.md. A template is
docs/pilot/sus-responses-template.csv. Scoring is Brooke's (1996): an odd item counts its answer
minus 1, an even item 5 minus its answer, and the sum times 2.5 is the participant's score, 0 to
100. The mean and the spread are printed with the number of people: with fewer than ten, the mean
is a rough guide, not a result to rank with.
"""

import csv
import statistics
import sys

ITEMS = 10
AVERAGE = 68  # the mean of many studies (Sauro 2011), as a point of comparison, not a target


def score(answers: list[int]) -> float:
    """The SUS score, 0 to 100, of the ten answers of one person."""
    if len(answers) != ITEMS:
        raise ValueError(f"need {ITEMS} answers, got {len(answers)}")
    if not all(isinstance(a, int) and 1 <= a <= 5 for a in answers):
        raise ValueError(f"every answer is a whole number from 1 to 5, got {answers}")
    return 2.5 * sum(a - 1 if i % 2 == 0 else 5 - a for i, a in enumerate(answers))


def read(path: str) -> dict[str, list[int]]:
    """participant -> the ten answers. A blank or wrong answer names the person and the item."""
    out = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if not any((v or "").strip() for v in row.values()):
                continue  # an empty row, as a spreadsheet leaves at the end
            who = (row.get("participant") or "").strip()
            if not who:
                raise ValueError("a row has no participant")
            if who in out:
                raise ValueError(f"{who} appears twice")
            answers = []
            for i in range(1, ITEMS + 1):
                raw = (row.get(f"q{i}") or "").strip()
                try:
                    answers.append(int(raw))
                except ValueError:
                    raise ValueError(f"{who}: q{i} is {raw!r}, not a whole number") from None
            out[who] = answers
    if not out:
        raise ValueError("no answers in the file")
    return out


def summary(answers: dict[str, list[int]]) -> str:
    scores = {who: score(a) for who, a in answers.items()}
    values = list(scores.values())
    lines = ["| Participant | SUS score |", "|---|---|"]
    lines += [f"| {who} | {s:.1f} |" for who, s in scores.items()]
    spread = statistics.stdev(values) if len(values) > 1 else float("nan")
    lines += [
        "",
        f"{len(values)} participants: mean {statistics.mean(values):.1f}, "
        f"median {statistics.median(values):.1f}, sd {spread:.1f}, "
        f"lowest {min(values):.1f}, highest {max(values):.1f} "
        f"(the usual average is {AVERAGE}).",
    ]
    if len(values) < ITEMS:
        lines.append(f"With fewer than {ITEMS} people the mean is a rough guide only.")
    # the item means show which statements pulled the score down
    odd = [(i + 1, statistics.mean(a[i] for a in answers.values())) for i in range(ITEMS)]
    lines += [
        "",
        "Mean answer of each statement (1 to 5): " + ", ".join(f"q{i} {m:.1f}" for i, m in odd),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python3 tools/sus_score.py answers.csv")
        return 2
    try:
        print(summary(read(args[0])))
    except (OSError, ValueError) as e:
        print(f"cannot score {args[0]}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
