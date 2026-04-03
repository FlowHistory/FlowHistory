"""Structural diff between Node-RED flow snapshots."""

import json
import logging
import tarfile

from backup.services.flow_parser import parse_flows

logger = logging.getLogger(__name__)


def diff_tab_summaries(prev, current):
    """Compare two parsed flow structures and return a changes summary.

    Args:
        prev: Output of flow_parser.parse_flows for the previous snapshot.
        current: Output of flow_parser.parse_flows for the current snapshot.

    Returns:
        {
            "tabs_added": [str],
            "tabs_removed": [str],
            "tabs_modified": [
                {
                    "label": str,
                    "nodes_before": int,
                    "nodes_after": int,
                    "nodes_added": [{"type", "name"?, "group"?}],
                    "nodes_removed": [...],
                    "nodes_modified": [{..., "changed_fields": [str]}],
                }
            ],
        }
    """
    prev_tabs = {t["id"]: t for t in prev.get("tabs", [])}
    curr_tabs = {t["id"]: t for t in current.get("tabs", [])}

    prev_groups = prev.get("groups", {})
    curr_groups = current.get("groups", {})
    prev_nodes = prev.get("nodes_by_id", {})
    curr_nodes = current.get("nodes_by_id", {})

    prev_ids = set(prev_tabs.keys())
    curr_ids = set(curr_tabs.keys())

    added = [curr_tabs[tid]["label"] for tid in (curr_ids - prev_ids)]
    removed = [prev_tabs[tid]["label"] for tid in (prev_ids - curr_ids)]
    modified = []

    for tid in prev_ids & curr_ids:
        # If we have node-level data, do a detailed diff
        if prev_nodes or curr_nodes:
            tab_diff = _diff_tab_nodes(
                tid, prev_nodes, curr_nodes, prev_groups, curr_groups,
            )
        else:
            # Backward compat: fall back to count-only comparison
            tab_diff = _diff_tab_counts(prev_tabs[tid], curr_tabs[tid])

        if tab_diff is not None:
            tab_diff["label"] = curr_tabs[tid]["label"]
            tab_diff["nodes_before"] = prev_tabs[tid]["node_count"]
            tab_diff["nodes_after"] = curr_tabs[tid]["node_count"]
            modified.append(tab_diff)

    return {
        "tabs_added": added,
        "tabs_removed": removed,
        "tabs_modified": modified,
    }


def _diff_tab_counts(prev_tab, curr_tab):
    """Backward-compat: detect modification by node count only."""
    if prev_tab["node_count"] != curr_tab["node_count"]:
        return {
            "nodes_added": [],
            "nodes_removed": [],
            "nodes_modified": [],
        }
    return None


def _diff_tab_nodes(tab_id, prev_nodes, curr_nodes, prev_groups, curr_groups):
    """Compare individual nodes within a tab, returning detailed changes."""
    prev_tab = {nid: n for nid, n in prev_nodes.items() if n["z"] == tab_id}
    curr_tab = {nid: n for nid, n in curr_nodes.items() if n["z"] == tab_id}

    prev_nids = set(prev_tab.keys())
    curr_nids = set(curr_tab.keys())

    nodes_added = [
        _describe_node(curr_tab[nid], curr_groups)
        for nid in sorted(curr_nids - prev_nids)
    ]
    nodes_removed = [
        _describe_node(prev_tab[nid], prev_groups)
        for nid in sorted(prev_nids - curr_nids)
    ]
    nodes_modified = []
    for nid in sorted(prev_nids & curr_nids):
        prev_data = prev_tab[nid].get("_data", {})
        curr_data = curr_tab[nid].get("_data", {})
        if prev_data != curr_data:
            desc = _describe_node(curr_tab[nid], curr_groups)
            changed = _changed_fields(prev_data, curr_data)
            if changed:
                desc["changed_fields"] = changed
            nodes_modified.append(desc)

    if not nodes_added and not nodes_removed and not nodes_modified:
        return None

    return {
        "nodes_added": nodes_added,
        "nodes_removed": nodes_removed,
        "nodes_modified": nodes_modified,
    }


def _describe_node(node_detail, groups):
    """Build a compact description of a node for the changes summary."""
    desc = {"type": node_detail["type"]}
    name = node_detail.get("name", "")
    if name:
        desc["name"] = name
    gid = node_detail.get("g", "")
    if gid and gid in groups:
        group_name = groups[gid].get("name", "")
        if group_name:
            desc["group"] = group_name
    return desc


def _changed_fields(prev_data, curr_data):
    """Return list of field names that differ between two node data dicts.

    Skips ``id`` and ``type`` since those are already shown in the node
    description and rarely (if ever) change for a given node ID.
    """
    all_keys = set(prev_data.keys()) | set(curr_data.keys())
    skip = {"id", "type"}
    changed = []
    for key in sorted(all_keys - skip):
        if prev_data.get(key) != curr_data.get(key):
            changed.append(key)
    return changed


def parse_flows_from_archive(archive_path):
    """Extract and parse flows.json from a tar.gz backup archive.

    Args:
        archive_path: Path to the .tar.gz archive.

    Returns:
        Parsed flow structure (dict) or None if extraction fails.
    """
    with tarfile.open(archive_path, "r:gz") as tar:
        member = tar.getmember("flows.json")
        f = tar.extractfile(member)
        if f is None:
            return None
        data = json.loads(f.read())
        return parse_flows(data)


def diff_backup_archives(archive_path_a, archive_path_b):
    """Compare flows.json from two backup archives.

    Args:
        archive_path_a: Path to the older archive.
        archive_path_b: Path to the newer archive.

    Returns:
        Dict with diff results plus both parsed structures:
        {"tabs_added", "tabs_removed", "tabs_modified", "prev", "current"}

    Raises:
        FileNotFoundError: If either archive is missing.
        tarfile.TarError: If an archive is corrupt.
    """
    prev = parse_flows_from_archive(archive_path_a)
    current = parse_flows_from_archive(archive_path_b)

    if prev is None or current is None:
        return {
            "tabs_added": [],
            "tabs_removed": [],
            "tabs_modified": [],
            "prev": prev,
            "current": current,
        }

    diff = diff_tab_summaries(prev, current)
    diff["prev"] = prev
    diff["current"] = current
    return diff
