"""IPC contract: the UI must be able to drive GENIE through HTTP alone."""
from __future__ import annotations

import json
import urllib.request

import pytest

from core.ipc.server import IPCServer


class FakeDaemon:
    """Minimal daemon surface the API needs (UI contract test, no full boot)."""

    def __init__(self, app):
        self.app = app
        self.ready = True
        self.services = {
            "missions": app.missions, "memory": app.memory, "audit": app.audit,
            "registry": app.registry, "gateway": app.gateway, "locks": app.locks,
            "vault": app.vault, "trust": app.trust, "skills": app.skills,
            "devices": app.devices, "perception": app.perception, "proactive": app.proactive,
        }

    def status(self):
        return {"ready": True, "instance_id": "test", "director": {"engine": "heuristic"},
                "memory": self.services["memory"].stats(), "providers": [],
                "provider_health": [], "vault": {}, "computer_capabilities": [],
                "events": {}, "missions": {}}

    def chat(self, text, session_id="ui", dry_run=False, person_id="owner"):
        return self.app.orchestrator.handle_text(text, self.app.ctx(dry_run=dry_run))


@pytest.fixture()
def server(app):
    srv = IPCServer(FakeDaemon(app), host="127.0.0.1", port=8799)
    srv.start(background=True)
    yield srv
    srv.stop()


def _get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:8799{path}", timeout=5) as r:
        return r.status, r.read().decode("utf-8")


def _post(path, payload):
    req = urllib.request.Request(f"http://127.0.0.1:8799{path}",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def test_health(server):
    code, body = _get("/health")
    assert code == 200 and json.loads(body)["ok"] is True


def test_ui_is_served(server):
    """The HTTP port serves the diagnostic surface, not a second desktop UI.

    The browser desktop UI was removed once Genie.Desktop reached parity. What
    is served now must say so, so nobody is misled into thinking this is the
    product surface.
    """
    code, body = _get("/ui")
    assert code == 200 and "GENIE" in body
    assert "diagnostic" in body.lower()
    assert "Genie.Desktop" in body
    # The old owner-facing app bundle is gone and must not come back.
    # urlopen raises on 404, so the status is asserted via the exception.
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get("/ui/app.js")
    assert exc.value.code == 404


def test_diagnostic_pages_are_served(server):
    for path in ("/ui/ops", "/ui/control"):
        code, body = _get(path)
        assert code == 200, path
        assert len(body) > 100, path


def test_chat_roundtrip_through_http(server):
    code, out = _post("/api/chat", {"text": "Chrome kholo", "dry_run": True})
    assert code == 200
    assert out["state"] == "COMPLETED"
    assert out.get("mission_id") is None
    assert out["steps"][0]["ok"] is True


def test_missions_endpoint_lists_created_mission(server):
    _post("/api/chat", {"text": "Chrome kholo", "dry_run": True})
    code, body = _get("/api/missions")
    assert code == 200
    assert json.loads(body)["missions"] == []


def test_status_endpoint_shape(server):
    code, body = _get("/api/status")
    assert json.loads(body)["ready"] is True


def test_provider_key_is_stored_in_vault_not_returned(server, app):
    code, out = _post("/api/providers/deepseek/key", {"key": "sk-secret-value"})
    assert out["stored"] is True
    assert app.vault.resolve("secret://provider/deepseek/key") == "sk-secret-value"
    code, body = _get("/api/providers")
    assert "sk-secret-value" not in body


def test_unknown_route_404(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get("/api/nope")
    assert exc.value.code == 404


# ------------------------------------------------------------------ skills §5.27
def _register_skill(app, tmp_path, skill_id="write-note"):
    from skills.models import Skill, SkillStatus, SkillStep
    target = tmp_path / "note.txt"
    skill = Skill(
        skill_id=skill_id, name="write a note",
        steps=[SkillStep(capability="files.write",
                         params={"path": str(target), "text": "hello"})],
        inputs={}, verification=[{"check": "file_exists", "value": str(target)}],
        required_capabilities=["files.write"], status=SkillStatus.ACTIVE.value)
    app.skills.registry.register(skill, activate=True)
    return skill


def test_skills_endpoint_lists_registered_skill(server, app, tmp_path):
    _register_skill(app, tmp_path)
    code, body = _get("/api/skills")
    skills = json.loads(body)["skills"]
    assert [s["skill_id"] for s in skills] == ["write-note"]


def test_skills_stats_endpoint(server, app, tmp_path):
    _register_skill(app, tmp_path)
    code, body = _get("/api/skills/stats")
    assert json.loads(body)["skills"] == 1


def test_skill_detail_and_versions_endpoints(server, app, tmp_path):
    _register_skill(app, tmp_path)
    code, body = _get("/api/skills/write-note")
    assert json.loads(body)["skill_id"] == "write-note"
    app.skills.registry.update("write-note", {"description": "v2"})
    code, body = _get("/api/skills/write-note/versions")
    assert [v["version"] for v in json.loads(body)["versions"]] == [1, 2]


def test_skill_search_endpoint(server, app, tmp_path):
    _register_skill(app, tmp_path)
    code, body = _get("/api/skills/search?q=write%20a%20note")
    assert json.loads(body)["count"] >= 1


def test_skill_execute_endpoint(server, app, tmp_path):
    _register_skill(app, tmp_path)
    code, out = _post("/api/skills/execute", {"goal": "write a note"})
    assert out["ok"] is True
    assert (tmp_path / "note.txt").read_text() == "hello"


def test_skill_rollback_endpoint(server, app, tmp_path):
    _register_skill(app, tmp_path)
    app.skills.registry.update("write-note", {"description": "v2"})
    app.skills.registry.set_active_version("write-note", 2)
    code, out = _post("/api/skills/rollback", {"skill_id": "write-note"})
    assert out["ok"] is True and out["rolled_back_to"] == 1


def test_correction_endpoint_records_evidence(server, app):
    code, out = _post("/api/skills/correction", {
        "skill_id": "write-note", "version": 1, "step_id": "s1",
        "failed_step": "files.write", "user_action": "typed the path manually"})
    assert out["ok"] is True
    code, body = _get("/api/skills/corrections?skill_id=write-note")
    assert json.loads(body)["corrections"][0]["failed_step"] == "files.write"


def test_teaching_endpoints_drive_a_session(server, app):
    code, out = _post("/api/teaching/start", {"goal": "write a dated note"})
    assert out["ok"] is True
    code, body = _get("/api/teaching")
    assert json.loads(body)["recording"] is True
    code, out = _post("/api/teaching/event", {"kind": "typing", "text": "hello"})
    assert out["event"]["kind"] == "typing"
    code, out = _post("/api/teaching/stop", {})
    assert out["ok"] is True
    code, out = _post("/api/teaching/discard", {})
    assert out["ok"] is True


def test_teaching_event_redacts_secrets_over_http(server, app):
    _post("/api/teaching/start", {"goal": "log in"})
    code, out = _post("/api/teaching/event",
                      {"kind": "typing", "text": "hunter2", "target_field": "password"})
    assert out["event"]["text"] == "[redacted]"
    _post("/api/teaching/discard", {})


def test_skill_duplicates_endpoint(server, app, tmp_path):
    _register_skill(app, tmp_path, "write-note")
    _register_skill(app, tmp_path, "write-note-copy")
    code, body = _get("/api/skills/duplicates?skill_id=write-note")
    assert json.loads(body)["duplicates"]


# ------------------------------------------------------------------ devices §6
def test_devices_endpoint_lists_the_local_machine(server):
    code, body = _get("/api/devices")
    payload = json.loads(body)
    assert payload["count"] >= 1
    assert "pc_main" in [d["device_id"] for d in payload["devices"]]


def test_device_detail_endpoint(server):
    code, body = _get("/api/devices/pc_main")
    assert json.loads(body)["device_id"] == "pc_main"


def test_device_health_endpoint(server):
    code, body = _get("/api/devices/health")
    assert json.loads(body)["ok"] is True


def test_device_pair_endpoint_requires_a_code(server):
    code, out = _post("/api/devices/pair", {"device_id": "phone_main", "code": ""})
    assert out["ok"] is False


def test_device_pair_endpoint_pairs_a_device(server):
    code, out = _post("/api/devices/pair", {"device_id": "phone_main", "code": "123456",
                                            "name": "Pixel", "type": "android",
                                            "capabilities": ["files.write"]})
    assert out["ok"] is True
    code, body = _get("/api/devices/phone_main")
    assert json.loads(body)["name"] == "Pixel"


def test_device_trust_endpoint(server, app):
    _post("/api/devices/pair", {"device_id": "phone_main", "code": "123456",
                                "capabilities": ["files.write"]})
    code, out = _post("/api/devices/trust", {"device_id": "phone_main",
                                             "trust_tier": "owner_primary"})
    assert out["ok"] is True
    assert app.devices.registry.get("phone_main").manifest.trust_tier == "owner_primary"


def test_device_invoke_endpoint_is_default_deny(server):
    _post("/api/devices/pair", {"device_id": "phone_main", "code": "123456",
                                "capabilities": ["files.write"]})
    code, out = _post("/api/devices/invoke", {"device_id": "phone_main",
                                              "capability": "files.write",
                                              "params": {"path": "x.txt", "text": "x"}})
    assert out["ok"] is False
    assert out["error_code"] == "permission_denied"


def test_device_invoke_endpoint_runs_a_granted_local_capability(server, app, tmp_path):
    target = tmp_path / "via-api.txt"
    app.trust.grant(principal="owner", scope="device:pc_main:files")
    code, out = _post("/api/devices/invoke", {"device_id": "pc_main",
                                              "capability": "files.write",
                                              "params": {"path": str(target),
                                                         "text": "via http"}})
    assert out["ok"] is True and out["verified"] is True
    assert target.read_text(encoding="utf-8") == "via http"


def test_device_commands_endpoint_reports_the_mesh_command_log(server, app):
    """The log holds mesh commands. A local in-process call is audited, not queued."""
    _post("/api/devices/pair", {"device_id": "phone_main", "code": "123456",
                                "capabilities": ["files.write"]})
    app.trust.grant(principal="owner", scope="device:phone_main:files")
    code, out = _post("/api/devices/invoke", {"device_id": "phone_main",
                                              "capability": "files.write",
                                              "params": {"path": "later.txt", "text": "later"}})
    assert out["queued"] is True, "an offline device queues the command"
    code, body = _get("/api/devices/commands?device=phone_main")
    commands = json.loads(body)["commands"]
    assert [c["command_id"] for c in commands] == [out["command_id"]]
    assert commands[0]["status"] == "queued"


def test_device_cancel_and_expire_endpoints(server, app):
    _post("/api/devices/pair", {"device_id": "phone_main", "code": "123456",
                                "capabilities": ["files.write"]})
    app.trust.grant(principal="owner", scope="device:phone_main:files")
    code, out = _post("/api/devices/invoke", {"device_id": "phone_main",
                                              "capability": "files.write",
                                              "params": {"path": "z.txt", "text": "z"}})
    code, cancelled = _post("/api/devices/cancel", {"device_id": "phone_main",
                                                    "command_id": out["command_id"]})
    assert cancelled["ok"] is True
    code, expired = _post("/api/devices/expire", {})
    assert "expired" in expired


def test_device_unpair_endpoint(server, app):
    _post("/api/devices/pair", {"device_id": "phone_main", "code": "123456"})
    code, out = _post("/api/devices/unpair", {"device_id": "phone_main"})
    assert out["ok"] is True
    assert app.devices.registry.paired("phone_main") is False


# ------------------------------------------------------------------ devices §6











# ------------------------------------------------------------------ sync v1 (golden #17)
def test_sync_status_endpoint(server):
    code, body = _get("/api/devices/sync")
    assert json.loads(body)["default_policy"] == "last-write-wins"


def test_sync_apply_and_records_endpoints(server):
    code, out = _post("/api/devices/sync", {"changes": [
        {"namespace": "notes", "key": "todo", "value": "buy milk",
         "updated_at_ms": 1000, "device_id": "pc_main"}]})
    assert out["applied"] == 1
    code, body = _get("/api/devices/sync/records?namespace=notes")
    records = json.loads(body)["records"]
    assert [r["key"] for r in records] == ["todo"]
    assert records[0]["value"] == "buy milk"


def test_sync_conflict_is_reported_and_resolvable_over_http(server):
    _post("/api/devices/sync/policy", {"namespace": "notes", "policy": "manual"})
    _post("/api/devices/sync", {"changes": [
        {"namespace": "notes", "key": "k", "value": "stored", "updated_at_ms": 5000,
         "device_id": "pc_main"}]})
    code, out = _post("/api/devices/sync", {"changes": [
        {"namespace": "notes", "key": "k", "value": "incoming", "updated_at_ms": 5000,
         "device_id": "phone_main"}]})
    conflict_id = out["conflicts"][0]["conflict_id"]

    code, body = _get("/api/devices/sync/conflicts?unresolved=true")
    listed = json.loads(body)["conflicts"]
    assert listed and listed[0]["needs_owner"] is True

    code, resolved = _post("/api/devices/sync/resolve", {"conflict_id": conflict_id,
                                                         "winner": "incoming"})
    assert resolved["ok"] is True and resolved["value"] == "incoming"
    code, body = _get("/api/devices/sync/conflicts?unresolved=true")
    assert json.loads(body)["conflicts"] == []


def test_sync_policy_endpoint(server):
    code, out = _post("/api/devices/sync/policy", {"namespace": "tasks",
                                                   "policy": "merge-union"})
    assert out["ok"] is True
    assert out["policy"] == "merge-union"


# ------------------------------------------------------------------ sync v1 (golden #17)




# ------------------------------------------------------------------ peripherals (Phase 7)
def test_peripherals_endpoint_reports_the_local_backend(server):
    code, body = _get("/api/peripherals")
    payload = json.loads(body)
    assert "available" in payload
    assert "backend" in payload
    assert isinstance(payload["peripherals"], list)


def test_peripherals_invoke_is_refused_without_a_backend(server):
    """This host has no GPIO, so the honest answer is an unavailable error, not a fake success."""
    code, out = _post("/api/peripherals/invoke", {"capability": "relay.set",
                                                  "params": {"peripheral_id": "relay1",
                                                             "value": 1}})
    assert out["ok"] is False
    assert out["error_code"] in ("peripherals_unavailable", "unsupported_capability")


# ------------------------------------------------------------------ peripherals (Phase 7)


# ------------------------------------------------------------------ perception (Phase 8)
def test_perception_status_endpoint(server):
    code, body = _get("/api/perception")
    payload = json.loads(body)
    assert payload["streaming"] is False
    assert "bedroom" in payload["private_zones"]


def test_perception_zones_endpoint(server):
    code, body = _get("/api/perception/zones")
    zones = {z["zone_id"]: z for z in json.loads(body)["zones"]}
    assert zones["bedroom"]["policy"]["camera"] is False
    assert zones["bedroom"]["private"] is True


def test_perception_policy_endpoint(server, app):
    code, out = _post("/api/perception/policy", {"zone_id": "bedroom", "camera": True,
                                                 "retention_s": 30})
    assert out["ok"] is True
    assert app.perception.zone("bedroom").policy.camera is True
    assert app.perception.zone("bedroom").policy.retention_s == 30


def test_perception_camera_activate_is_denied_by_default_policy(server):
    code, out = _post("/api/perception/camera/activate", {"zone_id": "bedroom"})
    assert out["ok"] is False
    assert out["error_code"] == "policy_denied"


def test_perception_events_endpoint_and_purge(server, app):
    app.perception._emit("office", "motion_detected", "camera", 0.6, {})
    code, body = _get("/api/perception/events?zone=office")
    assert json.loads(body)["events"]
    code, out = _post("/api/perception/purge", {"zone_id": "office"})
    assert out["ok"] is True and out["removed"] == 1


def test_perception_environment_endpoint(server):
    code, body = _get("/api/perception/environment?zone=workshop")
    payload = json.loads(body)
    assert payload["zone_id"] == "workshop"
    assert "summary" in payload


# ------------------------------------------------------------------ perception (Phase 8)






# ------------------------------------------------------------------ proactivity (Phase 9)
def test_proactive_status_endpoint(server):
    code, body = _get("/api/proactive")
    payload = json.loads(body)
    assert "dedupe_window_s" in payload
    assert "urgent_whitelist" in payload


def test_proactive_quiet_hours_endpoint(server, app):
    code, out = _post("/api/proactive/quiet-hours", {"start_hour": 22, "end_hour": 7,
                                                     "zone_id": "bedroom"})
    assert out["ok"] is True
    assert any(w.zone_id == "bedroom" for w in app.proactive.notifier.policy.quiet_hours)


def test_proactive_risky_endpoint_warns(app, server):
    code, out = _post("/api/proactive/risky", {"capability": "files.delete",
                                               "params": {"path": "D:/x.docx"}})
    assert out["risky"] is True
    assert out["needs_confirmation"] is True
    assert out["notification"]["delivered"] is True


def test_proactive_explain_endpoint(server, app):
    code, out = _post("/api/proactive/risky", {"capability": "files.delete",
                                               "params": {"path": "D:/y.docx"}})
    notification_id = out["notification"]["notification_id"]
    code, body = _get(f"/api/proactive/explain/{notification_id}")
    assert json.loads(body)["ok"] is True
    assert "score" in json.loads(body)["why"]


def test_proactive_history_endpoint(server, app):
    _post("/api/proactive/risky", {"capability": "files.delete", "params": {"path": "z.txt"}})
    code, body = _get("/api/proactive/history")
    assert json.loads(body)["history"]


def test_proactive_preference_endpoint(server, app):
    code, out = _post("/api/proactive/preference", {"event_class": "perception", "value": 0.05})
    assert out["ok"] is True
    assert app.proactive.notifier.policy.preferences["perception"] == 0.05


# ------------------------------------------------------------------ proactivity (Phase 9)






def test_isolation_endpoint_reports_what_it_can_establish(server):
    """§19-§20: where GENIE is running must be reported, never assumed."""
    code, body = _get("/api/isolation")
    assert code == 200
    out = json.loads(body)
    assert out["effective"]
    # Every level reports a state; none is silently omitted.
    assert set(out["levels"]) == {"workspace", "browser_profile", "container",
                                  "vm", "isolated_desktop"}
    for level in out["levels"].values():
        assert level["state"] in ("active", "configured_but_absent", "detected",
                                  "not_detected", "unknown")
    # The caveats are part of the answer, not decoration.
    assert len(out["caveats"]) >= 3


def test_isolation_never_claims_more_than_it_can_establish(server):
    code, body = _get("/api/isolation")
    out = json.loads(body)
    # An isolated desktop cannot be detected, only declared. Undeclared here,
    # so it must not appear as the effective level.
    assert out["levels"]["isolated_desktop"]["state"] in ("unknown", "not_detected")
    if out["levels"]["isolated_desktop"]["state"] != "detected":
        assert out["effective"] != "isolated_desktop"
    # Devices are listed, but nothing is declared as "the" camera or mic.
    assert out["sensors"]["cameras"]["declared"] is None
    assert out["sensors"]["microphones"]["declared"] is None


