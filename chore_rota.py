#!/usr/bin/env python3
"""Rota: fair household chore rotations kept in one JSON file.

Every chore has a weight. Planning hands each chore, week by week, to the
available person carrying the least weight so far, and avoids giving the same
person the same chore two weeks running when anyone else is free. Completed
work is recorded and a fairness report shows who has really been doing what.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from datetime import date, timedelta

DEFAULT_FILE = "rota.json"
UNASSIGNED = "UNASSIGNED"
MAX_WEIGHT = 100
MAX_WEEKS = 52
DEFAULT_TOLERANCE = 0.25


class RotaError(Exception):
    """A user-facing problem. The CLI prints it to stderr and exits with 2."""


# --------------------------------------------------------------------------
# Dates and names
# --------------------------------------------------------------------------

def parse_date(text):
    """Parse a YYYY-MM-DD string, raising RotaError on anything else."""
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        raise RotaError(f"invalid date {text!r}; use YYYY-MM-DD")


def date_arg(text):
    """argparse flavour of parse_date so bad dates exit with usage errors."""
    try:
        return parse_date(text)
    except RotaError as exc:
        raise argparse.ArgumentTypeError(str(exc))


def week_start(day):
    """The Monday that starts the week containing ``day``."""
    return day - timedelta(days=day.weekday())


def clean_name(text, kind):
    name = " ".join(str(text).split())
    if not name:
        raise RotaError(f"{kind} name cannot be empty")
    if len(name) > 40:
        raise RotaError(f"{kind} name is longer than 40 characters")
    return name


def find_person(state, wanted):
    lowered = wanted.strip().lower()
    for name in state["people"]:
        if name.lower() == lowered:
            return name
    raise RotaError(f"unknown person {wanted!r}")


def find_chore(state, wanted):
    lowered = wanted.strip().lower()
    for chore in state["chores"]:
        if chore["name"].lower() == lowered:
            return chore
    raise RotaError(f"unknown chore {wanted!r}")


def warn(message):
    print(f"rota: warning: {message}", file=sys.stderr)


def format_table(headers, rows):
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    def line(cells):
        return "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(cells)).rstrip()

    return "\n".join([line(headers), line(["-" * w for w in widths])] + [line(r) for r in rows])


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def empty_state():
    return {
        "version": 1,
        "people": [],
        "chores": [],
        "away": [],
        "history": [],
        "schedule": [],
    }


def load_state(path):
    if not os.path.exists(path):
        return empty_state()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise RotaError(f"{path} is not valid JSON (line {exc.lineno}: {exc.msg})")
    except OSError as exc:
        raise RotaError(f"cannot read {path}: {exc.strerror}")
    if not isinstance(data, dict) or data.get("version") != 1:
        raise RotaError(f"{path} is not a rota file (expected version 1)")
    state = empty_state()
    state.update(data)
    return state


def save_state(path, state):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError as exc:
        raise RotaError(f"cannot write {path}: {exc.strerror}")


# --------------------------------------------------------------------------
# Household: people, away periods, chores
# --------------------------------------------------------------------------

def add_person(state, name):
    name = clean_name(name, "person")
    if any(p.lower() == name.lower() for p in state["people"]):
        raise RotaError(f"{name!r} is already a person in this rota")
    state["people"].append(name)
    return f"Added person {name}."


def remove_person(state, wanted):
    person = find_person(state, wanted)
    referenced = any(h["person"] == person for h in state["history"]) or any(
        e["person"] == person for e in state["schedule"]
    )
    if referenced:
        raise RotaError(f"{person} has recorded or planned chores and cannot be removed")
    state["people"].remove(person)
    state["away"] = [a for a in state["away"] if a["person"] != person]
    return f"Removed person {person}."


def add_away(state, wanted, start, end):
    person = find_person(state, wanted)
    if end < start:
        raise RotaError("the away period ends before it starts")
    state["away"].append({"person": person, "start": start.isoformat(), "end": end.isoformat()})
    return f"{person} is away {start.isoformat()} to {end.isoformat()}; they will be skipped."


def add_chore(state, name, weight):
    name = clean_name(name, "chore")
    if not 1 <= weight <= MAX_WEIGHT:
        raise RotaError(f"weight must be between 1 and {MAX_WEIGHT}")
    if any(c["name"].lower() == name.lower() for c in state["chores"]):
        raise RotaError(f"chore {name!r} already exists")
    state["chores"].append({"name": name, "weight": weight})
    return f"Added chore {name} (weight {weight})."


def remove_chore(state, wanted):
    chore = find_chore(state, wanted)
    name = chore["name"]
    referenced = any(h["chore"] == name for h in state["history"]) or any(
        e["chore"] == name for e in state["schedule"]
    )
    if referenced:
        raise RotaError(f"{name} has recorded or planned work and cannot be removed")
    state["chores"].remove(chore)
    return f"Removed chore {name}."


# --------------------------------------------------------------------------
# Availability, loads and planning
# --------------------------------------------------------------------------

def is_available(state, person, week):
    """True unless an away period overlaps the seven days starting at ``week``."""
    week_end = week + timedelta(days=6)
    for away in state["away"]:
        if away["person"] != person:
            continue
        if parse_date(away["start"]) <= week_end and parse_date(away["end"]) >= week:
            return False
    return True


def person_totals(state):
    """Weight each person carries: completed work plus planned work not yet done."""
    totals = {p: 0 for p in state["people"]}
    for h in state["history"]:
        totals[h["person"]] = totals.get(h["person"], 0) + h["weight"]
    for e in state["schedule"]:
        if not e["done"] and e["person"] in totals:
            totals[e["person"]] += e["weight"]
    return totals


def assign_week(state, week, totals, previous, done_people):
    """Assign every chore for one week. Mutates ``totals`` and ``previous``.

    ``done_people`` maps (week, chore) to the person who already did it, so
    completed work is never planned again.
    """
    rows = []
    chores = sorted(state["chores"], key=lambda c: (-c["weight"], c["name"].lower()))
    for chore in chores:
        name = chore["name"]
        already = done_people.get((week.isoformat(), name))
        if already is not None:
            previous[name] = already
            continue
        candidates = [p for p in state["people"] if is_available(state, p, week)]
        if not candidates:
            rows.append(make_entry(week, chore, UNASSIGNED))
            warn(f"nobody is available for {name} in the week of {week.isoformat()}")
            continue
        person = min(
            candidates,
            key=lambda p: (previous.get(name) == p, totals[p], p.lower()),
        )
        totals[person] += chore["weight"]
        previous[name] = person
        rows.append(make_entry(week, chore, person))
    return rows


def make_entry(week, chore, person):
    return {
        "week": week.isoformat(),
        "chore": chore["name"],
        "person": person,
        "weight": chore["weight"],
        "done": False,
    }


def generate_plan(state, start, weeks, replace=False):
    if not 1 <= weeks <= MAX_WEEKS:
        raise RotaError(f"weeks must be between 1 and {MAX_WEEKS}")
    if not state["people"]:
        raise RotaError("add some people before planning")
    if not state["chores"]:
        raise RotaError("add some chores before planning")

    first = week_start(start)
    targets = [first + timedelta(weeks=i) for i in range(weeks)]
    target_keys = {w.isoformat() for w in targets}
    in_range = [e for e in state["schedule"] if e["week"] in target_keys]
    pending = [e for e in in_range if not e["done"]]
    if pending and not replace:
        raise RotaError(
            f"{len(pending)} entries already planned in that range; use --replace to overwrite"
        )
    done_people = {(e["week"], e["chore"]): e["person"] for e in in_range if e["done"]}
    # Completed work is kept no matter what; only pending slots are replanned.
    state["schedule"] = [
        e for e in state["schedule"] if e["week"] not in target_keys or e["done"]
    ]

    totals = person_totals(state)
    prior = (first - timedelta(weeks=1)).isoformat()
    previous = {e["chore"]: e["person"] for e in state["schedule"] if e["week"] == prior}

    new_rows = []
    for week in targets:
        new_rows.extend(assign_week(state, week, totals, previous, done_people))
    state["schedule"].extend(new_rows)
    state["schedule"].sort(key=lambda e: (e["week"], e["chore"].lower()))
    return new_rows


def record_done(state, day, chore_wanted, person_wanted):
    chore = find_chore(state, chore_wanted)
    person = find_person(state, person_wanted)
    week = week_start(day).isoformat()
    for entry in state["schedule"]:
        if entry["week"] == week and entry["chore"] == chore["name"] and not entry["done"]:
            entry["done"] = True
            entry["person"] = person
            break
    state["history"].append({
        "date": day.isoformat(),
        "chore": chore["name"],
        "person": person,
        "weight": chore["weight"],
    })
    return f"Recorded {chore['name']} by {person} on {day.isoformat()} (weight {chore['weight']})."


def swap_planned(state, week_day, chore_wanted, person_wanted, force=False):
    chore = find_chore(state, chore_wanted)
    person = find_person(state, person_wanted)
    week = week_start(week_day)
    matches = [
        e for e in state["schedule"]
        if e["week"] == week.isoformat() and e["chore"] == chore["name"]
    ]
    if not matches:
        raise RotaError(f"no planned {chore['name']} in the week of {week.isoformat()}")
    entry = matches[0]
    if entry["done"]:
        raise RotaError(f"{chore['name']} for that week is already recorded as done")
    if not is_available(state, person, week) and not force:
        raise RotaError(f"{person} is away that week; pass --force to assign anyway")
    previous = entry["person"]
    entry["person"] = person
    return f"{chore['name']} for the week of {week.isoformat()}: {previous} -> {person}."


def due_items(state, today):
    this_week = week_start(today).isoformat()
    overdue, current = [], []
    for entry in state["schedule"]:
        if entry["done"]:
            continue
        if entry["week"] < this_week:
            overdue.append(entry)
        elif entry["week"] == this_week:
            current.append(entry)
    return overdue, current


def fairness_report(state):
    """Return (rows, grand_total) where rows are per-person completed work."""
    totals = {p: [0, 0] for p in state["people"]}
    for h in state["history"]:
        bucket = totals.setdefault(h["person"], [0, 0])
        bucket[0] += 1
        bucket[1] += h["weight"]
    grand = sum(weight for _, weight in totals.values())
    rows = []
    for person, (count, weight) in totals.items():
        share = weight / grand if grand else 0.0
        rows.append({"person": person, "count": count, "weight": weight, "share": share})
    return rows, grand


def fairness_flag(row, n_people, tolerance, grand):
    if not grand or n_people == 0:
        return "-"
    fair = 1 / n_people
    if row["share"] > fair * (1 + tolerance):
        return "over"
    if row["share"] < fair * (1 - tolerance):
        return "under"
    return "ok"


def find_problems(state):
    problems = []
    people = set(state["people"])
    chores = {c["name"] for c in state["chores"]}
    for c in state["chores"]:
        if not isinstance(c.get("weight"), int) or not 1 <= c["weight"] <= MAX_WEIGHT:
            problems.append(f"chore {c['name']!r} has an invalid weight {c.get('weight')!r}")
    for a in state["away"]:
        if a["person"] not in people:
            problems.append(f"away period refers to unknown person {a['person']!r}")
        try:
            if parse_date(a["start"]) > parse_date(a["end"]):
                problems.append(f"away period for {a['person']} ends before it starts")
        except RotaError as exc:
            problems.append(f"away period for {a['person']}: {exc}")
    for h in state["history"]:
        if h["person"] not in people:
            problems.append(f"history on {h['date']} refers to unknown person {h['person']!r}")
        if h["chore"] not in chores:
            problems.append(f"history on {h['date']} refers to unknown chore {h['chore']!r}")
        try:
            parse_date(h["date"])
        except RotaError as exc:
            problems.append(f"history entry: {exc}")
    seen = set()
    for e in state["schedule"]:
        key = (e["week"], e["chore"])
        if key in seen:
            problems.append(f"chore {e['chore']!r} is planned twice for week {e['week']}")
        seen.add(key)
        if e["person"] not in people and e["person"] != UNASSIGNED:
            problems.append(f"plan for {e['week']} refers to unknown person {e['person']!r}")
        if e["chore"] not in chores:
            problems.append(f"plan for {e['week']} refers to unknown chore {e['chore']!r}")
    return problems


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_person_add(state, args, today):
    return add_person(state, args.name), 0


def cmd_person_remove(state, args, today):
    return remove_person(state, args.name), 0


def cmd_person_list(state, args, today):
    if not state["people"]:
        return "No people yet. Add one with: person add NAME", 0
    rows = []
    for person in state["people"]:
        periods = [a for a in state["away"] if a["person"] == person]
        away = ", ".join(f"{a['start']} to {a['end']}" for a in periods) or "-"
        rows.append((person, away))
    return format_table(["Person", "Away"], rows), 0


def cmd_away_add(state, args, today):
    return add_away(state, args.name, args.start, args.end), 0


def cmd_chore_add(state, args, today):
    return add_chore(state, args.name, args.weight), 0


def cmd_chore_remove(state, args, today):
    return remove_chore(state, args.name), 0


def cmd_chore_list(state, args, today):
    if not state["chores"]:
        return "No chores yet. Add one with: chore add NAME --weight N", 0
    rows = [(c["name"], c["weight"]) for c in state["chores"]]
    return format_table(["Chore", "Weight"], rows), 0


def cmd_plan(state, args, today):
    start = args.start or week_start(today)
    rows = generate_plan(state, start, args.weeks, replace=args.replace)
    table = format_table(
        ["Week", "Chore", "Weight", "Person"],
        [(r["week"], r["chore"], r["weight"], r["person"]) for r in rows],
    )
    return f"Planned {args.weeks} week(s) from {week_start(start).isoformat()}.\n{table}", 0


def cmd_schedule(state, args, today):
    entries = state["schedule"]
    if args.week:
        key = week_start(args.week).isoformat()
        entries = [e for e in entries if e["week"] == key]
    if not entries:
        return "Nothing planned. Create a plan with: plan --weeks N", 0
    rows = [
        (e["week"], e["chore"], e["weight"], e["person"], "done" if e["done"] else "planned")
        for e in entries
    ]
    return format_table(["Week", "Chore", "Weight", "Person", "Status"], rows), 0


def cmd_record(state, args, today):
    return record_done(state, args.date, args.chore, args.person), 0


def cmd_swap(state, args, today):
    return swap_planned(state, args.week, args.chore, args.person, force=args.force), 0


def cmd_due(state, args, today):
    overdue, current = due_items(state, today)
    if not overdue and not current:
        return "Nothing overdue or due this week.", 0
    lines = []
    if overdue:
        lines.append("Overdue:")
        lines += [f"  {e['week']}  {e['chore']}  ->  {e['person']}" for e in overdue]
    if current:
        lines.append(f"Due this week (starting {week_start(today).isoformat()}):")
        lines += [f"  {e['chore']}  ->  {e['person']}" for e in current]
    return "\n".join(lines), 0


def cmd_fairness(state, args, today):
    rows, grand = fairness_report(state)
    if not rows:
        return "No people yet.", 0
    if not grand:
        return "No completed chores recorded yet.", 0
    n = len(rows)
    table = format_table(
        ["Person", "Chores", "Weight", "Share", "Flag"],
        [
            (r["person"], r["count"], r["weight"], f"{r['share'] * 100:.1f}%",
             fairness_flag(r, n, args.tolerance, grand))
            for r in rows
        ],
    )
    weights = [r["weight"] for r in rows]
    spread = max(weights) - min(weights)
    fair = f"{100 / n:.1f}%"
    return f"Fair share is {fair} each. Spread between highest and lowest: {spread}.\n{table}", 0


def cmd_export(state, args, today):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["week", "chore", "weight", "person", "status"])
    for e in state["schedule"]:
        status = "done" if e["done"] else ("unassigned" if e["person"] == UNASSIGNED else "planned")
        writer.writerow([e["week"], e["chore"], e["weight"], e["person"], status])
    text = buf.getvalue()
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        except OSError as exc:
            raise RotaError(f"cannot write {args.out}: {exc.strerror}")
        return f"Exported {len(state['schedule'])} planned row(s) to {args.out}.", 0
    return text.rstrip("\n"), 0


def cmd_doctor(state, args, today):
    problems = find_problems(state)
    if not problems:
        return "All good: no problems found.", 0
    lines = [f"Found {len(problems)} problem(s):"] + [f"  - {p}" for p in problems]
    return "\n".join(lines), 1


def build_parser():
    parser = argparse.ArgumentParser(
        prog="rota",
        description="Fair household chore rotations kept in one JSON file.",
    )
    parser.add_argument("--file", default=DEFAULT_FILE, help=f"data file (default: {DEFAULT_FILE})")
    parser.add_argument("--today", type=date_arg, default=None,
                        help="pretend today is YYYY-MM-DD (useful for testing due dates)")
    sub = parser.add_subparsers(dest="command", required=True)

    def leaf(group, action, func, mutates, help_text):
        p = group.add_parser(action, help=help_text)
        p.set_defaults(func=func, mutates=mutates)
        return p

    person = sub.add_parser("person", help="manage people")
    psub = person.add_subparsers(dest="action", required=True)
    leaf(psub, "add", cmd_person_add, True, "add a person").add_argument("name")
    leaf(psub, "remove", cmd_person_remove, True, "remove a person with no history").add_argument("name")
    leaf(psub, "list", cmd_person_list, False, "list people and their away periods")

    away = sub.add_parser("away", help="mark periods when someone is unavailable")
    asub = away.add_subparsers(dest="action", required=True)
    a = leaf(asub, "add", cmd_away_add, True, "skip a person for a date range")
    a.add_argument("name")
    a.add_argument("start", type=date_arg)
    a.add_argument("end", type=date_arg)

    chore = sub.add_parser("chore", help="manage chores")
    csub = chore.add_subparsers(dest="action", required=True)
    c = leaf(csub, "add", cmd_chore_add, True, "add a chore")
    c.add_argument("name")
    c.add_argument("--weight", type=int, default=1, help="effort weight, 1 to 100 (default 1)")
    leaf(csub, "remove", cmd_chore_remove, True, "remove a chore with no history").add_argument("name")
    leaf(csub, "list", cmd_chore_list, False, "list chores and weights")

    p = leaf(sub, "plan", cmd_plan, True, "assign chores for upcoming weeks")
    p.add_argument("--start", type=date_arg, default=None, help="any date in the first week (default: this week)")
    p.add_argument("--weeks", type=int, default=4, help="how many weeks to plan (default 4)")
    p.add_argument("--replace", action="store_true", help="overwrite existing plans in that range")

    s = leaf(sub, "schedule", cmd_schedule, False, "show planned and completed chores")
    s.add_argument("--week", type=date_arg, default=None, help="only show the week containing this date")

    r = leaf(sub, "record", cmd_record, True, "record that a chore was done")
    r.add_argument("date", type=date_arg)
    r.add_argument("chore")
    r.add_argument("person")

    w = leaf(sub, "swap", cmd_swap, True, "reassign a planned chore to someone else")
    w.add_argument("week", type=date_arg)
    w.add_argument("chore")
    w.add_argument("person")
    w.add_argument("--force", action="store_true", help="allow assigning someone who is away")

    leaf(sub, "due", cmd_due, False, "show overdue and this week's undone chores")

    f = leaf(sub, "fairness", cmd_fairness, False, "compare completed work across people")
    f.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                   help="allowed deviation from a fair share, as a fraction (default 0.25)")

    e = leaf(sub, "export", cmd_export, False, "export the schedule as CSV")
    e.add_argument("--out", default=None, help="write to a file instead of stdout")

    leaf(sub, "doctor", cmd_doctor, False, "check the data file for inconsistencies")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    today = args.today or date.today()
    try:
        state = load_state(args.file)
        output, status = args.func(state, args, today)
        if args.mutates:
            save_state(args.file, state)
    except RotaError as exc:
        print(f"rota: error: {exc}", file=sys.stderr)
        return 2
    if output:
        print(output)
    return status


if __name__ == "__main__":
    sys.exit(main())
