import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import registered_tree


class RegisteredTree(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "study"
        self.source = self.root / "client/counter.c"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"archived client")
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        (self.root / "protocol.json").write_text(json.dumps({"sources_sha256": {"client/counter.c": digest}}))

    def git(self, *args):
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
             "-c", "commit.gpgSign=false", *args], check=True, capture_output=True,
        )

    def registered(self, root):
        with patch.object(registered_tree, "ROOT", root):
            return registered_tree.registered_root(self, "protocol.json")

    def test_full_history_restores_deleted_client(self):
        self.git("init")
        self.git("add", ".")
        self.git("commit", "-m", "Register source")
        self.git("rm", "client/counter.c")
        self.git("commit", "-m", "Remove client")
        restored = self.registered(self.root)
        self.assertEqual((restored / "client/counter.c").read_bytes(), b"archived client")
        shallow = self.root.parent / "shallow"
        self.git("clone", "--depth=1", self.root.as_uri(), str(shallow))
        with self.assertRaisesRegex(unittest.SkipTest, "fetch full history"):
            self.registered(shallow)

    def test_unregistered_digest_fails_in_full_history(self):
        self.source.write_bytes(b"different client")
        self.git("init")
        self.git("add", ".")
        self.git("commit", "-m", "Register different source")
        with self.assertRaisesRegex(AssertionError, "client/counter.c"):
            self.registered(self.root)

    def test_archive_and_broken_checkout_are_distinguished(self):
        self.source.write_bytes(b"different client")
        with self.assertRaisesRegex(unittest.SkipTest, "source archive"):
            self.registered(self.root)
        (self.root / ".git").write_text("gitdir: missing-checkout\n")
        with self.assertRaises(subprocess.CalledProcessError):
            self.registered(self.root)
