import json
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings

from backup.models import BackupRecord, NodeRedConfig
from backup.services.backup_service import create_backup
from backup.services.diff_service import (
    diff_backup_archives,
    diff_tab_summaries,
    parse_flows_from_archive,
)
from backup.services.flow_parser import parse_flows
from backup.tests.helpers import SAMPLE_FLOWS, TempBackupDirMixin


class DiffTabSummariesTest(TestCase):
    def _make_parsed(self, nodes):
        """Helper: run parse_flows to get a full parsed structure for diff tests."""
        return parse_flows(nodes)

    def test_tabs_added(self):
        prev = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "A"},
            {"id": "n1", "type": "inject", "z": "t1"},
        ])
        curr = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "A"},
            {"id": "n1", "type": "inject", "z": "t1"},
            {"id": "t2", "type": "tab", "label": "B"},
            {"id": "n2", "type": "debug", "z": "t2"},
        ])
        diff = diff_tab_summaries(prev, curr)
        self.assertEqual(diff["tabs_added"], ["B"])
        self.assertEqual(diff["tabs_removed"], [])
        self.assertEqual(diff["tabs_modified"], [])

    def test_tabs_removed(self):
        prev = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "A"},
            {"id": "t2", "type": "tab", "label": "B"},
            {"id": "n1", "type": "inject", "z": "t1"},
        ])
        curr = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "A"},
            {"id": "n1", "type": "inject", "z": "t1"},
        ])
        diff = diff_tab_summaries(prev, curr)
        self.assertEqual(diff["tabs_removed"], ["B"])
        self.assertEqual(diff["tabs_added"], [])

    def test_tabs_modified_node_count_change(self):
        prev = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "A"},
            {"id": "n1", "type": "inject", "z": "t1"},
        ])
        curr = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "A"},
            {"id": "n1", "type": "inject", "z": "t1"},
            {"id": "n2", "type": "debug", "z": "t1"},
        ])
        diff = diff_tab_summaries(prev, curr)
        self.assertEqual(len(diff["tabs_modified"]), 1)
        self.assertEqual(diff["tabs_modified"][0]["nodes_before"], 1)
        self.assertEqual(diff["tabs_modified"][0]["nodes_after"], 2)
        self.assertEqual(len(diff["tabs_modified"][0]["nodes_added"]), 1)
        self.assertEqual(diff["tabs_modified"][0]["nodes_added"][0]["type"], "debug")

    def test_no_changes(self):
        parsed = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "A"},
            {"id": "n1", "type": "inject", "z": "t1"},
        ])
        diff = diff_tab_summaries(parsed, parsed)
        self.assertEqual(diff["tabs_added"], [])
        self.assertEqual(diff["tabs_removed"], [])
        self.assertEqual(diff["tabs_modified"], [])

    def test_node_added_in_tab(self):
        prev = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "Home"},
            {"id": "n1", "type": "inject", "z": "t1"},
        ])
        curr = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "Home"},
            {"id": "n1", "type": "inject", "z": "t1"},
            {"id": "n2", "type": "function", "z": "t1", "name": "Process"},
        ])
        diff = diff_tab_summaries(prev, curr)
        mod = diff["tabs_modified"][0]
        self.assertEqual(len(mod["nodes_added"]), 1)
        self.assertEqual(mod["nodes_added"][0]["type"], "function")
        self.assertEqual(mod["nodes_added"][0]["name"], "Process")

    def test_node_removed_from_tab(self):
        prev = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "Home"},
            {"id": "n1", "type": "inject", "z": "t1"},
            {"id": "n2", "type": "debug", "z": "t1", "name": "Logger"},
        ])
        curr = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "Home"},
            {"id": "n1", "type": "inject", "z": "t1"},
        ])
        diff = diff_tab_summaries(prev, curr)
        mod = diff["tabs_modified"][0]
        self.assertEqual(len(mod["nodes_removed"]), 1)
        self.assertEqual(mod["nodes_removed"][0]["type"], "debug")
        self.assertEqual(mod["nodes_removed"][0]["name"], "Logger")

    def test_node_modified_detects_field_change(self):
        prev = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "Home"},
            {"id": "n1", "type": "function", "z": "t1", "name": "Old Name", "func": "return msg;"},
        ])
        curr = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "Home"},
            {"id": "n1", "type": "function", "z": "t1", "name": "New Name", "func": "msg.payload = 1; return msg;"},
        ])
        diff = diff_tab_summaries(prev, curr)
        mod = diff["tabs_modified"][0]
        self.assertEqual(len(mod["nodes_modified"]), 1)
        self.assertEqual(mod["nodes_modified"][0]["name"], "New Name")
        self.assertIn("func", mod["nodes_modified"][0]["changed_fields"])
        self.assertIn("name", mod["nodes_modified"][0]["changed_fields"])

    def test_node_position_change_ignored(self):
        prev = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "Home"},
            {"id": "n1", "type": "inject", "z": "t1", "x": 100, "y": 200},
        ])
        curr = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "Home"},
            {"id": "n1", "type": "inject", "z": "t1", "x": 300, "y": 400},
        ])
        diff = diff_tab_summaries(prev, curr)
        self.assertEqual(diff["tabs_modified"], [])

    def test_node_with_group_shows_group_name(self):
        prev = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "Home"},
        ])
        curr = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "Home"},
            {"id": "g1", "type": "group", "name": "Sensors", "z": "t1"},
            {"id": "n1", "type": "inject", "z": "t1", "g": "g1", "name": "Trigger"},
        ])
        diff = diff_tab_summaries(prev, curr)
        mod = diff["tabs_modified"][0]
        # Find the inject node (not the group itself)
        inject_added = [n for n in mod["nodes_added"] if n["type"] == "inject"]
        self.assertEqual(len(inject_added), 1)
        self.assertEqual(inject_added[0]["group"], "Sensors")

    def test_backward_compat_no_nodes_by_id(self):
        """Old parsed data without nodes_by_id falls back to count-only comparison."""
        prev = {"tabs": [{"id": "t1", "label": "A", "node_count": 3}]}
        curr = {"tabs": [{"id": "t1", "label": "A", "node_count": 7}]}
        diff = diff_tab_summaries(prev, curr)
        self.assertEqual(len(diff["tabs_modified"]), 1)
        self.assertEqual(diff["tabs_modified"][0]["nodes_before"], 3)
        self.assertEqual(diff["tabs_modified"][0]["nodes_after"], 7)

    def test_subflow_node_modified(self):
        prev = self._make_parsed([
            {"id": "sf1", "type": "subflow", "name": "My Subflow"},
            {"id": "n1", "type": "function", "z": "sf1", "func": "return msg;"},
        ])
        curr = self._make_parsed([
            {"id": "sf1", "type": "subflow", "name": "My Subflow"},
            {"id": "n1", "type": "function", "z": "sf1", "func": "msg.payload = 1;\nreturn msg;"},
        ])
        diff = diff_tab_summaries(prev, curr)
        self.assertEqual(diff["tabs_modified"], [])
        self.assertEqual(len(diff["subflows_modified"]), 1)
        self.assertEqual(diff["subflows_modified"][0]["label"], "My Subflow")
        mod_node = diff["subflows_modified"][0]["nodes_modified"][0]
        self.assertIn("func", mod_node["changed_fields"])

    def test_subflow_added_removed(self):
        prev = self._make_parsed([
            {"id": "sf1", "type": "subflow", "name": "Old"},
            {"id": "n1", "type": "inject", "z": "sf1"},
        ])
        curr = self._make_parsed([
            {"id": "sf2", "type": "subflow", "name": "New"},
            {"id": "n2", "type": "debug", "z": "sf2"},
        ])
        diff = diff_tab_summaries(prev, curr)
        self.assertIn("Old", diff["subflows_removed"])
        self.assertIn("New", diff["subflows_added"])

    def test_field_diffs_unified_format(self):
        prev = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "Home"},
            {"id": "n1", "type": "function", "z": "t1", "func": "line1\nline2\nline3"},
        ])
        curr = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "Home"},
            {"id": "n1", "type": "function", "z": "t1", "func": "line1\nchanged\nline3"},
        ])
        diff = diff_tab_summaries(prev, curr)
        mod = diff["tabs_modified"][0]["nodes_modified"][0]
        self.assertIn("field_diffs", mod)
        func_diff = [fd for fd in mod["field_diffs"] if fd["field"] == "func"][0]
        self.assertIn("-line2", func_diff["diff"])
        self.assertIn("+changed", func_diff["diff"])

    def test_field_diffs_simple_value(self):
        prev = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "Home"},
            {"id": "n1", "type": "inject", "z": "t1", "repeat": "5"},
        ])
        curr = self._make_parsed([
            {"id": "t1", "type": "tab", "label": "Home"},
            {"id": "n1", "type": "inject", "z": "t1", "repeat": "10"},
        ])
        diff = diff_tab_summaries(prev, curr)
        mod = diff["tabs_modified"][0]["nodes_modified"][0]
        fd = [d for d in mod["field_diffs"] if d["field"] == "repeat"][0]
        self.assertIn("- 5", fd["diff"])
        self.assertIn("+ 10", fd["diff"])


class DiffServiceArchiveTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )

    def test_parse_flows_from_archive(self):
        record = create_backup(config=self.config, trigger="manual")
        parsed = parse_flows_from_archive(record.file_path)
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed["tabs"]), 2)

    def test_diff_backup_archives_detects_added_tab(self):
        record_a = create_backup(config=self.config, trigger="manual")
        new_flows = SAMPLE_FLOWS + [{"id": "tab3", "type": "tab", "label": "New Tab"}]
        self.flows_file.write_text(json.dumps(new_flows))
        record_b = create_backup(config=self.config, trigger="manual")
        diff = diff_backup_archives(record_a.file_path, record_b.file_path)
        self.assertIn("New Tab", diff["tabs_added"])
        self.assertIn("prev", diff)
        self.assertIn("current", diff)

    def test_diff_backup_archives_no_changes(self):
        record_a = create_backup(config=self.config, trigger="manual")
        record_b = create_backup(config=self.config, trigger="manual")
        diff = diff_backup_archives(record_a.file_path, record_b.file_path)
        self.assertEqual(diff["tabs_added"], [])
        self.assertEqual(diff["tabs_removed"], [])
        self.assertEqual(diff["tabs_modified"], [])


@override_settings(REQUIRE_AUTH=False)
class DiffViewTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )
        with patch("backup.services.retention_service.apply_retention"):
            self.backup_a = create_backup(config=self.config, trigger="manual")
            # Modify flows for second backup
            new_flows = SAMPLE_FLOWS + [
                {"id": "tab3", "type": "tab", "label": "New Tab"},
            ]
            self.flows_file.write_text(json.dumps(new_flows))
            self.backup_b = create_backup(config=self.config, trigger="manual")

    def test_diff_vs_previous_returns_200(self):
        resp = self.client.get(f"/instance/{self.config.slug}/diff/{self.backup_b.pk}/")
        self.assertEqual(resp.status_code, 200)

    def test_diff_vs_previous_shows_changes(self):
        resp = self.client.get(f"/instance/{self.config.slug}/diff/{self.backup_b.pk}/")
        self.assertContains(resp, "New Tab")

    def test_diff_compare_returns_200(self):
        resp = self.client.get(f"/instance/{self.config.slug}/diff/{self.backup_b.pk}/{self.backup_a.pk}/")
        self.assertEqual(resp.status_code, 200)

    def test_diff_compare_shows_changes(self):
        resp = self.client.get(f"/instance/{self.config.slug}/diff/{self.backup_b.pk}/{self.backup_a.pk}/")
        self.assertContains(resp, "New Tab")

    def test_diff_first_backup_no_previous(self):
        resp = self.client.get(f"/instance/{self.config.slug}/diff/{self.backup_a.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No previous backup")

    def test_diff_nonexistent_backup_redirects(self):
        resp = self.client.get(f"/instance/{self.config.slug}/diff/99999/")
        self.assertEqual(resp.status_code, 302)

    def test_diff_failed_backup_404(self):
        failed = BackupRecord.objects.create(
            config=self.config,
            filename="fail.tar.gz",
            file_path="/nonexistent",
            file_size=0,
            status="failed",
        )
        resp = self.client.get(f"/instance/{self.config.slug}/diff/{failed.pk}/")
        self.assertEqual(resp.status_code, 302)

    def test_diff_falls_back_to_stored_summary(self):
        # Delete archives so archive diff fails, should fall back to stored summary
        self.backup_b.refresh_from_db()
        self.assertTrue(self.backup_b.changes_summary)
        Path(self.backup_a.file_path).unlink()
        Path(self.backup_b.file_path).unlink()
        resp = self.client.get(f"/instance/{self.config.slug}/diff/{self.backup_b.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "New Tab")

    def test_diff_shows_error_when_no_archives_or_summary(self):
        # Delete archives AND clear stored summary — should show error
        self.backup_b.changes_summary = {}
        self.backup_b.save(update_fields=["changes_summary"])
        Path(self.backup_a.file_path).unlink()
        Path(self.backup_b.file_path).unlink()
        resp = self.client.get(f"/instance/{self.config.slug}/diff/{self.backup_b.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "no longer available")

    def test_comparison_dropdown_lists_other_backups(self):
        resp = self.client.get(f"/instance/{self.config.slug}/diff/{self.backup_b.pk}/")
        # The dropdown should contain backup_a as an option
        content = resp.content.decode()
        self.assertIn(str(self.backup_a.pk), content)
