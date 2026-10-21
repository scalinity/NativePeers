import os
from pathlib import Path
import subprocess
import sys
import unittest

from nativepeers.process import ancestry, identity, matches, signal_owned


class ProcessTests(unittest.TestCase):
    def test_identity_and_stale_start(self):
        record = identity(os.getpid()).json()
        self.assertTrue(matches(record))
        self.assertFalse(matches(dict(record, start="stale")))
        self.assertFalse(signal_owned(dict(record, start="stale")))
        self.assertEqual(ancestry()[0].pid, os.getpid())

    def test_lease_eof_ends_only_owned_child(self):
        read_fd, write_fd = os.pipe()
        probe = Path(__file__).resolve().parents[1]/"probes/seams.py"
        child = subprocess.Popen([sys.executable, str(probe), "lease-child", "--lease-fd", str(read_fd)],
                                 pass_fds=(read_fd,), close_fds=True, start_new_session=True)
        os.close(read_fd)
        self.assertIsNone(child.poll())
        os.close(write_fd)
        self.assertEqual(child.wait(timeout=3), 0)
        self.assertTrue(matches(identity(os.getpid())))


if __name__ == "__main__": unittest.main()
