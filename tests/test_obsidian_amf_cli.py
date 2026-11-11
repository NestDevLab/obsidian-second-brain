import os
import tempfile
import unittest
from pathlib import Path

from obsidian_amf.__main__ import amf_token_from_environment

# The AMF loaders refuse symlinked path components, and macOS temp dirs sit under
# the /var -> /private/var symlink.
tempfile.tempdir = os.path.realpath(tempfile.gettempdir())


class AmfTokenEnvironmentTests(unittest.TestCase):
    def test_reads_a_bearer_from_a_regular_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            token_file = Path(temporary) / "token"
            token_file.write_text("token-value\n", encoding="utf-8")
            token_file.chmod(0o600)
            self.assertEqual(
                amf_token_from_environment({"OBSIDIAN_AMF_TOKEN_FILE": str(token_file)}),
                "token-value",
            )

    def test_protected_file_takes_precedence_over_environment_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            token_file = Path(temporary) / "token"
            token_file.write_text("token-value", encoding="utf-8")
            token_file.chmod(0o600)
            self.assertEqual(amf_token_from_environment({
                "OBSIDIAN_AMF_TOKEN": "environment-token",
                "OBSIDIAN_AMF_TOKEN_FILE": str(token_file),
            }), "token-value")

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "no no-follow support")
    def test_rejects_a_symlink_token_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "token"
            link = root / "token-link"
            target.write_text("token-value", encoding="utf-8")
            target.chmod(0o600)
            link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "amf_token_file_unsafe"):
                amf_token_from_environment({"OBSIDIAN_AMF_TOKEN_FILE": str(link)})
