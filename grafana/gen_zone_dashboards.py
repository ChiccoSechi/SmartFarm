#!/usr/bin/env python3
"""Generate 3 per-zone dashboards from smartfarm.json, injecting into every Flux query a
`r.zone == "<Z>"` filter after range() (the filter runs in InfluxDB, no custom code at runtime)."""
import copy
import json
import os

# Paths relative to this script, so it is reproducible on any clone.
HERE = os.path.dirname(os.path.abspath(__file__))
DASH_DIR = os.path.join(HERE, "provisioning", "dashboards")
BASE = os.path.join(DASH_DIR, "smartfarm.json")

ZONES = ["A", "B", "C"]

# Navigation dropdown, shared with the overview/map generators.
from _common import NAV_LINK


def inject_zone_filter(query: str, zone: str) -> str:
    """Insert `|> filter(fn: (r) => r.zone == "<zone>")` after range() (index 2 of the Flux pipe split)."""
    sep = "\n  |> "
    parts = query.split(sep)
    if len(parts) < 2 or not parts[1].startswith("range("):
        # Defensive: if the shape is not the expected one, leave the query untouched.
        return query
    parts.insert(2, f'filter(fn: (r) => r.zone == "{zone}")')
    return sep.join(parts)


def main():
    with open(BASE, encoding="utf-8") as fh:
        base = json.load(fh)

    for zone in ZONES:
        d = copy.deepcopy(base)
        zl = zone.lower()
        d["uid"] = f"smartfarm-zone-{zl}"
        d["title"] = f"Smart Farm: Zone {zone}"
        d["tags"] = ["smartfarm", f"zone-{zone}"]
        d["id"] = None
        d["version"] = 1
        d["links"] = [copy.deepcopy(NAV_LINK)]

        for panel in d.get("panels", []):
            for t in panel.get("targets", []):
                if "query" in t:
                    t["query"] = inject_zone_filter(t["query"], zone)

        # Annotations (bucket "alerts") are filtered by zone too: events carry the zone tag, same injection as panels.
        for ann in d.get("annotations", {}).get("list", []):
            tgt = ann.get("target", {})
            if isinstance(tgt, dict) and "query" in tgt:
                tgt["query"] = inject_zone_filter(tgt["query"], zone)

        out = os.path.join(DASH_DIR, f"zone_{zl}.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print(f"wrote {out}  (uid={d['uid']}, title={d['title']})")


if __name__ == "__main__":
    main()
