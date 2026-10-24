import http.client
import json
import threading
import time
from urllib.parse import urlencode
from unittest.mock import patch

from test_board import BoardFixture, FakeLive, live_plan
from runtime.core import BoardError
from runtime.harness import contract


# Public/personal 1.2.2 share the same runtime and host-instruction fingerprint.
LEGACY = "15e6a41441b8660aaf0f9cb7b8afd56824c0a3bd462aa0797d0fc4cb065770dd"
V123 = "d714360639068e2d79d920e3a5b10e75712530b60cfad8aa7848d71416b85bbd"


from legacy_migration_fixture import LegacyMigrationFixture


class TransportRepairTests(LegacyMigrationFixture, BoardFixture):
    def failed_legacy_job(self, implementation=LEGACY):
        self.live = FakeLive(fail_once="gemini-4-argon", uncertain=True)
        self.board.providers["vertex"] = self.live
        legacy = dict(contract(), implementation_sha256=implementation)
        with patch("runtime.harness.contract", return_value=legacy):
            proposal = self.prepare(live_plan(max_rounds=1))
            self.approve(proposal)
            result = self.wait(proposal["job_id"])
        self.assertEqual("needs_attention", result["state"])
        return proposal, result

    def test_v123_transport_update_preserves_prior_confirmation_and_drafts(self):
        proposal, original = self.failed_legacy_job(V123)
        update = self.board.resume(self.scope, proposal['job_id'])
        info = self.board.store.ticket_info(update['confirmation_token'])
        self.assertEqual('1.2.3', info['transport_update']['from_version'])
        self.assertEqual('1.3.1', info['transport_update']['to_version'])
        self.assertEqual(4, len(self.live.calls))
        self.approve(update)
        result = self.wait(proposal['job_id'])
        self.assertEqual('waiting_chairman', result['state'])
        succeeded = {a['id'] for a in original['attempts'] if a['state'] == 'succeeded'}
        self.assertEqual(4, len([a for a in result['attempts'] if a['id'] in succeeded]))
        self.assertEqual(proposal['plan_hash'], result['execution_updates'][0]['previous_plan_hash'])

    def test_patch_resume_needs_new_approval_and_reuses_four_answers(self):
        proposal, original = self.failed_legacy_job()
        job = proposal["job_id"]
        successful = [a["id"] for a in original["attempts"] if a["state"] == "succeeded"]
        self.assertEqual(4, len(successful))
        update = self.board.resume(self.scope, job)
        info = self.board.store.ticket_info(update["confirmation_token"])
        self.assertEqual("transport_resume", info["kind"])
        self.assertNotEqual(proposal["plan_hash"], info["job_info"]["plan_hash"])
        self.assertEqual(proposal["plan_hash"], self.board.store.get(self.scope, job)["plan_hash"])
        self.board.dispatch()
        self.assertEqual(4, len(self.live.calls))
        self.approve(update)
        result = self.wait(job)
        self.assertEqual("waiting_chairman", result["state"])
        self.assertEqual(successful, [a["id"] for a in result["attempts"] if a["id"] in successful])
        self.assertEqual(9, len(self.live.calls))  # 4 old drafts + 1 retry + 4 reviews
        self.assertAlmostEqual(original["accounted_cost_usd"], result["accounted_cost_usd"])
        self.assertEqual(proposal["plan_hash"], result["execution_updates"][0]["previous_plan_hash"])
        with self.assertRaises(BoardError):
            self.board.store.confirm(update["confirmation_token"])

    def test_unknown_runtime_changed_prompt_or_existing_review_cannot_repair(self):
        proposal, _ = self.failed_legacy_job()
        job = proposal["job_id"]
        with self.assertRaises(BoardError):
            self.board.resume(self.scope2, job)
        with patch("runtime.transport_repair.work_contract", return_value="changed-prompt"), self.assertRaises(BoardError):
            self.board.resume(self.scope, job)
        with self.board.store.connect() as db:
            db.execute("INSERT INTO review_packets VALUES(?,1,0,'{}','synthetic')", (job,))
        with self.assertRaises(BoardError):
            self.board.resume(self.scope, job)
        with self.board.store.connect() as db:
            db.execute("DELETE FROM review_packets WHERE job=?", (job,))
        with patch("runtime.transport_repair.LEGACY_BUILDS", {}), self.assertRaises(BoardError):
            self.board.resume(self.scope, job)
        self.assertEqual(4, len(self.live.calls))

    def test_attempt_changes_and_fresh_runtime_changes_invalidate_pending_ticket(self):
        proposal, _ = self.failed_legacy_job()
        job = proposal["job_id"]
        update = self.board.resume(self.scope, job)
        changed = dict(contract(), implementation_sha256="another-build")
        with patch("runtime.harness.contract", return_value=changed), self.assertRaises(BoardError):
            self.board.store.confirm(update["confirmation_token"])
        with self.board.store.connect() as db:
            db.execute("UPDATE attempts SET charged=0.001 WHERE job=? AND state='uncertain'", (job,))
        with self.assertRaises(BoardError):
            self.board.store.confirm(update["confirmation_token"])
        self.assertEqual(proposal["plan_hash"], self.board.store.get(self.scope, job)["plan_hash"])
        self.assertEqual(4, len(self.live.calls))

    def test_price_expiry_and_plan_tampering_cannot_transfer_approval(self):
        proposal, _ = self.failed_legacy_job()
        job = proposal["job_id"]
        update = self.board.resume(self.scope, job)
        later = time.time() + 4000
        with patch("runtime.catalog.time.time", return_value=later), self.assertRaises(BoardError):
            self.board.store.confirm(update["confirmation_token"])
        with self.board.store.connect() as db:
            saved = db.execute("SELECT plan FROM transport_repairs WHERE token=?", (update["confirmation_token"],)).fetchone()[0]
            plan = json.loads(saved)
            plan["max_cost_usd"] += 1
            from runtime.core import canonical, digest
            db.execute("UPDATE transport_repairs SET plan=?,plan_hash=? WHERE token=?", (canonical(plan), digest(plan), update["confirmation_token"]))
        with self.assertRaises(BoardError):
            self.board.store.confirm(update["confirmation_token"])
        self.assertEqual(proposal["plan_hash"], self.board.store.get(self.scope, job)["plan_hash"])
        self.assertEqual(4, len(self.live.calls))

    def test_local_form_discloses_repair_and_rejects_null_origin_before_dispatch(self):
        from runtime.server import LocalServer
        proposal, _ = self.failed_legacy_job()
        update = self.board.resume(self.scope, proposal["job_id"])
        token = update["confirmation_token"]
        from runtime import localization
        localization.preference(self.board.store, self.scope, 'synthetic-ui', 'zh-Hans')
        localization.bind_ticket(self.board.store, update['confirmation_token'], self.scope, 'synthetic-ui')
        server = LocalServer(self.board, "synthetic-server-token")
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            conn.request("GET", "/confirm/" + token)
            response = conn.getresponse()
            self.assertEqual("same-origin", response.getheader("Referrer-Policy"))
            body = response.read().decode()
            self.assertIn('批准传输更新并续跑', body)
            self.assertIn('沿用 4 份已完成初稿', body)
            conn.close()
            fields = urlencode({"token": token, "consent": "yes"})
            for origin, expected in (("null", 403), (server.origin, 200)):
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                conn.request("POST", "/approve", fields, {"Origin": origin, "Content-Type": "application/x-www-form-urlencoded"})
                response = conn.getresponse()
                self.assertEqual(expected, response.status)
                response.read(); conn.close()
                if origin == "null":
                    self.assertEqual(4, len(self.live.calls))
            self.assertEqual("waiting_chairman", self.wait(proposal["job_id"])["state"])
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    import unittest
    unittest.main()
