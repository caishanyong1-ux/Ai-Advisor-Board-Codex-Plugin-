import http.client
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import threading
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

from test_board import plan, live_plan

ROOT = Path(os.environ.get("AAB_PLUGIN_ROOT", Path(__file__).resolve().parent.parent))


class Session:
    def __init__(self, env):
        self.process = subprocess.Popen([sys.executable, str(ROOT / "mcp/launch.py")], cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        self.sequence = 0
        self.rpc("initialize", {"protocolVersion": "2024-11-05", "clientInfo": {"name": "isolated-test", "version": "1"}, "capabilities": {}})

    def rpc(self, method, params):
        self.sequence += 1
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise AssertionError("MCP closed unexpectedly: " + self.process.stderr.read())
        result = json.loads(line)
        if "error" in result:
            raise AssertionError(str(result))
        return result["result"]

    def call(self, name, args):
        result = self.rpc("tools/call", {"name": name, "arguments": args})
        if result.get("isError"):
            raise AssertionError(result["content"][0]["text"])
        return json.loads(result["content"][0]["text"])

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            self.process.wait(timeout=5)
        self.process.stdout.close()
        self.process.stderr.close()


class StdioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aab-stdio-")
        self.root = Path(self.temp.name)
        self.home = self.root / "state"
        self.env = {**os.environ, "AAB_CODEX_HOME": str(self.home), "PYTHONDONTWRITEBYTECODE": "1"}
        self.env.pop("OPENROUTER_API_KEY", None)
        self.sessions = []
        self.one = self.session()
        for name in ("project-a", "project-b"):
            (self.root / name).mkdir()
        result = self.one.call("board_bind_project", {"workspace_path": str(self.root / "project-a")})
        self.scope = {key: result[key] for key in ("project_id", "project_token")}

    def session(self):
        session = Session(self.env)
        self.sessions.append(session)
        return session

    def tearDown(self):
        for session in self.sessions:
            session.close()
        endpoint = self.home / "endpoint.json"
        if endpoint.exists():
            pid = json.loads(endpoint.read_text())["pid"]
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            time.sleep(.4)
        self.temp.cleanup()

    def http(self, url, method="GET", fields=None, origin=True, auth=None, host=None):
        target = urlsplit(url)
        conn = http.client.HTTPConnection("127.0.0.1", target.port, timeout=5)
        headers = {}
        if fields is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            if origin:
                headers["Origin"] = "http://" + target.netloc if origin is True else origin
        if auth:
            headers["Authorization"] = "Bearer " + auth
        if host:
            headers["Host"] = host
        conn.request(method, target.path + ('?' + target.query if target.query else ''), urlencode(fields).encode() if fields is not None else None, headers)
        response = conn.getresponse()
        self.last_headers = dict(response.getheaders())
        body = response.read().decode()
        status = response.status
        conn.close()
        return status, body

    def confirm_mock(self, url):
        # This fixture simulates the human only in an isolated mock state directory.
        token = urlsplit(url).path.split("/")[-1]
        self.assertEqual(200, self.http(url.rsplit("/confirm/", 1)[0] + "/approve", "POST", {"token": token, "consent": "yes"})[0])

    def wait(self, session, job, target="waiting_chairman"):
        for _ in range(100):
            result = session.call("board_status", {"scope": self.scope, "job_id": job})
            if result["state"] == target:
                return result
            time.sleep(.03)
        self.fail(str(result))

    def test_close_mcp_then_reconnect_background_result_and_other_project(self):
        proposal = self.one.call("board_prepare", {"scope": self.scope, "conversation_id": "test-chat-a", "plan": plan()})
        url, job = proposal["approval_url"], proposal["job_id"]
        status, body = self.http(url)
        self.assertEqual(200, status)
        self.assertIn("project-a", body)
        self.assertIn("mock/one", body)
        self.one.close()
        self.confirm_mock(url)
        two = self.session()
        self.assertEqual("waiting_chairman", self.wait(two, job)["state"])
        result = two.call("board_result", {"scope": self.scope, "job_id": job})
        self.assertEqual(4, len(result["results"]))
        other = two.call("board_bind_project", {"workspace_path": str(self.root / "project-b")})
        other = {k: other[k] for k in ("project_id", "project_token")}
        error = two.rpc("tools/call", {"name": "board_result", "arguments": {"scope": other, "job_id": job}})
        self.assertTrue(error["isError"])
        self.assertEqual([], two.call("board_list", {"scope": other})["jobs"])

    def test_independent_codex_bridge_and_scoring_over_real_mcp(self):
        from runtime.providers import MockProvider
        raw=plan(include_codex_member=True,rubric_profile="research_software",members=[dict(model="mock/"+str(i),role="Independent",rationale="Synthetic") for i in range(4)])
        proposal=self.one.call("board_prepare",{"scope":self.scope,"conversation_id":"isolated-agents","plan":raw})
        _,html=self.http(proposal["approval_url"])
        self.assertIn('Shared scoring',html)
        self.assertIn("engineering_proportionality",html)
        self.assertIn("research_writing",html)
        args={"scope":self.scope,"job_id":proposal["job_id"]}
        premature=self.one.rpc("tools/call",{"name":"board_codex_packet","arguments":dict(args,stage="proposal")})
        self.assertTrue(premature["isError"])
        self.confirm_mock(proposal["approval_url"])
        for stage in ("proposal","review"):
            self.wait(self.one,proposal["job_id"],"waiting_codex_"+stage)
            packet=self.one.call("board_codex_packet",dict(args,stage=stage))
            answer=MockProvider(0).call({"model":"mock/local"},[{"content":packet["prompt"]}],30,"test",threading.Event())["content"]
            submitted=self.one.call("board_submit_codex",dict(args,assignment_id=packet["assignment_id"],packet_hash=packet["packet_hash"],agent_id="synthetic-"+stage,answer=answer))
            self.assertTrue(submitted["accepted"])
        self.wait(self.one,proposal["job_id"])
        result=self.one.call("board_result",args)
        self.assertEqual(5,result["deliberations"][0]["valid_reviewers"])
        packet=self.one.call("board_chairman_packet",args)
        self.assertEqual(result["deliberations"][0]["report_hash"],packet["deliberation_hash"])
        self.assertIn("STAGE 1",packet["content"])
        self.assertEqual(10,len(result["attempts"]))

    def test_browser_origin_token_and_html_escaping(self):
        proposal = self.one.call("board_prepare", {"scope": self.scope, "conversation_id": "test-chat-a", "plan": plan(title="<script>alert(1)</script>")})
        url = proposal["approval_url"]
        status, body = self.http(url)
        self.assertEqual(200, status)
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)
        fields = {"token": urlsplit(url).path.split("/")[-1], "consent": "yes"}
        post = url.rsplit("/confirm/", 1)[0] + "/approve"
        self.assertEqual(403, self.http(post, "POST", fields, origin=False)[0])
        self.assertEqual(403, self.http(url, host="malicious.example")[0])
        self.assertEqual("awaiting_approval", self.one.call("board_status", {"scope": self.scope, "job_id": proposal["job_id"]})["state"])
        self.confirm_mock(url)
        self.assertEqual(400, self.http(post, "POST", fields)[0])
        self.wait(self.one, proposal["job_id"])

    def test_worker_restart_preserves_completed_results(self):
        proposal = self.one.call("board_prepare", {"scope": self.scope, "conversation_id": "test-chat-a", "plan": plan()})
        self.confirm_mock(proposal["approval_url"])
        before = self.wait(self.one, proposal["job_id"])
        endpoint = json.loads((self.home / "endpoint.json").read_text())
        os.kill(endpoint["pid"], signal.SIGTERM)
        time.sleep(.6)
        after = self.one.call("board_status", {"scope": self.scope, "job_id": proposal["job_id"]})
        self.assertEqual("waiting_chairman", after["state"])
        self.assertEqual([a["id"] for a in before["attempts"]], [a["id"] for a in after["attempts"]])
        self.assertNotEqual(endpoint["pid"], json.loads((self.home / "endpoint.json").read_text())["pid"])

    def test_task_sources_completed_followup_and_session_exit_over_stdio(self):
        chat = {"scope": self.scope, "conversation_id": "meeting-chat"}
        opened = self.one.call("board_open_task", {**chat, "title": "Synthetic ongoing project", "brief": "Compare choices using the local specification"})
        self.assertEqual("active", opened["task"]["state"])
        source = self.root / "project-a" / "spec.txt"
        source.write_text("Unselected preface\nThe original budget is 40 credits.\n")
        captured = self.one.call("board_capture_sources", {**chat, "files": [{"path": "spec.txt", "start_line": 2, "purpose": "Use the original budget"}]})
        proposal = self.one.call("board_prepare", {**chat, "plan": plan(source_packet_ids=[captured["packet_id"]])})
        status, body = self.http(proposal["approval_url"])
        self.assertEqual(200, status)
        self.assertIn("spec.txt", body)
        self.assertIn("40 credits", body)
        self.assertNotIn("Unselected preface", body)
        self.confirm_mock(proposal["approval_url"])
        self.wait(self.one, proposal["job_id"])
        conclusion = {"decision": "Use A", "rationale": "Fits budget", "evidence": "Synthetic evidence", "unresolved": "Changed deadline", "next_steps": "Propose follow-up"}
        self.one.call("board_record_conclusion", {"scope": self.scope, "job_id": proposal["job_id"], "conclusion": conclusion})
        self.one.close()
        two = self.session()
        context = two.call("board_session", chat)
        self.assertTrue(context["active"])
        self.assertEqual("completed", context["job"]["state"])
        self.assertFalse(two.call("board_session", {**chat, "conversation_id": "unrelated-chat"})["active"])
        child = two.call("board_followup", {**chat, "job_id": proposal["job_id"], "plan": plan(brief="Compare alternatives with a new deadline")})
        self.assertEqual("pending_approval", child["provenance"]["kind"])
        self.assertEqual([], two.call("board_status", {"scope": self.scope, "job_id": child["job_id"]})["attempts"])
        status, body = self.http(child["approval_url"])
        self.assertEqual(200, status)
        self.assertIn('Continuation awaiting approval', body)
        self.assertIn(proposal["job_id"], body)
        self.assertIn("40 credits", body)
        self.confirm_mock(child["approval_url"])
        self.wait(two, child["job_id"])
        two.call("board_record_conclusion", {"scope": self.scope, "job_id": child["job_id"], "conclusion": conclusion})
        final = two.call("board_result", {"scope": self.scope, "job_id": child["job_id"]})
        self.assertEqual("board_conclusion", final["provenance"]["kind"])
        self.assertEqual("mock", final["provenance"]["provider"])
        self.assertEqual(2, final["provenance"]["agenda_number"])
        ended = two.call("board_session", {**chat, "action": "end"})
        self.assertFalse(ended["active"])
        self.assertEqual("open", ended["project_status"])
        two.call("board_task_lifecycle", {**chat, "action": "close", "user_statement": 'Synthetic human instruction: 结案'})
        self.assertEqual("closed", two.call("board_session", chat)["project_status"])
        two.call("board_task_lifecycle", {**chat, "action": "reopen", "user_statement": "Synthetic human instruction: reopen"})
        self.assertEqual("open", two.call("board_session", chat)["project_status"])
        policy = two.call("board_model_policy", {"excluded_publishers": ["anthropic"], "reason": "Synthetic user exclusion"})
        self.assertEqual(["anthropic"], policy["excluded_publishers"])
        self.assertFalse(any(m["id"] == "claude-haiku-5-5" for m in two.call("board_models", {})["models"]))

    def test_setup_form_origin_and_project_configuration(self):
        with self.assertRaises(AssertionError):
            self.one.call("board_setup", {})
        setup = self.one.call("board_setup", {"user_request":"Synthetic human request: configure Vertex"})
        self.assertEqual({}, setup["status"]["configuration"])
        url = setup["setup_url"]
        token = urlsplit(url).path.split("/")[-1]
        self.assertEqual(200, self.http(url)[0])
        self.assertEqual("same-origin", self.last_headers["Referrer-Policy"])
        endpoint = url.rsplit("/confirm/", 1)[0] + "/setup"
        fields = {"token": token, "project": "synthetic-project"}
        for origin in (False, "null", "https://untrusted.example"):
            self.assertEqual(403, self.http(endpoint, "POST", fields, origin=origin)[0])
        self.assertEqual(400, self.http(endpoint, "POST", {**fields, "project": "Not a project ID"})[0])
        self.assertEqual(200, self.http(endpoint, "POST", fields)[0])
        again = self.one.call("board_setup", {"user_request":"Synthetic human request: configure Vertex"})
        self.assertEqual("synthetic-project", again["status"]["configuration"]["project_id"])
        self.assertEqual(0o600, (self.home / "vertex-config.json").stat().st_mode & 0o777)
        self.assertNotIn("access_token", json.dumps(again))
        self.assertFalse((self.home / "openrouter-key").exists())
        self.assertEqual(400, self.http(endpoint, "POST", fields)[0])

    def test_api_settings_local_form_and_sanitized_mcp_listing(self):
        with self.assertRaises(AssertionError):
            self.one.call("board_api_setup", {})
        setup=self.one.call("board_api_setup", {"user_request": "Synthetic human request: add an API"})
        url=setup["setup_url"];origin=url.rsplit("/confirm/",1)[0];token=urlsplit(url).path.split("/")[-1]
        status,body=self.http(url)
        self.assertEqual(200,status);self.assertIn("API key",body)
        fields=dict(token=token,connection="",preset="kimi",name="Synthetic <service>",base_url="https://api.moonshot.ai/v1",api_key="SYNTHETIC-SECRET")
        self.assertEqual(403,self.http(origin+"/api/save","POST",fields,origin=False)[0])
        status,body=self.http(origin+"/api/save","POST",fields)
        self.assertEqual(200,status);self.assertNotIn("SYNTHETIC-SECRET",body);self.assertIn("&lt;service&gt;",body)
        self.assertNotIn('<form',body)
        self.assertIn('Codex handles model and pricing settings',body)
        connections=self.one.call("board_connections",{})
        self.assertNotIn("SYNTHETIC-SECRET",json.dumps(connections))
        row=connections['connections'][0]
        connection=row['id']
        self.assertEqual(('moonshotai','max_completion_tokens','bearer'),(row['publisher'],row['token_field'],row['auth']))
        status,edit=self.http(url+'?connection='+connection)
        self.assertEqual(200,status)
        for field in ('auth','publisher','token_field','model','input_price','parameters'):
            self.assertNotIn("name='%s'" % field,edit)
        # A basic edit retains the key and hidden transport settings.
        edited=dict(fields,connection=connection,preset='custom',api_key='',name='Renamed API')
        self.assertEqual(200,self.http(origin+'/api/save','POST',edited)[0])
        current=self.one.call('board_connections',{})['connections'][0]
        self.assertTrue(current['has_key'])
        self.assertEqual(row['credential_version'],current['credential_version'])
        self.assertEqual('moonshotai',current['publisher'])
        model=self.one.call("board_configure_model",dict(connection_id=connection,model=dict(model="qwen-synthetic",parameters={"enable_thinking":False})))
        self.assertEqual("qwen-synthetic",model["wire_model"])
        self.assertFalse(self.one.call("board_models",{"query":"qwen-synthetic"})["models"][0]["selection_allowed"])
        self.assertEqual(0o600,(self.home/"api-connections.json").stat().st_mode & 0o777)
        # An API setup capability cannot approve any meeting.
        self.assertEqual(400,self.http(origin+"/approve","POST",dict(token=token,consent="yes"))[0])

    def test_codex_discovers_saved_api_models_without_inference(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        requests=[]
        class Models(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                requests.append((self.command,self.path,self.headers.get('Authorization')))
                raw=json.dumps({'data':[{'id':'kimi-synthetic'},{'id':'SYNTHETIC-SECRET'}]}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_POST(self):
                requests.append((self.command,self.path,None));self.send_error(405)
        server=ThreadingHTTPServer(('127.0.0.1',0),Models)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            setup=self.one.call('board_api_setup',{'user_request':'Synthetic user request: add API'})
            url=setup['setup_url'];origin=url.rsplit('/confirm/',1)[0];token=urlsplit(url).path.split('/')[-1]
            fields=dict(token=token,connection='',preset='custom',name='Synthetic API',base_url='http://127.0.0.1:%s/v1'%server.server_port,api_key='SYNTHETIC-SECRET')
            self.assertEqual(200,self.http(origin+'/api/save','POST',fields)[0])
            connection=self.one.call('board_connections',{})['connections'][0]['id']
            result=self.one.call('board_discover_models',{'connection_id':connection})
            self.assertEqual(['kimi-synthetic'],result['model_ids'])
            self.assertEqual([('GET','/v1/models','Bearer SYNTHETIC-SECRET')],requests)
            self.assertNotIn('SYNTHETIC-SECRET',json.dumps(result))
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)

    def test_complete_inventory_and_unselected_status_through_real_tools_and_page(self):
        setup = self.one.call('board_setup', {'user_request':'Synthetic human request: configure Vertex'})
        url = setup['setup_url']
        origin = url.rsplit('/confirm/', 1)[0]
        token = urlsplit(url).path.split('/')[-1]
        self.assertEqual(200, self.http(origin + '/setup', 'POST', dict(token=token, project='synthetic-project'))[0])
        # Synthetic local credentials prove only local readiness; no token or model call.
        cli = self.home / 'tools/google-cloud-sdk/bin/gcloud'
        cli.parent.mkdir(parents=True, exist_ok=True)
        cli.write_text('#!/bin/sh\nexit 1\n'); cli.chmod(0o700)
        adc = self.home / 'gcloud/application_default_credentials.json'
        adc.parent.mkdir(exist_ok=True); adc.write_text('{}')
        inventory = self.one.call('board_models', dict(limit=10))
        self.assertTrue(inventory['has_more'])
        rest = self.one.call('board_models', dict(offset=inventory['next_offset']))
        self.assertEqual(inventory['total'], len(inventory['models']) + len(rest['models']))
        models = inventory['models'] + rest['models']
        selected = {'gemini-4-argon': 'medium', 'gemini-3.8-flash': 'high',
                    'deepseek-ai/deepseek-v4.1-flash-maas': 'none', 'zai-org/glm-5.2-maas': 'none'}
        stamp = datetime.now(timezone.utc).isoformat()
        args = dict(scope=self.scope, task_type='Synthetic tool integration', criteria='Synthetic task comparison',
                    thinking_level='balanced', difficulty_assessment='Synthetic task difficulty',
                    evidence=[dict(url=u, checked_at=stamp, kind='benchmark', finding='Synthetic evidence only')
                              for u in ['https://first.example.test/bench', 'https://second.example.test/bench']],
                    candidates=[dict(model=m['id'], effort=selected.get(m['id'], m['efforts'][0]), task_fit='Synthetic task fit',
                                     value_reason='Synthetic assessment reason', evidence_indices=[0, 1], recommend=m['id'] in selected)
                                for m in models if m['selection_allowed']],
                    route_comparisons=[dict(model=ident, comparison='Synthetic Vertex versus direct API comparison', evidence_indices=[0,1]) for ident in selected])
        with self.assertRaises(AssertionError):
            self.one.call('board_recommend', args)
        args.update(catalog_fingerprint=inventory['catalog_fingerprint'],
                    considered_models=[m['model'] for m in inventory['inventory'] if m['selection_allowed']])
        research = self.one.call('board_recommend', args)
        self.assertEqual({'selection_id','recommended_lineup','choices','instruction'}, set(research))
        self.assertEqual(['Codex'] + list(selected), research['recommended_lineup'])
        self.assertEqual('Details', research['choices'][-1])
        self.assertNotIn('candidates', research)
        self.assertNotIn('cost', research)
        self.assertNotIn('route_comparisons', research)
        details = self.one.call('board_selection_details', dict(scope=self.scope, selection_id=research['selection_id']))
        self.assertEqual(set(args['considered_models']), {m['model'] for m in details['candidates']})
        self.assertEqual(4, len(details['route_comparisons']))
        raw = live_plan(selection_id=research['selection_id'], members=[dict(model=ident, effort=effort,
                        role='Independent adviser', rationale='Synthetic fit', value_rationale='Synthetic capability complement', max_output_tokens=2000)
                        for ident, effort in selected.items()])
        proposed = self.one.call('board_prepare', dict(scope=self.scope, conversation_id='inventory-test', plan=raw))
        status, body = self.http(proposed['approval_url'])
        self.assertEqual(200, status)
        self.assertIn('meta/llama-4-scout-17b-16e-instruct-maas — Not selected', body)
        self.assertIn('mistral-medium-3 — Not selected', body)
        self.assertNotIn('gemini-4-argon — Not selected', body)
        self.assertNotIn('Elimination reason', body)
        self.assertIn('<details><summary>Full selection details</summary>', body)
        self.assertNotIn('<details open', body)
        self.assertIn('Balanced: capability and value', body)
        self.assertIn('Current Codex + 4 external models', body)
        job = self.one.call('board_status', dict(scope=self.scope, job_id=proposed['job_id']))
        self.assertEqual('awaiting_approval', job['state'])
        self.assertFalse(job['attempts'])

    def test_meta_standard_button_over_http_requires_account_model_evidence(self):
        from runtime.connections import Connections
        setup = self.one.call('board_api_setup', {'user_request':'Synthetic human request: connect Muse'})
        url=setup['setup_url']; origin=url.rsplit('/confirm/',1)[0]; token=urlsplit(url).path.split('/')[-1]
        fields=dict(token=token,connection='',name='Synthetic Meta',base_url='https://api.meta.ai/v1',
                    api_key='SYNTHETIC-META-NONSECRET',auth='bearer',publisher='meta',token_field='max_completion_tokens')
        self.assertEqual(200,self.http(origin+'/api/save','POST',fields)[0])
        row=self.one.call('board_connections',{})['connections'][0]
        params=dict(token=token,connection=row['id'],wire_model='muse-spark-1.3')
        self.assertEqual(400,self.http(origin+'/api/meta-model','POST',params)[0])
        # Inject only a synthetic metadata response into this isolated state; no real API call.
        connections=Connections(self.home)
        connections.record_discovery(row['id'],connections.target(row),['muse-spark-1.3'])
        self.assertEqual(403,self.http(origin+'/api/meta-model','POST',params,origin=False)[0])
        status,body=self.http(origin+'/api/meta-model','POST',params)
        self.assertEqual(200,status)
        self.assertNotIn('SYNTHETIC-META-NONSECRET',body)
        configured=self.one.call('board_connections',{})['connections'][0]['models']
        self.assertEqual(['muse-spark-1.3'],[m['wire_model'] for m in configured.values()])
        self.assertEqual(400,self.http(origin+'/api/meta-model','POST',dict(params,wire_model='muse-wonder-1.0'))[0])

    def test_midmeeting_reconfigure_over_stdio_requires_new_approval(self):
        chat=dict(scope=self.scope,conversation_id="swap-chat")
        first=self.one.call("board_prepare",dict(**chat,plan=plan(peer_review=False)))
        self.confirm_mock(first["approval_url"]);self.wait(self.one,first["job_id"])
        self.one.call("board_pause",dict(scope=self.scope,job_id=first["job_id"]))
        child=self.one.call("board_reconfigure",dict(**chat,job_id=first["job_id"],reason="Synthetic change",plan=plan(members=[dict(model="mock/new",role="New specialist",rationale="Changed needs")],peer_review=False)))
        status,body=self.http(child["approval_url"])
        self.assertEqual(200,status);self.assertIn('Change members during a meeting',body);self.assertIn("mock/new",body);self.assertIn("mock/one",body)
        self.assertEqual([],self.one.call("board_status",dict(scope=self.scope,job_id=child["job_id"]))["attempts"])
        self.confirm_mock(child["approval_url"]);self.wait(self.one,child["job_id"])
        self.assertEqual("superseded",self.one.call("board_status",dict(scope=self.scope,job_id=first["job_id"]))["state"])
        self.assertEqual("open",self.one.call("board_session",chat)["project_status"])



if __name__ == "__main__":
    unittest.main()
