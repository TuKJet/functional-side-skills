from __future__ import annotations

import contextlib
import importlib.util
import inspect
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


WORKFLOW_PATH = (
    Path(__file__).parents[1]
    / ".codex/skills/material-code/scripts/workflow.py"
)
SCRIPTS_DIR = WORKFLOW_PATH.parent
sys.path.insert(0, str(SCRIPTS_DIR))
SPEC = importlib.util.spec_from_file_location("material_code_workflow", WORKFLOW_PATH)
assert SPEC and SPEC.loader
WORKFLOW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORKFLOW)


class DatabaseConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "material_codes.db"
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE materials (retired INTEGER NOT NULL)")
        con.execute("INSERT INTO materials(retired) VALUES (0)")
        con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        con.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("source_excel", r"D:\master.xlsx"),
                ("synced_at", "2026-07-30T12:00:00"),
            ],
        )
        con.commit()
        con.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assert_confirmation_rejected(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                WORKFLOW.require_database_confirmation(
                    self.root, self.db, WORKFLOW.database_meta(self.db)
                )
        self.assertEqual(raised.exception.code, 2)

    def test_missing_confirmation_is_rejected(self) -> None:
        self.assert_confirmation_rejected()

    def test_confirmation_is_valid_once_then_consumed(self) -> None:
        path, _ = WORKFLOW.record_database_confirmation(
            self.root, self.db, "use_existing"
        )
        _, confirmation = WORKFLOW.require_database_confirmation(
            self.root, self.db, WORKFLOW.database_meta(self.db)
        )
        WORKFLOW.consume_database_confirmation(
            path, confirmation, self.root / "job.json"
        )
        self.assert_confirmation_rejected()

    def test_database_change_invalidates_confirmation(self) -> None:
        WORKFLOW.record_database_confirmation(self.root, self.db, "use_existing")
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO materials(retired) VALUES (0)")
        con.commit()
        con.close()
        self.assert_confirmation_rejected()

    def test_new_sync_attempt_invalidates_previous_choice(self) -> None:
        WORKFLOW.record_database_confirmation(self.root, self.db, "use_existing")
        WORKFLOW.invalidate_database_confirmation(
            self.root, "用户改为选择新总表同步"
        )
        self.assert_confirmation_rejected()

    def test_successful_sync_decision_uses_same_one_time_gate(self) -> None:
        master = self.root / "new-master.xlsx"
        master.touch()
        _, confirmation = WORKFLOW.record_database_confirmation(
            self.root, self.db, "synced_new_master", master
        )
        self.assertEqual(confirmation["decision"], "synced_new_master")
        self.assertEqual(confirmation["master_excel"], str(master.resolve()))
        WORKFLOW.require_database_confirmation(
            self.root, self.db, WORKFLOW.database_meta(self.db)
        )

    def test_prepare_consumes_confirmation_and_blocks_next_application(self) -> None:
        application = self.root / "申请.xlsx"
        application.touch()
        WORKFLOW.record_database_confirmation(self.root, self.db, "use_existing")

        def fake_run_child(root: Path, script: str, args: list[str]):
            if "--precompute" in args:
                cache = Path(args[args.index("--cache") + 1])
                WORKFLOW.atomic_json(cache, {"items": []})
            return SimpleNamespace(returncode=0, stdout="")

        args = SimpleNamespace(application=str(application))
        with mock.patch.object(WORKFLOW, "find_project_root", return_value=self.root):
            with mock.patch.object(WORKFLOW, "run_child", side_effect=fake_run_child):
                WORKFLOW.cmd_prepare(args)

        confirmation = WORKFLOW.read_json(
            WORKFLOW.database_confirmation_path(self.root)
        )
        self.assertTrue(confirmation["consumed_by"])
        self.assertTrue(WORKFLOW.active_pointer(self.root).is_file())

        with mock.patch.object(WORKFLOW, "find_project_root", return_value=self.root):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    WORKFLOW.cmd_prepare(args)
        self.assertEqual(raised.exception.code, 2)


class PortableHardConstraintTests(unittest.TestCase):
    def test_runtime_scripts_are_self_contained(self) -> None:
        required = {
            "workflow.py",
            "project_paths.py",
            "workflow_guard.py",
            "matlib.py",
            "process_application.py",
            "sync_db.py",
            "search_material.py",
            "export_excel.py",
        }
        self.assertTrue(required.issubset({path.name for path in SCRIPTS_DIR.iterdir()}))
        for name in required:
            source = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn(".workbuddy", source)
            self.assertNotRegex(source, r"[A-Za-z]:\\")
        matlib_source = (SCRIPTS_DIR / "matlib.py").read_text(encoding="utf-8")
        self.assertNotIn("def insert_material", matlib_source)

    def test_script_resolution_stays_inside_codex_skill(self) -> None:
        path = WORKFLOW.script_path(Path.cwd(), "process_application.py")
        self.assertEqual(path.parent.resolve(), SCRIPTS_DIR.resolve())

    def test_project_root_can_be_selected_without_absolute_code_default(self) -> None:
        import project_paths

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "wuliao-bianma"\nversion = "0.0.0"\n',
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"MATERIAL_CODE_ROOT": str(root)}):
                self.assertEqual(project_paths.find_project_root(), root.resolve())

    def test_cli_does_not_expose_candidate_count_or_retired_bypass(self) -> None:
        parser = WORKFLOW.build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["prepare", "申请.xlsx", "--top", "9"])
            with self.assertRaises(SystemExit):
                parser.parse_args(["search", "测试", "--include-retired"])

        import matlib

        self.assertEqual(list(inspect.signature(matlib.search_top).parameters), ["con", "query"])
        self.assertNotIn("count", inspect.signature(matlib.generate_codes).parameters)

    def test_top1_never_resolves_exact_match_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache = root / "candidates.json"
            WORKFLOW.atomic_json(
                cache,
                {
                    "items": [
                        {
                            "idx": 1,
                            "candidates": [{"id": 1}],
                            "exact_conflicts": [{"code": "0000000001"}],
                        },
                        {"idx": 2, "candidates": [{"id": 2}]},
                    ]
                },
            )
            manifest = {"cache": str(cache), "processed_items": []}
            args = SimpleNamespace(confirmed=True, job=None, from_item=1)
            with mock.patch.object(WORKFLOW, "find_project_root", return_value=root):
                with mock.patch.object(
                    WORKFLOW, "resolve_job", return_value=(root / "job.json", manifest)
                ):
                    with mock.patch.object(WORKFLOW, "execute_job_write") as execute:
                        WORKFLOW.cmd_top1(args)
            extra_args = execute.call_args.args[3]
            self.assertEqual(extra_args, ["--commit", "2:1"])

    def test_project_has_one_complete_authoritative_skill(self) -> None:
        project_root = Path(__file__).parents[1]
        canonical = project_root / ".codex/skills/material-code"
        relative_files = [
            "SKILL.md",
            "agents/openai.yaml",
            "references/business-rules.md",
            "scripts/workflow.py",
            "scripts/project_paths.py",
            "scripts/workflow_guard.py",
            "scripts/matlib.py",
            "scripts/process_application.py",
            "scripts/sync_db.py",
            "scripts/search_material.py",
            "scripts/export_excel.py",
        ]
        for relative in relative_files:
            self.assertTrue((canonical / relative).is_file(), relative)

        ignore_rules = (project_root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".workbuddy/", ignore_rules)


if __name__ == "__main__":
    unittest.main()
