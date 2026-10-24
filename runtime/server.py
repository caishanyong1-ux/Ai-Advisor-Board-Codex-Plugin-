from __future__ import annotations

import fcntl
import hmac
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import threading
import time
from urllib.parse import parse_qs, urlsplit

from .core import BoardError, VERSION, canonical, number, private_dir, state_home, text_field, write_private
from .worker import Board
from .providers import CallFailed
from . import connection_ui, localization


STYLE = """body{font:16px/1.6 -apple-system,BlinkMacSystemFont,sans-serif;background:#f3f5f7;color:#172537;margin:0}main{max-width:920px;margin:40px auto;padding:32px;background:white;border-radius:18px}h1{line-height:1.25}h2{margin-top:28px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;padding:18px;border-radius:10px}button{font:inherit;border:0;border-radius:8px;padding:14px 22px;background:#155f57;color:white;cursor:pointer}input[type=text],input[type=password],input[type=number],textarea,select{font:inherit;padding:12px;width:100%;box-sizing:border-box;display:block;margin:12px 0}label{display:block;margin:20px 0}.card{padding:16px;border:1px solid #dbe2e9;border-radius:10px;margin:12px 0}.columns{display:flex;gap:20px}.columns label{flex:1;min-width:0}textarea{box-sizing:border-box;max-width:100%}.notice{padding:16px;border-left:4px solid #c58a32;background:#fff6e6}small{color:#516173}li{margin-bottom:8px}@media(max-width:700px){main{margin:12px;padding:20px}.columns{display:block}}"""


def page(title, body, t=None):
    t = t or localization.Translator()
    return ("<!doctype html><html lang='%s' dir='%s'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>" % (html.escape(t.language, quote=True), t.direction) + html.escape(title) + "</title><style>" + STYLE + '</style><main><small>AI ADVISORY BOARD · ' + html.escape(t('Local Codex plugin')) + '</small><h1>' + html.escape(title) + "</h1>" + body + "</main></html>").encode()


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, board, token):
        self.board, self.token = board, token
        super().__init__(("127.0.0.1", 0), Handler)
        self.origin = "http://127.0.0.1:%s" % self.server_port

    def link(self, result, args):
        if "confirmation_token" in result:
            result = dict(result)
            if args.get('conversation_id'):
                localization.bind_ticket(self.board.store, result['confirmation_token'], args.get('scope'), args['conversation_id'])
            result["approval_url"] = self.origin + "/confirm/" + result.pop("confirmation_token")
            result["instruction"] = "The user must open this local page and confirm the displayed plan. The assistant must not submit this form or mark approval on the user's behalf."
        return result

    def rpc(self, name, args):
        if name == 'board_language':
            return localization.preference(self.board.store, args.get('scope'), args.get('conversation_id'), args.get('language'), args.get('messages'), args.get('template_hash'))
        # Resolve before mutations. No process-wide locale or browser-language inference.
        scope, conversation, job = args.get('scope'), args.get('conversation_id'), args.get('job_id')
        t = localization.context(self.board.store, scope, conversation, job) if conversation or job or name in {'board_setup', 'board_api_setup'} else localization.Translator()
        if name in {'board_setup', 'board_api_setup', 'board_prepare', 'board_followup', 'board_reconfigure', 'board_resume', 'board_approval_link'}:
            t.require_ready()
        result = self._rpc(name, args, t)
        if isinstance(result, dict) and (conversation or job or name in {'board_setup', 'board_api_setup'}):
            result = dict(result, presentation=t.metadata())
        return result

    def _rpc(self, name, args, t):
        board = self.board
        scope, job = args.get("scope"), args.get("job_id")
        if name == "board_bind_project":
            return board.store.bind(args.get("workspace_path"))
        if name == "board_models":
            return board.models(args.get("query", ""), args.get("refresh", True), args.get("limit", 100), args.get("offset", 0), args.get("include_unavailable", False))
        if name == "board_model_policy":
            return board.model_policy(args.get("excluded_publishers"), args.get("reason"))
        if name == "board_session":
            return board.session(scope, args.get("conversation_id"), args.get("action", "inspect"), job)
        if name == "board_open_task":
            return board.open_task(scope, args.get("conversation_id"), args.get("title"), args.get("brief"))
        if name == "board_capture_sources":
            return board.capture_sources(scope, args.get("conversation_id"), args.get("files"))
        if name == "board_task_lifecycle":
            return board.task_lifecycle(scope, args.get("conversation_id"), args.get("action"), args.get("user_statement"))
        if name == "board_update_prices":
            return board.update_prices(args.get("quotes"))
        if name == "board_register_vertex_model":
            from .catalog import register_vertex
            return register_vertex(board.providers["vertex"].registry, args.get("model"))
        if name == "board_recommend":
            result = board.recommend(scope, args.get("task_type"), args.get("criteria"), args.get("evidence"), args.get("candidates"), args.get('catalog_fingerprint'), args.get('considered_models'), args.get('thinking_level'), args.get('difficulty_assessment'), args.get('route_comparisons'))
            return dict(selection_id=result['selection_id'], recommended_lineup=['Codex'] + [m['model'] for m in result['candidates'] if m['recommend']],
                        choices=[t('Use recommended lineup'), t('Adjust models'), t('Details')],
                        instruction='Show only the recommended lineup and these choices. Read board_selection_details only when the human asks for details/costs/reasons; selection does not approve spending.')
        if name == 'board_selection_details':
            return board.selection_details(scope, args.get('selection_id'))
        if name == "board_pause":
            return board.pause(scope, job)
        if name == "board_reconfigure":
            return self.link(board.reconfigure(scope, job, args.get("plan"), args.get("conversation_id"), args.get("reason"), args.get("history_summary")), args)
        if name == "board_api_setup":
            text_field(args.get('user_request'), 'explicit human request to add or manage API connections', 4000)
            token = board.store.ticket("providers")
            localization.bind_ticket(board.store, token, scope, args.get('conversation_id'))
            return {"setup_url": self.origin + "/confirm/" + token, "edition": board.edition["edition"], "instruction": "Enter API keys only on this local form. No model call or paid request is made by setup."}
        if name == "board_connections":
            return {"connections": board.connections.list(), "existing_grok_available": any(m["connection_ready"] for m in board.grok.catalog().values()), "edition": board.edition["edition"]}
        if name == "board_discover_models":
            return board.providers["custom"].discover(args.get("connection_id"))
        if name == "board_configure_model":
            return board.connections.save_model(args.get("connection_id"), args.get("model"))
        if name == "board_preflight":
            return board.providers["vertex"].preflight(args.get("check_cloud", False))
        if name == "board_value_summary":
            return board.value_summary(scope)
        if name == "board_prepare":
            return self.link(board.prepare(scope, args.get("plan"), args.get("conversation_id")), args)
        if name == "board_followup":
            return self.link(board.followup(scope, job, args.get("plan"), args.get("conversation_id"), args.get("history_summary")), args)
        if name == "board_approval_link":
            return self.link(board.confirmation(scope, job), args)
        if name == "board_status":
            return board.store.get(scope, job)
        if name == "board_codex_packet":
            return board.codex_packet(scope,job,args.get("stage"))
        if name == "board_submit_codex":
            return board.submit_codex(scope,job,args.get("assignment_id"),args.get("packet_hash"),args.get("agent_id"),args.get("answer"))
        if name == "board_chairman_packet":
            return board.chairman_packet(scope,job,number(args.get("offset",0),"offset",0,12000000,True))
        if name == "board_result":
            offset = number(args.get("offset", 0), "offset", 0, 10000, True)
            limit = number(args.get("limit", 4), "limit", 1, 12, True)
            return board.store.get(scope, job, True, offset, limit)
        if name == "board_read_answer":
            return board.store.read_answer(scope, job, args.get("attempt_id"), number(args.get("offset", 0), "offset", 0, 12000000, True))
        if name == "board_list":
            return {"jobs": board.store.list_jobs(scope)}
        if name == "board_continue":
            return board.continue_round(scope, job, args.get("feedback"))
        if name in {"board_record_conclusion", "board_finalize"}:
            return board.finalize(scope, job, args.get("conclusion"))
        if name == "board_resume":
            return self.link(board.resume(scope, job), args)
        if name == 'board_recover_background':
            return board.recover_background(scope, job)
        if name == "board_cancel":
            return board.cancel(scope, job)
        if name == "board_setup":
            text_field(args.get('user_request'), 'explicit human request to set up or manage Vertex access', 4000)
            token = board.store.ticket("setup")
            localization.bind_ticket(board.store, token, scope, args.get('conversation_id'))
            return {"setup_url": self.origin + "/confirm/" + token, "status": board.providers["vertex"].auth.status(), "instruction": "Complete Google sign-in and project settings on this local page. No paid request is made. No keys, tokens or authorization codes belong in chat."}
        raise BoardError("Unknown tool")


class Handler(BaseHTTPRequestHandler):
    server_version = "AdvisoryBoard/" + VERSION
    sys_version = ""

    def log_message(self, *args):
        # Paths contain confirmation capabilities; never put them into access logs.
        pass

    def send_body(self, status, body, mime="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def page(self, title, body):
        return page(title, body, self.t)

    def json(self, status, value):
        self.send_body(status, canonical(value).encode())

    def valid_host(self):
        return self.headers.get("Host") == "127.0.0.1:%s" % self.server.server_port

    def authorized(self):
        return hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + self.server.token)

    def do_GET(self):
        self.t = t = localization.Translator()
        if not self.valid_host():
            return self.json(403, {"error": "Invalid local host"})
        path = urlsplit(self.path).path
        if path == "/health":
            if not self.authorized():
                return self.json(403, {"error": "Unauthorized"})
            return self.json(200, {"version": VERSION, "pid": os.getpid()})
        if not path.startswith("/confirm/"):
            return self.json(404, {"error": "Not found"})
        token = path[len("/confirm/"):]
        try:
            self.t = t = localization.for_ticket(self.server.board.store, token)
            t.require_ready()
            info = self.server.board.store.ticket_info(token)
            hidden = "<input type='hidden' name='token' value='%s'>" % html.escape(token, quote=True)
            if info["kind"] == "providers":
                query = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query, max_num_fields=5).items()}
                return self.send_body(200, self.page(t('API connections'), connection_ui.render(self.server.board, token, query, t=t)), "text/html; charset=utf-8")
            if info["kind"] == "setup":
                status = self.server.board.providers["vertex"].auth.status()
                config = status["configuration"]
                disabled = "" if status["gcloud_installed"] else " disabled"
                body = t("<p>Connect Google Cloud, then approve each meeting's models, workflow, and costs. Setup makes no paid model calls.</p>")
                body += t('<h2>1. Sign in to Google</h2><p>Google Cloud CLI: %s; local credentials: %s; sign-in status: %s.</p>') % (t('Installed') if status["gcloud_installed"] else t('Not installed'), t('Present (connection check pending)') if status["adc_file_present"] else t('Not created'), html.escape({"not_started": t('Not started'), "running": t('Awaiting Google authorization'), "finished": t('Complete'), "failed": t('Incomplete; try again')}[status["login_state"]]))
                body += "<form method='post' action='/login'>" + hidden + "<button" + disabled + t(">Sign in to Google in your browser</button></form><p>Sign-in opens Google's official authorization page. Complete it yourself, then refresh this page.</p>")
                body += t("<h2>2. Create a project and set up billing</h2><p><a href='https://console.cloud.google.com/projectcreate' target='_blank' rel='noopener noreferrer'>Create a Google Cloud project</a> · <a href='https://console.cloud.google.com/billing' target='_blank' rel='noopener noreferrer'>Set up billing</a></p><p>A dedicated project makes board costs easier to track. Confirm your Google account, terms, and payment details yourself.</p>")
                body += t("<h2>3. Save your project</h2><form method='post' action='/setup'>") + hidden
                body += "<label>Google Cloud project ID<input type='text' name='project' required minlength='6' maxlength='30' value='%s'></label>" % html.escape(config.get("project_id", ""), quote=True)
                body += t("<label>Quota project ID (usually the same; optional)<input type='text' name='quota_project' maxlength='30' value='%s'></label>") % html.escape(config.get("quota_project_id", ""), quote=True)
                body += t("<p>Each model's project, API, and region appear on the approval page. The global endpoint does not guarantee a single data region.</p><button>Save project settings</button></form>")
                if config:
                    project = html.escape(config["project_id"], quote=True)
                    body += t("<h2>4. Enable services and check access</h2><p><a href='https://console.cloud.google.com/apis/library/aiplatform.googleapis.com?project=") + project + t("' target='_blank' rel='noopener noreferrer'>Enable Vertex / Agent Platform API</a> · <a href='https://console.cloud.google.com/vertex-ai/model-garden?project=") + project + t("' target='_blank' rel='noopener noreferrer'>View available models</a> · <a href='https://console.cloud.google.com/billing/budgets?project=") + project + t("' target='_blank' rel='noopener noreferrer'>Set up spending alerts</a></p>")
                    body += "<form method='post' action='/check'>" + hidden + t('<button>Check project connection (no model call)</button></form>')
                body += t("<p class='notice'>After setup, return to Codex to prepare a small test. Model requests start only after you approve the specific models and workflow.</p>")
                return self.send_body(200, self.page(t('Set up the Vertex board'), body), "text/html; charset=utf-8")
            job = info["job_info"]
            plan = json.loads(job["plan"])
            action = t('Approve and start') if info["kind"] == "approve" else t('Approve remaining steps')
            if info["kind"] == "transport_resume":
                action = t('Approve transport update and resume')
            elif info['kind'] == 'capacity_resume':
                action = t('Approve capacity update and continue reviews')
            elif info['kind'] == 'background_resume':
                action = t('Approve background execution and resume')
            elif info['kind'] == 'ballot_resume':
                action = t('Approve review format correction')
            state = t('Mock test · No external model calls') if plan["provider"] == "mock" else t('External models · API charges may apply')
            members = ""
            for m in plan["members"] + ([] if plan["chairman"] == "codex" else [dict(plan["chairman"], role="Chairman — " + plan["chairman"]["role"])]):
                price = m.get("catalog_snapshot", {}).get("pricing", {})
                detail = t('Per million tokens: input US$%s / output US$%s') % (price.get("input_per_million", "—"), price.get("output_per_million", "—"))
                snapshot = m.get("catalog_snapshot", {})
                target = snapshot.get("api_target") or m.get("vertex_target", {})
                destination = target.get("base_url") or ("Vertex · " + target.get("location", "global") + " · " + target.get("project_id", ""))
                detail += " · " + destination + " · " + snapshot.get("access_note", "")
                if m.get("thinking_budget"):
                    detail += " · thinking tokens " + str(m["thinking_budget"])
                if price:
                    detail += " · " + price.get("note", "")
                members += t('<li><strong>%s</strong> · %s<br><small>Reasoning: %s; output parameter limit: %s (visible answer only for Grok)<br>%s</small><details><summary>Model selection details</summary>%s<br>%s</details></li>') % (html.escape(m["model"]), html.escape(snapshot.get('publisher', 'mock')), html.escape(str(m["effort"])), m["max_output_tokens"], html.escape(detail), html.escape(m["rationale"]), html.escape(m.get("value_rationale", t('Historical plan'))))
            body = t("<p class='notice'>%s</p><p><strong>Source project:</strong> %s<br><strong>Task:</strong> %s<br><strong>Plan fingerprint:</strong> <small>%s</small></p>") % (state, html.escape(job["path"]), html.escape(plan["title"]), job["plan_hash"])
            if info.get('transport_update') and info['kind'] == 'ballot_resume':
                repair = plan['review_repair']
                names = ', '.join(plan['members'][s['slot']]['model'] for s in repair['slots'])
                estimate = sum(s['estimated_reservation_usd'] for s in repair['slots'])
                body += t('<h2>Correct review format and complete reasons</h2><p>%s: at most one call per reviewer. Each original reviewer receives only its frozen anonymous packet, its own prior answer, and the format errors. Existing scores, ranking, commentary, issues, and uncertainties stay fixed. Missing criterion reasons are newly authored by that same model.</p><p>Completed drafts and valid reviews are reused. After strict validation, the meeting waits for chairman review without producing a final report. Calls share the original budget; failures do not retry automatically.</p><details><summary>Cost reservation and limits</summary><p>The complete correction packet reserves an estimated US$%.4f. Prior costs and uncertain reservations remain; the total budget does not increase. Actual cost depends on provider usage. Larger packets undergo fresh capacity and budget checks.</p></details>') % (html.escape(names), estimate)
            elif info.get('transport_update') and info['kind'] == 'background_resume':
                update = info['transport_update']
                body += t('<h2>Gemini background execution update</h2><p>%s → %s: reuse %s completed steps, preserving drafts, anonymous packets, valid scores, and the original budget. Only incomplete steps run. Earlier requests without a cloud ID cannot be recovered this way; their uncertain cost reservations remain.</p><p>Once a new request has a cloud ID, reconnect by reading that same operation without generating again. The original plan and approval are archived.</p>') % (html.escape(update['from_version']), html.escape(update['to_version']), update['reused_completed_drafts'])
            elif info.get('transport_update') and info['kind'] == 'capacity_resume':
                update = info['transport_update']
                body += t("<h2>Remove the fixed input limit and continue anonymous review</h2><p>%s → %s: reuse all %s drafts and their original anonymous ordering. Replace the plugin's fixed 100,000-token gate with model capacity and verified long-context pricing.</p><p>Approved output settings, models, question, sources, rubric, and total budget stay fixed. Only reviews that have not started run. The original plan and approval are archived; uncertain costs remain reserved.</p>") % (html.escape(update['from_version']), html.escape(update['to_version']), update['reused_completed_drafts'])
            elif info.get("transport_update"):
                update = info["transport_update"]
                body += t('<h2>Resume after the connection keepalive update</h2><p>%s → %s: enable TCP keepalive on model connections without changing the system VPN or network settings. This retry tests long waits with the original question; provider or intermediary disconnects may still occur.</p><p>Reuse %s completed drafts and run only missing steps, followed by the approved anonymous review. Question, sources, models, rubric, and total budget stay fixed.</p><p>The original plan and approval are archived. This confirmation binds the new fingerprint above. Prior uncertain cost reservations remain.</p><details><summary>Previous plan fingerprint</summary><code>%s</code></details>') % (html.escape(update["from_version"]), html.escape(update["to_version"]), update["reused_completed_drafts"], html.escape(update["previous_plan_hash"]))
            if plan.get('background_storage'):
                body += t('<p class="notice"><strong>Gemini cloud storage:</strong> The Preview Interactions API uses background=true and store=true. Google stores the approved question and answer; the plugin requests deletion after retrieving the terminal answer and usage. Retention follows Google policy. Unconfirmed deletion or remote cancellation remains visible as pending. Recovery reads an existing ID; it does not generate again automatically.</p>')
            if plan.get("reconfiguration"):
                change = plan["reconfiguration"]
                body += t('<h2>Change members during a meeting</h2><p>%s</p><p>Previous members: %s. New members receive the saved contributions.</p><p>Prior accounted cost: US$%.4f; previous remaining budget: US$%.4f; newly approved budget: US$%.4f; prior cost plus new budget: US$%.4f.</p>') % (html.escape(change["reason"]), html.escape(", ".join(change["previous_members"])), change["prior_accounted_cost_usd"], change["previous_remaining_budget_usd"], change["new_execution_budget_usd"], change["combined_accounted_plus_new_budget_usd"])
            if plan.get("selection_research"):
                research = plan["selection_research"]
                body += t('<details><summary>Full selection details</summary><p>%s</p><p>%s</p><p>Checked: %s UTC. This is a fixed research snapshot; approval does not trigger automatic member changes.</p><ul>') % (html.escape(research["criteria"]), html.escape(research.get('difficulty_assessment', '')), time.strftime("%Y-%m-%d %H:%M", time.gmtime(research["checked_at"])))
                for source in research["evidence"]:
                    body += "<li><a href='%s' target='_blank' rel='noopener noreferrer'>%s</a>：%s%s</li>" % (html.escape(source["url"], quote=True), html.escape(source["kind"]), t('Source unavailable; ') if not source["available"] else "", html.escape(source["finding"]))
                body += "</ul>"
                chosen = {m['model'] for m in plan['members']}
                if plan['chairman'] != 'codex':
                    chosen.add(plan['chairman']['model'])
                body += t('<h3>All considered models</h3><ul>')
                for candidate in research['candidates']:
                    body += '<li><strong>%s · %s</strong><br>%s<br>%s<br>%s</li>' % (html.escape(candidate['model']), t('Selected') if candidate['model'] in chosen else t('Not selected'), html.escape(candidate['task_fit']), html.escape(candidate['value_reason']), html.escape(candidate['limitations']))
                body += t('</ul><h3>Route and price comparisons</h3><ul>')
                for route in research.get('route_comparisons', []):
                    body += '<li>%s：%s</li>' % (html.escape(route['model']), html.escape(route['comparison']))
                body += '</ul></details>'
                others = [m for m in plan.get('model_selection', []) if m['status'] == 'not_selected']
                if others:
                    body += t('<details><summary>Other candidates: %s not selected</summary><ul>') % len(others)
                    body += ''.join(t('<li>%s — Not selected</li>') % html.escape(m['model']) for m in others)
                    body += '</ul></details>'
            unbounded = any(m.get("catalog_snapshot", {}).get("budget_type") == "estimated_dispatch_gate" for m in plan["members"] + ([] if plan["chairman"] == "codex" else [plan["chairman"]]))
            if unbounded:
                body += t("<p class='notice'><strong>Grok cost control:</strong> xAI's output parameter limits visible answers, not reasoning tokens. The budget below is an estimated dispatch gate, not a guaranteed billing cap; actual costs may exceed it. These requests run one at a time, with returned costs checked before continuing and no automatic retry. Configure account spending limits separately at xAI.</p>")
            if plan.get("board_task"):
                task = plan["board_task"]
                body += t('<p><strong>Board project:</strong> %s <small>%s</small></p>') % (html.escape(task["title"]), html.escape(task["id"]))
            if plan.get("source_context"):
                body += t('<h2>Selected local sources</h2><ul>')
                for packet in plan["source_context"]:
                    for source in packet["snapshot"]["files"]:
                        body += t('<li>%s (lines %s–%s): %s<br><small>File SHA256: %s</small></li>') % (html.escape(source["path"]), source["start_line"], source["end_line"], html.escape(source["purpose"]), source["file_sha256"])
                body += t('</ul><p>Only approved text snapshots are sent. Capture a new snapshot when files change.</p><details><summary>Inspect source text</summary><pre>%s</pre></details>') % html.escape(json.dumps(plan["source_context"], ensure_ascii=False, indent=2))
            if plan.get("continuity"):
                chain = plan["continuity"]
                history = plan["history_context"]
                mode = t('Full visible history') if history["mode"] == "full_visible_history" else t('Chairman history summary (original text omitted)')
                body += t('<h2>Continuation awaiting approval · Agenda %s</h2><p>Continues job <code>%s</code>. This agenda has not consulted models and needs your approval.</p>') % (chain["agenda_number"], html.escape(chain["parent_job_id"]))
                body += t('<p>Data to send: %s, covering %s prior jobs. Historical conclusions may be reassessed and do not establish current agreement.</p><details><summary>Inspect approved history</summary><pre>%s</pre></details>') % (mode, len(history["sources"]), html.escape(json.dumps(history, ensure_ascii=False, indent=2)))
            if plan.get("stage_contracts"):
                delivery = plan["stage_contracts"]
                body += t('<p><strong>Internal working language:</strong> American English; <strong>formal task deliverable language:</strong> %s.</p>') % html.escape(delivery["deliverable_language"])
                body += t('<details><summary>Member, review, and chairman requirements</summary><pre>%s</pre></details>') % html.escape(json.dumps(delivery, ensure_ascii=False, indent=2))
            levels = {'highest': t('Highest: capability first'), 'balanced': t('Balanced: capability and value'), 'economy': t('Economy: value while meeting the quality bar')}
            body += t('<p><strong>Thinking level:</strong> %s</p>') % html.escape(levels.get(plan.get('thinking_level'), t('Historical plan')))
            if plan.get('token_policy'):
                body += t('<p>Task and review materials remain complete; the provider enforces actual context capacity. Highest uses verified model output capacity. Balanced and Economy use task-adjustable output allowances. Explicit settings approved for this round take precedence. The monetary budget is approved separately.</p>')
            if plan.get('composition'):
                body += t('<p><strong>Board size:</strong> Current Codex + %s external models, selected for complementary capabilities rather than a manufacturer quota.</p>') % plan['composition']['external_models']
            body += t('<h2>Workflow: %s</h2><details><summary>Workflow details</summary>%s</details><h2>Models and routes</h2><ul>%s</ul><p><strong>Chairman:</strong> %s</p>') % (html.escape(plan["workflow"]), html.escape(plan["workflow_reason"]), members, html.escape(t('Current Codex chat') if plan["chairman"] == "codex" else plan["chairman"]["model"]))
            body += t('<p><strong>Up to %s rounds; peer review %s; dispatch budget US$%.4f.</strong></p><p>Costs are conservatively reserved before dispatch; final provider billing may differ. Cancellation cannot recall requests already processed by a provider. Work is saved while waiting for Codex decisions.</p>') % (plan["max_rounds"], t('enabled') if plan["peer_review"] else t('disabled'), plan["max_cost_usd"])
            body += t('<p>Each model operation has an approved deadline of %s seconds. Gemini background work saves a cloud ID for polling; other routes checkpoint streamed text. Only complete answers count as finished. Expired background work is canceled where possible; unconfirmed remote cancellation retains uncertain costs. No automatic regeneration.</p>') % plan["timeout_seconds"]
            if plan.get("deliberation_harness"):
                criteria = plan["deliberation_harness"]["criteria"]
                body += t('<h2>Shared scoring and anonymous review</h2><p>Required workflow: independent answers → anonymous peer review, shared scores, and ranking → chairman synthesis. Original mean ranks and agreement statistics are retained. Weighted scores out of 100 are not correctness probabilities and do not automatically select a winner.</p>')
                body += t('<p>Task profile: %s. Avoid overengineering and defensive writing while preserving necessary evidence, limitations, tests, and safeguards.</p>') % html.escape(plan["rubric_profile"])
                body += t('<details><summary>Shared scoring criteria and weights</summary><ul>') + "".join("<li>%s：%s%% — %s</li>"%(html.escape(key),value["weight"],html.escape(value["description"])) for key,value in criteria.items()) + "</ul></details>"
                if plan.get("include_codex_member"):
                    body += t('<p>Codex has a separate independent seat: one fresh isolated agent drafts, and another fresh isolated agent reviews. Every reviewer sees a different randomized order. All drafts must finish before review, and all valid reviews before synthesis. Missing agents leave the meeting waiting; the seat is never skipped. Codex usage is separate from the external API budget.</p>')
            if plan.get("vertex_target"):
                target = plan["vertex_target"]
                body += t('<p><strong>Google Cloud:</strong> %s; quota project: %s; model regions are listed above.</p>') % (html.escape(target["project_id"]), html.escape(target["quota_project_id"]))
            if plan.get("value_case"):
                value = plan["value_case"]
                body += t('<h2>Costs and execution conditions</h2><p><strong>Estimated US$%.4f–%.4f</strong> (based on the assumptions below; billing may differ)</p>') % (value["expected_cost_low_usd"], value["expected_cost_high_usd"])
                for label, key in [(t('Quality gate'), "quality_gate"), (t('Estimation assumptions'), "cost_assumptions"), (t('Stop condition'), "stop_condition")]:
                    body += "<p><strong>" + label + "：</strong>" + html.escape(value[key]) + "</p>"
                body += t('<details><summary>Tradeoffs and alternatives</summary>')
                for label, key in [(t('Alternative'), 'alternative'), (t('Choice and tradeoffs'), 'tradeoff'), (t('Capability complementarity'), 'member_count_reason')]:
                    body += '<p><strong>%s：</strong>%s</p>' % (label, html.escape(value[key]))
                body += '</details>'
            if plan.get("cost_estimate"):
                estimate = plan["cost_estimate"]
                body += t('<p>First-round draft reservation: US$%.4f; up to %s scheduled model calls. Reviews, later rounds, and retries share the approved budget. Insufficient budget saves progress and stops dispatch.</p>') % (estimate["first_round_proposals_reserve_usd"], estimate["max_scheduled_calls_without_retries"])
            if info["kind"] in {"resume", "transport_resume", "capacity_resume", "background_resume", "ballot_resume"}:
                with self.server.board.store.connect() as db:
                    rows = db.execute("SELECT round,stage,slot,model,state,reserved,charged,error FROM attempts WHERE job=? ORDER BY created", (job["id"],)).fetchall()
                body += t("<p class='notice'>Successful steps are reused. Earlier uncertain requests may already have incurred charges; their reservations still count against the budget. Models and total budget stay fixed.</p><details><summary>Execution and cost history</summary><pre>") + html.escape(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2)) + "</pre></details>"
            body += t('<h2>Data scope</h2><p>') + html.escape(plan["data_scope"]) + t('</p><details><summary>Full plan, task content, and model evidence</summary><pre>') + html.escape(json.dumps(plan, ensure_ascii=False, indent=2)) + "</pre></details>"
            body += "<form method='post' action='/approve'>" + hidden + t("<label><input type='checkbox' name='consent' value='yes' required> I reviewed the source project, workflow, lineup, data scope, and costs, and approve execution of this plan.</label><button>") + action + t('</button></form><p><small>This link expires after 6 hours and is single-use. Return to Codex to change the plan.</small></p>')
            if unbounded:
                body = body.replace("<button>" + action, t("<label><input type='checkbox' name='variable_cost_consent' value='yes' required> I understand Grok reasoning costs may exceed the dispatch budget and approve this model and cost risk.</label><button>") + action)
            self.send_body(200, self.page(t('Confirm board plan'), body), "text/html; charset=utf-8")
        except BoardError as error:
            self.send_body(400, self.page(t('This link cannot be used'), t.error(error)), "text/html; charset=utf-8")

    def do_POST(self):
        self.t = t = localization.Translator()
        if not self.valid_host():
            return self.json(403, {"error": "Invalid local host"})
        path = urlsplit(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.json(400, {"error": "Invalid request length"})
        if not 0 < length <= 1500000:
            return self.json(413, {"error": "Request too large or empty"})
        if path == "/rpc":
            if not self.authorized() or self.headers.get("Origin"):
                return self.json(403, {"error": "Unauthorized"})
            try:
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict) or not isinstance(request.get("args", {}), dict):
                    raise BoardError("Invalid request")
                result = self.server.rpc(request.get("name"), request.get("args", {}))
                return self.json(200, {"result": result})
            except (BoardError, ValueError, TypeError, CallFailed) as error:
                return self.json(400, {"error": str(error)})
            except Exception as error:
                return self.json(500, {"error": "Local service error: " + type(error).__name__})
        if path not in {"/approve", "/setup", "/login", "/check", "/api/save", "/api/discover", "/api/manage", "/api/remove-model", "/api/model", "/api/meta-model"}:
            return self.json(404, {"error": "Not found"})
        if self.headers.get("Origin") != self.server.origin or self.headers.get("Content-Type", "").split(";")[0] != "application/x-www-form-urlencoded":
            return self.json(403, {"error": "Use the local confirmation form"})
        try:
            form = parse_qs(self.rfile.read(length).decode(), strict_parsing=True, keep_blank_values=True, max_num_fields=40)
            token = form.get("token", [""])[0]
            self.t = t = localization.for_ticket(self.server.board.store, token)
            t.require_ready()
            info = self.server.board.store.ticket_info(token)
            if any(len(values) != 1 for values in form.values()):
                raise BoardError("Duplicate form fields are not accepted")
            if path.startswith("/api/"):
                if info["kind"] != "providers":
                    raise BoardError("Invalid API settings link")
                body = connection_ui.submit(self.server.board, token, path, form, t=t)
                return self.send_body(200, self.page(t('API connections'), body), "text/html; charset=utf-8")
            if path in {"/setup", "/login", "/check"}:
                if info["kind"] != "setup":
                    raise BoardError("Invalid setup link")
                provider = self.server.board.providers["vertex"]
                back = "<p><a href='/confirm/" + html.escape(token, quote=True) + t("'>Return to setup / check sign-in status</a></p>")
                if path == "/login":
                    provider.auth.login()
                    body = t("<p>Google sign-in started. Complete it on Google's official page, then return to setup.</p>") + back
                elif path == "/check":
                    result = provider.preflight(True)
                    body = t('<p>Checks complete. This was not an inference test; model entitlement and capacity still need an approved test.</p><pre>') + html.escape(json.dumps(result, ensure_ascii=False, indent=2)) + "</pre>" + back
                else:
                    provider.auth.configure(form.get("project", [""])[0], form.get("quota_project", [""])[0] or None)
                    with self.server.board.store.connect() as db:
                        db.execute("UPDATE tickets SET used=1 WHERE token=?", (token,))
                    new_token = self.server.board.store.ticket("setup")
                    localization.copy_ticket(self.server.board.store, token, new_token)
                    body = t("<p>Project saved. No model has been called.</p><p><a href='/confirm/") + new_token + t("'>Continue sign-in and service checks</a></p>")
            else:
                if form.get("consent") != ["yes"]:
                    raise BoardError("Confirm the displayed plan before starting")
                if info["kind"] not in {"approve", "resume", "transport_resume", "capacity_resume", "background_resume", "ballot_resume"}:
                    raise BoardError("Invalid approval link")
                plan = json.loads(info["job_info"]["plan"])
                if any(m.get("catalog_snapshot", {}).get("budget_type") == "estimated_dispatch_gate" for m in plan["members"] + ([] if plan["chairman"] == "codex" else [plan["chairman"]])) and form.get("variable_cost_consent") != ["yes"]:
                    raise BoardError("Acknowledge Grok's variable reasoning cost before approving")
                self.server.board.validate_execution(plan)
                job = self.server.board.store.confirm(token)
                self.server.board.dispatch()
                body = t('<p>Plan approved. Background work is queued.</p><p>Job ID: <code>') + html.escape(job) + t('</code></p><p>Return to Codex and ask to view the board results. Closing this page does not cancel the job; keep the Mac on, awake, and connected.</p>')
            self.send_body(200, self.page(t('Complete'), body), "text/html; charset=utf-8")
        except (BoardError, ValueError, CallFailed) as error:
            self.send_body(400, self.page(t('Not confirmed'), t.error(error)), "text/html; charset=utf-8")


def serve():
    os.umask(0o077)
    home = private_dir(state_home())
    with open(home / "worker.lock", "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        board = Board(home)
        token = secrets.token_urlsafe(40)
        server = LocalServer(board, token)
        write_private(home / "endpoint.json", canonical({"port": server.server_port, "token": token, "pid": os.getpid(), "version": VERSION}))
        stopped = threading.Event()

        def stop(signum, frame):
            stopped.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

        def schedule():
            while not stopped.wait(0.5):
                board.dispatch()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        threading.Thread(target=schedule, daemon=True).start()
        board.dispatch()
        try:
            server.serve_forever(poll_interval=0.3)
        finally:
            stopped.set()
            server.server_close()


if __name__ == "__main__":
    serve()
