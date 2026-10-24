import copy
import http.client
import json
import threading
from unittest.mock import patch

from test_board import BoardFixture, FakeLive, live_plan
from runtime.core import BoardError, canonical, digest
from runtime.harness import contract
from runtime.maas_repair import LEGACY, WORK_CONTRACT, work_contract
from runtime.providers import CallFailed

MODEL = 'deepseek-ai/deepseek-v4.1-flash-maas'


class MaasRepairTests(BoardFixture):
    def failed_old_job(self):
        self.live = FakeLive(fail_once=MODEL)
        self.board.providers['vertex'] = self.live
        with patch('runtime.harness.contract', return_value=dict(contract(), implementation_sha256=LEGACY)):
            proposal = self.prepare(live_plan(max_rounds=1))
            self.approve(proposal)
            self.wait(proposal['job_id'])
        # An offline fixture of the exact old schema; no production records.
        with self.board.store.connect() as db:
            row=db.execute('SELECT plan FROM jobs WHERE id=?',(proposal['job_id'],)).fetchone()
            old=json.loads(row['plan'])
            for member in old['members']:
                member.pop('output_policy',None)
            for estimate in old['cost_estimate']['members']:
                estimate.pop('request_limits',None)
            fingerprint=digest(old)
            db.execute('UPDATE jobs SET plan=?,plan_hash=?,approved_hash=? WHERE id=?',
                       (canonical(old),fingerprint,fingerprint,proposal['job_id']))
        proposal['plan_hash']=fingerprint
        return proposal,self.board.store.get(self.scope,proposal['job_id'],True)

    def test_current_build_is_explicitly_audited(self):
        self.assertEqual(WORK_CONTRACT,work_contract())

    def test_reapproval_reuses_answers_and_preserves_old_plan_and_budget(self):
        proposal,old=self.failed_old_job()
        job=proposal['job_id']
        success={a['id'] for a in old['attempts'] if a['state']=='succeeded'}
        update=self.board.resume(self.scope,job)
        ticket=self.board.store.ticket_info(update['confirmation_token'])
        self.assertEqual('maas_resume',ticket['kind'])
        self.assertEqual('1.3.3',ticket['transport_update']['from_version'])
        changed=json.loads(ticket['job_info']['plan'])
        for key in ('thinking_level','max_cost_usd','goal','brief','stage_contracts'):
            self.assertEqual(old['plan'][key],changed[key])
        for original,member in zip(old['plan']['members'],changed['members']):
            self.assertEqual(original,{k:v for k,v in member.items() if k!='output_policy'})
        self.assertEqual(old['plan_hash'],self.board.store.get(self.scope,job)['plan_hash'])
        self.board.dispatch()
        self.assertEqual(4,len(self.live.calls))
        self.approve(update)
        new=self.wait(job)
        self.assertEqual('waiting_chairman',new['state'])
        self.assertEqual(success,{a['id'] for a in new['attempts'] if a['id'] in success})
        self.assertEqual(9,len(self.live.calls))  # only missing draft plus 4 reviews
        with self.board.store.connect() as db:
            archived=db.execute('SELECT previous_plan,previous_approved_hash FROM transport_repairs WHERE token=?', (update['confirmation_token'],)).fetchone()
            self.assertEqual(old['plan'],json.loads(archived['previous_plan']))
            self.assertEqual(old['plan_hash'],archived['previous_approved_hash'])
        with self.assertRaises(BoardError):
            self.board.store.confirm(update['confirmation_token'])

    def test_mutated_attempts_review_packets_or_new_runtime_block_reapproval(self):
        proposal,old=self.failed_old_job()
        job=proposal['job_id']
        with patch('runtime.maas_repair.work_contract',return_value='unreviewed'), self.assertRaises(BoardError):
            self.board.resume(self.scope,job)
        update=self.board.resume(self.scope,job)
        with self.board.store.connect() as db:
            db.execute("UPDATE attempts SET charged=.01 WHERE job=? AND state='failed'",(job,))
        with self.assertRaisesRegex(BoardError,'saved attempts changed'):
            self.board.store.confirm(update['confirmation_token'])
        with self.board.store.connect() as db:
            db.execute("INSERT INTO review_packets VALUES(?,1,0,'{}','synthetic')",(job,))
        with self.assertRaisesRegex(BoardError,'existing reviews'):
            self.board.resume(self.scope,job)
        self.assertEqual(old['plan_hash'],self.board.store.get(self.scope,job)['plan_hash'])

    def test_approval_page_discloses_effective_limit_without_dispatch(self):
        from runtime.server import LocalServer
        from runtime import localization
        proposal,_=self.failed_old_job()
        update=self.board.resume(self.scope,proposal['job_id'])
        localization.preference(self.board.store,self.scope,'synthetic-ui','zh-Hant')
        localization.bind_ticket(self.board.store,update['confirmation_token'],self.scope,'synthetic-ui')
        server=LocalServer(self.board,'synthetic-server-token')
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
        try:
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request('GET','/confirm/'+update['confirmation_token'])
            response=conn.getresponse();body=response.read().decode();conn.close()
            self.assertEqual(200,response.status)
            self.assertIn('批准共享上下文容量規則並續跑',body)
            self.assertIn('初稿實際輸出上限',body)
            self.assertEqual(4,len(self.live.calls))
        finally:
            server.shutdown();server.server_close();thread.join(2)

    def test_failure_metadata_survives_worker_and_status(self):
        class Rejected(FakeLive):
            def call(self,member,*args):
                if member['model']==MODEL:
                    raise CallFailed('Google HTTP 400: INVALID_ARGUMENT: invalid max_tokens',False,400,
                                     {'http_status':400,'provider_status':'INVALID_ARGUMENT','message':'invalid max_tokens'})
                return super().call(member,*args)
        self.board.providers['vertex']=Rejected()
        proposal=self.prepare(live_plan(max_rounds=1))
        self.approve(proposal)
        result=self.wait(proposal['job_id'])
        failed=next(a for a in result['attempts'] if a['model']==MODEL)
        self.assertEqual(400,failed['failure']['http_status'])
        self.assertEqual('failed',failed['state'])
        self.assertEqual(0,failed['charged'])
        self.assertEqual(2000,failed['request_limits']['effective_max_output_tokens'])
        self.assertEqual(1,len([a for a in result['attempts'] if a['model']==MODEL]))

    def test_external_chairman_alone_discloses_shared_context_policy(self):
        from runtime.server import LocalServer
        self.live=FakeLive();self.board.providers['vertex']=self.live
        raw=live_plan(max_rounds=1,max_cost_usd=50)
        template=raw['members'][0]
        selected=[m for m in self.live.catalog()['models'].values() if m.get('pricing') and m['api_family'] in {'gemini','claude'}][:4]
        self.assertEqual(4,len(selected))
        raw['members']=[dict(template,model=m['id'],effort=m['efforts'][0]) for m in selected]
        raw['chairman']=dict(template,model=MODEL,effort='none',max_output_tokens=1048576)
        proposal=self.prepare(raw)
        server=LocalServer(self.board,'synthetic-server-token')
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
        try:
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request('GET','/confirm/'+proposal['confirmation_token'])
            response=conn.getresponse();body=response.read().decode();conn.close()
            self.assertEqual(200,response.status)
            self.assertIn('Shared-context limits:',body)
            self.assertIn('chairman requested maximum 1048576',body)
            self.assertIn('calculated from the complete synthesis packet',body)
            self.assertEqual([],self.live.calls)
        finally:
            server.shutdown();server.server_close();thread.join(2)
