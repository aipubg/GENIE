#!/usr/bin/env python3
"""GENIE launcher.

    python genie.py daemon            # start core + local API (http://127.0.0.1:8787/ui)
    python genie.py status            # print daemon status JSON
    python genie.py chat "Chrome kholo" [--dry-run]
    python genie.py selftest          # run the E2E text loop without a server

The UI never contains business logic: it talks to this daemon over HTTP + SSE.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def cmd_daemon(args) -> int:
    from core.config import get_config
    from core.ipc.server import IPCServer
    from core.lifecycle import Daemon

    cfg = get_config()
    daemon = Daemon(cfg).start()
    server = IPCServer(daemon, host=cfg.get("ipc.host", "127.0.0.1"),
                       port=int(cfg.get("ipc.port", 8787)))
    server.start(background=True)
    print(f"GENIE ready -> http://{cfg.get('ipc.host')}:{cfg.get('ipc.port')}/ui")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
        daemon.stop()
    return 0


def cmd_status(args) -> int:
    from core.lifecycle import get_daemon
    print(json.dumps(get_daemon().status(), indent=2, ensure_ascii=False))
    return 0


def cmd_chat(args) -> int:
    from core.lifecycle import get_daemon
    out = get_daemon().chat(args.text, dry_run=args.dry_run)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_selftest(args) -> int:
    from core.lifecycle import get_daemon
    d = get_daemon()
    for text in ["Chrome kholo", "volume 30", "phone ka next song"]:
        out = d.chat(text, dry_run=True)
        print(f"\n> {text}\n{json.dumps({k: v for k, v in out.items() if k != 'decision'}, indent=2, ensure_ascii=False)}")
    return 0


def cmd_needle_setup(args) -> int:
    """Installer/bootstrap step: provision + verify the official Needle 2 runtime."""
    from director.needle_runtime import get_runtime
    rt = get_runtime()
    print(f"runtime dir: {rt.dir}")
    result = rt.provision(force=args.force)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result.get("ok"):
        return 1
    print("\nverification:", json.dumps(rt.verify(), indent=2))
    if not args.no_smoke:
        return cmd_needle_smoke(args)
    return 0


def cmd_needle_smoke(args) -> int:
    """Routing smoke test against the real NEDLE2 engine."""
    from director.nedle2 import NeedleDirector, reset_director
    from director.needle_runtime import get_runtime

    reset_director()
    director = NeedleDirector()
    if not director.available():
        print(json.dumps({"ok": False, "error": "runtime unavailable",
                          "runtime": get_runtime().status()}, indent=2))
        return 1
    result = director.smoke_test()
    print(json.dumps({k: v for k, v in result.items() if k != "runtime"},
                     indent=2, ensure_ascii=False))
    large = getattr(args, "large", 0)
    if large:
        print("\nlarge catalogue probe:")
        print(json.dumps(director.large_catalogue_probe(large), indent=2, ensure_ascii=False))
    director.close()
    return 0 if result.get("ok") else 1


def cmd_voice_devices(args) -> int:
    from voice import devices as voice_devices
    print(json.dumps(voice_devices.summary(), indent=2, ensure_ascii=False))
    return 0


def cmd_voice_status(args) -> int:
    from core.lifecycle import get_daemon
    print(json.dumps(get_daemon().services["voice"].status(), indent=2, ensure_ascii=False))
    return 0


def cmd_voice_selftest(args) -> int:
    from core.lifecycle import get_daemon
    report = get_daemon().services["voice"].selftest(args.command)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("ok") else 1


def cmd_voice_ptt(args) -> int:
    from core.lifecycle import get_daemon
    voice = get_daemon().services["voice"]
    print(f"listening for {args.seconds}s — speak now…", file=sys.stderr)
    out = voice.push_to_talk(args.seconds)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


def cmd_voice_e2e(args) -> int:
    """Real microphone-to-action test: speak, and GENIE executes + verifies + answers."""
    from core.lifecycle import get_daemon

    voice = get_daemon().services["voice"]
    status = voice.pipeline.status()
    mic = status["input"].get("device")
    print("=" * 68, file=sys.stderr)
    print("REAL MICROPHONE E2E — no transcript injection", file=sys.stderr)
    print(f"  input   : {status['input']['name']} (device {mic})", file=sys.stderr)
    print(f"  stt     : {status['stt']['name']}", file=sys.stderr)
    print(f"  tts     : {status['tts']['name']}", file=sys.stderr)
    print(f"  window  : {args.seconds:.0f}s — speak clearly after the beep", file=sys.stderr)
    print("=" * 68, file=sys.stderr)
    if args.phrase:
        print(f'  say: "{args.phrase}"', file=sys.stderr)
    print(">>> listening now…", file=sys.stderr)
    report = voice.real_e2e(args.seconds, expected=args.expect or "")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("ok") else 1


def cmd_voice_say(args) -> int:
    from core.lifecycle import get_daemon
    print(json.dumps(get_daemon().services["voice"].say(args.text), indent=2, ensure_ascii=False))
    return 0


def cmd_voice_acoustic(args) -> int:
    """Automated acoustic round trip: GENIE speaks, the microphone hears it, STT acts on it."""
    from core.lifecycle import get_daemon
    report = get_daemon().services["voice"].acoustic_loop(args.phrase, args.seconds)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("ok") else 1


def cmd_plugins(args) -> int:
    """List plugins, their permissions and runtime state."""
    from core.lifecycle import get_daemon
    status = get_daemon().services["plugins"].status()
    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False))
        return 0
    print(f"{status['count']} plugin(s), {len(status['capabilities'])} capabilities\n")
    for plugin in status["plugins"]:
        granted = plugin["granted_permissions"]
        missing = plugin["missing_permissions"]
        runtime = plugin.get("runtime", {})
        print(f"  {plugin['id']:16s} v{plugin['version']:8s} {plugin['state']:12s} "
              f"{'enabled' if plugin['enabled'] else 'disabled':9s} "
              f"caps={len(plugin['capabilities']):2d} runtime={runtime.get('state','?'):10s}")
        if plugin["permissions"]:
            print(f"      permissions : {', '.join(plugin['permissions'])}")
            print(f"      granted     : {', '.join(granted) if granted else 'NONE (default deny)'}")
            if missing:
                print(f"      missing     : {', '.join(missing)}")
    return 0


def cmd_plugins_grant(args) -> int:
    """Grant a plugin's declared permissions (explicit owner action)."""
    from core.lifecycle import get_daemon
    service = get_daemon().services["plugins"]
    record = service.registry.get(args.plugin_id)
    if record is None:
        print(f"unknown plugin {args.plugin_id}", file=sys.stderr)
        return 1
    permissions = args.permissions or record.manifest.permissions
    result = service.grant(args.plugin_id, permissions)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def cmd_plugins_selftest(args) -> int:
    """Run each plugin's host self-test in its own process."""
    from plugins.host import PluginHost
    from plugins.registry import PluginRegistry
    from core.db import get_db
    registry = PluginRegistry(get_db())
    registry.load_all()
    overall = True
    for record in registry.all():
        host = PluginHost(record.directory)
        report = host.self_test()
        ok = bool(report.get("steps", {}).get("initialize", {}).get("ok"))
        overall = overall and ok
        print(f"  {record.manifest.id:16s} {'OK' if ok else 'FAILED':7s} "
              f"{len(report.get('steps', {})) - 2} capabilities exercised")
        if args.verbose:
            print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if overall else 1


def cmd_skills(args) -> int:
    """List learned/taught skills and their lifecycle state."""
    from core.lifecycle import get_daemon
    service = get_daemon().services["skills"]
    skills = service.list(status=args.status, scope=args.scope)
    if args.json:
        print(json.dumps({"skills": skills, "stats": service.stats()},
                         indent=2, ensure_ascii=False))
        return 0
    stats = service.stats()
    print(f"{stats['skills']} skill(s) — "
          f"{stats['successes']} success / {stats['failures']} failure runs\n")
    for s in skills:
        marker = "ACTIVE" if s.get("active") else ""
        print(f"  {s['skill_id']:28s} v{s['version']:<3d} {s['status']:11s} "
              f"{s.get('scope',''):9s} steps={len(s.get('steps', [])):2d} {marker}")
        if s.get("name"):
            print(f"      {s['name'][:100]}")
    return 0


def cmd_skills_search(args) -> int:
    """Search the skill registry for a compatible skill."""
    from core.lifecycle import get_daemon
    from core.contracts import CallContext
    service = get_daemon().services["skills"]
    result = service.search(CallContext(), args.query, limit=args.limit)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("count") else 1


def cmd_skills_run(args) -> int:
    """Execute the best compatible learned skill for a goal."""
    from core.lifecycle import get_daemon
    from core.contracts import CallContext
    service = get_daemon().services["skills"]
    out = service.execute(CallContext(person_id="owner"), args.goal,
                          dry_run=bool(args.dry_run))
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0 if out.get("ok") else 1


def cmd_skills_versions(args) -> int:
    """Show every version of a skill and which one is active."""
    from core.lifecycle import get_daemon
    service = get_daemon().services["skills"]
    versions = service.versions(args.skill_id)
    if not versions:
        print(f"unknown skill {args.skill_id}", file=sys.stderr)
        return 1
    for v in versions:
        print(f"  v{v['version']:<4d} {v['status']:11s} "
              f"ok={v['success_count']:<3d} fail={v['failure_count']:<3d} "
              f"{'<- active' if v['active'] else ''}")
    return 0


def cmd_skills_rollback(args) -> int:
    """Roll an active skill back to a previous version."""
    from core.lifecycle import get_daemon
    service = get_daemon().services["skills"]
    result = service.rollback(args.skill_id, args.version)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def cmd_skills_learn(args) -> int:
    """Learn a candidate skill from a completed mission trace."""
    from core.lifecycle import get_daemon
    from core.contracts import CallContext
    service = get_daemon().services["skills"]
    out = service.learn_from_mission(CallContext(person_id="owner"), args.mission_id,
                                     auto_save=bool(args.save), activate=bool(args.activate))
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0 if out.get("ok") else 1


def cmd_skills_teach(args) -> int:
    """Teaching mode: start/stop a demonstration and turn it into a skill."""
    from core.lifecycle import get_daemon
    service = get_daemon().services["skills"]
    action = args.action
    if action == "start":
        out = service.start_teaching(goal=args.goal or "", application=args.app or "")
    elif action == "stop":
        out = service.stop_teaching()
    elif action == "learn":
        out = service.learn_from_demonstration(name=args.name or "",
                                               auto_save=not args.no_save,
                                               activate=bool(args.activate))
    elif action == "discard":
        out = service.discard_teaching()
    else:
        out = service.teaching_status()
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0 if out.get("ok", True) else 1


def cmd_skills_selftest(args) -> int:
    """Verify the skill subsystem end to end on this machine."""
    from core.db import get_db
    from core.contracts import CallContext
    from skills.models import Skill, SkillStep, SkillStatus
    from skills.service import SkillService
    db = get_db()
    db.migrate()
    service = SkillService(db)
    problems: list[str] = []
    # 1. register a trivial skill and read it back
    skill = Skill(skill_id="selftest-echo", name="selftest echo", version=1,
                  status=SkillStatus.CANDIDATE.value,
                  steps=[SkillStep(capability="files.exists", params={"path": "${path}"})],
                  inputs={"path": {"type": "string", "required": True}},
                  verification=[{"check": "file_exists", "value": "${path}"}])
    reg = service.registry.register(skill)
    if not reg.get("ok"):
        problems.append(f"register failed: {reg}")
    if service.get("selftest-echo", 1) is None:
        problems.append("skill not retrievable")
    # 2. versioning + rollback
    service.registry.update("selftest-echo", {"description": "v2"}, as_new_version=True)
    service.registry.set_active_version("selftest-echo", 2)
    rb = service.registry.rollback("selftest-echo")
    if not rb.get("ok"):
        problems.append(f"rollback failed: {rb}")
    # 3. selection refuses weak matches
    weak = service.select("totally unrelated gibberish zzz")
    if weak is not None and weak.score >= 0.45:
        problems.append("selection threshold did not reject a weak match")
    # 4. cleanup
    service.registry.delete("selftest-echo")
    ok = not problems
    print(f"skill selftest: {'OK' if ok else 'FAILED'}")
    for p in problems:
        print(f"  - {p}")
    return 0 if ok else 1


def cmd_devices(args) -> int:
    """List known devices, their trust tier, presence and capability count."""
    from core.lifecycle import get_daemon
    service = get_daemon().services["devices"]
    status = service.status()
    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False, default=str))
        return 0
    print(f"{status['count']} device(s), {status['online']} online, "
          f"{status['queued']} queued command(s)")
    transport = status.get("transport", {})
    print(f"transport: {'listening' if transport.get('listening') else 'stopped'} on "
          f"{transport.get('host')}:{transport.get('port')}\n")
    for device in status["devices"]:
        # "reachable" is the heartbeat-backed fact; "state" is the lifecycle value. Showing both
        # avoids the confusion of a device that is `online` by state but has stopped answering.
        presence = "reachable" if device["online"] else "unreachable"
        print(f"  {device['device_id']:16s} {device['type']:11s} "
              f"{device['trust_tier']:16s} {device['state']:10s} {presence:12s} "
              f"caps={len(device['capabilities']):2d} "
              f"paired={'yes' if device['paired_at'] else 'no'}")
        if device.get("last_error"):
            print(f"      last error: {device['last_error'][:90]}")
    return 0


def cmd_devices_pair(args) -> int:
    """Pair a device using the code it displayed."""
    from core.lifecycle import get_daemon
    result = get_daemon().services["devices"].pair(
        args.device_id, args.code, name=args.name or "", type=args.type or "")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("ok") else 1


def cmd_devices_unpair(args) -> int:
    """Revoke a device pairing."""
    from core.lifecycle import get_daemon
    result = get_daemon().services["devices"].unpair(args.device_id)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("ok") else 1


def cmd_devices_trust(args) -> int:
    """Set a device's trust tier (owner action)."""
    from core.lifecycle import get_daemon
    result = get_daemon().services["devices"].registry.set_trust(args.device_id, args.tier)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("ok") else 1


def cmd_devices_grant(args) -> int:
    """Grant a device capability scope (default deny until granted)."""
    from core.lifecycle import get_daemon
    service = get_daemon().services["devices"]
    scopes = args.scopes or [service.scope_for(args.device_id, c)
                             for c in service.registry.capabilities(args.device_id)]
    from core.contracts import GrantType
    from core.lifecycle import get_daemon as _gd
    trust = _gd().services["trust"]
    granted = [trust.grant(principal="owner", scope=scope, grant_type=GrantType.STANDING,
                           granted_by="owner", reason="device grant")
               for scope in scopes]
    print(json.dumps({"device_id": args.device_id, "scopes": scopes, "grant_ids": granted},
                     indent=2, ensure_ascii=False))
    return 0


def cmd_devices_run(args) -> int:
    """Send a capability to a device and report the verified result."""
    from core.lifecycle import get_daemon
    from core.contracts import CallContext
    service = get_daemon().services["devices"]
    params = json.loads(args.params) if args.params else {}
    invocation = service.execute(CallContext(person_id="owner"), args.device_id,
                                 args.capability, params)
    print(json.dumps(invocation.to_dict(), indent=2, ensure_ascii=False, default=str))
    return 0 if invocation.ok else 1


def cmd_devices_node(args) -> int:
    """Run a GENIE device node on this machine (a real node, not a mock)."""
    from devices.node import main as node_main
    argv = ["--device-id", args.device_id, "--host", args.host, "--port", str(args.port)]
    if args.name:
        argv += ["--name", args.name]
    if args.type:
        argv += ["--type", args.type]
    if args.code:
        argv += ["--code", args.code]
    if args.root:
        argv += ["--root", args.root]
    if args.seconds:
        argv += ["--seconds", str(args.seconds)]
    return node_main(argv)


def cmd_devices_selftest(args) -> int:
    """Verify the mesh end to end: pair a real node, run a real capability, verify it."""
    import socket
    import tempfile
    from pathlib import Path
    from core.db import get_db
    from core.contracts import CallContext
    from security.audit import AuditLog
    from security.trust import TrustService
    from security.vault import Vault
    from devices.node import DeviceNode, ReferenceHandler
    from devices.service import DeviceService

    problems: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="genie-device-selftest-"))
    db = get_db()
    db.migrate()
    audit = AuditLog(db)
    trust = TrustService(db, audit=audit, default_deny=True)
    vault = Vault(tmp / "vault.enc", audit=audit)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    service = DeviceService(db, audit=audit, trust=trust, vault=vault, port=port,
                            command_timeout_s=20.0)
    if not service._started:
        print("device selftest: FAILED — transport did not start")
        return 1
    node = DeviceNode(device_id="selftest_node", name="Selftest Node", handler=ReferenceHandler(tmp / "root"),
                      host="127.0.0.1", port=port, code="000111")
    try:
        node.start_background()
        time.sleep(1.0)
        if service.server.connected("selftest_node"):
            problems.append("an unpaired node must not be served")
        service.pair("selftest_node", "000111", name="Selftest Node", type="virtual")
        deadline = time.time() + 20
        while time.time() < deadline and not service.server.connected("selftest_node"):
            time.sleep(0.2)
        if not service.server.connected("selftest_node"):
            problems.append("the node did not come online after pairing")
        else:
            denied = service.execute(CallContext(person_id="owner"), "selftest_node",
                                     "files.write", {"path": "a.txt", "text": "a"})
            if denied.error_code != "permission_denied":
                problems.append(f"expected default deny, got {denied.error_code}")
            trust.grant(principal="owner", scope="device:selftest_node:files")
            invocation = service.execute(CallContext(person_id="owner"), "selftest_node",
                                         "files.write", {"path": "a.txt", "text": "verified"})
            if not invocation.ok or not invocation.verified:
                problems.append(f"command was not verified: {invocation.to_dict()}")
            written = tmp / "root" / "a.txt"
            if not written.exists() or written.read_text(encoding="utf-8") != "verified":
                problems.append("the file was not really written on the node")
    finally:
        node.stop()
        service.stop()
    ok = not problems
    print(f"device mesh selftest: {'OK' if ok else 'FAILED'}")
    for problem in problems:
        print(f"  - {problem}")
    return 0 if ok else 1


def cmd_agents(args) -> int:
    """Report the agent factory, live teams, budgets and experience."""
    from core.lifecycle import get_daemon
    status = get_daemon().services["agents_service"].status()
    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False, default=str))
        return 0
    factory = status["factory"]
    print(f"agents: {factory['agents']}  candidates: {factory['candidates']}  "
          f"failovers: {status['failovers']}")
    print(f"experience: {factory['experience']['records']} record(s), "
          f"success rate {factory['experience']['success_rate']}")
    print(f"artifacts: {status['artifacts']['count']}  "
          f"global remaining: {status['budgets']['global_remaining']}\n")
    for team in status["teams"]:
        inner = team["status"]
        done = len([t for t in inner["tasks"]["tasks"] if t["status"] == "done"])
        print(f"  {team['mission_id']:14s} agents={len(inner['team']):2d} "
              f"tasks={done}/{len(inner['tasks']['tasks']):2d} "
              f"provider={team['provider'] or '-':12s} "
              f"failovers={team['failovers']} orphans={len(inner['orphans'])}")
        for task in inner["tasks"]["tasks"]:
            deps = f" <- {','.join(task['depends_on'])}" if task["depends_on"] else ""
            print(f"      {task['task_id']:14s} {task['status']:9s} "
                  f"{task['objective'][:44]}{deps}")
    return 0


def cmd_agents_plan(args) -> int:
    """Show what team a mission would use, and the cheaper single-agent alternative."""
    from core.lifecycle import get_daemon
    plan = get_daemon().services["agents_service"].factory.plan_team(
        objective=args.objective, complexity=args.complexity,
        needs=args.needs or [], budget_usd=args.budget or 0.0)
    if args.json:
        print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False, default=str))
        return 0
    print(f"{len(plan.team)} agent(s), estimated ${plan.estimated_cost_usd:.4f}, "
          f"~{plan.estimated_model_calls} model call(s)")
    for member in plan.team:
        print(f"  {member['role']:12s} {member['capability']:10s} {member['quality']:9s} "
              f"${member['estimated_cost_usd']:.4f}  {member['purpose'][:44]}")
    print(f"\nwhy: {plan.rationale}")
    print(f"alternative: {plan.single_agent_alternative}")
    return 0


def cmd_agents_failover(args) -> int:
    """Simulate a provider dying mid-mission and show the continuation packet."""
    from core.lifecycle import get_daemon
    result = get_daemon().services["agents_service"].failover(
        args.mission_id, from_provider=args.from_provider, reason=args.reason)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("ok") else 1


def cmd_agents_cancel(args) -> int:
    """Cancel a mission team, release its locks and retire its agents."""
    from core.lifecycle import get_daemon
    result = get_daemon().services["agents_service"].cancel(args.mission_id, args.reason)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("ok") else 1






# ----------------------------------------------------------------- operator console (Phase 11 §11)
def _agents_service():
    from core.lifecycle import get_daemon
    return get_daemon().services["agents_service"]


def cmd_ops(args) -> int:
    """Live operator dashboard: fleet-wide state, budgets, audit trail, improvements."""
    dash = _agents_service().ops_dashboard()
    if args.json:
        print(json.dumps(dash, indent=2, ensure_ascii=False, default=str))
        return 0
    g = dash["global"]
    print(f"teams={g['teams']}  paused={g['paused']}  cancelled={g['cancelled']}  "
          f"working={g['working_agents']}  idle={g['idle_agents']}")
    print(f"failovers={g['failovers']}  global budget left="
          f"{g['global_budget_remaining']}")
    print(f"experience: {g['experience']['records']} record(s), "
          f"success rate {g['experience']['success_rate']}\n")
    for t in dash["teams"]:
        print(f"  {t['mission_id']:14s} {t['provider'] or '-':12s} "
              f"tasks={t['tasks']['done']}/{t['tasks']['total']} "
              f"workers={t['working']} orphans={len(t['orphans'])} "
              f"failovers={t['failovers']} conc={t['max_concurrency']} "
              f"{'PAUSED' if t['paused'] else ''}{'CANCELLED' if t['cancelled'] else ''}")
        print(f"      budget_left={t['budget_remaining']}  artifacts={t['artifacts']} "
              f"mb={t['mailbox_count']} bb={t['blackboard_count']} "
              f"~{t['throughput_per_min']}/min")
    if dash["audit"]:
        print("\n  recent audit:")
        for a in dash["audit"][:8]:
            print(f"    {a.get('ts')} {a.get('who')} {a.get('action')} -> {a.get('result')}")
    if dash["improvements"]:
        print("\n  improvements:")
        for s in dash["improvements"]:
            print(f"    [{s['severity']}] {s['text']}")
    return 0


def cmd_ops_control(args) -> int:
    """Perform one audited operator action on a live team."""
    params = {}
    if args.action == "force_failover":
        params["from_provider"] = args.from_provider
        params["reason"] = args.reason
        params["to_provider"] = args.to_provider
    elif args.action == "set_budget_cap":
        params = {"tokens": args.tokens, "cost_usd": args.cost_usd,
                  "model_calls": args.model_calls, "tool_calls": args.tool_calls,
                  "wall_ms": args.wall_ms}
    elif args.action == "throttle":
        params = {"max_concurrency": args.max_concurrency}
    elif args.action in ("pause", "cancel"):
        params = {"reason": args.reason}
    result = _agents_service().ops_control(args.action, mission_id=args.mission_id,
                                            actor=args.actor, **params)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("ok") else 1


def cmd_ops_audit(args) -> int:
    """Show the recent tamper-evident operator audit trail."""
    rows = _agents_service().ops_audit(limit=args.limit, mission_id=args.mission)
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return 0
    for a in rows:
        print(f"{a.get('ts')}  {a.get('who'):12s} {a.get('action'):28s} "
              f"{a.get('result')}  {a.get('why', '')[:60]}")
    return 0


def cmd_ops_outcomes(args) -> int:
    """Show recorded per-mission outcome snapshots (the Phase 12 input)."""
    rows = _agents_service().ops_outcomes(limit=args.limit)
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return 0
    for o in rows:
        print(f"{o.get('mission_id')}  {o.get('final_status'):9s} "
              f"team={o.get('team_size')} done={o.get('tasks_done')}/{o.get('tasks_total')} "
              f"fail={o.get('tasks_failed')} cost=${o.get('cost_usd')} "
              f"orphans={o.get('orphan_count')}")
    return 0


def cmd_ops_improve(args) -> int:
    """Show experience->improvement suggestions (the raw signal Phase 12 consumes)."""
    sugg = _agents_service().ops_improve()
    if args.json:
        print(json.dumps(sugg, indent=2, ensure_ascii=False, default=str))
        return 0
    if not sugg:
        print("no suggestions")
        return 0
    for s in sugg:
        print(f"[{s.get('severity', 'info')}] {s.get('text')}")
    return 0


# ----------------------------------------------------------------- backup (Phase 14.1)
def _backup_service():
    from core.backup import BackupService
    from core.config import USER_CONFIG_PATH, get_config
    cfg = get_config()
    return BackupService(db_path=cfg.db_path, user_file=USER_CONFIG_PATH,
                         vault_path=cfg.vault_path), cfg


def cmd_backup_create(args) -> int:
    """Snapshot the database, config and vault into a verified backup directory."""
    svc, cfg = _backup_service()
    dest = args.dest or str(Path(cfg.data_dir) / "backups")
    manifest = svc.create(dest, include_vault=not args.no_vault, note=args.note or "")
    if args.json:
        print(json.dumps(manifest, indent=2, ensure_ascii=False, default=str))
        return 0
    print(f"backup {manifest['backup_id']}  files={len(manifest['files'])}  -> {manifest['path']}")
    for f in manifest["files"]:
        print(f"    {f['name']:16s} {f['bytes']:>10,} B  {f['sha256'][:16]}…")
    return 0


def cmd_backup_list(args) -> int:
    """List available backups."""
    svc, cfg = _backup_service()
    items = svc.list(args.dir or str(Path(cfg.data_dir) / "backups"))
    if args.json:
        print(json.dumps(items, indent=2, ensure_ascii=False, default=str))
        return 0
    if not items:
        print("no backups")
        return 0
    for b in items:
        print(f"{b['backup_id']:28s} files={len(b['files']):2d} vault={b.get('includes_vault')} "
              f"{b.get('note', '')}")
    return 0


def cmd_backup_restore(args) -> int:
    """Restore a backup. Verifies integrity first and refuses a tampered archive."""
    svc, _ = _backup_service()
    result = svc.restore(args.backup, verify=not args.no_verify, restore_vault=not args.no_vault)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("ok") else 1


def cmd_backup_verify(args) -> int:
    """Check a backup's integrity without restoring it."""
    svc, _ = _backup_service()
    check = svc.verify(args.backup)
    print(json.dumps(check, indent=2, ensure_ascii=False, default=str))
    return 0 if check.get("ok") else 1


# ----------------------------------------------------------------- updater (Phase 14.3)
def _updater():
    from core.config import get_config
    from core.updater import Updater
    cfg = get_config()
    return Updater(Path(cfg.data_dir) / "releases")


def cmd_update_status(args) -> int:
    """Show the active release, the rollback target and all staged releases."""
    status = _updater().status()
    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False, default=str))
        return 0
    print(f"active:   {status['active_version'] or '-'}")
    print(f"rollback: {status['previous_version'] or '-'}")
    print("releases: " + (", ".join(status["releases"]) or "none"))
    return 0


def cmd_update_stage(args) -> int:
    """Stage a build directory as a release (copied and hashed)."""
    manifest = _updater().stage(args.version, args.source, note=args.note or "")
    print(json.dumps(manifest, indent=2, ensure_ascii=False, default=str))
    return 0 if manifest.get("ok") else 1


def cmd_update_activate(args) -> int:
    """Activate a staged release, remembering the current one for rollback."""
    result = _updater().activate(args.version, verify=not args.no_verify)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("ok") else 1


def cmd_update_rollback(args) -> int:
    """Revert to the previous release."""
    result = _updater().rollback()
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("ok") else 1


def cmd_update_fetch(args) -> int:
    """Fetch a release manifest over HTTP, verify it and stage it (channel-agnostic)."""
    from core.release_fetch import fetch_and_stage
    result = fetch_and_stage(args.url, _updater(), note=args.note or "")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("ok") else 1


def cmd_proactive(args) -> int:
    """Report proactivity: delivered, withheld, quiet hours and recent decisions."""
    from core.lifecycle import get_daemon
    status = get_daemon().services["proactive"].status()
    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False, default=str))
        return 0
    print(f"dedupe window: {status['dedupe_window_s']}s   "
          f"delivered: {status['delivered']}   withheld: {status['suppressed']}")
    for window in status["quiet_hours"]:
        scope = window["zone_id"] or window["person_id"] or "global"
        print(f"  quiet hours [{scope}] {window['start_hour']:02d}:00-"
              f"{window['end_hour']:02d}:00 enabled={window['enabled']}")
    print(f"  urgent whitelist: {', '.join(status['urgent_whitelist'])}\n")
    for item in status["history"]:
        marker = "WITHHELD" if item["suppressed"] else item["outcome"]
        print(f"  {marker:18s} {item['event_class']:14s} {item['title'][:52]}")
    return 0


def cmd_proactive_quiet(args) -> int:
    """Set quiet hours (per person and/or per zone)."""
    from core.lifecycle import get_daemon
    result = get_daemon().services["proactive"].set_quiet_hours(
        start_hour=args.start, end_hour=args.end, zone_id=args.zone or "",
        person_id=args.person or "", enabled=not args.off)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def cmd_perception(args) -> int:
    """Report perception zones, their sensing policies and the camera state."""
    from core.lifecycle import get_daemon
    status = get_daemon().services["perception"].status()
    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False, default=str))
        return 0
    camera = status["camera"]
    print(f"camera: {'available' if camera['available'] else 'unavailable'}"
          + (f" ({camera['reason']})" if not camera["available"] else ""))
    print(f"vision: {status['vision']['provider']} "
          f"({'available' if status['vision']['available'] else 'not configured'})")
    print(f"streaming: {status['streaming']}  events: {status['events']}\n")
    for zone in status["zones"]:
        policy = zone["policy"]
        print(f"  {zone['zone_id']:10s} {zone['kind']:9s} "
              f"camera={'on ' if policy['camera'] else 'off'} "
              f"mic={policy['microphone']:9s} motion={'on ' if policy['motion'] else 'off'} "
              f"retention={policy['retention_s']:4d}s"
              + ("  PRIVATE" if zone["private"] else ""))
    return 0


def cmd_perception_policy(args) -> int:
    """Set a zone's sensing policy (owner action)."""
    from core.lifecycle import get_daemon
    kwargs = {}
    if args.camera is not None:
        kwargs["camera"] = args.camera == "on"
    if args.mic:
        kwargs["microphone"] = args.mic
    if args.motion is not None:
        kwargs["motion"] = args.motion == "on"
    if args.retention is not None:
        kwargs["retention_s"] = args.retention
    result = get_daemon().services["perception"].set_policy(args.zone_id, **kwargs)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("ok") else 1


def cmd_perception_events(args) -> int:
    """Show recent perception events, or purge derived data."""
    from core.lifecycle import get_daemon
    service = get_daemon().services["perception"]
    if args.purge:
        print(json.dumps(service.purge(args.zone or ""), indent=2, ensure_ascii=False))
        return 0
    events = service.recent_events(limit=args.limit, zone_id=args.zone or "")
    if args.json:
        print(json.dumps({"events": events}, indent=2, ensure_ascii=False, default=str))
        return 0
    if not events:
        print("no perception events")
        return 0
    for event in events:
        print(f"  {event['type']:22s} {event['zone_id']:10s} "
              f"confidence={event['confidence']:.2f} source={event['source']}")
    return 0


def cmd_peripherals(args) -> int:
    """Report the local machine's GPIO / relay / sensor backends."""
    from core.lifecycle import get_daemon
    status = get_daemon().services["devices"].peripherals.status()
    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False, default=str))
        return 0
    if not status["available"]:
        print(f"no peripheral backend: {status['reason']}")
        print(f"capabilities: {', '.join(status['capabilities']) or 'none'}")
        return 0
    print(f"backend: {status['backend']} — {len(status['peripherals'])} peripheral(s)")
    for item in status["peripherals"]:
        print(f"  {item['peripheral_id']:10s} {item['kind']:7s} {item['name']:18s} "
              f"readable={item['readable']} writable={item['writable']}")
    return 0


def cmd_peripherals_run(args) -> int:
    """Run a peripheral capability on the local machine."""
    from core.lifecycle import get_daemon
    from core.contracts import CallContext
    params = json.loads(args.params) if args.params else {}
    invocation = get_daemon().services["devices"].execute(
        CallContext(person_id="owner"), "pc_main", args.capability, params)
    print(json.dumps(invocation.to_dict(), indent=2, ensure_ascii=False, default=str))
    return 0 if invocation.ok else 1


def cmd_devices_sync(args) -> int:
    """Inspect or drive sync v1: records, conflicts, policy, owner resolution."""
    from core.lifecycle import get_daemon
    from core.contracts import CallContext
    sync = get_daemon().services["devices"].sync
    action = args.action
    if action == "status":
        out = sync.status()
    elif action == "records":
        out = {"records": sync.records(args.namespace or "", since_cursor=args.since or 0)}
    elif action == "conflicts":
        out = {"conflicts": sync.conflicts(unresolved_only=args.unresolved,
                                           namespace=args.namespace or "")}
    elif action == "policy":
        if not args.policy:
            out = {"namespace": args.namespace or "default",
                   "policy": sync.policy_for(args.namespace or "default")}
        else:
            out = sync.set_policy(args.namespace or "default", args.policy)
    elif action == "resolve":
        if not args.conflict_id or not args.winner:
            print("resolve needs --conflict-id and --winner incoming|stored", file=sys.stderr)
            return 1
        out = sync.resolve_conflict(args.conflict_id, winner=args.winner)
    else:
        out = sync.status()
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0


def main(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="genie")
    p.add_argument("--safe", action="store_true",
                   help="safe mode (§14.2): disable automation capabilities "
                        "(browser/shell/input/plugins) while chat, memory and missions work")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("plugins", help="list plugins, permissions and runtime state")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_plugins)
    pg = sub.add_parser("plugins-grant", help="grant a plugin's declared permissions")
    pg.add_argument("plugin_id")
    pg.add_argument("permissions", nargs="*")
    pg.set_defaults(func=cmd_plugins_grant)
    ps = sub.add_parser("plugins-selftest", help="run each plugin's self-test")
    ps.add_argument("--verbose", action="store_true")
    ps.set_defaults(func=cmd_plugins_selftest)

    sk = sub.add_parser("skills", help="list learned and taught skills")
    sk.add_argument("--status")
    sk.add_argument("--scope")
    sk.add_argument("--json", action="store_true")
    sk.set_defaults(func=cmd_skills)
    sks = sub.add_parser("skills-search", help="search the skill registry")
    sks.add_argument("query")
    sks.add_argument("--limit", type=int, default=5)
    sks.set_defaults(func=cmd_skills_search)
    skr = sub.add_parser("skills-run", help="execute the best compatible learned skill")
    skr.add_argument("goal")
    skr.add_argument("--dry-run", action="store_true")
    skr.set_defaults(func=cmd_skills_run)
    skv = sub.add_parser("skills-versions", help="show a skill's versions")
    skv.add_argument("skill_id")
    skv.set_defaults(func=cmd_skills_versions)
    skb = sub.add_parser("skills-rollback", help="roll a skill back to a version")
    skb.add_argument("skill_id")
    skb.add_argument("--version", type=int)
    skb.set_defaults(func=cmd_skills_rollback)
    skl = sub.add_parser("skills-learn", help="learn a candidate skill from a mission")
    skl.add_argument("mission_id")
    skl.add_argument("--save", action="store_true", help="validate and save the candidate")
    skl.add_argument("--activate", action="store_true")
    skl.set_defaults(func=cmd_skills_learn)
    skt = sub.add_parser("skills-teach", help="teaching mode: start/stop/learn/discard/status")
    skt.add_argument("action", choices=["start", "stop", "learn", "discard", "status"])
    skt.add_argument("--goal")
    skt.add_argument("--app")
    skt.add_argument("--name")
    skt.add_argument("--no-save", action="store_true")
    skt.add_argument("--activate", action="store_true")
    skt.set_defaults(func=cmd_skills_teach)
    sst = sub.add_parser("skills-selftest", help="verify the skill subsystem")
    sst.set_defaults(func=cmd_skills_selftest)

    dv = sub.add_parser("devices", help="list devices, trust tiers and presence")
    dv.add_argument("--json", action="store_true")
    dv.set_defaults(func=cmd_devices)
    dvp = sub.add_parser("devices-pair", help="pair a device using its displayed code")
    dvp.add_argument("device_id")
    dvp.add_argument("--code", required=True)
    dvp.add_argument("--name")
    dvp.add_argument("--type")
    dvp.set_defaults(func=cmd_devices_pair)
    dvu = sub.add_parser("devices-unpair", help="revoke a device pairing")
    dvu.add_argument("device_id")
    dvu.set_defaults(func=cmd_devices_unpair)
    dvt = sub.add_parser("devices-trust", help="set a device's trust tier")
    dvt.add_argument("device_id")
    dvt.add_argument("tier", choices=["owner_primary", "owner_secondary", "family",
                                      "guest", "untrusted"])
    dvt.set_defaults(func=cmd_devices_trust)
    dvg = sub.add_parser("devices-grant", help="grant a device's capability scopes")
    dvg.add_argument("device_id")
    dvg.add_argument("scopes", nargs="*")
    dvg.set_defaults(func=cmd_devices_grant)
    dvr = sub.add_parser("devices-run", help="send a capability to a device")
    dvr.add_argument("device_id")
    dvr.add_argument("capability")
    dvr.add_argument("--params", help="JSON object of parameters")
    dvr.set_defaults(func=cmd_devices_run)
    dvn = sub.add_parser("devices-node", help="run a GENIE device node on this machine")
    dvn.add_argument("--device-id", required=True)
    dvn.add_argument("--name")
    dvn.add_argument("--type")
    dvn.add_argument("--host", default="127.0.0.1")
    dvn.add_argument("--port", type=int, default=8765)
    dvn.add_argument("--code", help="pairing code (generated and printed if omitted)")
    dvn.add_argument("--root", help="sandbox root for file capabilities")
    dvn.add_argument("--seconds", type=float, default=0)
    dvn.set_defaults(func=cmd_devices_node)
    dvs = sub.add_parser("devices-selftest", help="verify the device mesh end to end")
    dvs.set_defaults(func=cmd_devices_selftest)
    ag = sub.add_parser("agents", help="report agent teams, budgets and experience")
    ag.add_argument("--json", action="store_true")
    ag.set_defaults(func=cmd_agents)
    agp = sub.add_parser("agents-plan", help="show the team a mission would use")
    agp.add_argument("objective")
    agp.add_argument("--complexity", type=float, default=0.5)
    agp.add_argument("--needs", nargs="*")
    agp.add_argument("--budget", type=float, default=0.0)
    agp.add_argument("--json", action="store_true")
    agp.set_defaults(func=cmd_agents_plan)
    agf = sub.add_parser("agents-failover", help="simulate a provider dying mid-mission")
    agf.add_argument("mission_id")
    agf.add_argument("--from-provider", required=True)
    agf.add_argument("--reason", default="provider outage")
    agf.set_defaults(func=cmd_agents_failover)
    agc = sub.add_parser("agents-cancel", help="cancel a mission team")
    agc.add_argument("mission_id")
    agc.add_argument("--reason", default="owner requested")
    agc.set_defaults(func=cmd_agents_cancel)
    # --- operator control surface (Phase 11 §11) ---
    op = sub.add_parser("ops", help="live operator dashboard: teams, budgets, audit, improvements")
    op.add_argument("--json", action="store_true")
    op.set_defaults(func=cmd_ops)
    opc = sub.add_parser("ops-control", help="perform one audited operator action on a team")
    opc.add_argument("action",
                     choices=["pause", "resume", "cancel", "force_failover",
                              "retire_orphans", "set_budget_cap", "throttle"])
    opc.add_argument("mission_id", nargs="?", default="")
    opc.add_argument("--from-provider", default="")
    opc.add_argument("--reason", default="operator requested")
    opc.add_argument("--to-provider", default="")
    opc.add_argument("--tokens", type=int, default=0)
    opc.add_argument("--cost-usd", type=float, default=0.0)
    opc.add_argument("--model-calls", type=int, default=0)
    opc.add_argument("--tool-calls", type=int, default=0)
    opc.add_argument("--wall-ms", type=int, default=0)
    opc.add_argument("--max-concurrency", type=int, default=0)
    opc.add_argument("--actor", default="operator")
    opc.add_argument("--json", action="store_true")
    opc.set_defaults(func=cmd_ops_control)
    opa = sub.add_parser("ops-audit", help="recent operator audit trail")
    opa.add_argument("--limit", type=int, default=25)
    opa.add_argument("--mission")
    opa.add_argument("--json", action="store_true")
    opa.set_defaults(func=cmd_ops_audit)
    opo = sub.add_parser("ops-outcomes", help="recorded per-mission outcome snapshots")
    opo.add_argument("--limit", type=int, default=50)
    opo.add_argument("--json", action="store_true")
    opo.set_defaults(func=cmd_ops_outcomes)
    opi = sub.add_parser("ops-improve", help="experience->improvement suggestions (Phase 12 input)")
    opi.add_argument("--json", action="store_true")
    opi.set_defaults(func=cmd_ops_improve)
    # --- backup & restore (Phase 14.1) ---
    bk = sub.add_parser("backup", help="create a verified backup of db, config and vault")
    bk.add_argument("--dest")
    bk.add_argument("--note", default="")
    bk.add_argument("--no-vault", action="store_true", help="exclude the encrypted vault")
    bk.add_argument("--json", action="store_true")
    bk.set_defaults(func=cmd_backup_create)
    bkl = sub.add_parser("backup-list", help="list available backups")
    bkl.add_argument("--dir")
    bkl.add_argument("--json", action="store_true")
    bkl.set_defaults(func=cmd_backup_list)
    bkv = sub.add_parser("backup-verify", help="check a backup's integrity without restoring")
    bkv.add_argument("backup")
    bkv.set_defaults(func=cmd_backup_verify)
    bkr = sub.add_parser("backup-restore", help="restore a backup (verifies first)")
    bkr.add_argument("backup")
    bkr.add_argument("--no-verify", action="store_true",
                     help="skip integrity checks (not recommended)")
    bkr.add_argument("--no-vault", action="store_true")
    bkr.set_defaults(func=cmd_backup_restore)
    # --- updater & rollback (Phase 14.3) ---
    us = sub.add_parser("update-status", help="active release, rollback target, staged releases")
    us.add_argument("--json", action="store_true")
    us.set_defaults(func=cmd_update_status)
    ust = sub.add_parser("update-stage", help="stage a build directory as a release")
    ust.add_argument("version")
    ust.add_argument("source")
    ust.add_argument("--note", default="")
    ust.set_defaults(func=cmd_update_stage)
    ua = sub.add_parser("update-activate", help="activate a staged release")
    ua.add_argument("version")
    ua.add_argument("--no-verify", action="store_true", help="skip integrity checks (not advised)")
    ua.set_defaults(func=cmd_update_activate)
    ur = sub.add_parser("update-rollback", help="revert to the previous release")
    ur.set_defaults(func=cmd_update_rollback)
    uf = sub.add_parser("update-fetch",
                        help="fetch a release manifest and stage it (any URL you trust)")
    uf.add_argument("url")
    uf.add_argument("--note", default="")
    uf.set_defaults(func=cmd_update_fetch)
    pa = sub.add_parser("proactive", help="report notifications, quiet hours and decisions")
    pa.add_argument("--json", action="store_true")
    pa.set_defaults(func=cmd_proactive)
    paq = sub.add_parser("proactive-quiet", help="set quiet hours")
    paq.add_argument("--start", type=int, required=True)
    paq.add_argument("--end", type=int, required=True)
    paq.add_argument("--zone")
    paq.add_argument("--person")
    paq.add_argument("--off", action="store_true")
    paq.set_defaults(func=cmd_proactive_quiet)
    pc = sub.add_parser("perception", help="report perception zones and sensing policies")
    pc.add_argument("--json", action="store_true")
    pc.set_defaults(func=cmd_perception)
    pcp = sub.add_parser("perception-policy", help="set a zone's sensing policy")
    pcp.add_argument("zone_id")
    pcp.add_argument("--camera", choices=["on", "off"])
    pcp.add_argument("--mic", choices=["off", "wake_only", "on"])
    pcp.add_argument("--motion", choices=["on", "off"])
    pcp.add_argument("--retention", type=int)
    pcp.set_defaults(func=cmd_perception_policy)
    pce = sub.add_parser("perception-events", help="show or purge perception events")
    pce.add_argument("--zone")
    pce.add_argument("--limit", type=int, default=20)
    pce.add_argument("--purge", action="store_true")
    pce.add_argument("--json", action="store_true")
    pce.set_defaults(func=cmd_perception_events)
    pr = sub.add_parser("peripherals", help="report GPIO/relay/sensor backends")
    pr.add_argument("--json", action="store_true")
    pr.set_defaults(func=cmd_peripherals)
    prr = sub.add_parser("peripherals-run", help="run a peripheral capability locally")
    prr.add_argument("capability")
    prr.add_argument("--params", help="JSON object of parameters")
    prr.set_defaults(func=cmd_peripherals_run)
    dvy = sub.add_parser("devices-sync", help="inspect/drive sync v1 (records, conflicts, policy)")
    dvy.add_argument("action", nargs="?", default="status",
                     choices=["status", "records", "conflicts", "policy", "resolve"])
    dvy.add_argument("--namespace")
    dvy.add_argument("--policy")
    dvy.add_argument("--since", type=int, default=0)
    dvy.add_argument("--unresolved", action="store_true")
    dvy.add_argument("--conflict-id")
    dvy.add_argument("--winner", choices=["incoming", "stored"])
    dvy.set_defaults(func=cmd_devices_sync)

    sub.add_parser("voice-devices").set_defaults(func=cmd_voice_devices)
    sub.add_parser("voice-status").set_defaults(func=cmd_voice_status)
    vs = sub.add_parser("voice-selftest")
    vs.add_argument("--command", default="volume 30")
    vs.set_defaults(func=cmd_voice_selftest)
    vp = sub.add_parser("voice-ptt")
    vp.add_argument("--seconds", type=float, default=5.0)
    vp.set_defaults(func=cmd_voice_ptt)
    vy = sub.add_parser("voice-say")
    vy.add_argument("text")
    vy.set_defaults(func=cmd_voice_say)
    ve = sub.add_parser("voice-e2e", help="real microphone -> STT -> action -> verified -> spoken")
    ve.add_argument("--seconds", type=float, default=8.0)
    ve.add_argument("--phrase", default="GENIE, awaz 30 kar do")
    ve.add_argument("--expect", default="", help="substring expected in the transcript")
    ve.set_defaults(func=cmd_voice_e2e)
    va = sub.add_parser("voice-acoustic", help="automated acoustic round trip (speaker -> mic)")
    va.add_argument("--phrase", default="volume 30")
    va.add_argument("--seconds", type=float, default=7.0)
    va.set_defaults(func=cmd_voice_acoustic)

    sub.add_parser("daemon").set_defaults(func=cmd_daemon)
    sub.add_parser("status").set_defaults(func=cmd_status)

    ns = sub.add_parser("needle-setup", help="provision + verify the official Needle 2 runtime")
    ns.add_argument("--force", action="store_true", help="re-download even if verified")
    ns.add_argument("--no-smoke", action="store_true")
    ns.set_defaults(func=cmd_needle_setup)

    nsm = sub.add_parser("needle-smoke", help="run the NEDLE2 routing smoke test")
    nsm.add_argument("--large", type=int, default=0,
                     help="also probe routing with N extra filler tools")
    nsm.set_defaults(func=cmd_needle_smoke)
    c = sub.add_parser("chat")
    c.add_argument("text")
    c.add_argument("--dry-run", action="store_true")
    c.set_defaults(func=cmd_chat)
    sub.add_parser("selftest").set_defaults(func=cmd_selftest)

    args = p.parse_args(argv)
    if getattr(args, "safe", False):
        from core.hardening import set_safe_mode
        set_safe_mode(True)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
