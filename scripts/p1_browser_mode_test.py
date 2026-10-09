"""Phase 1 corrective pass — browser mode resolution + owner-browser safety.

Pure unit tests (no real browser). Verifies:
  * detection of the owner's running browser (found / running-without-port /
    running-with-port) using injected process listers + reachability probes;
  * decide_browser_mode routes named-browser + signed-in-site to owner_existing,
    generic browsing/YouTube and "your own browser" to genie_owned;
  * the executor NEVER launches a GENIE profile, closes or relaunches the owner's
    browser when owner_existing attachment is impossible;
  * a blocked owner_existing request is NOT silently downgraded to a logged-out
    GENIE profile on a later call;
  * shutdown DISCONNECTS from the owner's browser without killing it.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from browser.mode import (MODE_OWNER_EXISTING, MODE_GENIE_OWNED,
                          decide_browser_mode, detect_owner_browser)
from browser.service import BrowserService


def _running(cmdline, pid="12345"):
    return {"pid": pid, "cmdline": cmdline}


class ModeDetectionTests(unittest.TestCase):
    def test_not_running(self):
        info = detect_owner_browser("brave",
                                    lister=lambda: [], reach_probe=lambda p: False)
        self.assertFalse(info.get("found"))

    def test_running_without_debug_port_is_blocked(self):
        brave = ('"C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe" '
                 '--user-data-dir="C:\\Users\\ghostt\\AppData\\Local\\BraveSoftware\\'
                 'Brave-Browser\\User Data"')
        info = detect_owner_browser("brave", lister=lambda: [_running(brave)],
                                    reach_probe=lambda p: False)
        self.assertTrue(info.get("found"))
        self.assertFalse(info.get("attachable"))
        self.assertIn("owner_action", info)
        self.assertIn("remote-debugging port", info["owner_action"])
        self.assertIn("use your own browser", info["owner_action"])

    def test_running_with_reachable_debug_port_is_attachable(self):
        brave = ('"C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe" '
                 '--remote-debugging-port=9222 '
                 '--user-data-dir="C:\\Users\\ghostt\\AppData\\Local\\BraveSoftware\\'
                 'Brave-Browser\\User Data"')
        info = detect_owner_browser("brave", lister=lambda: [_running(brave)],
                                    reach_probe=lambda p: p == 9222,
                                    port_owner=lambda p: 12345)
        self.assertTrue(info.get("attachable"))
        self.assertEqual(info.get("debug_port"), 9222)
        self.assertEqual(info.get("pid"), 12345)
        self.assertIn("Brave-Browser\\User Data", info.get("user_data_dir", ""))


# ---------------------------------------------------- acceptance: identity safety
# Verifies the four scenarios from the BROWSER IDENTITY SAFETY CORRECTION:
#   (a) a real owner Brave is correctly identified and attachable;
#   (b) a GENIE-owned Brave is REJECTED (never mistaken for the owner's);
#   (c) an unrelated Chrome holding the debug port is REJECTED;
#   (d) an unidentifiable reachable CDP endpoint is REJECTED (owner_existing).
_OWNER_BRAVE = ('"C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe" '
                '--remote-debugging-port=9222 '
                '--user-data-dir="C:\\Users\\ghostt\\AppData\\Local\\BraveSoftware\\'
                'Brave-Browser\\User Data"')
_GENIE_BRAVE = ('"C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe" '
                '--remote-debugging-port=9222 '
                '--user-data-dir="E:\\G3\\GENIE\\browser-profile"')
_UNRELATED_CHROME = ('"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
                     '--remote-debugging-port=9222 '
                     '--user-data-dir="C:\\Users\\ghostt\\AppData\\Local\\Google\\'
                     'Chrome\\User Data"')


class IdentitySafetyTests(unittest.TestCase):
    def test_a_real_owner_brave_is_attachable(self):
        info = detect_owner_browser("brave", lister=lambda: [_running(_OWNER_BRAVE)],
                                    reach_probe=lambda p: p == 9222,
                                    port_owner=lambda p: 12345)
        self.assertTrue(info.get("found"))
        self.assertTrue(info.get("attachable"))
        self.assertEqual(info.get("debug_port"), 9222)
        self.assertIn("brave.exe", (info.get("exe") or "").lower())
        self.assertIn("Brave-Browser\\User Data", info.get("user_data_dir", ""))
        self.assertNotIn("genie_owned_conflict", info)
        self.assertNotIn("unidentifiable", info)

    def test_b_genie_owned_brave_is_rejected(self):
        info = detect_owner_browser("brave", lister=lambda: [_running(_GENIE_BRAVE)],
                                    reach_probe=lambda p: p == 9222,
                                    port_owner=lambda p: 9999)
        self.assertTrue(info.get("found"))
        self.assertFalse(info.get("attachable"))
        self.assertTrue(info.get("genie_owned_conflict"))
        self.assertIn("owner_action", info)
        self.assertIn("browser-profile", info.get("owner_action", ""))

    def test_c_unrelated_chrome_is_rejected(self):
        # Owner asked for Brave; only an unrelated Chrome holds port 9222.
        info = detect_owner_browser("brave", lister=lambda: [_running(_UNRELATED_CHROME)],
                                    reach_probe=lambda p: p == 9222,
                                    port_owner=lambda p: 7777)
        self.assertFalse(info.get("attachable"))
        # The reachable port must NOT be claimed as the owner's Brave.
        self.assertFalse(info.get("debug_port") == 9222 and info.get("attachable"))

    def test_d_unidentifiable_endpoint_is_rejected(self):
        # A port is reachable but no matching owner browser process exists at all.
        info = detect_owner_browser("brave", lister=lambda: [],
                                    reach_probe=lambda p: p == 9222,
                                    port_owner=lambda p: None)
        self.assertFalse(info.get("attachable"))
        self.assertTrue(info.get("unidentifiable"))
        self.assertIn("owner_action", info)
        self.assertIn("will NOT", info.get("owner_action", ""))

    def test_port_owned_by_different_process_is_rejected(self):
        # A brave.exe declares port 9222 but another process actually owns it.
        brave_other = ('"C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe" '
                       '--remote-debugging-port=9222 '
                       '--user-data-dir="C:\\Users\\ghostt\\AppData\\Local\\BraveSoftware\\'
                       'Brave-Browser\\User Data"')
        info = detect_owner_browser("brave", lister=lambda: [_running(brave_other)],
                                    reach_probe=lambda p: p == 9222,
                                    port_owner=lambda p: 424242)
        self.assertFalse(info.get("attachable"))
        self.assertTrue(info.get("debug_port") == 9222)


class DecideModeTests(unittest.TestCase):
    def test_brave_chatgpt_is_owner_existing(self):
        self.assertEqual(
            decide_browser_mode("Brave browser mein ChatGPT kholo", "brave", "chatgpt"),
            MODE_OWNER_EXISTING)

    def test_openai_image_is_owner_existing(self):
        self.assertEqual(
            decide_browser_mode("open chatgpt in brave and draw a cat", "brave", "openai"),
            MODE_OWNER_EXISTING)

    def test_youtube_is_genie_owned(self):
        self.assertEqual(
            decide_browser_mode("play a song on youtube", "", "youtube"),
            MODE_GENIE_OWNED)

    def test_explicit_own_browser_is_genie_owned(self):
        self.assertEqual(
            decide_browser_mode("use your own browser to open chatgpt", "brave", "chatgpt"),
            MODE_GENIE_OWNED)

    def test_explicit_my_browser_is_owner_existing(self):
        self.assertEqual(
            decide_browser_mode("open my brave and check gmail", "brave", "gmail"),
            MODE_OWNER_EXISTING)


class ExecutorModeTests(unittest.TestCase):
    def setUp(self):
        self.svc = BrowserService()

    def test_owner_existing_not_running_never_launches(self):
        exe = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
        with mock.patch("browser.service.cdp.find_browser_for", return_value=exe), \
             mock.patch("browser.service.detect_owner_browser",
                               return_value={"found": False}), \
             mock.patch.object(self.svc, "_connect_page") as cp, \
             mock.patch("browser.service.cdp.launch", side_effect=AssertionError(
                 "cdp.launch must NOT be called for owner_existing")):
            res = self.svc._ensure_requested_browser("brave", mode=MODE_OWNER_EXISTING)
        self.assertFalse(res.get("ok"))
        self.assertTrue(res.get("needs_owner_action"))
        self.assertFalse(self.svc._owner_browser)
        cp.assert_not_called()

    def test_owner_existing_running_without_port_never_launches(self):
        info = {"found": True, "running": True, "attachable": False,
                "exe": r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                "pid": 9, "user_data_dir": "X", "debug_port": None,
                "reason": "no port", "owner_action": "do X"}
        with mock.patch("browser.service.cdp.find_browser_for",
                        return_value=info["exe"]), \
             mock.patch("browser.service.detect_owner_browser",
                               return_value=info), \
             mock.patch.object(self.svc, "_connect_page"), \
             mock.patch("browser.service.cdp.launch", side_effect=AssertionError(
                 "cdp.launch must NOT be called when owner browser is not attachable")):
            res = self.svc._ensure_requested_browser("brave", mode=MODE_OWNER_EXISTING)
        self.assertFalse(res.get("ok"))
        self.assertTrue(res.get("needs_owner_action"))
        self.assertEqual(res.get("detail"), "do X")
        self.assertFalse(self.svc._owner_browser)

    def test_owner_existing_attachable_attaches_without_launch(self):
        info = {"found": True, "running": True, "attachable": True,
                "exe": r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                "pid": 9, "user_data_dir": "X", "debug_port": 9222}
        with mock.patch("browser.service.cdp.find_browser_for",
                        return_value=info["exe"]), \
             mock.patch("browser.service.detect_owner_browser",
                               return_value=info), \
             mock.patch.object(self.svc, "_connect_page",
                               return_value=mock.MagicMock()) as cp, \
             mock.patch("browser.service.cdp.browser_ready", return_value=None), \
             mock.patch("browser.service.cdp.launch", side_effect=AssertionError(
                 "cdp.launch must NOT be called when attaching to owner browser")):
            res = self.svc._ensure_requested_browser("brave", mode=MODE_OWNER_EXISTING)
        self.assertTrue(res.get("ok"))
        self.assertTrue(res.get("attached"))
        self.assertTrue(res.get("owner_existing"))
        self.assertTrue(self.svc._owner_browser)
        self.assertEqual(self.svc.port, 9222)
        cp.assert_called()

    def test_owner_existing_genie_owned_brave_is_rejected(self):
        info = {"found": True, "running": True, "attachable": False,
                "genie_owned_conflict": True,
                "exe": r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                "pid": 9, "user_data_dir": r"E:\G3\GENIE\browser-profile",
                "debug_port": 9222, "reason": "genie owns this profile",
                "owner_action": "this is GENIE's own profile, not yours"}
        with mock.patch("browser.service.cdp.find_browser_for",
                        return_value=info["exe"]), \
             mock.patch("browser.service.detect_owner_browser",
                               return_value=info), \
             mock.patch.object(self.svc, "_connect_page"), \
             mock.patch("browser.service.cdp.launch", side_effect=AssertionError(
                 "cdp.launch must NOT be called for a GENIE-owned profile")):
            res = self.svc._ensure_requested_browser("brave", mode=MODE_OWNER_EXISTING)
        self.assertFalse(res.get("ok"))
        self.assertTrue(res.get("needs_owner_action"))
        self.assertFalse(self.svc._owner_browser)
        self.assertFalse(res.get("owner_existing"))

    def test_owner_existing_unidentifiable_endpoint_is_rejected(self):
        info = {"found": True, "running": False, "attachable": False,
                "unidentifiable": True, "debug_port": 9222,
                "owner_action": "debug port reachable but cannot confirm it is your Brave"}
        with mock.patch("browser.service.cdp.find_browser_for",
                        return_value=r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"), \
             mock.patch("browser.service.detect_owner_browser",
                               return_value=info), \
             mock.patch.object(self.svc, "_connect_page"), \
             mock.patch("browser.service.cdp.launch", side_effect=AssertionError(
                 "cdp.launch must NOT be called for an unidentifiable endpoint")):
            res = self.svc._ensure_requested_browser("brave", mode=MODE_OWNER_EXISTING)
        self.assertFalse(res.get("ok"))
        self.assertTrue(res.get("needs_owner_action"))
        self.assertFalse(self.svc._owner_browser)
        self.assertFalse(res.get("owner_existing"))

    def test_owner_existing_port_owned_by_other_process_is_rejected(self):
        info = {"found": True, "running": True, "attachable": False,
                "exe": r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                "pid": 9, "user_data_dir": "X", "debug_port": 9222,
                "reason": "port owned by another process",
                "owner_action": "the debug port is held by another process"}
        with mock.patch("browser.service.cdp.find_browser_for",
                        return_value=info["exe"]), \
             mock.patch("browser.service.detect_owner_browser",
                               return_value=info), \
             mock.patch.object(self.svc, "_connect_page"), \
             mock.patch("browser.service.cdp.launch", side_effect=AssertionError(
                 "cdp.launch must NOT be called when the debug port is owned by another process")):
            res = self.svc._ensure_requested_browser("brave", mode=MODE_OWNER_EXISTING)
        self.assertFalse(res.get("ok"))
        self.assertTrue(res.get("needs_owner_action"))
        self.assertFalse(self.svc._owner_browser)

    def test_blocked_owner_request_is_not_silently_downgraded(self):
        info = {"found": True, "running": True, "attachable": False,
                "exe": r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                "pid": 9, "user_data_dir": "X", "debug_port": None,
                "reason": "no port", "owner_action": "do X"}
        with mock.patch("browser.service.cdp.find_browser_for",
                        return_value=info["exe"]), \
             mock.patch("browser.service.detect_owner_browser",
                               return_value=info), \
             mock.patch.object(self.svc, "_connect_page"), \
             mock.patch("browser.service.cdp.launch", side_effect=AssertionError(
                 "must NOT silently launch a logged-out GENIE profile after a "
                 "blocked owner_existing request")):
            blocked = self.svc._ensure_requested_browser("brave", mode=MODE_OWNER_EXISTING)
            self.assertFalse(blocked.get("ok"))
            # A later GENIE_OWNED call for the same browser must still refuse
            # (owner must explicitly choose Mode 2), never auto-launch:
            retry = self.svc._ensure_requested_browser("brave", mode=MODE_GENIE_OWNED)
        self.assertFalse(retry.get("ok"))
        self.assertTrue(retry.get("needs_owner_action"))

    def test_genie_owned_launches_separate_profile(self):
        exe = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
        fake_target = {"id": "t1", "type": "page", "webSocketDebuggerUrl": "ws://x"}
        with mock.patch("browser.service.cdp.find_browser_for", return_value=exe), \
             mock.patch("browser.service.cdp.launch", return_value={
                 "ok": True, "pid": 555, "exe": exe, "port": 9222,
                 "profile": str(REPO / "browser-profile"), "browser": "Brave"}) as la, \
             mock.patch.object(self.svc._resolver, "resolve",
                               return_value=fake_target), \
             mock.patch("browser.service.cdp.CDPClient", return_value=mock.MagicMock()):
            res = self.svc._ensure_requested_browser("brave", mode=MODE_GENIE_OWNED)
        self.assertTrue(res.get("ok"))
        self.assertFalse(res.get("owner_existing"))
        la.assert_called_once()

    def test_shutdown_disconnects_owner_browser_without_killing(self):
        self.svc._owner_browser = True
        self.svc._owner_info = {"pid": 4242}
        with mock.patch("browser.service.cdp.shutdown_owned",
                        side_effect=AssertionError("owner browser must NOT be killed")) as so, \
             mock.patch.object(self.svc, "close") as cl:
            res = self.svc.shutdown()
        so.assert_not_called()
        cl.assert_called_once()
        self.assertFalse(self.svc._owner_browser)
        self.assertTrue(res.get("owner_browser"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
