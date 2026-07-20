import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = REPOSITORY_ROOT / "hooks"


class NoDangerousFlagsTests(unittest.TestCase):
    def test_no_hook_script_uses_dangerously_skip_permissions(self):
        offenders = []
        for pattern in ("*.sh", "*.py"):
            for script in sorted(HOOKS_DIR.glob(pattern)):
                for line in script.read_text(encoding="utf-8").splitlines():
                    if line.lstrip().startswith("#"):
                        continue
                    if "--dangerously-skip-permissions" in line:
                        offenders.append(script.name)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
