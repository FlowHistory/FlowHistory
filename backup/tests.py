import hashlib
import json
import tarfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings

from backup.models import BackupRecord, NodeRedConfig
from backup.services.backup_service import (
    _diff_tab_summaries,
    create_backup,
)
from backup.services.flow_parser import (
    get_tab_names,
    parse_flows,
    parse_flows_file,
)

# ---------------------------------------------------------------------------
# Sample flows data
# ---------------------------------------------------------------------------

SAMPLE_FLOWS = [
    {"id": "tab1", "type": "tab", "label": "Home Automation"},
    {"id": "tab2", "type": "tab", "label": "API Endpoints"},
    {"id": "sf1", "type": "subflow", "name": "Error Handler"},
    {"id": "n1", "type": "inject", "z": "tab1"},
    {"id": "n2", "type": "debug", "z": "tab1"},
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
        self.assertEqual(tab_map["Home Automation"], 2)
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


class FlowParserFileTest(TestCase):
    def setUp(self):
        self.tmp_dir = Path(settings.BACKUP_DIR) / "_test_parser"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.flows_file = self.tmp_dir / "flows.json"

    def tearDown(self):
        if self.flows_file.exists():
            self.flows_file.unlink()
        if self.tmp_dir.exists():
            self.tmp_dir.rmdir()

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
    def test_tabs_added(self):
        prev = {"tabs": [{"id": "t1", "label": "A", "node_count": 3}]}
        curr = {
            "tabs": [
                {"id": "t1", "label": "A", "node_count": 3},
                {"id": "t2", "label": "B", "node_count": 5},
            ]
        }
        diff = _diff_tab_summaries(prev, curr)
        self.assertEqual(diff["tabs_added"], ["B"])
        self.assertEqual(diff["tabs_removed"], [])
        self.assertEqual(diff["tabs_modified"], [])

    def test_tabs_removed(self):
        prev = {
            "tabs": [
                {"id": "t1", "label": "A", "node_count": 3},
                {"id": "t2", "label": "B", "node_count": 5},
            ]
        }
        curr = {"tabs": [{"id": "t1", "label": "A", "node_count": 3}]}
        diff = _diff_tab_summaries(prev, curr)
        self.assertEqual(diff["tabs_removed"], ["B"])
        self.assertEqual(diff["tabs_added"], [])

    def test_tabs_modified(self):
        prev = {"tabs": [{"id": "t1", "label": "A", "node_count": 3}]}
        curr = {"tabs": [{"id": "t1", "label": "A", "node_count": 7}]}
        diff = _diff_tab_summaries(prev, curr)
        self.assertEqual(len(diff["tabs_modified"]), 1)
        self.assertEqual(diff["tabs_modified"][0]["nodes_before"], 3)
        self.assertEqual(diff["tabs_modified"][0]["nodes_after"], 7)

    def test_no_changes(self):
        tabs = {"tabs": [{"id": "t1", "label": "A", "node_count": 3}]}
        diff = _diff_tab_summaries(tabs, tabs)
        self.assertEqual(diff["tabs_added"], [])
        self.assertEqual(diff["tabs_removed"], [])
        self.assertEqual(diff["tabs_modified"], [])


class BackupServiceTest(TestCase):
    def setUp(self):
        self.tmp_dir = Path(settings.BACKUP_DIR) / "_test_svc"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.flows_file = self.tmp_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )

    def tearDown(self):
        # Clean up archives
        for f in Path(settings.BACKUP_DIR).glob("nodered_backup_*.tar.gz"):
            f.unlink()
        for f in self.tmp_dir.iterdir():
            f.unlink()
        self.tmp_dir.rmdir()

    def test_create_backup_success(self):
        record = create_backup(config=self.config, trigger="manual")
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "success")
        self.assertEqual(record.trigger, "manual")
        self.assertTrue(record.filename.startswith("nodered_backup_"))
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

    def test_includes_credentials_when_present(self):
        cred_file = self.tmp_dir / "flows_cred.json"
        cred_file.write_text('{"encrypted": true}')
        self.config.backup_credentials = True
        self.config.save()
        record = create_backup(config=self.config, trigger="manual")
        self.assertTrue(record.includes_credentials)
        with tarfile.open(record.file_path, "r:gz") as tar:
            self.assertIn("flows_cred.json", tar.getnames())
        cred_file.unlink()

    def test_excludes_credentials_when_disabled(self):
        cred_file = self.tmp_dir / "flows_cred.json"
        cred_file.write_text('{"encrypted": true}')
        self.config.backup_credentials = False
        self.config.save()
        record = create_backup(config=self.config, trigger="manual")
        self.assertFalse(record.includes_credentials)
        with tarfile.open(record.file_path, "r:gz") as tar:
            self.assertNotIn("flows_cred.json", tar.getnames())
        cred_file.unlink()


class ApiCreateBackupTest(TestCase):
    def setUp(self):
        self.tmp_dir = Path(settings.BACKUP_DIR) / "_test_api"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.flows_file = self.tmp_dir / "flows.json"
        self.flows_file.write_text(json.dumps(SAMPLE_FLOWS))
        self.config = NodeRedConfig.objects.create(
            pk=1,
            flows_path=str(self.flows_file),
        )

    def tearDown(self):
        for f in Path(settings.BACKUP_DIR).glob("nodered_backup_*.tar.gz"):
            f.unlink()
        for f in self.tmp_dir.iterdir():
            f.unlink()
        self.tmp_dir.rmdir()

    def test_post_creates_backup(self):
        resp = self.client.post("/api/backup/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("backup", data)
        self.assertIn("filename", data["backup"])

    def test_get_not_allowed(self):
        resp = self.client.get("/api/backup/")
        self.assertEqual(resp.status_code, 405)

    def test_missing_flows_returns_500(self):
        self.config.flows_path = "/nonexistent/flows.json"
        self.config.save()
        resp = self.client.post("/api/backup/")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["status"], "error")
