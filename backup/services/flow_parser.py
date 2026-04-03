"""Parse Node-RED flows.json into a tab/node structure."""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_flows_file(flows_path):
    """Read and parse a flows.json file from disk.

    Returns the parsed structure or None if the file can't be read.
    """
    path = Path(flows_path)
    if not path.is_file():
        logger.warning("flows.json not found at %s", flows_path)
        return None

    try:
        raw = path.read_text(encoding="utf-8")
        return parse_flows(json.loads(raw))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to parse flows.json: %s", e)
        return None


def parse_flows(nodes):
    """Parse a list of Node-RED node objects into a structured summary.

    Returns:
        {
            "tabs": [
                {"id": "...", "label": "...", "node_count": N},
                ...
            ],
            "subflows": [
                {"id": "...", "name": "...", "node_count": N},
                ...
            ],
            "config_nodes": N,
            "global_nodes": N,
            "total_nodes": N,
        }
    """
    if not isinstance(nodes, list):
        return _empty_summary()

    tabs = {}
    subflows = {}
    config_nodes = 0
    global_nodes = 0

    # First pass: identify tabs and subflows
    for node in nodes:
        node_type = node.get("type", "")
        node_id = node.get("id", "")

        if node_type == "tab":
            tabs[node_id] = {
                "id": node_id,
                "label": node.get("label", "Unnamed"),
                "node_count": 0,
            }
        elif node_type == "subflow":
            subflows[node_id] = {
                "id": node_id,
                "name": node.get("name", "Unnamed"),
                "node_count": 0,
            }

    # Second pass: count nodes per tab/subflow
    for node in nodes:
        node_type = node.get("type", "")
        if node_type in ("tab", "subflow"):
            continue

        parent_id = node.get("z", "")

        if parent_id in tabs:
            tabs[parent_id]["node_count"] += 1
        elif parent_id in subflows:
            subflows[parent_id]["node_count"] += 1
        elif parent_id:
            # Belongs to an unknown parent (subflow template node, etc.)
            if parent_id.startswith("subflow:"):
                pass  # subflow instance, counted elsewhere
            else:
                global_nodes += 1
        else:
            # No z field — config node or global
            if node_type.startswith("subflow:"):
                # subflow instance without a parent tab
                global_nodes += 1
            else:
                config_nodes += 1

    return {
        "tabs": sorted(tabs.values(), key=lambda t: t["label"]),
        "subflows": sorted(subflows.values(), key=lambda s: s["name"]),
        "config_nodes": config_nodes,
        "global_nodes": global_nodes,
        "total_nodes": len(nodes),
    }


def get_tab_names(flows_path):
    """Return a simple list of tab names from flows.json, for BackupRecord.tab_summary."""
    parsed = parse_flows_file(flows_path)
    if parsed is None:
        return []
    return [tab["label"] for tab in parsed["tabs"]]


def _empty_summary():
    return {
        "tabs": [],
        "subflows": [],
        "config_nodes": 0,
        "global_nodes": 0,
        "total_nodes": 0,
    }
