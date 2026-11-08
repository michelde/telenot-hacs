#!/usr/bin/env python3
"""Render a captured core-discovery report with readable names, without TCP.

Preserve the original report and all raw metadata bytes. Derived name fields
and grouped inventory are added to a separate file for local inspection.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.telenot.protocol.model import PanelState  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    state = PanelState(report["state"]["identity"])
    if not state.restore(report["state"]):
        parser.error("Report contains an invalid state snapshot")
    report["state"] = state.export()
    report["source_report"] = str(args.report)
    output = args.report.with_stem(args.report.stem + "-readable")
    with output.open("x", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)
        file.write("\n")
    print(output)


if __name__ == "__main__":
    main()
