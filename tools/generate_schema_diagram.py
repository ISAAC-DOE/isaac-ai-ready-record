#!/usr/bin/env python3
"""
Generate the Schema-Architecture mermaid diagram FROM the schema JSON.

The diagram in the wiki rotted within weeks of being hand-drawn (it was
missing the entire computation block by June). This generator makes the
schema file the single source of truth: run it after any schema change,
or with --check in CI to fail when the published diagram is stale.

Usage:
  python3 tools/generate_schema_diagram.py            # print mermaid
  python3 tools/generate_schema_diagram.py --check FILE  # exit 1 if FILE lacks current diagram
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((REPO / "schema" / "isaac_record_v1.json").read_text())

# Sub-objects worth their own class box (path -> display name)
DETAIL = {
    "context.electrochemistry": "Electrochemistry",
    "context.electrochemistry.reference_electrode": "ReferenceElectrode",
    "context.electrochemistry.potential_vs_RHE": "PotentialVsRHE",
    "context.transport": "Transport",
    "measurement.series": "Series",
    "computation.method": "Method",
    "computation.slab_model": "SlabModel",
    "sample.library": "Library",
}

OPEN_NAMESPACES = {"sample.composition", "sample.geometry", "system.configuration"}


def lock_mark(node, path=""):
    if path in OPEN_NAMESPACES:
        return " [OPEN namespace]"
    return " [locked]" if node.get("additionalProperties") is False else ""


def fields_of(node, limit=14):
    out = []
    for k, sub in list(node.get("properties", {}).items())[:limit]:
        t = sub.get("type", "object")
        if isinstance(t, list):
            t = "/".join(str(x) for x in t)
        if "enum" in sub:
            t = "enum"
        out.append(f"    +{t} {k}")
    if len(node.get("properties", {})) > limit:
        out.append(f"    +... {len(node['properties']) - limit} more")
    return out


def resolve(path):
    node = SCHEMA
    for part in path.split("."):
        node = node.get("properties", {}).get(part, {})
        if node.get("type") == "array":
            node = node.get("items", {})
    return node


def generate():
    L = ["classDiagram", "    direction LR", ""]
    blocks = ["sample", "context", "system", "measurement", "computation",
              "descriptors", "assets", "links", "timestamps", "attribution"]
    # Root: render every scalar/array root field that is NOT one of the object
    # blocks (those get their own nodes). Derived from the schema so a new root
    # field (e.g. tags) can never be silently missing from the diagram.
    L.append("    class Record {")
    for k, sub in SCHEMA.get("properties", {}).items():
        if k in blocks:
            continue
        typ = sub.get("type")
        if typ == "string":
            L.append(f"    +String {k}")
        elif typ == "array":
            L.append(f"    +String[] {k}")
    L.append("    }")
    L.append("")
    for b in blocks:
        node = SCHEMA["properties"].get(b)
        if node is None:
            continue
        if node.get("type") == "array":
            node_i = node.get("items", {})
            cname = b.capitalize()
            L.append(f'    class {cname}["{cname} (array){lock_mark(node_i)}"] {{')
            L.extend(fields_of(node_i))
        else:
            cname = b.capitalize()
            L.append(f'    class {cname}["{cname}{lock_mark(node, b)}"] {{')
            L.extend(fields_of(node))
        L.append("    }")
        L.append(f"    Record *-- {cname}")
        L.append("")
    for path, cname in DETAIL.items():
        node = resolve(path)
        if not node.get("properties"):
            continue
        parent = path.split(".")[0].capitalize()
        L.append(f'    class {cname}["{cname}{lock_mark(node, path)}"] {{')
        L.extend(fields_of(node))
        L.append("    }")
        L.append(f"    {parent} *-- {cname}")
        L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    diagram = generate()
    if len(sys.argv) > 2 and sys.argv[1] == "--check":
        published = Path(sys.argv[2]).read_text()
        if diagram not in published:
            print("STALE: published diagram does not match the schema. Regenerate with tools/generate_schema_diagram.py")
            sys.exit(1)
        print("diagram up to date")
        sys.exit(0)
    print(diagram)
