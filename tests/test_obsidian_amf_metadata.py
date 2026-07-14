import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from obsidian_amf import CLIENT_VERSION, client_metadata, client_source_root, load_amf_token
from obsidian_amf.credentials import MAX_TOKEN_BYTES, private_token
from obsidian_amf.metadata import SOURCE_FILES


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class ObsidianAmfMetadataTests(unittest.TestCase):
    def test_metadata_is_deterministic_location_independent_and_complete(self):
        first = client_metadata()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for relative_path in SOURCE_FILES:
                shutil.copy2(client_source_root() / relative_path, target / relative_path)
            second = client_metadata(target)

        self.assertEqual(first, second)
        self.assertEqual(first["schema"], "obsidian-amf-client/v1")
        self.assertEqual(first["version"], CLIENT_VERSION)
        self.assertEqual(first["modes"], ["standalone", "shadow", "active"])
        self.assertEqual(first["scheduledModes"], ["shadow"])
        self.assertNotIn("sourceRoot", first)

        files = first["source"]["files"]
        self.assertEqual([item["path"] for item in files], list(SOURCE_FILES))
        encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assertEqual(first["source"]["digest"], f"sha256:{hashlib.sha256(encoded).hexdigest()}")

    def test_source_digest_changes_with_installed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for relative_path in SOURCE_FILES:
                shutil.copy2(client_source_root() / relative_path, target / relative_path)
            before = client_metadata(target)
            with (target / "bridge.py").open("ab") as handle:
                handle.write(b"\n# parity change\n")
            after = client_metadata(target)
        self.assertNotEqual(before["source"]["digest"], after["source"]["digest"])

    def test_source_manifest_rejects_symlinked_files(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for relative_path in SOURCE_FILES:
                shutil.copy2(client_source_root() / relative_path, target / relative_path)
            (target / "bridge.py").unlink()
            (target / "bridge.py").symlink_to(client_source_root() / "bridge.py")
            with self.assertRaisesRegex(ValueError, "client_source_unsafe:bridge.py"):
                client_metadata(target)

    def test_installed_skill_wrapper_resolves_its_own_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "obsidian-memory"
            scripts = skill / "scripts"
            module = scripts / "obsidian_amf"
            module.mkdir(parents=True)
            shutil.copy2(REPOSITORY_ROOT / "skills/obsidian-memory/scripts/obsidian-memory", scripts / "obsidian-memory")
            for relative_path in SOURCE_FILES:
                shutil.copy2(client_source_root() / relative_path, module / relative_path)

            result = subprocess.run(
                [str(scripts / "obsidian-memory"), "client-source"],
                check=True,
                cwd="/",
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)
            expected_digest = client_metadata(module)["source"]["digest"]

        self.assertEqual(payload["sourceRoot"], str(module))
        self.assertEqual(payload["metadata"]["source"]["digest"], expected_digest)

    def test_openpack_attaches_the_complete_client_to_the_skill(self):
        manifest = json.loads((REPOSITORY_ROOT / "openpack.json").read_text(encoding="utf-8"))
        skill = next(item for item in manifest["provides"] if item["type"] == "skills")
        asset = skill["assets"][0]
        self.assertEqual(asset["from"], "obsidian_amf")
        self.assertEqual(asset["into"], "scripts/obsidian_amf")
        self.assertIn("*.py", asset["include"])
        self.assertEqual(set(SOURCE_FILES), {path.name for path in (REPOSITORY_ROOT / "obsidian_amf").glob("*.py")})

    def test_token_file_is_private_bounded_and_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            path.write_text("file-secret\n", encoding="utf-8")
            path.chmod(0o600)
            self.assertEqual(private_token(path), "file-secret")
            self.assertEqual(load_amf_token({
                "OBSIDIAN_AMF_TOKEN_FILE": str(path),
                "OBSIDIAN_AMF_TOKEN": "interactive-secret",
            }), "file-secret")
        self.assertEqual(load_amf_token({"OBSIDIAN_AMF_TOKEN": "interactive-secret"}), "interactive-secret")

    def test_token_file_rejects_unsafe_paths_permissions_links_and_size(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "private"
            private.write_text("secret", encoding="utf-8")
            private.chmod(0o600)

            public = root / "public"
            public.write_text("secret", encoding="utf-8")
            public.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "amf_token_file_unsafe"):
                private_token(public)

            symlink = root / "symlink"
            symlink.symlink_to(private)
            with self.assertRaisesRegex(ValueError, "amf_token_file_unsafe"):
                private_token(symlink)

            real_parent = root / "real-parent"
            real_parent.mkdir()
            nested = real_parent / "token"
            nested.write_text("secret", encoding="utf-8")
            nested.chmod(0o600)
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "amf_token_file_unsafe"):
                private_token(linked_parent / "token")

            hardlink = root / "hardlink"
            os.link(private, hardlink)
            with self.assertRaisesRegex(ValueError, "amf_token_file_unsafe"):
                private_token(private)

            oversized = root / "oversized"
            oversized.write_bytes(b"x" * (MAX_TOKEN_BYTES + 1))
            oversized.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "amf_token_file_unsafe"):
                private_token(oversized)

    def test_token_file_fails_closed_without_nofollow_support(self):
        with mock.patch.object(os, "O_NOFOLLOW", new=None):
            del os.O_NOFOLLOW
            with self.assertRaisesRegex(ValueError, "amf_token_file_unsafe"):
                private_token(Path("/does/not/matter"))

    def test_token_file_rejects_control_characters_and_repeated_line_endings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            for value in (
                b"secret\n\n",
                b"secret\r\n\r\n",
                b"secret\r",
                b"sec\x00ret",
                "secr\N{LATIN SMALL LETTER E WITH ACUTE}t".encode("utf-8"),
            ):
                path.write_bytes(value)
                path.chmod(0o600)
                with self.assertRaisesRegex(ValueError, "amf_token_file_invalid"):
                    private_token(path)

    def test_client_outputs_never_include_configured_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "token"
            path.write_text("never-print-this-token", encoding="utf-8")
            path.chmod(0o600)
            environment = {**os.environ, "OBSIDIAN_AMF_TOKEN_FILE": str(path)}
            for command in ("client-metadata", "client-source"):
                result = subprocess.run(
                    [str(REPOSITORY_ROOT / "skills/obsidian-memory/scripts/obsidian-memory"), command],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertNotIn("never-print-this-token", result.stdout)
                self.assertNotIn("never-print-this-token", result.stderr)

            vault = root / "vault"
            vault.mkdir()
            status = subprocess.run(
                [
                    str(REPOSITORY_ROOT / "skills/obsidian-memory/scripts/obsidian-memory"),
                    "status",
                    "--vault", str(vault),
                    "--vault-id", "vault-test",
                    "--mode", "active",
                    "--amf-url", "https://amf.invalid",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            payload = json.loads(status.stdout)
            self.assertEqual(payload["client"]["version"], CLIENT_VERSION)
            self.assertNotIn("never-print-this-token", status.stdout)
            self.assertNotIn("never-print-this-token", status.stderr)


if __name__ == "__main__":
    unittest.main()
