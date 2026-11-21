"""Validate the schema-2 tagged successor counter and all original trip invariants."""
from pathlib import Path
import runpy

HISTORICAL = Path(__file__).resolve().parents[2] / "gcd_controls/trip_probe/receipt.py"
validate_v1 = runpy.run_path(str(HISTORICAL))["validate"]


def validate(data, pid):
    if (type(data) is not dict or type(data.get("schema")) is not int
            or data["schema"] != 2 or type(data.get("sites")) is not list
            or len(data["sites"]) > 1024):
        raise ValueError("invalid history probe receipt")
    original_sites = []
    for site in data["sites"]:
        if type(site) is not dict:
            raise ValueError("invalid history probe site")
        count, changes = site.get("unpredicted_changes"), site.get("changes")
        if (type(count) is not int or type(changes) is not int
                or not 0 <= count <= changes or (changes and not count)):
            raise ValueError("invalid unpredicted change count")
        original_sites.append({k: v for k, v in site.items() if k != "unpredicted_changes"})
    validate_v1({**data, "schema": 1, "sites": original_sites}, pid)
    return data
