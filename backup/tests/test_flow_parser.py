import json

from django.test import TestCase

from backup.services.flow_parser import get_tab_names, parse_flows, parse_flows_file
from backup.tests.helpers import SAMPLE_FLOWS, TempBackupDirMixin


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
