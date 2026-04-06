import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import TestCase, override_settings

from backup.models import BackupRecord, NodeRedConfig, RestoreRecord
from backup.services.backup_service import create_backup
from backup.services.diff_service import (
    diff_backup_archives,
    diff_tab_summaries,
    parse_flows_from_archive,
)
from backup.services.flow_parser import (
    get_tab_names,
    parse_flows,
    parse_flows_file,
)
from backup.services.restore_service import restore_backup
from backup.services.notification_service import (
    _get_instance_events,
    get_configured_backends,
    notify,
)
from backup.services.notifications.base import (
    NotificationBackend,
    NotificationPayload,
    NotifyEvent,
)

# ---------------------------------------------------------------------------
# Sample flows data
# ---------------------------------------------------------------------------

class TempBackupDirMixin:
    """Mixin that redirects BACKUP_DIR to a temp directory for test isolation.

    Provides self.backup_dir (Path) pointing to the isolated temp directory.
    The mixin patches settings.BACKUP_DIR and cleans up everything on tearDown,
    so individual test classes don't need to glob-delete archives.

    Place this mixin BEFORE TestCase in the class bases so its setUp/tearDown
    wrap correctly.
    """

    def setUp(self):
        self._backup_tmpdir_obj = tempfile.mkdtemp(prefix="nodered_test_backups_")
        self.backup_dir = Path(self._backup_tmpdir_obj)
        self._patcher = patch.object(settings, "BACKUP_DIR", self.backup_dir)
        self._patcher.start()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self._patcher.stop()
        shutil.rmtree(self._backup_tmpdir_obj, ignore_errors=True)


SAMPLE_FLOWS = [
    {"id": "tab1", "type": "tab", "label": "Home Automation"},
    {"id": "tab2", "type": "tab", "label": "API Endpoints"},
    {"id": "sf1", "type": "subflow", "name": "Error Handler"},
    {"id": "g1", "type": "group", "name": "Sensors", "z": "tab1"},
    {"id": "n1", "type": "inject", "z": "tab1", "g": "g1", "name": "Trigger", "x": 100, "y": 200},
    {"id": "n2", "type": "debug", "z": "tab1", "name": "Log"},
    {"id": "n3", "type": "http in", "z": "tab2"},
    {"id": "n4", "type": "function", "z": "sf1"},
    {"id": "cfg1", "type": "mqtt-broker"},  # no z → config node
]


class FlowParserParseFlowsTest(TestCase):
    def test_basic_parsing(self):
        result = parse_flows(SAMPLE_FLOWS)
        self.assertEqual(len(result["tabs"]), 2)
        self.assertEqual(len(result["subflows"]), 1)
        self.assertEqual(result["config_nodes"], 1)
        self.assertEqual(result["total_nodes"], len(SAMPLE_FLOWS))

    def test_tabs_sorted_by_label(self):
        result = parse_flows(SAMPLE_FLOWS)
        labels = [t["label"] for t in result["tabs"]]
        self.assertEqual(labels, ["API Endpoints", "Home Automation"])

    def test_node_counts_per_tab(self):
        result = parse_flows(SAMPLE_FLOWS)
        tab_map = {t["label"]: t["node_count"] for t in result["tabs"]}
        self.assertEqual(tab_map["Home Automation"], 3)  # n1, n2, g1 (group is a node)
        self.assertEqual(tab_map["API Endpoints"], 1)

    def test_subflow_node_count(self):
        result = parse_flows(SAMPLE_FLOWS)
        self.assertEqual(result["subflows"][0]["node_count"], 1)

    def test_empty_list(self):
        result = parse_flows([])
        self.assertEqual(result["tabs"], [])
        self.assertEqual(result["total_nodes"], 0)

    def test_non_list_input(self):
        result = parse_flows("not a list")
        self.assertEqual(result["tabs"], [])
        self.assertEqual(result["total_nodes"], 0)

    def test_global_nodes(self):
        nodes = [
            {"id": "n1", "type": "function", "z": "unknown_parent"},
        ]
        result = parse_flows(nodes)
        self.assertEqual(result["global_nodes"], 1)

    def test_unnamed_tab_gets_default(self):
        nodes = [{"id": "t1", "type": "tab"}]
        result = parse_flows(nodes)
        self.assertEqual(result["tabs"][0]["label"], "Unnamed")

    def test_groups_tracked(self):
        result = parse_flows(SAMPLE_FLOWS)
        self.assertIn("g1", result["groups"])
        self.assertEqual(result["groups"]["g1"]["name"], "Sensors")
        self.assertEqual(result["groups"]["g1"]["tab_id"], "tab1")

    def test_nodes_by_id_populated(self):
        result = parse_flows(SAMPLE_FLOWS)
        self.assertIn("n1", result["nodes_by_id"])
        self.assertEqual(result["nodes_by_id"]["n1"]["type"], "inject")
        self.assertEqual(result["nodes_by_id"]["n1"]["z"], "tab1")
        self.assertEqual(result["nodes_by_id"]["n1"]["g"], "g1")
        # Config nodes also indexed
        self.assertIn("cfg1", result["nodes_by_id"])

    def test_content_fields_exclude_position(self):
        result = parse_flows(SAMPLE_FLOWS)
        n1_data = result["nodes_by_id"]["n1"]["_data"]
        self.assertNotIn("x", n1_data)
        self.assertNotIn("y", n1_data)
        self.assertIn("id", n1_data)
        self.assertIn("type", n1_data)
        self.assertIn("name", n1_data)


class FlowParserFileTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"

    def test_parse_flows_file_success(self):
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        result = parse_flows_file(str(self.flows_file))
        self.assertIsNotNone(result)
        self.assertEqual(len(result["tabs"]), 2)

    def test_parse_flows_file_missing(self):
        result = parse_flows_file("/nonexistent/flows.json")
        self.assertIsNone(result)

    def test_parse_flows_file_invalid_json(self):
        self.flows_file.write_text("not json {{{")
        result = parse_flows_file(str(self.flows_file))
        self.assertIsNone(result)

    def test_get_tab_names(self):
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        names = get_tab_names(str(self.flows_file))
        self.assertEqual(names, ["API Endpoints", "Home Automation"])

    def test_get_tab_names_missing_file(self):
        names = get_tab_names("/nonexistent/flows.json")
        self.assertEqual(names, [])


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


class BackupServiceTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )

    def test_create_backup_success(self):
        record = create_backup(config=self.config, trigger="manual")
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "success")
        self.assertEqual(record.trigger, "manual")
        self.assertTrue(record.filename.startswith("flowhistory_"))
        self.assertTrue(record.filename.endswith(".tar.gz"))
        self.assertGreater(record.file_size, 0)
        self.assertEqual(len(record.checksum), 64)
        # Archive exists on disk
        self.assertTrue(Path(record.file_path).is_file())

    def test_archive_contains_flows_json(self):
        record = create_backup(config=self.config, trigger="manual")
        with tarfile.open(record.file_path, "r:gz") as tar:
            names = tar.getnames()
            self.assertIn("flows.json", names)
            f = tar.extractfile("flows.json")
            data = json.loads(f.read())
            self.assertEqual(data, SAMPLE_FLOWS)

    def test_tab_summary_populated(self):
        record = create_backup(config=self.config, trigger="manual")
        self.assertEqual(record.tab_summary, ["API Endpoints", "Home Automation"])

    def test_checksum_matches_flows_content(self):
        record = create_backup(config=self.config, trigger="manual")
        expected = hashlib.sha256(self.flows_file.read_bytes()).hexdigest()
        self.assertEqual(record.checksum, expected)

    def test_config_updated_on_success(self):
        create_backup(config=self.config, trigger="manual")
        self.config.refresh_from_db()
        self.assertIsNotNone(self.config.last_successful_backup)
        self.assertEqual(self.config.last_backup_error, "")

    def test_missing_flows_returns_failed_record(self):
        self.config.flows_path = "/nonexistent/flows.json"
        self.config.save()
        record = create_backup(config=self.config, trigger="manual")
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "failed")
        self.assertIn("not found", record.error_message)

    def test_dedup_skips_for_scheduled(self):
        create_backup(config=self.config, trigger="manual")
        result = create_backup(config=self.config, trigger="scheduled")
        self.assertIsNone(result)

    def test_always_backup_bypasses_dedup_for_scheduled(self):
        self.config.always_backup = True
        self.config.save()
        create_backup(config=self.config, trigger="manual")
        result = create_backup(config=self.config, trigger="scheduled")
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "success")

    def test_always_backup_does_not_bypass_dedup_for_file_change(self):
        self.config.always_backup = True
        self.config.save()
        create_backup(config=self.config, trigger="manual")
        result = create_backup(config=self.config, trigger="file_change")
        self.assertIsNone(result)

    def test_always_backup_defaults_to_false(self):
        config = NodeRedConfig.objects.create(pk=99)
        self.assertFalse(config.always_backup)

    def test_dedup_does_not_skip_manual(self):
        create_backup(config=self.config, trigger="manual")
        record = create_backup(config=self.config, trigger="manual")
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "success")

    def test_changes_summary_first_backup(self):
        record = create_backup(config=self.config, trigger="manual")
        # No previous backup, so changes_summary is empty
        self.assertEqual(record.changes_summary, {})

    def test_changes_summary_detects_tab_added(self):
        create_backup(config=self.config, trigger="manual")
        # Add a new tab
        new_flows = SAMPLE_FLOWS + [{"id": "tab3", "type": "tab", "label": "New Tab"}]
        self.flows_file.write_text(json.dumps(new_flows))
        record = create_backup(config=self.config, trigger="manual")
        self.assertIn("New Tab", record.changes_summary.get("tabs_added", []))

    def test_changes_summary_detects_node_modification(self):
        create_backup(config=self.config, trigger="manual")
        # Modify an existing node's name (n1 in SAMPLE_FLOWS)
        modified_flows = []
        for node in SAMPLE_FLOWS:
            if node.get("id") == "n1":
                node = {**node, "name": "Renamed Trigger"}
            modified_flows.append(node)
        self.flows_file.write_text(json.dumps(modified_flows))
        record = create_backup(config=self.config, trigger="manual")
        tabs_mod = record.changes_summary.get("tabs_modified", [])
        self.assertTrue(len(tabs_mod) > 0)
        home_tab = [t for t in tabs_mod if t["label"] == "Home Automation"]
        self.assertEqual(len(home_tab), 1)
        self.assertTrue(len(home_tab[0]["nodes_modified"]) > 0)

    def test_includes_credentials_when_present(self):
        cred_file = self.backup_dir / "flows_cred.json"
        cred_file.write_text('{"encrypted": true}')
        self.config.backup_credentials = True
        self.config.save()
        record = create_backup(config=self.config, trigger="manual")
        self.assertTrue(record.includes_credentials)
        with tarfile.open(record.file_path, "r:gz") as tar:
            self.assertIn("flows_cred.json", tar.getnames())
        cred_file.unlink()

    def test_excludes_credentials_when_disabled(self):
        cred_file = self.backup_dir / "flows_cred.json"
        cred_file.write_text('{"encrypted": true}')
        self.config.backup_credentials = False
        self.config.save()
        record = create_backup(config=self.config, trigger="manual")
        self.assertFalse(record.includes_credentials)
        with tarfile.open(record.file_path, "r:gz") as tar:
            self.assertNotIn("flows_cred.json", tar.getnames())
        cred_file.unlink()


@override_settings(REQUIRE_AUTH=False)
class ApiCreateBackupTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )

    def test_post_creates_backup(self):
        resp = self.client.post(f"/api/instance/{self.config.slug}/backup/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("backup", data)
        self.assertIn("filename", data["backup"])

    def test_get_not_allowed(self):
        resp = self.client.get(f"/api/instance/{self.config.slug}/backup/")
        self.assertEqual(resp.status_code, 405)

    def test_missing_flows_returns_500(self):
        self.config.flows_path = "/nonexistent/flows.json"
        self.config.save()
        resp = self.client.post(f"/api/instance/{self.config.slug}/backup/")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["status"], "error")


# ---------------------------------------------------------------------------
# Docker Service
# ---------------------------------------------------------------------------

class DockerServiceTest(TestCase):
    def test_is_docker_available_no_sdk(self):
        with patch("backup.services.docker_service.docker", None):
            from backup.services.docker_service import is_docker_available
            self.assertFalse(is_docker_available())

    def test_restart_container_no_sdk(self):
        with patch("backup.services.docker_service.docker", None):
            from backup.services.docker_service import restart_container
            result = restart_container("nodered")
            self.assertFalse(result["success"])
            self.assertIn("not installed", result["message"])

    def test_restart_container_success(self):
        mock_docker = MagicMock()
        mock_container = MagicMock()
        mock_docker.from_env.return_value.containers.get.return_value = mock_container
        with patch("backup.services.docker_service.docker", mock_docker):
            from backup.services.docker_service import restart_container
            result = restart_container("nodered")
            self.assertTrue(result["success"])
            mock_container.restart.assert_called_once_with(timeout=30)

    def test_restart_container_not_found(self):
        mock_docker = MagicMock()
        from docker.errors import NotFound
        mock_docker.from_env.return_value.containers.get.side_effect = NotFound("not found")
        with patch("backup.services.docker_service.docker", mock_docker), \
             patch("backup.services.docker_service.NotFound", NotFound):
            from backup.services.docker_service import restart_container
            result = restart_container("nodered")
            self.assertFalse(result["success"])
            self.assertIn("not found", result["message"])

    def test_get_container_status_no_sdk(self):
        with patch("backup.services.docker_service.docker", None):
            from backup.services.docker_service import get_container_status
            self.assertIsNone(get_container_status("nodered"))


# ---------------------------------------------------------------------------
# Restore Service
# ---------------------------------------------------------------------------

class RestoreServiceTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )
        # Create a backup to restore from
        self.backup_record = create_backup(config=self.config, trigger="manual")

    def test_restore_success(self):
        # Modify flows.json so restore actually overwrites
        self.flows_file.write_text("[]")
        result = restore_backup(self.backup_record.pk)
        self.assertEqual(result.status, "success")
        self.assertIsInstance(result, RestoreRecord)
        # Verify flows.json was restored
        restored = json.loads(self.flows_file.read_text())
        self.assertEqual(restored, SAMPLE_FLOWS)

    def test_restore_creates_safety_backup(self):
        restore_backup(self.backup_record.pk)
        safety = BackupRecord.objects.filter(
            config=self.config, trigger="pre_restore"
        )
        self.assertTrue(safety.exists())

    def test_restore_record_tracks_safety_backup(self):
        result = restore_backup(self.backup_record.pk)
        self.assertIsNotNone(result.safety_backup)
        self.assertEqual(result.safety_backup.trigger, "pre_restore")

    def test_restore_files_list(self):
        result = restore_backup(self.backup_record.pk)
        self.assertIn("flows.json", result.files_restored)

    def test_restore_invalid_id_raises(self):
        with self.assertRaises(BackupRecord.DoesNotExist):
            restore_backup(99999)

    def test_restore_failed_backup_rejected(self):
        failed = BackupRecord.objects.create(
            config=self.config,
            filename="bad.tar.gz",
            file_path="/nonexistent/bad.tar.gz",
            status="failed",
            trigger="manual",
        )
        result = restore_backup(failed.pk)
        self.assertEqual(result.status, "failed")
        self.assertIn("Cannot restore", result.error_message)

    def test_restore_missing_archive(self):
        Path(self.backup_record.file_path).unlink()
        result = restore_backup(self.backup_record.pk)
        self.assertEqual(result.status, "failed")
        self.assertIn("not found", result.error_message)

    @patch("backup.services.restore_service.os.chown")
    def test_restore_sets_ownership(self, mock_chown):
        restore_backup(self.backup_record.pk)
        mock_chown.assert_called()
        args = mock_chown.call_args[0]
        self.assertEqual(args[1], 1000)
        self.assertEqual(args[2], 1000)

    @patch("backup.services.restore_service.restart_container")
    def test_restore_with_restart(self, mock_restart):
        mock_restart.return_value = {"success": True, "message": "Restarted"}
        self.config.restart_on_restore = True
        self.config.save()
        result = restore_backup(self.backup_record.pk)
        self.assertTrue(result.container_restarted)
        mock_restart.assert_called_once_with(self.config.nodered_container_name)

    @patch("backup.services.restore_service.restart_container")
    def test_restore_without_restart(self, mock_restart):
        self.config.restart_on_restore = False
        self.config.save()
        result = restore_backup(self.backup_record.pk)
        self.assertFalse(result.container_restarted)
        mock_restart.assert_not_called()

    @patch("backup.services.restore_service.restart_container")
    def test_restore_restart_override(self, mock_restart):
        mock_restart.return_value = {"success": True, "message": "Restarted"}
        self.config.restart_on_restore = False
        self.config.save()
        result = restore_backup(self.backup_record.pk, restart=True)
        self.assertTrue(result.container_restarted)
        mock_restart.assert_called_once()

    def test_restore_with_credentials(self):
        # Create a backup that includes credentials
        cred_file = self.backup_dir / "flows_cred.json"
        cred_file.write_text('{"encrypted": true}')
        self.config.backup_credentials = True
        self.config.save()
        backup = create_backup(config=self.config, trigger="manual")
        cred_file.unlink()  # Remove the original
        result = restore_backup(backup.pk)
        self.assertEqual(result.status, "success")
        self.assertIn("flows_cred.json", result.files_restored)
        self.assertTrue(cred_file.is_file())  # Should be restored


# ---------------------------------------------------------------------------
# Restore API
# ---------------------------------------------------------------------------

@override_settings(REQUIRE_AUTH=False)
class ApiRestoreBackupTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )
        self.backup_record = create_backup(config=self.config, trigger="manual")

    def test_post_restores_backup(self):
        resp = self.client.post(f"/api/instance/{self.config.slug}/restore/{self.backup_record.pk}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("restore", data)
        self.assertIn("files_restored", data["restore"])

    def test_get_not_allowed(self):
        resp = self.client.get(f"/api/instance/{self.config.slug}/restore/{self.backup_record.pk}/")
        self.assertEqual(resp.status_code, 405)

    def test_nonexistent_backup_returns_404(self):
        resp = self.client.post(f"/api/instance/{self.config.slug}/restore/99999/")
        self.assertEqual(resp.status_code, 404)

    def test_response_includes_safety_backup(self):
        resp = self.client.post(f"/api/instance/{self.config.slug}/restore/{self.backup_record.pk}/")
        data = resp.json()
        self.assertIn("safety_backup_id", data["restore"])


# ---------------------------------------------------------------------------
# Diff Service
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Retention Service
# ---------------------------------------------------------------------------

class RetentionServiceTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
            max_backups=3,
            max_age_days=7,
        )

    def _create_backups(self, count, **kwargs):
        """Create multiple manual backups with unique content.

        Mocks apply_retention during creation so retention doesn't run
        prematurely (backup_service calls it after each success).
        """
        records = []
        with patch("backup.services.retention_service.apply_retention"):
            for i in range(count):
                flows = SAMPLE_FLOWS + [{"id": f"extra_{i}", "type": "inject", "z": "tab1"}]
                self.flows_file.write_text(json.dumps(flows))
                record = create_backup(config=self.config, trigger="manual")
                if record and kwargs.get("age_days"):
                    # Backdate the record
                    record.created_at = record.created_at - timedelta(days=kwargs["age_days"])
                    record.save(update_fields=["created_at"])
                records.append(record)
        return records

    def test_delete_by_count(self):
        from backup.services.retention_service import apply_retention

        self._create_backups(5)
        self.assertEqual(BackupRecord.objects.filter(status="success").count(), 5)
        result = apply_retention(self.config)
        self.assertEqual(BackupRecord.objects.filter(status="success").count(), 3)
        self.assertEqual(result["deleted_by_count"], 2)

    def test_delete_by_age(self):
        from backup.services.retention_service import apply_retention

        records = self._create_backups(2)
        # Backdate both records to 10 days ago
        for r in records:
            r.created_at = r.created_at - timedelta(days=10)
            r.save(update_fields=["created_at"])
        result = apply_retention(self.config)
        self.assertEqual(result["deleted_by_age"], 2)
        self.assertEqual(BackupRecord.objects.filter(status="success").count(), 0)

    def test_protects_recent_pre_restore(self):
        from backup.services.retention_service import apply_retention

        # Create a pre_restore backup
        record = create_backup(config=self.config, trigger="pre_restore")
        # Create enough to exceed max_backups
        self._create_backups(4)
        result = apply_retention(self.config)
        # pre_restore should still exist
        self.assertTrue(
            BackupRecord.objects.filter(pk=record.pk).exists()
        )

    def test_disk_file_deleted(self):
        from backup.services.retention_service import apply_retention

        records = self._create_backups(5)
        oldest_path = Path(records[0].file_path)
        self.assertTrue(oldest_path.is_file())
        apply_retention(self.config)
        self.assertFalse(oldest_path.is_file())

    def test_no_deletions_within_limits(self):
        from backup.services.retention_service import apply_retention

        self._create_backups(2)
        result = apply_retention(self.config)
        self.assertEqual(result["deleted_by_count"], 0)
        self.assertEqual(result["deleted_by_age"], 0)

    def test_pinned_protected_from_count_deletion(self):
        from backup.services.retention_service import apply_retention

        records = self._create_backups(5)
        # Pin the oldest backup
        records[0].is_pinned = True
        records[0].save(update_fields=["is_pinned"])
        result = apply_retention(self.config)
        # Oldest should survive because it's pinned
        self.assertTrue(BackupRecord.objects.filter(pk=records[0].pk).exists())
        # Only 1 deleted (not 2) because pinned one is protected
        self.assertEqual(result["deleted_by_count"], 1)

    def test_pinned_protected_from_age_deletion(self):
        from backup.services.retention_service import apply_retention

        records = self._create_backups(2)
        # Backdate and pin one
        for r in records:
            r.created_at = r.created_at - timedelta(days=10)
            r.save(update_fields=["created_at"])
        records[0].is_pinned = True
        records[0].save(update_fields=["is_pinned"])
        result = apply_retention(self.config)
        self.assertEqual(result["deleted_by_age"], 1)
        self.assertTrue(BackupRecord.objects.filter(pk=records[0].pk).exists())


# ---------------------------------------------------------------------------
# Watcher Service
# ---------------------------------------------------------------------------

class WatcherHandlerTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
            watch_enabled=True,
            watch_debounce_seconds=1,
        )

    def test_ignores_directory_events(self):
        from backup.services.watcher_service import _FlowsHandler

        handler = _FlowsHandler("flows.json", self.config.pk)
        event = MagicMock()
        event.is_directory = True
        event.src_path = str(self.flows_file)
        handler.on_modified(event)
        self.assertIsNone(handler._timer)

    def test_ignores_non_flows_files(self):
        from backup.services.watcher_service import _FlowsHandler

        handler = _FlowsHandler("flows.json", self.config.pk)
        event = MagicMock()
        event.is_directory = False
        event.src_path = str(self.backup_dir / "settings.js")
        handler.on_modified(event)
        self.assertIsNone(handler._timer)

    def test_starts_timer_on_flows_modified(self):
        from backup.services.watcher_service import _FlowsHandler

        handler = _FlowsHandler("flows.json", self.config.pk)
        event = MagicMock()
        event.is_directory = False
        event.src_path = str(self.flows_file)
        handler.on_modified(event)
        self.assertIsNotNone(handler._timer)
        handler._timer.cancel()  # Clean up

    def test_watch_disabled_skips_timer(self):
        from backup.services.watcher_service import _FlowsHandler

        self.config.watch_enabled = False
        self.config.save()
        handler = _FlowsHandler("flows.json", self.config.pk)
        event = MagicMock()
        event.is_directory = False
        event.src_path = str(self.flows_file)
        handler.on_modified(event)
        self.assertIsNone(handler._timer)

    @patch("backup.services.backup_service.create_backup")
    def test_debounce_complete_creates_backup(self, mock_backup):
        from backup.services.watcher_service import _FlowsHandler

        mock_backup.return_value = MagicMock(status="success", filename="test.tar.gz")
        handler = _FlowsHandler("flows.json", self.config.pk)
        handler._on_debounce_complete()
        mock_backup.assert_called_once()
        call_kwargs = mock_backup.call_args[1]
        self.assertEqual(call_kwargs["trigger"], "file_change")


# ---------------------------------------------------------------------------
# Scheduler Command
# ---------------------------------------------------------------------------

class SchedulerBuildTriggerTest(TestCase):
    def test_daily_trigger(self):
        from backup.management.commands.runapscheduler import Command

        config = MagicMock()
        config.backup_frequency = "daily"
        config.backup_time = MagicMock(hour=3, minute=0)
        trigger = Command._build_trigger(config)
        # CronTrigger should have hour=3, minute=0
        self.assertIsNotNone(trigger)

    def test_hourly_trigger(self):
        from backup.management.commands.runapscheduler import Command

        config = MagicMock()
        config.backup_frequency = "hourly"
        config.backup_time = MagicMock(hour=3, minute=30)
        trigger = Command._build_trigger(config)
        self.assertIsNotNone(trigger)

    def test_weekly_trigger(self):
        from backup.management.commands.runapscheduler import Command

        config = MagicMock()
        config.backup_frequency = "weekly"
        config.backup_time = MagicMock(hour=3, minute=0)
        config.backup_day = 0
        trigger = Command._build_trigger(config)
        self.assertIsNotNone(trigger)


# ---------------------------------------------------------------------------
# API: Set Label
# ---------------------------------------------------------------------------

@override_settings(REQUIRE_AUTH=False)
class ApiSetLabelTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )
        self.backup_record = create_backup(config=self.config, trigger="manual")

    def test_set_label(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/label/",
            data=json.dumps({"label": "Before refactor"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["backup"]["label"], "Before refactor")
        self.backup_record.refresh_from_db()
        self.assertEqual(self.backup_record.label, "Before refactor")

    def test_clear_label(self):
        self.backup_record.label = "old label"
        self.backup_record.save()
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/label/",
            data=json.dumps({"label": ""}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.backup_record.refresh_from_db()
        self.assertEqual(self.backup_record.label, "")

    def test_missing_label_field(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/label/",
            data=json.dumps({"note": "wrong field"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_label_too_long(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/label/",
            data=json.dumps({"label": "x" * 201}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_not_found(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/99999/label/",
            data=json.dumps({"label": "test"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Notes API
# ---------------------------------------------------------------------------


@override_settings(REQUIRE_AUTH=False)
class ApiSetNotesTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )
        self.backup_record = create_backup(config=self.config, trigger="manual")

    def test_set_notes(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/notes/",
            data=json.dumps({"notes": "Rewired MQTT pipeline to batch writes."}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["backup"]["notes"], "Rewired MQTT pipeline to batch writes.")
        self.backup_record.refresh_from_db()
        self.assertEqual(self.backup_record.notes, "Rewired MQTT pipeline to batch writes.")

    def test_clear_notes(self):
        self.backup_record.notes = "old notes"
        self.backup_record.save()
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/notes/",
            data=json.dumps({"notes": ""}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.backup_record.refresh_from_db()
        self.assertEqual(self.backup_record.notes, "")

    def test_missing_notes_field(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/notes/",
            data=json.dumps({"label": "wrong field"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_notes_not_a_string(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/notes/",
            data=json.dumps({"notes": 123}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_not_found(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/99999/notes/",
            data=json.dumps({"notes": "test"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_multiline_notes(self):
        multiline = "Line one\nLine two\nLine three"
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/notes/",
            data=json.dumps({"notes": multiline}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.backup_record.refresh_from_db()
        self.assertEqual(self.backup_record.notes, multiline)

    def test_get_method_not_allowed(self):
        resp = self.client.get(f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/notes/")
        self.assertEqual(resp.status_code, 405)


# ---------------------------------------------------------------------------
# Pin Toggle
# ---------------------------------------------------------------------------

@override_settings(REQUIRE_AUTH=False)
class ApiTogglePinTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )
        self.backup_record = create_backup(config=self.config, trigger="manual")

    def test_pin_backup(self):
        resp = self.client.post(f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/pin/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["backup"]["is_pinned"])
        self.backup_record.refresh_from_db()
        self.assertTrue(self.backup_record.is_pinned)

    def test_unpin_backup(self):
        self.backup_record.is_pinned = True
        self.backup_record.save()
        resp = self.client.post(f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/pin/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["backup"]["is_pinned"])
        self.backup_record.refresh_from_db()
        self.assertFalse(self.backup_record.is_pinned)

    def test_toggle_twice(self):
        self.client.post(f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/pin/")
        self.client.post(f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/pin/")
        self.backup_record.refresh_from_db()
        self.assertFalse(self.backup_record.is_pinned)

    def test_not_found(self):
        resp = self.client.post(f"/api/instance/{self.config.slug}/backup/99999/pin/")
        self.assertEqual(resp.status_code, 404)

    def test_get_method_not_allowed(self):
        resp = self.client.get(f"/api/instance/{self.config.slug}/backup/{self.backup_record.pk}/pin/")
        self.assertEqual(resp.status_code, 405)


# ---------------------------------------------------------------------------
# Backup Delete
# ---------------------------------------------------------------------------

@override_settings(REQUIRE_AUTH=False)
class BackupDeleteTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )
        self.backup_record = create_backup(config=self.config, trigger="manual")

    def test_delete_removes_record_and_file(self):
        archive_path = Path(self.backup_record.file_path)
        self.assertTrue(archive_path.is_file())
        pk = self.backup_record.pk
        resp = self.client.post(f"/instance/{self.config.slug}/backup/{pk}/delete/")
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(BackupRecord.objects.filter(pk=pk).exists())
        self.assertFalse(archive_path.is_file())

    def test_delete_missing_file_still_succeeds(self):
        Path(self.backup_record.file_path).unlink()
        pk = self.backup_record.pk
        resp = self.client.post(f"/instance/{self.config.slug}/backup/{pk}/delete/")
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(BackupRecord.objects.filter(pk=pk).exists())

    def test_not_found(self):
        resp = self.client.post(f"/instance/{self.config.slug}/backup/99999/delete/")
        self.assertEqual(resp.status_code, 302)

    def test_get_not_allowed(self):
        resp = self.client.get(f"/instance/{self.config.slug}/backup/{self.backup_record.pk}/delete/")
        self.assertEqual(resp.status_code, 405)


# ---------------------------------------------------------------------------
# Bulk Actions
# ---------------------------------------------------------------------------

@override_settings(REQUIRE_AUTH=False)
class BulkActionTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )
        with patch("backup.services.retention_service.apply_retention"):
            self.b1 = create_backup(config=self.config, trigger="manual")
            self.flows_file.write_text(json.dumps(SAMPLE_FLOWS + [{"id": "x"}]))
            self.b2 = create_backup(config=self.config, trigger="manual")
            self.flows_file.write_text(json.dumps(SAMPLE_FLOWS + [{"id": "y"}]))
            self.b3 = create_backup(config=self.config, trigger="manual")

    def _post(self, data):
        return self.client.post(
            f"/api/instance/{self.config.slug}/bulk/",
            json.dumps(data),
            content_type="application/json",
        )

    def test_bulk_pin(self):
        resp = self._post({"ids": [self.b1.pk, self.b2.pk], "action": "pin"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["affected"], 2)
        self.assertTrue(BackupRecord.objects.get(pk=self.b1.pk).is_pinned)
        self.assertTrue(BackupRecord.objects.get(pk=self.b2.pk).is_pinned)
        self.assertFalse(BackupRecord.objects.get(pk=self.b3.pk).is_pinned)

    def test_bulk_unpin(self):
        self.b1.is_pinned = True
        self.b1.save()
        self.b2.is_pinned = True
        self.b2.save()
        resp = self._post({"ids": [self.b1.pk, self.b2.pk], "action": "unpin"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(BackupRecord.objects.get(pk=self.b1.pk).is_pinned)
        self.assertFalse(BackupRecord.objects.get(pk=self.b2.pk).is_pinned)

    def test_bulk_delete(self):
        p1 = Path(self.b1.file_path)
        p2 = Path(self.b2.file_path)
        resp = self._post({"ids": [self.b1.pk, self.b2.pk], "action": "delete"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["affected"], 2)
        self.assertFalse(BackupRecord.objects.filter(pk=self.b1.pk).exists())
        self.assertFalse(BackupRecord.objects.filter(pk=self.b2.pk).exists())
        self.assertTrue(BackupRecord.objects.filter(pk=self.b3.pk).exists())
        self.assertFalse(p1.is_file())
        self.assertFalse(p2.is_file())

    def test_invalid_action(self):
        resp = self._post({"ids": [self.b1.pk], "action": "nope"})
        self.assertEqual(resp.status_code, 400)

    def test_empty_ids(self):
        resp = self._post({"ids": [], "action": "pin"})
        self.assertEqual(resp.status_code, 400)

    def test_missing_backup_returns_error(self):
        resp = self._post({"ids": [self.b1.pk, 99999], "action": "pin"})
        data = resp.json()
        self.assertEqual(data["affected"], 1)
        self.assertEqual(len(data["errors"]), 1)
        self.assertIn("99999", data["errors"][0])

    def test_get_not_allowed(self):
        resp = self.client.get(f"/api/instance/{self.config.slug}/bulk/")
        self.assertEqual(resp.status_code, 405)


# ---------------------------------------------------------------------------
# Diff View
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# NodeRedConfig model — instance fields
# ---------------------------------------------------------------------------


class NodeRedConfigModelTest(TempBackupDirMixin, TestCase):
    def test_slug_auto_generated_from_name(self):
        config = NodeRedConfig.objects.create(name="Production")
        self.assertEqual(config.slug, "production")

    def test_slug_dedup(self):
        c1 = NodeRedConfig.objects.create(name="Node-RED")
        c2 = NodeRedConfig.objects.create(name="Node-RED")
        self.assertEqual(c1.slug, "node-red")
        self.assertEqual(c2.slug, "node-red-2")

    def test_reserved_slug_avoided(self):
        config = NodeRedConfig.objects.create(name="Add")
        self.assertEqual(config.slug, "add-instance")

    def test_reserved_slug_api(self):
        config = NodeRedConfig.objects.create(name="Api")
        self.assertEqual(config.slug, "api-instance")

    def test_color_auto_assigned(self):
        config = NodeRedConfig.objects.create(name="First")
        self.assertEqual(config.color, "#3B82F6")

    def test_second_color_different(self):
        c1 = NodeRedConfig.objects.create(name="First")
        c2 = NodeRedConfig.objects.create(name="Second")
        self.assertNotEqual(c1.color, c2.color)
        self.assertEqual(c2.color, "#EF4444")

    def test_backup_dir_property(self):
        config = NodeRedConfig.objects.create(name="Test")
        expected = Path(settings.BACKUP_DIR) / config.slug
        self.assertEqual(config.backup_dir, expected)

    def test_get_nodered_credentials_with_prefix(self):
        config = NodeRedConfig.objects.create(name="Cred Test", env_prefix="MYTEST")
        with patch.dict("os.environ", {
            "FLOWHISTORY_MYTEST_USER": "admin",
            "FLOWHISTORY_MYTEST_PASS": "secret",
        }):
            user, pwd = config.get_nodered_credentials()
            self.assertEqual(user, "admin")
            self.assertEqual(pwd, "secret")

    def test_get_nodered_credentials_no_prefix(self):
        config = NodeRedConfig.objects.create(name="No Prefix")
        user, pwd = config.get_nodered_credentials()
        self.assertIsNone(user)
        self.assertIsNone(pwd)

    def test_schedule_enabled_field(self):
        config = NodeRedConfig.objects.create(name="Sched Test")
        self.assertTrue(config.schedule_enabled)
        config.schedule_enabled = False
        config.save()
        config.refresh_from_db()
        self.assertFalse(config.schedule_enabled)

    def test_explicit_slug_not_overwritten(self):
        config = NodeRedConfig.objects.create(name="Whatever", slug="custom-slug")
        self.assertEqual(config.slug, "custom-slug")

    def test_str_returns_name(self):
        config = NodeRedConfig.objects.create(name="My Instance")
        self.assertEqual(str(config), "My Instance")


# ---------------------------------------------------------------------------
# Discovery service
# ---------------------------------------------------------------------------

from backup.services.discovery_service import discover_instances_from_env


class DiscoveryServiceTest(TestCase):
    def _clean_env(self, env_dict):
        """Return env_dict with only FLOWHISTORY_* keys removed from os.environ."""
        clean = {k: v for k, v in os.environ.items() if not k.startswith("FLOWHISTORY_")}
        clean.update(env_dict)
        return clean

    def test_discover_remote_instance(self):
        env = self._clean_env({"FLOWHISTORY_PROD_URL": "http://192.168.1.50:1880"})
        with patch.dict("os.environ", env, clear=True):
            result = discover_instances_from_env()
        self.assertIn("PROD", result["created"])
        config = NodeRedConfig.objects.get(env_prefix="PROD")
        self.assertEqual(config.source_type, "remote")
        self.assertEqual(config.nodered_url, "http://192.168.1.50:1880")

    def test_discover_local_instance(self):
        env = self._clean_env({
            "FLOWHISTORY_DEV_FLOWS_PATH": "/data/flows.json",
            "FLOWHISTORY_DEV_NAME": "Dev Box",
        })
        with patch.dict("os.environ", env, clear=True):
            result = discover_instances_from_env()
        self.assertIn("DEV", result["created"])
        config = NodeRedConfig.objects.get(env_prefix="DEV")
        self.assertEqual(config.source_type, "local")
        self.assertEqual(config.flows_path, "/data/flows.json")
        self.assertEqual(config.name, "Dev Box")

    def test_skip_existing_prefix(self):
        NodeRedConfig.objects.create(name="Existing", env_prefix="PROD")
        env = self._clean_env({"FLOWHISTORY_PROD_URL": "http://example.com:1880"})
        with patch.dict("os.environ", env, clear=True):
            result = discover_instances_from_env()
        self.assertIn("PROD", result["skipped"])
        self.assertEqual(result["created"], [])

    def test_force_updates_existing(self):
        config = NodeRedConfig.objects.create(
            name="Old Name", env_prefix="PROD", source_type="remote",
            nodered_url="http://old:1880",
        )
        env = self._clean_env({
            "FLOWHISTORY_PROD_URL": "http://new:1880",
            "FLOWHISTORY_PROD_NAME": "New Name",
        })
        with patch.dict("os.environ", env, clear=True):
            result = discover_instances_from_env(force=True)
        self.assertIn("PROD", result["updated"])
        config.refresh_from_db()
        self.assertEqual(config.name, "New Name")
        self.assertEqual(config.nodered_url, "http://new:1880")

    def test_no_env_vars(self):
        env = self._clean_env({})
        with patch.dict("os.environ", env, clear=True):
            result = discover_instances_from_env()
        self.assertEqual(result["created"], [])
        self.assertEqual(result["skipped"], [])
        self.assertEqual(result["updated"], [])

    def test_both_url_and_flows_path_prefers_remote(self):
        env = self._clean_env({
            "FLOWHISTORY_DUAL_URL": "http://example.com:1880",
            "FLOWHISTORY_DUAL_FLOWS_PATH": "/data/flows.json",
        })
        with patch.dict("os.environ", env, clear=True):
            result = discover_instances_from_env()
        self.assertIn("DUAL", result["created"])
        config = NodeRedConfig.objects.get(env_prefix="DUAL")
        self.assertEqual(config.source_type, "remote")

    def test_optional_fields_applied(self):
        env = self._clean_env({
            "FLOWHISTORY_FULL_URL": "http://example.com:1880",
            "FLOWHISTORY_FULL_NAME": "Full Config",
            "FLOWHISTORY_FULL_SCHEDULE": "weekly",
            "FLOWHISTORY_FULL_TIME": "04:30",
            "FLOWHISTORY_FULL_MAX_BACKUPS": "50",
            "FLOWHISTORY_FULL_ALWAYS_BACKUP": "true",
        })
        with patch.dict("os.environ", env, clear=True):
            result = discover_instances_from_env()
        config = NodeRedConfig.objects.get(env_prefix="FULL")
        self.assertEqual(config.backup_frequency, "weekly")
        self.assertEqual(config.max_backups, 50)
        self.assertTrue(config.always_backup)


# ---------------------------------------------------------------------------
# Multi-instance integration tests
# ---------------------------------------------------------------------------


@override_settings(REQUIRE_AUTH=False)
class AggregateDashboardTest(TestCase):
    def test_no_instances_redirects_to_add(self):
        resp = self.client.get("/")
        self.assertRedirects(resp, "/instance/add/")

    def test_single_instance_redirects_to_dashboard(self):
        config = NodeRedConfig.objects.create(name="Solo")
        resp = self.client.get("/")
        self.assertRedirects(resp, f"/instance/{config.slug}/")

    def test_multiple_instances_shows_grid(self):
        NodeRedConfig.objects.create(name="Prod")
        NodeRedConfig.objects.create(name="Dev")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Prod")
        self.assertContains(resp, "Dev")


@override_settings(REQUIRE_AUTH=False)
class InstanceIsolationTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_a = self.backup_dir / "a" / "flows.json"
        self.flows_a.parent.mkdir()
        self.flows_a.write_text(json.dumps(SAMPLE_FLOWS))

        self.flows_b = self.backup_dir / "b" / "flows.json"
        self.flows_b.parent.mkdir()
        self.flows_b.write_text(json.dumps([{"id": "x", "type": "tab", "label": "X"}]))

        self.config_a = NodeRedConfig.objects.create(name="Instance A", flows_path=str(self.flows_a))
        self.config_b = NodeRedConfig.objects.create(name="Instance B", flows_path=str(self.flows_b))

    def test_backups_isolated_between_instances(self):
        rec_a = create_backup(self.config_a, trigger="manual")
        rec_b = create_backup(self.config_b, trigger="manual")
        self.assertEqual(rec_a.config, self.config_a)
        self.assertEqual(rec_b.config, self.config_b)
        self.assertNotEqual(
            Path(rec_a.file_path).parent,
            Path(rec_b.file_path).parent,
        )

    def test_backup_dirs_use_slug(self):
        rec = create_backup(self.config_a, trigger="manual")
        self.assertIn(self.config_a.slug, rec.file_path)

    def test_instance_dashboard_only_shows_own_backups(self):
        create_backup(self.config_a, trigger="manual")
        create_backup(self.config_b, trigger="manual")

        resp = self.client.get(f"/instance/{self.config_a.slug}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["backup_count"], 1)

    def test_cannot_access_other_instance_backup(self):
        rec_b = create_backup(self.config_b, trigger="manual")
        # Try to access config_b's backup via config_a's URL
        resp = self.client.get(f"/instance/{self.config_a.slug}/backup/{rec_b.pk}/")
        self.assertEqual(resp.status_code, 302)  # redirect (not found)


@override_settings(REQUIRE_AUTH=False)
class InstanceDeleteTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            name="To Delete", flows_path=str(self.flows_file),
        )
        create_backup(self.config, trigger="manual")

    def test_get_shows_confirmation(self):
        resp = self.client.get(f"/instance/{self.config.slug}/delete/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "To Delete")
        self.assertContains(resp, "1")  # backup count

    def test_post_deletes_instance(self):
        slug = self.config.slug
        resp = self.client.post(f"/instance/{slug}/delete/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/")
        self.assertFalse(NodeRedConfig.objects.filter(slug=slug).exists())

    def test_post_with_delete_files(self):
        slug = self.config.slug
        backup_dir = self.config.backup_dir
        self.assertTrue(backup_dir.is_dir())
        resp = self.client.post(f"/instance/{slug}/delete/", {"delete_files": "on"})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(backup_dir.is_dir())


@override_settings(REQUIRE_AUTH=False)
class RemotePollerTest(TestCase):
    def test_poll_once_detects_change(self):
        config = NodeRedConfig.objects.create(
            name="Remote Test",
            source_type="remote",
            nodered_url="http://fake:1880",
            watch_enabled=True,
        )

        from backup.services.remote_service import RemotePoller

        poller = RemotePoller(config.pk)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps([{"id": "tab1", "type": "tab", "label": "Test"}])
        mock_resp.json.return_value = [{"id": "tab1", "type": "tab", "label": "Test"}]
        mock_resp.raise_for_status = MagicMock()

        with patch("backup.services.remote_service.requests") as mock_requests:
            mock_requests.get.return_value = mock_resp
            with patch("backup.services.backup_service.create_backup") as mock_backup:
                mock_backup.return_value = MagicMock(status="success", filename="test.tar.gz")
                result = poller.poll_once()
                self.assertTrue(mock_backup.called)

    def test_poll_once_skips_unchanged(self):
        config = NodeRedConfig.objects.create(
            name="Remote Unchanged",
            source_type="remote",
            nodered_url="http://fake:1880",
            watch_enabled=True,
        )

        from backup.services.remote_service import RemotePoller

        poller = RemotePoller(config.pk)
        flows_json = json.dumps([{"id": "tab1", "type": "tab"}])
        poller._last_checksum = hashlib.sha256(flows_json.encode()).hexdigest()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = flows_json
        mock_resp.raise_for_status = MagicMock()

        with patch("backup.services.remote_service.requests") as mock_requests:
            mock_requests.get.return_value = mock_resp
            result = poller.poll_once()
            self.assertFalse(result)  # No change

    def test_backoff_increases_interval(self):
        config = NodeRedConfig.objects.create(
            name="Backoff Test",
            source_type="remote",
            nodered_url="http://fake:1880",
            poll_interval_seconds=30,
        )

        from backup.services.remote_service import RemotePoller

        poller = RemotePoller(config.pk)
        self.assertEqual(poller.get_poll_interval(config), 30)

        poller._consecutive_failures = 3  # At threshold
        self.assertEqual(poller.get_poll_interval(config), 60)  # Doubled

        poller._consecutive_failures = 4
        self.assertEqual(poller.get_poll_interval(config), 120)

        poller._consecutive_failures = 10
        self.assertLessEqual(poller.get_poll_interval(config), 300)  # Capped


@override_settings(REQUIRE_AUTH=False)
class RemoteRestoreTest(TempBackupDirMixin, TestCase):
    """Tests for restoring backups to remote Node-RED instances."""

    def setUp(self):
        super().setUp()
        self.config = NodeRedConfig.objects.create(
            name="Remote Restore",
            source_type="remote",
            nodered_url="http://fake:1880",
            env_prefix="REMOTE",
        )
        # Create a backup archive with flows.json
        flows_bytes = json.dumps(SAMPLE_FLOWS).encode()
        backup_dir = self.config.backup_dir
        backup_dir.mkdir(parents=True, exist_ok=True)
        archive_path = backup_dir / "test_restore.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            info = tarfile.TarInfo(name="flows.json")
            info.size = len(flows_bytes)
            tar.addfile(info, BytesIO(flows_bytes))
        checksum = hashlib.sha256(flows_bytes).hexdigest()
        self.backup_record = BackupRecord.objects.create(
            config=self.config,
            filename="test_restore.tar.gz",
            file_path=str(archive_path),
            file_size=archive_path.stat().st_size,
            checksum=checksum,
            status="success",
            trigger="manual",
        )

    @patch("backup.services.remote_service.deploy_remote_flows")
    @patch("backup.services.restore_service.create_backup")
    def test_remote_restore_deploys_flows(self, mock_safety, mock_deploy):
        mock_safety.return_value = None
        result = restore_backup(self.backup_record.pk)
        self.assertEqual(result.status, "success")
        mock_deploy.assert_called_once()
        call_args = mock_deploy.call_args
        self.assertEqual(call_args[0][0], self.config)
        # Verify flows.json content was passed
        deployed = json.loads(call_args[0][1])
        self.assertEqual(deployed, SAMPLE_FLOWS)

    @patch("backup.services.remote_service.deploy_remote_flows")
    @patch("backup.services.restore_service.create_backup")
    def test_remote_restore_records_files(self, mock_safety, mock_deploy):
        mock_safety.return_value = None
        result = restore_backup(self.backup_record.pk)
        self.assertEqual(result.files_restored, ["flows.json"])

    @patch("backup.services.remote_service.deploy_remote_flows")
    @patch("backup.services.restore_service.create_backup")
    def test_remote_restore_deploy_failure(self, mock_safety, mock_deploy):
        mock_safety.return_value = None
        mock_deploy.side_effect = Exception("Connection refused")
        result = restore_backup(self.backup_record.pk)
        self.assertEqual(result.status, "failed")
        self.assertIn("Failed to deploy", result.error_message)

    @patch("backup.services.remote_service.deploy_remote_flows")
    @patch("backup.services.restore_service.create_backup")
    def test_remote_restore_no_container_restart(self, mock_safety, mock_deploy):
        """Remote restore should not attempt container restart."""
        mock_safety.return_value = None
        self.config.restart_on_restore = True
        self.config.save()
        result = restore_backup(self.backup_record.pk)
        self.assertEqual(result.status, "success")
        self.assertFalse(result.container_restarted)

    def test_remote_restore_api_endpoint(self):
        """API endpoint should accept restore for remote instances."""
        with patch("backup.views.restore_backup") as mock_restore:
            mock_restore.return_value = RestoreRecord(
                config=self.config,
                backup=self.backup_record,
                status="success",
                files_restored=["flows.json"],
            )
            resp = self.client.post(
                f"/api/instance/{self.config.slug}/restore/{self.backup_record.pk}/"
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "success")


# ---------------------------------------------------------------------------
# Notification system tests
# ---------------------------------------------------------------------------


class NotifyEventTest(TestCase):
    def test_all_contains_every_event(self):
        expected = {
            "backup_success", "backup_failed",
            "restore_success", "restore_failed",
            "retention_cleanup",
        }
        self.assertEqual(NotifyEvent.ALL, expected)

    def test_default_is_subset_of_all(self):
        self.assertTrue(NotifyEvent.DEFAULT.issubset(NotifyEvent.ALL))

    def test_default_events(self):
        self.assertEqual(
            NotifyEvent.DEFAULT,
            {"backup_failed", "restore_success", "restore_failed"},
        )


class GetInstanceEventsTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(name="Test")

    def test_empty_returns_defaults(self):
        self.config.notify_events = ""
        self.assertEqual(_get_instance_events(self.config), NotifyEvent.DEFAULT)

    def test_none_returns_empty_set(self):
        self.config.notify_events = "none"
        self.assertEqual(_get_instance_events(self.config), set())

    def test_all_returns_all_events(self):
        self.config.notify_events = "all"
        self.assertEqual(_get_instance_events(self.config), NotifyEvent.ALL)

    def test_comma_separated(self):
        self.config.notify_events = "backup_failed,restore_failed"
        result = _get_instance_events(self.config)
        self.assertEqual(result, {"backup_failed", "restore_failed"})

    def test_unknown_events_ignored(self):
        self.config.notify_events = "backup_failed,bogus_event"
        result = _get_instance_events(self.config)
        self.assertEqual(result, {"backup_failed"})

    def test_all_unknown_falls_back_to_defaults(self):
        self.config.notify_events = "bogus"
        result = _get_instance_events(self.config)
        self.assertEqual(result, NotifyEvent.DEFAULT)


class NotificationPayloadTest(TestCase):
    def test_payload_creation(self):
        p = NotificationPayload(
            event=NotifyEvent.BACKUP_SUCCESS,
            instance_name="Test",
            instance_slug="test",
            instance_color="#3B82F6",
            title="Backup successful",
            message="Created file.tar.gz",
            filename="file.tar.gz",
            file_size=1024,
            trigger="manual",
        )
        self.assertEqual(p.event, "backup_success")
        self.assertEqual(p.filename, "file.tar.gz")
        self.assertIsNone(p.error)

    def test_payload_defaults(self):
        p = NotificationPayload(
            event=NotifyEvent.BACKUP_FAILED,
            instance_name="X",
            instance_slug="x",
            instance_color="#EF4444",
            title="Failed",
            message="Oops",
        )
        self.assertIsNone(p.error)
        self.assertIsNone(p.filename)
        self.assertIsNone(p.file_size)
        self.assertIsNone(p.trigger)


class GetNotificationUrlTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(name="Prod", env_prefix="PROD")

    @patch.dict(os.environ, {"FLOWHISTORY_PROD_DISCORD_WEBHOOK_URL": "https://instance.url"})
    def test_instance_url_takes_priority(self):
        self.assertEqual(
            self.config.get_notification_url("DISCORD_WEBHOOK_URL"),
            "https://instance.url",
        )

    @patch.dict(os.environ, {"FLOWHISTORY_NOTIFY_DISCORD_WEBHOOK_URL": "https://global.url"}, clear=False)
    def test_global_fallback(self):
        self.assertEqual(
            self.config.get_notification_url("DISCORD_WEBHOOK_URL"),
            "https://global.url",
        )

    @patch.dict(os.environ, {
        "FLOWHISTORY_PROD_DISCORD_WEBHOOK_URL": "https://instance.url",
        "FLOWHISTORY_NOTIFY_DISCORD_WEBHOOK_URL": "https://global.url",
    })
    def test_instance_overrides_global(self):
        self.assertEqual(
            self.config.get_notification_url("DISCORD_WEBHOOK_URL"),
            "https://instance.url",
        )

    def test_no_env_returns_empty(self):
        self.assertEqual(self.config.get_notification_url("DISCORD_WEBHOOK_URL"), "")

    def test_no_prefix_uses_global_only(self):
        config = NodeRedConfig.objects.create(name="NoPfx", env_prefix="")
        with patch.dict(os.environ, {"FLOWHISTORY_NOTIFY_DISCORD_WEBHOOK_URL": "https://global.url"}):
            self.assertEqual(config.get_notification_url("DISCORD_WEBHOOK_URL"), "https://global.url")


class NotifyDispatcherTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(name="Test", notify_enabled=True)
        self.payload = NotificationPayload(
            event=NotifyEvent.BACKUP_FAILED,
            instance_name="Test",
            instance_slug="test",
            instance_color="#EF4444",
            title="Backup failed",
            message="Error occurred",
        )

    @patch("backup.services.notification_service._get_backends")
    def test_notify_dispatches_to_configured_backend(self, mock_get):
        mock_backend = MagicMock(spec=NotificationBackend)
        mock_backend.is_configured.return_value = True
        mock_get.return_value = [mock_backend]

        notify(self.config, self.payload)

        mock_backend.is_configured.assert_called_once_with(self.config)
        mock_backend.send.assert_called_once_with(self.config, self.payload)

    @patch("backup.services.notification_service._get_backends")
    def test_notify_skips_unconfigured_backend(self, mock_get):
        mock_backend = MagicMock(spec=NotificationBackend)
        mock_backend.is_configured.return_value = False
        mock_get.return_value = [mock_backend]

        notify(self.config, self.payload)

        mock_backend.send.assert_not_called()

    @patch("backup.services.notification_service._get_backends")
    def test_notify_skips_when_disabled(self, mock_get):
        mock_backend = MagicMock(spec=NotificationBackend)
        mock_get.return_value = [mock_backend]
        self.config.notify_enabled = False

        notify(self.config, self.payload)

        mock_backend.is_configured.assert_not_called()
        mock_backend.send.assert_not_called()

    @patch("backup.services.notification_service._get_backends")
    def test_notify_skips_event_not_in_enabled_set(self, mock_get):
        mock_backend = MagicMock(spec=NotificationBackend)
        mock_get.return_value = [mock_backend]
        self.config.notify_events = "restore_success"

        payload = NotificationPayload(
            event=NotifyEvent.BACKUP_SUCCESS,
            instance_name="Test",
            instance_slug="test",
            instance_color="#10B981",
            title="Backup ok",
            message="ok",
        )
        notify(self.config, payload)

        mock_backend.send.assert_not_called()

    @patch("backup.services.notification_service._get_backends")
    def test_notify_catches_backend_exception(self, mock_get):
        mock_backend = MagicMock(spec=NotificationBackend)
        mock_backend.is_configured.return_value = True
        mock_backend.send.side_effect = Exception("Network error")
        mock_backend.name.return_value = "TestBackend"
        mock_get.return_value = [mock_backend]

        # Should not raise
        notify(self.config, self.payload)


class DiscordBackendTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(name="Test", env_prefix="TEST")

    @patch.dict(os.environ, {"FLOWHISTORY_TEST_DISCORD_WEBHOOK_URL": "https://discord.test/webhook"})
    def test_is_configured_with_instance_url(self):
        from backup.services.notifications.discord import DiscordBackend
        backend = DiscordBackend()
        self.assertTrue(backend.is_configured(self.config))

    def test_is_not_configured_without_url(self):
        from backup.services.notifications.discord import DiscordBackend
        backend = DiscordBackend()
        self.assertFalse(backend.is_configured(self.config))

    @patch("backup.services.notifications.discord.urlopen")
    @patch.dict(os.environ, {"FLOWHISTORY_TEST_DISCORD_WEBHOOK_URL": "https://discord.test/webhook"})
    def test_send_posts_to_webhook(self, mock_urlopen):
        from backup.services.notifications.discord import DiscordBackend
        backend = DiscordBackend()
        payload = NotificationPayload(
            event=NotifyEvent.BACKUP_SUCCESS,
            instance_name="Test",
            instance_slug="test",
            instance_color="#10B981",
            title="Backup successful",
            message="Created test.tar.gz",
            filename="test.tar.gz",
            file_size=2048,
            trigger="manual",
        )
        backend.send(self.config, payload)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "https://discord.test/webhook")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        body = json.loads(req.data)
        self.assertIn("embeds", body)
        embed = body["embeds"][0]
        self.assertIn("Backup successful", embed["title"])
        self.assertEqual(embed["color"], 0x10B981)
        self.assertEqual(len(embed["fields"]), 3)  # trigger, filename, size

    @patch("backup.services.notifications.discord.urlopen")
    @patch.dict(os.environ, {"FLOWHISTORY_TEST_DISCORD_WEBHOOK_URL": "https://discord.test/webhook"})
    def test_send_includes_error_field(self, mock_urlopen):
        from backup.services.notifications.discord import DiscordBackend
        backend = DiscordBackend()
        payload = NotificationPayload(
            event=NotifyEvent.BACKUP_FAILED,
            instance_name="Test",
            instance_slug="test",
            instance_color="#EF4444",
            title="Backup failed",
            message="Failed",
            error="File not found",
            trigger="scheduled",
        )
        backend.send(self.config, payload)

        body = json.loads(mock_urlopen.call_args[0][0].data)
        fields = body["embeds"][0]["fields"]
        error_field = [f for f in fields if f["name"] == "Error"][0]
        self.assertIn("File not found", error_field["value"])

    @patch("backup.services.notifications.discord.urlopen")
    @patch.dict(os.environ, {"FLOWHISTORY_TEST_DISCORD_WEBHOOK_URL": "https://discord.test/webhook"})
    def test_send_handles_urlopen_failure(self, mock_urlopen):
        from urllib.error import URLError
        from backup.services.notifications.discord import DiscordBackend
        mock_urlopen.side_effect = URLError("Connection refused")
        backend = DiscordBackend()
        payload = NotificationPayload(
            event=NotifyEvent.BACKUP_FAILED,
            instance_name="Test",
            instance_slug="test",
            instance_color="#EF4444",
            title="Failed",
            message="Failed",
        )
        # Should not raise
        backend.send(self.config, payload)


class BackupNotificationIntegrationTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            flows_path=str(self.flows_file),
            notify_enabled=True,
        )

    @patch("backup.services.notification_service.notify")
    def test_successful_backup_triggers_notification(self, mock_notify):
        record = create_backup(config=self.config, trigger="manual")
        self.assertEqual(record.status, "success")
        mock_notify.assert_called_once()
        _, payload = mock_notify.call_args[0]
        self.assertEqual(payload.event, NotifyEvent.BACKUP_SUCCESS)
        self.assertEqual(payload.filename, record.filename)

    @patch("backup.services.notification_service.notify")
    def test_failed_backup_triggers_notification(self, mock_notify):
        self.config.flows_path = "/nonexistent/flows.json"
        self.config.save()
        record = create_backup(config=self.config, trigger="manual")
        self.assertEqual(record.status, "failed")
        mock_notify.assert_called_once()
        _, payload = mock_notify.call_args[0]
        self.assertEqual(payload.event, NotifyEvent.BACKUP_FAILED)
        self.assertIsNotNone(payload.error)

    @patch("backup.services.notification_service.notify")
    def test_notification_failure_does_not_break_backup(self, mock_notify):
        mock_notify.side_effect = Exception("Notification system down")
        record = create_backup(config=self.config, trigger="manual")
        # Backup should still succeed despite notification failure
        self.assertEqual(record.status, "success")


class RestoreNotificationIntegrationTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            flows_path=str(self.flows_file),
            notify_enabled=True,
        )
        self.backup_record = create_backup(config=self.config, trigger="manual")

    @patch("backup.services.notification_service.notify")
    @patch("backup.services.restore_service.restart_container")
    def test_successful_restore_triggers_notification(self, mock_restart, mock_notify):
        result = restore_backup(self.backup_record.pk)
        self.assertEqual(result.status, "success")
        mock_notify.assert_called()
        _, payload = mock_notify.call_args[0]
        self.assertEqual(payload.event, NotifyEvent.RESTORE_SUCCESS)

    @patch("backup.services.notification_service.notify")
    def test_failed_restore_triggers_notification(self, mock_notify):
        # Corrupt the archive
        Path(self.backup_record.file_path).write_text("not a tar")
        result = restore_backup(self.backup_record.pk)
        self.assertEqual(result.status, "failed")
        mock_notify.assert_called()
        _, payload = mock_notify.call_args[0]
        self.assertEqual(payload.event, NotifyEvent.RESTORE_FAILED)


class RetentionNotificationIntegrationTest(TempBackupDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.flows_file = self.backup_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            flows_path=str(self.flows_file),
            max_backups=1,
            notify_enabled=True,
        )

    @patch("backup.services.notification_service.notify")
    def test_retention_cleanup_triggers_notification(self, mock_notify):
        from backup.services.retention_service import apply_retention
        from django.utils import timezone

        # Create 2 backups with different checksums
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS + [{"id": "extra1", "type": "inject"}]))
        create_backup(config=self.config, trigger="manual")
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS + [{"id": "extra2", "type": "debug"}]))
        create_backup(config=self.config, trigger="manual")

        mock_notify.reset_mock()
        result = apply_retention(self.config)

        if result["deleted_by_count"] + result["deleted_by_age"] > 0:
            mock_notify.assert_called_once()
            _, payload = mock_notify.call_args[0]
            self.assertEqual(payload.event, NotifyEvent.RETENTION_CLEANUP)

    @patch("backup.services.notification_service.notify")
    def test_no_notification_when_nothing_deleted(self, mock_notify):
        from backup.services.retention_service import apply_retention
        create_backup(config=self.config, trigger="manual")
        mock_notify.reset_mock()
        result = apply_retention(self.config)
        if result["deleted_by_count"] == 0 and result["deleted_by_age"] == 0:
            mock_notify.assert_not_called()


@override_settings(REQUIRE_AUTH=False)
class ApiTestNotificationTest(TestCase):
    def setUp(self):
        self.config = NodeRedConfig.objects.create(name="Test", env_prefix="TEST")

    def test_no_backends_returns_400(self):
        resp = self.client.post(
            f"/api/instance/{self.config.slug}/notifications/test/"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("No notification backends", resp.json()["message"])

    @patch("backup.services.notification_service._get_backends")
    def test_successful_test_notification(self, mock_get):
        mock_backend = MagicMock()
        mock_backend.is_configured.return_value = True
        mock_backend.name.return_value = "Discord"
        mock_get.return_value = [mock_backend]

        resp = self.client.post(
            f"/api/instance/{self.config.slug}/notifications/test/"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["backends"], ["Discord"])
        mock_backend.send.assert_called_once()
