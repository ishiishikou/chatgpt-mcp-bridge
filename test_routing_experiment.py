"""Offline only: temporary ledgers and an in-memory MCP transport, no host UI."""
import importlib
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from routing_experiment_store import RoutingStore


class RoutingStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "routing-experiment-v1.sqlite3"
        self.store = RoutingStore(self.path)

    def claim(self, mode="active", device="iOS", run_id=None):
        return self.store.claim(mode, device, run_id or str(uuid4()), "2026-11-15T23:00:00.000Z")

    def test_lazy_empty_and_four_independent_slots(self):
        self.assertFalse(self.path.exists())
        for mode in ("active", "new"):
            for device in ("iOS", "Windows"):
                self.assertTrue(self.claim(mode, device)["granted"])
                self.assertEqual(self.claim(mode, device)["reason"], "already_claimed")

    def test_same_mode_device_visible_and_hidden_are_distinct_one_shot_slots(self):
        run_visible = str(uuid4())
        run_hidden = str(uuid4())
        self.assertTrue(self.store.claim("new", "iOS", run_visible, "2026-11-15T23:00:00Z", "visible")["granted"])
        self.assertTrue(self.store.claim("new", "iOS", run_hidden, "2026-11-15T23:00:00Z", "hidden")["granted"])
        self.assertFalse(self.store.claim("new", "iOS", str(uuid4()), "2026-11-15T23:00:00Z", "visible")["granted"])
        self.assertFalse(self.store.claim("new", "iOS", str(uuid4()), "2026-11-15T23:00:00Z", "hidden")["granted"])
        self.assertFalse(self.store.claim("new", "Windows", str(uuid4()), "2026-11-15T23:00:00Z", "invisible")["granted"])

    def test_parallel_regeneration_grants_exactly_one(self):
        def attempt(_):
            return RoutingStore(self.path).claim("new", "Windows", str(uuid4()),
                                                 "2026-11-15T23:00:00.000Z")
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(attempt, range(32)))
        self.assertEqual(sum(x["granted"] for x in results), 1)

    def test_persists_after_restart_and_same_run_never_regrants(self):
        run = str(uuid4())
        self.assertTrue(self.claim(run_id=run)["granted"])
        self.store = RoutingStore(self.path)
        self.assertFalse(self.claim(run_id=run)["granted"])
        self.assertFalse(self.claim()["granted"])
        # Reusing a Run ID for a different slot must not grant another attempt.
        self.assertFalse(self.claim("new", run_id=run)["granted"])

    def test_unknown_and_each_result_permanently_consume_slot(self):
        for outcome in ("unknown", "host_accepted", "host_rejected", "skipped"):
            with self.subTest(outcome=outcome):
                with tempfile.TemporaryDirectory() as directory:
                    store = RoutingStore(Path(directory) / self.path.name)
                    result = store.claim("active", "iOS", str(uuid4()), "2026-11-15T23:00:00Z")
                    self.assertTrue(store.report(result["claim_token"], outcome)["recorded"])
                    self.assertFalse(store.report(result["claim_token"], "host_accepted")["recorded"])
                    self.assertFalse(store.claim("active", "iOS", str(uuid4()),
                                                 "2026-11-15T23:00:00Z")["granted"])

    def test_bad_inputs_never_create_database(self):
        for args in (("bad", "iOS", str(uuid4()), "2026-11-15T23:00:00Z"),
                     ("new", "unknown", str(uuid4()), "2026-11-15T23:00:00Z"),
                     ("new", "Windows", "arbitrary text", "2026-11-15T23:00:00Z"),
                     ("new", "Windows", str(uuid4()), "not a timestamp")):
            self.assertFalse(self.store.claim(*args)["granted"])
        self.assertFalse(self.store.report("bad", "unknown")["recorded"])
        self.assertFalse(self.path.exists())

    def test_storage_failure_fails_closed_without_repair(self):
        self.path.write_text("not a SQLite database")
        self.assertEqual(self.claim()["reason"], "storage_unavailable")
        self.assertEqual(self.path.read_text(), "not a SQLite database")

    def test_wrong_filename_symlink_and_hardlink_are_refused(self):
        with self.assertRaises(ValueError):
            RoutingStore(Path(self.tmp.name) / "dispatch.sqlite3")
        other = Path(self.tmp.name) / "retired.sqlite3"
        other.write_text("do not touch")
        self.path.symlink_to(other)
        self.assertFalse(self.claim()["granted"])
        self.path.unlink()
        self.path.hardlink_to(other)
        self.assertFalse(self.claim()["granted"])
        self.assertEqual(other.read_text(), "do not touch")

    def test_report_requires_token_and_leaves_initial_unknown(self):
        result = self.claim()
        self.assertFalse(self.store.report("0" * 64, "host_accepted")["recorded"])
        self.assertFalse(self.store.report(result["claim_token"], "delivered")["recorded"])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT outcome FROM claims").fetchone()[0], "unknown")

    def test_lifecycle_events_bounded_and_without_free_text(self):
        run = str(uuid4())
        for _ in range(64):
            self.assertTrue(self.store.record("active", "iOS", run, "visibilitychange",
                                             "hidden", 123)["recorded"])
        self.assertEqual(self.store.record("active", "iOS", run, "teardown", "hidden", 123)
                         ["reason"], "log_limit")
        self.assertFalse(self.store.record("active", "iOS", run, "secret text", "hidden", 0)["recorded"])
        # Logging alone never consumes a sending slot.
        self.assertTrue(self.claim(run_id=run)["granted"])


class RoutingServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_and_read_only_calls_do_not_create_ledger(self):
        import routing_experiment_server as server
        with ExitStack() as stack:
            for operation in ("sqlite3.connect", "pathlib.Path.mkdir", "pathlib.Path.write_text"):
                stack.enter_context(patch(operation, side_effect=AssertionError(operation)))
            importlib.reload(server)
            for name in ("show_lifecycle_probe", "show_auto_active_probe", "show_auto_new_probe"):
                result = await server.mcp.call_tool(name, {})
                self.assertFalse(result.is_error)
            for uri in (server.lifecycle.URI, *server.URIS.values()):
                self.assertTrue(list(await server.mcp.read_resource(uri)))

    async def test_sdk_tools_metadata_and_lifecycle_unchanged(self):
        import routing_experiment_server as server
        import lifecycle_server
        tools = {t.name: t for t in await server.mcp.list_tools()}
        self.assertEqual(set(tools), {"show_lifecycle_probe", "show_auto_active_probe",
                                     "show_auto_new_probe", "claim_routing_probe",
                                     "report_routing_probe", "record_routing_event"})
        for helper in ("claim_routing_probe", "report_routing_probe", "record_routing_event"):
            self.assertEqual(tools[helper].meta["ui"], {"visibility": ["app"]})
        self.assertIs(server.show_lifecycle_probe, lifecycle_server.show_lifecycle_probe)
        self.assertEqual(tools["show_lifecycle_probe"].model_dump(),
                         (await lifecycle_server.mcp.list_tools())[0].model_dump())
        for mode in ("active", "new"):
            tool = tools[f"show_auto_{mode}_probe"]
            self.assertIn("automatically", tool.description)
            self.assertIn("15 seconds", tool.description)
            self.assertEqual(tool.meta["ui"]["resourceUri"], server.URIS[mode])
        resources = await server.mcp.list_resources()
        self.assertEqual(len(resources), 3)
        for uri in server.URIS.values():
            contents = list(await server.mcp.read_resource(uri))[0]
            self.assertEqual(contents.meta["ui"]["csp"],
                             {"connectDomains": [], "resourceDomains": []})

    async def test_mcp_protocol_list_read_and_claim_over_memory_transport(self):
        """Real MCP initialize/tools/list/resources/read/tools/call, without sockets."""
        import anyio
        from mcp import ClientSession
        from mcp.shared.memory import create_client_server_memory_streams
        import routing_experiment_server as server
        with tempfile.TemporaryDirectory() as directory:
            store = RoutingStore(Path(directory) / "routing-experiment-v1.sqlite3")
            with patch.object(server, "store", store):
                async with create_client_server_memory_streams() as (client, transport):
                    async with anyio.create_task_group() as group:
                        group.start_soon(server.mcp._lowlevel_server.run, *transport,
                                         server.mcp._lowlevel_server.create_initialization_options())
                        async with ClientSession(*client) as session:
                            await session.initialize()
                            tools = await session.list_tools()
                            self.assertEqual(len(tools.tools), 6)
                            for uri in (server.lifecycle.URI, *server.URIS.values()):
                                resource = await session.read_resource(uri)
                                self.assertEqual(resource.contents[0].mime_type,
                                                 "text/html;profile=mcp-app")
                            args = dict(mode="new", device="Windows", run_id=str(uuid4()),
                                        generated_at="2026-11-15T23:00:00Z")
                            result = await session.call_tool("claim_routing_probe", args)
                            self.assertTrue(json.loads(result.content[0].text)["granted"])
                            result = await session.call_tool("claim_routing_probe", args)
                            self.assertFalse(json.loads(result.content[0].text)["granted"])
                        group.cancel_scope.cancel()


if __name__ == "__main__":
    unittest.main()
