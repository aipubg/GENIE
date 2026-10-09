import threading
from types import SimpleNamespace

from computer.capability_manifest import model_tools
from computer.tool_bridge import BRIDGE_CAPABILITY, execute
from core.contracts import CallContext
from director.heuristics import HeuristicDirector


def test_storage_is_exposed_through_the_shared_read_only_manifest():
    declarations = {item["name"]: item for item in model_tools("chat")}
    assert "storage_info" in declarations
    assert BRIDGE_CAPABILITY["storage_info"] == "files.disk_usage"
    assert declarations["storage_info"]["parameters"]["required"] == []


def test_storage_bridge_dispatches_to_the_existing_canonical_capability():
    calls = []

    class Computer:
        def execute(self, ctx, capability, params, cancel_event=None):
            calls.append((capability, params))
            return SimpleNamespace(to_dict=lambda: {
                "ok": True, "verified": True, "capability": capability,
                "detail": "read-only capability returned successfully",
                "data": {"ok": True, "drive": params["path"],
                         "total_gb": 100.0, "used_gb": 60.0, "free_gb": 40.0},
            })

    result = execute(Computer(), CallContext(), "storage_info",
                     {"drive": "C:"}, threading.Event())
    assert result["ok"] and result["verified"]
    assert calls == [("files.disk_usage", {"path": "C:"})]


def test_storage_tool_rejects_non_drive_paths_before_execution():
    class Computer:
        def execute(self, *args, **kwargs):
            raise AssertionError("invalid path reached the computer service")

    result = execute(Computer(), CallContext(), "storage_info",
                     {"drive": "C:\\Users\\owner"}, threading.Event())
    assert not result["ok"]
    assert result["error_code"] == "invalid_arguments"


def test_english_and_hinglish_drive_questions_route_to_storage():
    director = HeuristicDirector()
    for request in ("How much storage does C: drive have?",
                    "C drive mein kitni jagah khali hai?"):
        decision = director.classify(request, CallContext())
        assert [task.capability for task in decision.tasks] == ["files.disk_usage"]
        assert decision.tasks[0].params["path"] == "C:\\"


def test_audio_device_question_leaves_room_for_result_summary():
    decision = HeuristicDirector().classify(
        "List the actual microphone and speaker devices available on this PC.",
        CallContext())
    assert decision.tasks[0].capability == "system.audio.devices"
    assert decision.reply_hint == ""


def test_storage_measurement_survives_worker_and_reaches_owner_summary():
    from agents.runtime import CapabilityWorker
    from core.orchestrator import Orchestrator
    from core.contracts import ActionResult

    class Computer:
        def execute(self, ctx, capability, params, cancel_event=None):
            return ActionResult(True, capability, "read-only capability returned successfully",
                                verified=True, data={"total_gb": 100.0,
                                                    "used_gb": 60.0, "free_gb": 40.0,
                                                    "drive": "C:"})

    out = CapabilityWorker(computer=Computer())(
        CallContext(), {"type": "computer_action", "capability": "files.disk_usage",
                        "params": {"path": "C:\\"}})
    assert out["data"]["total_gb"] == 100.0
    assert "C: total 100.0 GB, used 60.0 GB, free 40.0 GB" in Orchestrator._summarize([
        {"capability": "files.disk_usage", "status": "succeeded",
         "ok": True, "data": out["data"]}])


def test_audio_device_inventory_survives_worker_and_is_reported():
    from agents.runtime import CapabilityWorker
    from core.orchestrator import Orchestrator
    from core.contracts import ActionResult

    class Computer:
        def execute(self, ctx, capability, params, cancel_event=None):
            return ActionResult(True, capability, verified=True,
                                data={"devices": [
                                    {"name": "Mic A", "input_channels": 1, "output_channels": 0},
                                    {"name": "Speaker B", "input_channels": 0, "output_channels": 2}]})

    out = CapabilityWorker(computer=Computer())(
        CallContext(), {"type": "computer_action", "capability": "system.audio.devices", "params": {}})
    summary = Orchestrator._summarize([{"capability": "system.audio.devices",
                                        "status": "succeeded", "data": out["data"]}])
    assert "Microphones: Mic A" in summary
    assert "speakers/output: Speaker B" in summary


def test_audio_inventory_summary_deduplicates_host_api_aliases():
    from core.orchestrator import Orchestrator

    summary = Orchestrator._summarize([{"capability": "system.audio.devices", "data": {
        "devices": [
            {"name": "Microsoft Sound Mapper - Input", "input_channels": 2},
            {"name": "Mic (Realtek)", "input_channels": 2},
            {"name": "Mic (Realtek)", "input_channels": 2},
            {"name": "Primary Sound Driver", "output_channels": 2},
            {"name": "Speakers (Realtek)", "output_channels": 2},
        ]}}])
    assert "Microsoft Sound Mapper" not in summary
    assert "Primary Sound Driver" not in summary
    assert summary.count("Mic (Realtek)") == 1
    assert summary.count("Speakers (Realtek)") == 1


def test_network_inventory_payload_reaches_owner_summary():
    from agents.runtime import CapabilityWorker
    from core.orchestrator import Orchestrator
    from core.contracts import ActionResult

    class Computer:
        def execute(self, ctx, capability, params, cancel_event=None):
            return ActionResult(True, capability, verified=True, data={
                "interfaces": [{"Name": "Wi-Fi", "Status": "Up"},
                               {"Name": "Ethernet", "Status": "Disconnected"}]})

    out = CapabilityWorker(computer=Computer())(
        CallContext(), {"type": "computer_action", "capability": "system.network.state",
                        "params": {}})
    summary = Orchestrator._summarize([{"capability": "system.network.state",
                                        "status": "succeeded", "data": out["data"]}])
    assert "Wi-Fi: Up" in summary
    assert "Ethernet: Disconnected" in summary
