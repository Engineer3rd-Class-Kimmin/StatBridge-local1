"""Reconcile master counts with the refreshed source metadata CSVs."""
import csv
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
(ROOT / "evaluation_runs").mkdir(parents=True, exist_ok=True)
DATA = ROOT / "data/runtime_data/processed"
master_path = DATA / "bok_table_master.csv"
backup = ROOT / "evaluation_runs" / ("bok_table_master_before_counts_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv")
shutil.copy2(master_path, backup)
groups = {}
for kind in ("items", "classifications"):
    rows = defaultdict(list)
    with (DATA / f"bok_{kind}.csv").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f): rows[row.get("TBL_ID") or row["TBL_ID_BASE"]].append(row)
    groups[kind] = rows
with master_path.open(encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f); fields = reader.fieldnames; master = list(reader)
assert len(master) == 349
for row in master:
    tid = row["TBL_ID"]
    row["ITEM_COUNT"] = str(len(groups["items"][tid]))
    row["CLASS_ROW_COUNT"] = str(len(groups["classifications"][tid]))
with master_path.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows(master)
print("349 master rows reconciled; previous master retained")
