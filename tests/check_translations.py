"""Assert strings.json, en.json and fr.json share the same key set. Run: python tests/check_translations.py"""

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent / "custom_components" / "deciplus"


def keys(obj, prefix=""):
    if isinstance(obj, dict):
        return set().union(*(keys(v, f"{prefix}{k}.") for k, v in obj.items()))
    return {prefix.rstrip(".")}


files = [ROOT / "strings.json", ROOT / "translations" / "en.json", ROOT / "translations" / "fr.json"]
sets = {f.name: keys(json.loads(f.read_text(encoding="utf-8"))) for f in files}
ref = sets["strings.json"]
for name, s in sets.items():
    assert s == ref, f"{name}: missing {ref - s}, extra {s - ref}"
print("translations OK:", len(ref), "keys")
