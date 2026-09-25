"""Guard: nothing personal from data/ may appear in files that get committed.

Runs with the other tests. It reads the private ledger (when present) and
fails if any account or loan number, or any client name, shows up in code
or docs that are pushed to GitHub.
"""
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = [*ROOT.glob("engine/*.py"), *ROOT.glob("engine/*.sh"), ROOT / "CLAUDE.md", ROOT / "PLAN.md",
          ROOT / "requirements.txt", ROOT / ".gitignore"]


class Privacy(unittest.TestCase):
    def test_no_ledger_identifiers_in_public_files(self):
        path = ROOT / "data" / "ledger.yaml"
        if not path.exists():
            self.skipTest("no private ledger in this checkout")
        ledger = yaml.safe_load(path.read_text()) or {}
        needles = set()
        for section in ("accounts", "debts", "reserves", "retirement"):
            for item in ledger.get(section) or []:
                for num in re.findall(r"\d{4,}", str(item.get("id", ""))):
                    if not re.fullmatch(r"(19|20)\d\d", num):  # skip years like 2024
                        needles.add(num)
        for section in ("receivables", "pipeline", "closed", "jobs"):
            for item in ledger.get(section) or []:
                client = str(item.get("client") or "").split(" (")[0].split(" /")[0].strip()
                if len(client) > 4:
                    needles.add(client)
        hits = [f"{f.relative_to(ROOT)}: {n}" for f in PUBLIC if f.exists()
                for n in sorted(needles) if n in f.read_text()]
        self.assertEqual(hits, [], "personal identifiers in files that get published")


if __name__ == "__main__":
    unittest.main()
