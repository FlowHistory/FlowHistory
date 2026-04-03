"""Structural diff between Node-RED flow snapshots."""

import difflib
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
            "tabs_modified": [{...}],
            "subflows_added": [str],
            "subflows_removed": [str],
            "subflows_modified": [{...}],
        }
    """
    prev_groups = prev.get("groups", {})
    curr_groups = current.get("groups", {})
    prev_nodes = prev.get("nodes_by_id", {})
    curr_nodes = current.get("nodes_by_id", {})

    # Diff tabs
    prev_tabs = {t["id"]: t for t in prev.get("tabs", [])}
    curr_tabs = {t["id"]: t for t in current.get("tabs", [])}
    tabs_result = _diff_container_set(
        prev_tabs, curr_tabs, "label", prev_nodes, curr_nodes,
        prev_groups, curr_groups,
    )

    # Diff subflows
    prev_sfs = {s["id"]: s for s in prev.get("subflows", [])}
    curr_sfs = {s["id"]: s for s in current.get("subflows", [])}
    sfs_result = _diff_container_set(
        prev_sfs, curr_sfs, "name", prev_nodes, curr_nodes,
        prev_groups, curr_groups,
    )

    return {
        "tabs_added": tabs_result["added"],
        "tabs_removed": tabs_result["removed"],
        "tabs_modified": tabs_result["modified"],
        "subflows_added": sfs_result["added"],
        "subflows_removed": sfs_result["removed"],
        "subflows_modified": sfs_result["modified"],
    }


def _diff_container_set(prev_map, curr_map, label_key, prev_nodes, curr_nodes,
                        prev_groups, curr_groups):
    """Diff a set of containers (tabs or subflows)."""
    prev_ids = set(prev_map.keys())
    curr_ids = set(curr_map.keys())

    added = [curr_map[cid][label_key] for cid in (curr_ids - prev_ids)]
    removed = [prev_map[cid][label_key] for cid in (prev_ids - curr_ids)]
    modified = []

    for cid in prev_ids & curr_ids:
        if prev_nodes or curr_nodes:
            container_diff = _diff_container_nodes(
                cid, prev_nodes, curr_nodes, prev_groups, curr_groups,
            )
        else:
            container_diff = _diff_tab_counts(prev_map[cid], curr_map[cid])

        if container_diff is not None:
            container_diff["label"] = curr_map[cid][label_key]
            container_diff["nodes_before"] = prev_map[cid]["node_count"]
            container_diff["nodes_after"] = curr_map[cid]["node_count"]
            modified.append(container_diff)

    return {"added": added, "removed": removed, "modified": modified}


def _diff_tab_counts(prev_tab, curr_tab):
    """Backward-compat: detect modification by node count only."""
    if prev_tab["node_count"] != curr_tab["node_count"]:
        return {
            "nodes_added": [],
            "nodes_removed": [],
            "nodes_modified": [],
        }
    return None


def _diff_container_nodes(container_id, prev_nodes, curr_nodes,
                          prev_groups, curr_groups):
    """Compare individual nodes within a tab or subflow."""
    prev_tab = {nid: n for nid, n in prev_nodes.items() if n["z"] == container_id}
    curr_tab = {nid: n for nid, n in curr_nodes.items() if n["z"] == container_id}

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
            field_diffs = _field_diffs(prev_data, curr_data)
            if field_diffs:
                desc["changed_fields"] = [f["field"] for f in field_diffs]
                desc["field_diffs"] = field_diffs
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


def _field_diffs(prev_data, curr_data):
    """Return detailed per-field diffs between two node data dicts.

    Each entry: {"field": str, "diff": str} where diff is a unified diff
    for multi-line strings, or "old_value → new_value" for simple values.
    """
    all_keys = set(prev_data.keys()) | set(curr_data.keys())
    skip = {"id", "type"}
    result = []

    for key in sorted(all_keys - skip):
        old = prev_data.get(key)
        new = curr_data.get(key)
        if old == new:
            continue

        diff_text = _format_value_diff(key, old, new)
        result.append({"field": key, "diff": diff_text})

    return result


def _format_value_diff(field, old, new):
    """Produce a human-readable diff for a single field change."""
    old_str = _to_str(old)
    new_str = _to_str(new)

    # Use unified diff for multi-line strings
    if "\n" in old_str or "\n" in new_str:
        lines = list(difflib.unified_diff(
            old_str.splitlines(keepends=True),
            new_str.splitlines(keepends=True),
            fromfile=f"a/{field}",
            tofile=f"b/{field}",
            lineterm="",
        ))
        if lines:
            return "\n".join(lines)

    # Simple before → after for short values
    if old is None:
        return f"+ {new_str}"
    if new is None:
        return f"- {old_str}"
    return f"- {old_str}\n+ {new_str}"


def _to_str(value):
    """Convert a value to a string suitable for diffing."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2)


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
        {"tabs_added", "tabs_removed", "tabs_modified",
         "subflows_added", "subflows_removed", "subflows_modified",
         "prev", "current"}

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
            "subflows_added": [],
            "subflows_removed": [],
            "subflows_modified": [],
            "prev": prev,
            "current": current,
        }

    diff = diff_tab_summaries(prev, current)
    diff["prev"] = prev
    diff["current"] = current
    return diff
