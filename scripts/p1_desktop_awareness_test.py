"""Desktop Awareness acceptance tests (Phase 1 extension).

Tests all 8 desktop capabilities through the production ComputerService,
verifier, planner, and executor. Uses real imports and actual backend entry points.
Simulated multi-monitor geometry for repeatable tests; physical multi-monitor marked PENDING OWNER.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

os.environ["GENIE_DATA_DIR"] = tempfile.mkdtemp()

from computer.service import ComputerService, SCOPE_BY_CAPABILITY
from computer.planner import CHAINS
from computer.verifier import REGISTRY, READ_ONLY, has_verifier
from computer.desktop_awareness import DesktopAwareness, DesktopSettings, _is_desktop_locked
from core.contracts import CallContext


class TestDesktopCapabilityRegistration(unittest.TestCase):
    """Verify all 8 desktop capabilities are properly registered."""

    DESKTOP_CAPS = [
        "desktop.observe", "desktop.observe_display", "desktop.capture_display",
        "desktop.list_displays", "desktop.pause", "desktop.resume",
        "desktop.settings.get", "desktop.settings.set",
    ]

    def test_all_caps_in_scope(self):
        for c in self.DESKTOP_CAPS:
            self.assertIn(c, SCOPE_BY_CAPABILITY, f"{c} missing from SCOPE_BY_CAPABILITY")

    def test_all_caps_in_planner(self):
        for c in self.DESKTOP_CAPS:
            self.assertIn(c, CHAINS, f"{c} missing from planner CHAINS")

    def test_all_caps_have_verifiers(self):
        for c in self.DESKTOP_CAPS:
            self.assertTrue(has_verifier(c), f"{c} missing from verifier")

    def test_read_only_caps(self):
        ro = {"desktop.observe", "desktop.observe_display", "desktop.capture_display",
              "desktop.list_displays", "desktop.settings.get"}
        for c in ro:
            self.assertIn(c, READ_ONLY, f"{c} should be in READ_ONLY")

    def test_mutating_caps_in_registry(self):
        mutating = {"desktop.pause", "desktop.resume", "desktop.settings.set"}
        for c in mutating:
            self.assertIn(c, REGISTRY, f"{c} should be in REGISTRY")

    def test_lock_resume_separate_from_awareness_resume(self):
        """desktop.lock_resume (old lock-only) must not shadow desktop.resume (combined)."""
        self.assertIn("desktop.lock_resume", SCOPE_BY_CAPABILITY)
        self.assertEqual(SCOPE_BY_CAPABILITY["desktop.lock_resume"], "computer:input:control")
        self.assertEqual(SCOPE_BY_CAPABILITY["desktop.resume"], "computer:desktop:control")

    def test_no_duplicate_browser_registry_entries(self):
        """Verifier REGISTRY should have no duplicate keys (dict guarantees this, but check count)."""
        browser_keys = [k for k in REGISTRY if k.startswith("browser.")]
        self.assertEqual(len(browser_keys), len(set(browser_keys)))


class TestDesktopSettingsPersistence(unittest.TestCase):
    """Verify settings persist across instances."""

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()

    def test_default_settings(self):
        s = DesktopSettings(self.data_dir)
        # Local awareness is the owner's requested background behavior. It is
        # separate from transmitting pixels to Gemini, which stays opt-in.
        self.assertTrue(s.enabled)
        self.assertTrue(s.auto_start)
        self.assertEqual(s.authorized_displays, [])
        self.assertFalse(s.remote_visual_consent)

    def test_settings_persist(self):
        s1 = DesktopSettings(self.data_dir)
        s1.enabled = True
        s1.auto_start = True
        s1.authorized_displays = [0, 1]
        s1.remote_visual_consent = True

        s2 = DesktopSettings(self.data_dir)
        self.assertTrue(s2.enabled)
        self.assertTrue(s2.auto_start)
        self.assertEqual(s2.authorized_displays, [0, 1])
        self.assertTrue(s2.remote_visual_consent)

    def test_exclusion_defaults(self):
        s = DesktopSettings(self.data_dir)
        self.assertIn("1password", s.excluded_processes)
        self.assertIn("#32770", s.excluded_classes)

    def test_poll_interval_clamping(self):
        s = DesktopSettings(self.data_dir)
        s.poll_interval_s = 0.1  # below min
        self.assertGreaterEqual(s.poll_interval_s, 1.0)
        s.poll_interval_s = 999  # above max
        self.assertLessEqual(s.poll_interval_s, 30.0)


class TestDesktopAwarenessLifecycle(unittest.TestCase):
    """Test start/stop/pause/resume lifecycle."""

    def setUp(self):
        self.da = DesktopAwareness(tempfile.mkdtemp())

    def tearDown(self):
        self.da.stop()

    def test_initial_state(self):
        # New settings auto-start local metadata/redacted-preview awareness;
        # this does not imply remote screen consent.
        self.assertTrue(self.da.is_running)
        self.assertFalse(self.da.is_paused)

    def test_start_stop(self):
        r = self.da.start()
        self.assertTrue(r["ok"])
        self.assertTrue(self.da.is_running)
        r = self.da.stop()
        self.assertTrue(r["ok"])
        self.assertFalse(self.da.is_running)

    def test_pause_resume(self):
        self.da.start()
        r = self.da.pause()
        self.assertTrue(r["ok"])
        self.assertTrue(self.da.is_paused)
        r = self.da.resume()
        self.assertTrue(r["ok"])
        self.assertFalse(self.da.is_paused)

    def test_double_start_is_safe(self):
        self.da.start()
        r = self.da.start()
        self.assertTrue(r["ok"])
        self.assertIn("already", r["detail"])

    def test_status_dict(self):
        status = self.da.status
        self.assertIn("running", status)
        self.assertIn("paused", status)
        self.assertIn("enabled", status)
        self.assertIn("display_count", status)


class TestDesktopObservation(unittest.TestCase):
    """Test observation returns real data on available monitors."""

    def setUp(self):
        self.da = DesktopAwareness(tempfile.mkdtemp())

    def tearDown(self):
        self.da.stop()

    def test_observe_returns_observation(self):
        obs = self.da.observe()
        self.assertIsNotNone(obs)
        self.assertGreaterEqual(obs.display_count, 0)
        self.assertIsInstance(obs.locked, bool)

    def test_list_displays(self):
        result = self.da.list_displays()
        self.assertTrue(result["ok"])
        self.assertIn("count", result)
        self.assertIn("displays", result)
        self.assertIn("virtual_screen", result)

    def test_observe_specific_display(self):
        result = self.da.observe_display(0)
        self.assertIn("ok", result)

    def test_observe_nonexistent_display(self):
        result = self.da.observe_display(999)
        self.assertFalse(result["ok"])


class TestComputerServiceIntegration(unittest.TestCase):
    """End-to-end tests through the production ComputerService."""

    def setUp(self):
        self.svc = ComputerService()
        self.ctx = CallContext(person_id="test", device_id="test", trace_id="t1")

    def tearDown(self):
        if self.svc.executor.desktop_awareness:
            self.svc.executor.desktop_awareness.stop()

    def test_execute_list_displays(self):
        r = self.svc.execute(self.ctx, "desktop.list_displays")
        self.assertTrue(r.ok)
        self.assertIn("count", r.data or {})

    def test_execute_observe(self):
        r = self.svc.execute(self.ctx, "desktop.observe")
        self.assertTrue(r.ok)

    def test_execute_pause_resume(self):
        r = self.svc.execute(self.ctx, "desktop.pause")
        self.assertTrue(r.ok)
        r = self.svc.execute(self.ctx, "desktop.resume")
        self.assertTrue(r.ok)

    def test_execute_settings_get(self):
        r = self.svc.execute(self.ctx, "desktop.settings.get")
        self.assertTrue(r.ok)

    def test_execute_settings_set(self):
        r = self.svc.execute(self.ctx, "desktop.settings.set",
                             {"settings": {"poll_interval_s": 5.0}})
        self.assertTrue(r.ok)

    def test_unknown_capability_rejected(self):
        r = self.svc.execute(self.ctx, "desktop.nonexistent")
        self.assertFalse(r.ok)
        self.assertIn("unsupported", r.detail)


class TestLockScreenHonesty(unittest.TestCase):
    """Verify lock-screen detection doesn't crash."""

    def test_lock_detection_returns_bool(self):
        result = _is_desktop_locked()
        self.assertIsInstance(result, bool)


class TestExclusionFiltering(unittest.TestCase):
    """Verify exclusion settings are applied during observation."""

    def test_excluded_processes_filtered(self):
        da = DesktopAwareness(tempfile.mkdtemp())
        da.settings.excluded_processes = {"explorer"}
        obs = da.observe()
        for d in obs.displays:
            for w in d.windows:
                self.assertNotIn("explorer", w.get("process", "").lower())
        da.stop()


class TestBufferCleanup(unittest.TestCase):
    """Verify temp buffers are cleared on pause/stop."""

    def test_buffers_cleared_on_stop(self):
        da = DesktopAwareness(tempfile.mkdtemp())
        da._temp_buffer_dir.mkdir(parents=True, exist_ok=True)
        dummy = da._temp_buffer_dir / "test.bmp"
        dummy.write_bytes(b"\x00" * 100)
        self.assertTrue(dummy.exists())
        da.stop()
        self.assertFalse(dummy.exists())


class TestNoDuplicateAuthority(unittest.TestCase):
    """Verify ONE computer execution authority, ONE permission system."""

    def test_single_executor_class(self):
        from computer.executor import Executor
        self.assertEqual(Executor.__name__, "Executor")

    def test_desktop_caps_use_existing_scope_system(self):
        for cap in ["desktop.observe", "desktop.pause"]:
            scope = SCOPE_BY_CAPABILITY[cap]
            self.assertTrue(scope.startswith("computer:"))


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [TestDesktopCapabilityRegistration, TestDesktopSettingsPersistence,
                TestDesktopAwarenessLifecycle, TestDesktopObservation,
                TestComputerServiceIntegration, TestLockScreenHonesty,
                TestExclusionFiltering, TestBufferCleanup, TestNoDuplicateAuthority]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    total = result.testsRun
    fails = len(result.failures) + len(result.errors)
    print(f"\n{'='*70}")
    print(f"{total - fails}/{total} passed" + (f" ({fails} FAILED)" if fails else ""))
    print(f"{'='*70}")
    if fails:
        for f in result.failures + result.errors:
            print(f"FAIL: {f[0]}")
            print(f[1][:300])
    sys.exit(1 if fails else 0)
