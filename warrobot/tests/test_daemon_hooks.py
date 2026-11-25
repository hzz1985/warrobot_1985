"""Guard: the daemon must keep every scheduled block.

A past rewrite silently dropped the nightly download/scan, which cost a day of
data and a missing email. These assertions fail the suite if that happens again.
"""
import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "run.py").read_text(encoding="utf-8")


class DaemonHooks(unittest.TestCase):
    def test_nightly_download_hook(self):
        self.assertIn("nightly_download()", SRC)
        self.assertIn("nightly download started", SRC)

    def test_nightly_scan_email_hook(self):
        self.assertIn("nightly_scan_and_email()", SRC)
        self.assertIn("nightly email sent", SRC)

    def test_auction_entry_hook(self):
        self.assertIn("auction entry", SRC)
        self.assertIn("preopen_retry_sec", SRC)

    def test_cancel_hooks(self):
        self.assertIn("cancel_if_not_locked_at", SRC)
        self.assertIn("cancel_unfilled_at", SRC)

    def test_sell_loop_hook(self):
        self.assertIn("sell_loop(dry=False)", SRC)

    def test_markers(self):
        self.assertIn("last_download.txt", SRC)
        self.assertIn("last_scan.txt", SRC)

    def test_watchdog_and_heartbeat(self):
        self.assertIn("WATCHDOG", SRC)
        self.assertIn("heartbeat", SRC)


    def test_ranking_policy(self):
        src = (Path(__file__).resolve().parents[1] / "bfomul_nightly.py").read_text(encoding="utf-8")
        self.assertIn("MIN_PREC", src)
        self.assertIn("MIN_SIG", src)
        self.assertIn("MAX_PICK", src)
        self.assertIn("rank_score", src)


if __name__ == "__main__":
    unittest.main()
