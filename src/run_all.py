"""Run the whole pipeline end to end. Stops on the first failing stage."""

import subprocess
import sys
import time

STAGES = [
    ("Generate synthetic data", "src/generate_data.py"),
    ("Load SQLite warehouse", "src/load_data.py"),
    ("Run SQL analysis views", "src/run_queries.py"),
    ("Estimate censored-demand lost revenue", "src/estimate_lost_revenue.py"),
    ("Validate against ground truth", "src/validate_estimates.py"),
    ("Build reorder-point model", "src/rop_model.py"),
    ("Write Excel workbook", "src/build_workbook.py"),
]


def main():
    t0 = time.time()
    for i, (label, script) in enumerate(STAGES, 1):
        print(f"\n{'=' * 78}\n[{i}/{len(STAGES)}] {label}\n{'=' * 78}", flush=True)
        r = subprocess.run([sys.executable, script],
                           env={**__import__("os").environ, "PYTHONPATH": "src"})
        if r.returncode != 0:
            print(f"\nFAILED at stage {i}: {script}")
            return r.returncode
    print(f"\n{'=' * 78}\nPipeline complete in {time.time() - t0:.1f}s")
    print("  Model : excel/dark_store_reorder_model.xlsx")
    print("  Memo  : docs/recommendation_memo.md")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
