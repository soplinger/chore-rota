# Rota

**Fair household chore rotations, planned and tracked in one JSON file.**

Rota hands out the weekly chores in your house so that the heavy jobs rotate and nobody quietly ends up doing everything. Each chore has a weight, each person has a running total, and planning always gives the next chore to the available person carrying the least weight. When someone is away, they are skipped automatically. A fairness report shows who has really been doing what.

It runs entirely on your machine: Python 3 standard library only, no accounts, no network calls, and no data leaves the file you keep it in.

## Features

- **Weighted chores**: give each chore an effort weight from 1 to 100 so a bathroom clean counts for more than a bin run.
- **Least-loaded planning**: `plan` assigns every chore for a span of weeks to whoever has the lightest load, counting both completed and still-planned work.
- **No repeats when avoidable**: the same person does not get the same chore two weeks running unless nobody else is free.
- **Away periods**: mark date ranges when someone is unavailable and planning skips them, including weeks they only partly overlap.
- **Unassigned warnings**: if everyone is away for a chore, that slot is marked `UNASSIGNED` and a warning is printed, never silently dropped.
- **Recording completed work**: `record` marks the planned slot done (even if a different person did it) and keeps a history entry with the weight at that time.
- **Swaps**: `swap` reassigns a planned chore, refusing completed slots and refusing people who are away unless you pass `--force`.
- **Due list**: `due` separates overdue chores from this week's undone ones.
- **Fairness report**: shows each person's share of completed weight, the spread between highest and lowest, and flags anyone outside a configurable tolerance.
- **CSV export**: `export` writes the schedule with status for spreadsheets, or to a file with `--out`.
- **Data health check**: `doctor` finds unknown people or chores, bad date ranges, invalid weights and duplicate plans, and exits non-zero when it finds any.
- **Safe storage**: the JSON file is written atomically, and invalid JSON reports the line number instead of crashing.
- **Clear errors**: every bad input exits with status 2 and a one-line message on stderr.

## Quick start

```bash
python3 chore_rota.py person add Ana
python3 chore_rota.py person add Ben
python3 chore_rota.py chore add Bins --weight 2
python3 chore_rota.py chore add Hoover --weight 3
python3 chore_rota.py plan --start 2026-10-12 --weeks 4
python3 chore_rota.py record 2026-10-13 Bins Ana
python3 chore_rota.py fairness
```

Everything is stored in `rota.json` in the current directory. Use `--file PATH` to keep it somewhere else.

## How to run

Requires Python 3 only. There is nothing to install.

```bash
python3 chore_rota.py --help
python3 chore_rota.py person list
python3 chore_rota.py away add Ben 2026-10-19 2026-10-25
python3 chore_rota.py plan --start 2026-10-12 --weeks 2 --replace
python3 chore_rota.py schedule --week 2026-10-12
python3 chore_rota.py swap 2026-10-19 Hoover Ana --force
python3 chore_rota.py due
python3 chore_rota.py export --out rota.csv
python3 chore_rota.py doctor
```

`plan --replace` only overwrites pending slots; anything already recorded as done is kept.

`--today YYYY-MM-DD` pretends the current date is something else, which is useful for checking `due` and the default start of `plan`.

## How to test

The tests use only `unittest`, so they run with either runner:

```bash
python3 -m unittest discover -s tests -v
```

or, if you have pytest installed:

```bash
python3 -m pytest tests -v
```

The suite covers week and date handling, availability, planning balance, repeat avoidance, unassigned slots, overwrite protection, recording, swaps, fairness flags, due classification, data validation, JSON storage, and end-to-end CLI runs in temporary directories.

## Files

- `chore_rota.py`: the whole application, logic and CLI.
- `tests/test_chore_rota.py`: the test suite.
- `rota.json`: created on first write in whichever directory you run from. Delete it to start over.
