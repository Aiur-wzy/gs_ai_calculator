"""RF08 boundaries for the new facility only; never launches a model or edits a sealed run."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("facility_v2_controlled_tools", ROOT / "benchmarks/followup/facility-v2/controlled_tools.py")
FACILITY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FACILITY)


class FacilityV2Tests(unittest.TestCase):
    def setUp(self):
        # Avoid OS pytest 0700 temporary-directory ACL behavior on Windows.
        self.root = ROOT / ".tmp" / "facility-v2-tests" / uuid.uuid4().hex
        for name in ("app", "data", "public", "outputs", "private"):
            (self.root / name).mkdir(parents=True)
        (self.root / "app/GenshinCalc.exe").write_bytes(b"engineering dummy; never executed")
        (self.root / "public/case.json").write_text("{}", encoding="utf-8")
        for name in ("plan.json", "audit.json", "query.json"):
            (self.root / "outputs" / name).write_text("{}", encoding="utf-8")
        self.proxy = FACILITY.ControlledTools(self.root / "app/GenshinCalc.exe", self.root / "data", self.root / "public",
                                              self.root / "outputs", "http://127.0.0.1:12345", public_files=["case.json"])

    def assertDenied(self, argv):
        with self.assertRaises(FACILITY.ProxyError):
            self.proxy._argv(argv)

    def test_workflow_bind_is_inplace_but_public_and_private_inputs_are_denied(self):
        actual = self.proxy._argv(["workflow", "bind", "--plan", "outputs/plan.json", "--audit", "outputs/audit.json"])
        self.assertIn(str(self.root / "outputs/audit.json"), actual)
        for path in ("public/case.json", "outputs/../private/audit.json", str(self.root / "private/audit.json")):
            self.assertDenied(["workflow", "bind", "--plan", "outputs/plan.json", "--audit", path])

    def test_workflow_bind_backup_and_lock_are_checked_before_process(self):
        original = Path.lstat
        for suffix in ("audit.json.previous", ".audit.json.plan.lock"):
            bad = self.root / "outputs" / suffix
            bad.write_bytes(b"reserved")
            def linked(path, *args, _bad=bad, **kwargs):
                if path == _bad:
                    return SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=2, st_file_attributes=0)
                return original(path, *args, **kwargs)
            with patch.object(Path, "lstat", linked), patch.object(FACILITY, "_run_process") as process:
                result = self.proxy.app_cli(["workflow", "bind", "--plan", "outputs/plan.json", "--audit", "outputs/audit.json"])
                self.assertFalse(result["ok"])
                self.assertEqual(result["proxy_error"]["reason"], "proxy_path_denied")
                process.assert_not_called()

    def test_real_hardlink_to_private_file_is_rejected(self):
        private = self.root / "private/secret.json"
        private.write_text("unreleased reference", encoding="utf-8")
        try:
            os.link(private, self.root / "outputs/audit.json.previous")
        except OSError as exc:
            self.skipTest(f"Host cannot create a hard link for this fixture: {exc}")
        self.assertDenied(["workflow", "bind", "--plan", "outputs/plan.json", "--audit", "outputs/audit.json"])
        self.assertEqual(private.read_text(encoding="utf-8"), "unreleased reference")

    def test_cache_directory_checks_descendant_reparse_point_before_launch(self):
        cache = self.root / "outputs/cache/context"
        cache.mkdir(parents=True)
        bad = cache / "cached-entry.json"
        bad.write_text("{}", encoding="utf-8")
        original = Path.lstat
        def reparse(path, *args, **kwargs):
            if path == bad:
                return SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=1, st_file_attributes=0x400)
            return original(path, *args, **kwargs)
        with patch.object(Path, "lstat", reparse), patch.object(FACILITY, "_run_process") as process:
            out = self.proxy.app_cli(["db", "context", "--input", "outputs/query.json", "--cache-dir", "cache/context"])
            self.assertFalse(out["ok"])
            process.assert_not_called()

    def test_workflow_export_known_outputs_and_sidecars_cannot_alias_inputs(self):
        (self.root / "outputs/delivery").mkdir()
        (self.root / "outputs/delivery/issues.json.previous").write_text("{}", encoding="utf-8")
        self.assertDenied(["workflow", "export", "--plan", "outputs/delivery/issues.json.previous", "--audit", "outputs/audit.json", "--output-dir", "outputs/delivery"])

    def test_new_commands_require_complete_arguments_and_fixed_routes(self):
        for argv in (["db", "context"], ["equipment", "workbook", "--plan", "outputs/plan.json"],
                     ["workflow", "bind", "--audit", "outputs/audit.json"], ["workflow", "export", "--plan", "outputs/plan.json", "--audit", "outputs/audit.json"],
                     ["db", "update"], ["workflow", "bind", "--plan", "outputs/plan.json", "--audit", "outputs/audit.json", "--data-dir", "outputs"]):
            with self.subTest(argv=argv):
                self.assertDenied(argv)
        for command in (["equipment", "workbook"], ["equipment", "enable-mechanics"], ["db", "context"], ["workflow", "bind"]):
            self.assertEqual(self.proxy._argv([*command, "--help"])[-1], "--help")

    def test_targeted_database_selectors_are_allowed_without_arbitrary_query_access(self):
        target = self.proxy._query_path("/v1/characters/1", {"sections": "skills,descriptions", "levels": "10,13"})
        self.assertIn("levels=10%2C13", target)
        self.assertIn("refinement=5", self.proxy._query_path("/v1/weapons/1", {"refinement": 5}))
        self.assertIn("set_thresholds=2%2C4", self.proxy._query_path("/v1/artifact-sets/1", {"set_thresholds": "2,4"}))
        for path, params in (("/v1/weapons/1", {"refinement": 6}), ("/v1/characters/1", {"levels": "10,10"}),
                             ("/v1/characters/1", {"levels": "16"}), ("/v1/characters/1", {"sql": "SELECT 1"}),
                             ("/v1/annotations", {"status": "draft"}), ("https://example.com", {})):
            with self.subTest(path=path, params=params), self.assertRaises(FACILITY.ProxyError):
                self.proxy._query_path(path, params)

    def test_frozen_public_file_change_is_not_silently_accepted(self):
        self.assertTrue(self.proxy.read_text("public/case.json")["ok"])
        (self.root / "public/case.json").write_text('{"changed":true}', encoding="utf-8")
        out = self.proxy.read_text("public/case.json")
        self.assertEqual(out["proxy_error"]["reason"], "proxy_fixture_changed")

    def test_product_failure_is_preserved_and_costs_do_not_invent_tokens_or_dollars(self):
        failed = {"exit_code": 1, "stdout": "", "stderr": '{"code":1,"message":"pending input"}',
                  "capture_truncated": False, "proxy_failure": None}
        with patch.object(FACILITY, "_run_process", return_value=failed):
            out = self.proxy.app_cli(["equipment", "workbook", "--plan", "outputs/plan.json", "--output", "outputs/result.xlsx"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["envelope"]["message"], "pending input")
        audit = json.loads(self.proxy.audit_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(audit["accounting"]["category"], "formula_workbook")
        self.assertIsNone(audit["accounting"]["tokens"])
        self.assertIsNone(audit["accounting"]["usd"])
        self.assertIsNone(audit["accounting"]["budget_charge"])
        self.assertGreaterEqual(audit["duration_seconds"], audit["product_wall_seconds"])

    def test_cache_hit_and_remote_lookup_costs_are_not_inferred_from_file_reads(self):
        self.assertEqual(FACILITY.cost_category("read_text", {"path": "cache/answer.json"}), "read_cache")
        self.assertEqual(FACILITY.cost_category("read_text", {"path": "outputs/cache/answer.json"}), "read_cache")
        self.assertEqual(FACILITY.cost_category("app_cli", {"argv": ["--pretty", "db", "context"]}), "local_context_query")
        self.assertEqual(FACILITY.cost_category("db_get", {}), "local_database_query")


if __name__ == "__main__":
    unittest.main()
