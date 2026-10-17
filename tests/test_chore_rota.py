import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import chore_rota as rota  # noqa: E402
from chore_rota import RotaError  # noqa: E402


def household(people=("Ana", "Ben"), chores=(("Bins", 2), ("Hoover", 3))):
    state = rota.empty_state()
    for p in people:
        rota.add_person(state, p)
    for name, weight in chores:
        rota.add_chore(state, name, weight)
    return state


class WeekAndDateTests(unittest.TestCase):
    def test_week_start_is_monday_for_midweek_date(self):
        self.assertEqual(rota.week_start(date(2026, 10, 14)), date(2026, 10, 12))

    def test_week_start_of_monday_is_itself(self):
        self.assertEqual(rota.week_start(date(2026, 10, 12)), date(2026, 10, 12))

    def test_week_start_of_sunday_is_previous_monday(self):
        self.assertEqual(rota.week_start(date(2026, 10, 18)), date(2026, 10, 12))

    def test_parse_date_rejects_bad_text(self):
        with self.assertRaises(RotaError):
            rota.parse_date("17/10/2026")

    def test_clean_name_collapses_spaces_and_rejects_empty(self):
        self.assertEqual(rota.clean_name("  Ana   Maria ", "person"), "Ana Maria")
        with self.assertRaises(RotaError):
            rota.clean_name("   ", "person")


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.state = household()

    def test_away_overlapping_the_week_makes_person_unavailable(self):
        rota.add_away(self.state, "Ana", date(2026, 10, 15), date(2026, 10, 16))
        self.assertFalse(rota.is_available(self.state, "Ana", date(2026, 10, 12)))

    def test_away_ending_the_day_before_the_week_does_not_block(self):
        rota.add_away(self.state, "Ana", date(2026, 10, 5), date(2026, 10, 11))
        self.assertTrue(rota.is_available(self.state, "Ana", date(2026, 10, 12)))

    def test_away_starting_on_sunday_still_blocks_the_week(self):
        rota.add_away(self.state, "Ana", date(2026, 10, 18), date(2026, 10, 20))
        self.assertFalse(rota.is_available(self.state, "Ana", date(2026, 10, 12)))

    def test_away_end_before_start_is_rejected(self):
        with self.assertRaises(RotaError):
            rota.add_away(self.state, "Ana", date(2026, 10, 9), date(2026, 10, 1))


class PlanningTests(unittest.TestCase):
    def test_plan_alternates_a_single_chore_between_two_people(self):
        state = household(chores=(("Bins", 2),))
        rota.generate_plan(state, date(2026, 10, 12), 4)
        assignees = [e["person"] for e in state["schedule"]]
        self.assertEqual(assignees, ["Ana", "Ben", "Ana", "Ben"])

    def test_plan_gives_heavy_chore_to_the_person_with_less_history(self):
        state = household(chores=(("Hoover", 3),))
        rota.record_done(state, date(2026, 10, 5), "Hoover", "Ana")
        rota.generate_plan(state, date(2026, 10, 12), 1)
        self.assertEqual(state["schedule"][0]["person"], "Ben")

    def test_plan_skips_people_who_are_away(self):
        state = household(chores=(("Bins", 2),))
        rota.add_away(state, "Ana", date(2026, 10, 12), date(2026, 10, 25))
        rota.generate_plan(state, date(2026, 10, 12), 2)
        self.assertEqual([e["person"] for e in state["schedule"]], ["Ben", "Ben"])

    def test_plan_marks_unassigned_when_everyone_is_away(self):
        state = household(chores=(("Bins", 2),))
        for p in ("Ana", "Ben"):
            rota.add_away(state, p, date(2026, 10, 12), date(2026, 10, 18))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rota.generate_plan(state, date(2026, 10, 12), 1)
        self.assertEqual(state["schedule"][0]["person"], rota.UNASSIGNED)
        self.assertIn("nobody is available", err.getvalue())

    def test_plan_refuses_to_overwrite_without_replace(self):
        state = household()
        rota.generate_plan(state, date(2026, 10, 12), 2)
        with self.assertRaises(RotaError):
            rota.generate_plan(state, date(2026, 10, 12), 2)
        rota.generate_plan(state, date(2026, 10, 12), 2, replace=True)
        self.assertEqual(len(state["schedule"]), 4)

    def test_plan_needs_people_and_chores(self):
        with self.assertRaises(RotaError):
            rota.generate_plan(rota.empty_state(), date(2026, 10, 12), 1)
        state = household(chores=())
        with self.assertRaises(RotaError):
            rota.generate_plan(state, date(2026, 10, 12), 1)

    def test_plan_rejects_out_of_range_week_counts(self):
        with self.assertRaises(RotaError):
            rota.generate_plan(household(), date(2026, 10, 12), 0)
        with self.assertRaises(RotaError):
            rota.generate_plan(household(), date(2026, 10, 12), rota.MAX_WEEKS + 1)


class RecordingTests(unittest.TestCase):
    def test_record_marks_planned_entry_done_and_adds_history(self):
        state = household(chores=(("Bins", 2),))
        rota.generate_plan(state, date(2026, 10, 12), 1)
        rota.record_done(state, date(2026, 10, 14), "bins", "Ben")
        entry = state["schedule"][0]
        self.assertTrue(entry["done"])
        self.assertEqual(entry["person"], "Ben")
        self.assertEqual(state["history"][0]["weight"], 2)

    def test_replan_with_replace_keeps_completed_entries(self):
        state = household(chores=(("Bins", 2),))
        rota.generate_plan(state, date(2026, 10, 12), 2)
        rota.record_done(state, date(2026, 10, 13), "Bins", "Ana")
        rota.generate_plan(state, date(2026, 10, 12), 2, replace=True)
        week_one = [e for e in state["schedule"] if e["week"] == "2026-10-12"]
        self.assertEqual(len(week_one), 1)
        self.assertTrue(week_one[0]["done"])
        self.assertEqual(week_one[0]["person"], "Ana")
        self.assertEqual(len(state["schedule"]), 2)
        self.assertEqual(rota.find_problems(state), [])

    def test_done_work_is_not_double_counted_in_loads(self):
        state = household(chores=(("Bins", 2),))
        rota.generate_plan(state, date(2026, 10, 12), 1)
        rota.record_done(state, date(2026, 10, 12), "Bins", "Ana")
        self.assertEqual(rota.person_totals(state)["Ana"], 2)

    def test_record_for_unplanned_week_only_adds_history(self):
        state = household()
        message = rota.record_done(state, date(2026, 10, 14), "Bins", "Ana")
        self.assertIn("Recorded Bins by Ana", message)
        self.assertEqual(state["schedule"], [])
        self.assertEqual(len(state["history"]), 1)

    def test_record_rejects_unknown_person_or_chore(self):
        state = household()
        with self.assertRaises(RotaError):
            rota.record_done(state, date(2026, 10, 14), "Bins", "Zed")
        with self.assertRaises(RotaError):
            rota.record_done(state, date(2026, 10, 14), "Mopping", "Ana")


class SwapTests(unittest.TestCase):
    def test_swap_reassigns_a_planned_chore(self):
        state = household(chores=(("Bins", 2),))
        rota.generate_plan(state, date(2026, 10, 12), 1)
        rota.swap_planned(state, date(2026, 10, 15), "Bins", "Ben")
        self.assertEqual(state["schedule"][0]["person"], "Ben")

    def test_swap_refuses_to_assign_someone_away_without_force(self):
        state = household(chores=(("Bins", 2),))
        rota.generate_plan(state, date(2026, 10, 12), 1)
        rota.add_away(state, "Ben", date(2026, 10, 12), date(2026, 10, 18))
        with self.assertRaises(RotaError):
            rota.swap_planned(state, date(2026, 10, 12), "Bins", "Ben")
        rota.swap_planned(state, date(2026, 10, 12), "Bins", "Ben", force=True)
        self.assertEqual(state["schedule"][0]["person"], "Ben")

    def test_swap_refuses_completed_entries(self):
        state = household(chores=(("Bins", 2),))
        rota.generate_plan(state, date(2026, 10, 12), 1)
        rota.record_done(state, date(2026, 10, 12), "Bins", "Ana")
        with self.assertRaises(RotaError):
            rota.swap_planned(state, date(2026, 10, 12), "Bins", "Ben")


class ManagementTests(unittest.TestCase):
    def test_duplicate_person_is_rejected_case_insensitively(self):
        state = household()
        with self.assertRaises(RotaError):
            rota.add_person(state, "ana")

    def test_chore_weight_must_be_in_range(self):
        state = household(chores=())
        with self.assertRaises(RotaError):
            rota.add_chore(state, "Bins", 0)
        with self.assertRaises(RotaError):
            rota.add_chore(state, "Bins", rota.MAX_WEIGHT + 1)

    def test_person_with_history_cannot_be_removed(self):
        state = household()
        rota.record_done(state, date(2026, 10, 14), "Bins", "Ana")
        with self.assertRaises(RotaError):
            rota.remove_person(state, "Ana")

    def test_removing_unused_person_also_clears_their_away_periods(self):
        state = household()
        rota.add_away(state, "Ben", date(2026, 10, 1), date(2026, 10, 2))
        rota.remove_person(state, "Ben")
        self.assertEqual(state["people"], ["Ana"])
        self.assertEqual(state["away"], [])


class FairnessAndDueTests(unittest.TestCase):
    def test_fairness_totals_and_flags_imbalance(self):
        state = household()
        rota.record_done(state, date(2026, 10, 5), "Hoover", "Ana")
        rota.record_done(state, date(2026, 10, 6), "Hoover", "Ana")
        rows, grand = rota.fairness_report(state)
        by_name = {r["person"]: r for r in rows}
        self.assertEqual(grand, 6)
        self.assertAlmostEqual(by_name["Ana"]["share"], 1.0)
        self.assertEqual(rota.fairness_flag(by_name["Ana"], 2, 0.25, grand), "over")
        self.assertEqual(rota.fairness_flag(by_name["Ben"], 2, 0.25, grand), "under")

    def test_fairness_is_ok_when_work_is_even(self):
        state = household()
        rota.record_done(state, date(2026, 10, 5), "Hoover", "Ana")
        rota.record_done(state, date(2026, 10, 6), "Hoover", "Ben")
        rows, grand = rota.fairness_report(state)
        for r in rows:
            self.assertEqual(rota.fairness_flag(r, 2, 0.25, grand), "ok")

    def test_due_separates_overdue_from_this_week(self):
        state = household(chores=(("Bins", 2),))
        rota.generate_plan(state, date(2026, 10, 5), 2)
        overdue, current = rota.due_items(state, date(2026, 10, 14))
        self.assertEqual([e["week"] for e in overdue], ["2026-10-05"])
        self.assertEqual([e["week"] for e in current], ["2026-10-12"])

    def test_doctor_reports_unknown_references(self):
        state = household()
        state["history"].append({"date": "2026-10-01", "chore": "Ghost", "person": "Nobody", "weight": 1})
        problems = rota.find_problems(state)
        self.assertTrue(any("Nobody" in p for p in problems))
        self.assertTrue(any("Ghost" in p for p in problems))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "rota.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_invalid_json_reports_line_number(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write('{"version": 1,\n "people": oops}')
        with self.assertRaises(RotaError) as ctx:
            rota.load_state(self.path)
        self.assertIn("line 2", str(ctx.exception))

    def test_save_then_load_round_trips(self):
        state = household()
        rota.generate_plan(state, date(2026, 10, 12), 2)
        rota.save_state(self.path, state)
        self.assertEqual(rota.load_state(self.path), state)
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_load_missing_file_gives_empty_state(self):
        self.assertEqual(rota.load_state(self.path), rota.empty_state())


class CliTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "rota.json")

    def tearDown(self):
        self.dir.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = rota.main(["--file", self.path, "--today", "2026-10-14", *args])
        return code, out.getvalue(), err.getvalue()

    def test_full_round_trip(self):
        self.assertEqual(self.run_cli("person", "add", "Ana")[0], 0)
        self.assertEqual(self.run_cli("person", "add", "Ben")[0], 0)
        self.assertEqual(self.run_cli("chore", "add", "Bins", "--weight", "2")[0], 0)
        code, out, _ = self.run_cli("plan", "--weeks", "2")
        self.assertEqual(code, 0)
        self.assertIn("Planned 2 week(s) from 2026-10-12", out)
        code, out, _ = self.run_cli("due")
        self.assertIn("Due this week", out)
        code, out, _ = self.run_cli("record", "2026-10-13", "Bins", "ana")
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli("fairness")
        self.assertIn("Ana", out)
        self.assertTrue(os.path.exists(self.path))

    def test_error_exits_two_and_leaves_file_untouched(self):
        self.run_cli("person", "add", "Ana")
        self.run_cli("chore", "add", "Bins")
        with open(self.path, encoding="utf-8") as fh:
            before = fh.read()
        code, _, err = self.run_cli("record", "2026-10-13", "Bins", "Nobody")
        self.assertEqual(code, 2)
        self.assertIn("unknown person", err)
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)

    def test_bad_date_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                self.run_cli("plan", "--start", "soon")
        self.assertEqual(ctx.exception.code, 2)

    def test_read_only_commands_do_not_create_the_file(self):
        self.run_cli("schedule")
        self.run_cli("person", "list")
        self.assertFalse(os.path.exists(self.path))

    def test_doctor_returns_one_when_problems_exist(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "people": [], "chores": [], "away": [],
                       "history": [{"date": "2026-10-01", "chore": "X", "person": "Y", "weight": 1}],
                       "schedule": []}, fh)
        code, out, _ = self.run_cli("doctor")
        self.assertEqual(code, 1)
        self.assertIn("problem", out)

    def test_export_writes_csv(self):
        self.run_cli("person", "add", "Ana")
        self.run_cli("chore", "add", "Bins")
        self.run_cli("plan", "--weeks", "1")
        code, out, _ = self.run_cli("export")
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual(lines[0], "week,chore,weight,person,status")
        self.assertEqual(lines[1], "2026-10-12,Bins,1,Ana,planned")


if __name__ == "__main__":
    unittest.main()
