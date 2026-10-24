"""Conversation language through real MCP and HTTP, with isolated synthetic state."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import re
import sqlite3
import unittest
from urllib.parse import urlsplit

import test_stdio
from test_board import plan
from runtime import localization
from runtime.core import BoardError


class CatalogTests(unittest.TestCase):
    def test_builtin_catalogs_preserve_links_forms_and_placeholders(self):
        for language in ('zh-Hans', 'zh-Hant'):
            localization.validate_messages({key: value[language] for key, value in localization.MESSAGES.items()})

    def test_translations_cannot_alter_forms_or_remove_parameters(self):
        messages = {key: key for key in localization.MESSAGES}
        keys = list(messages)
        for source, replacement in [
            ('Local Codex plugin', '<script>alert(1)</script>'),
            ('Local Codex plugin', 'Language %x'),
            (next(k for k in keys if "action='/api/manage'" in k), 'Deleted form'),
            (next(k for k in keys if '%s' in k), 'Deleted parameters'),
            (next(k for k in keys if '<a href=' in k), '<a href="https://untrusted.example">Submit</a>')]:
            with self.subTest(source=source), self.assertRaises(BoardError):
                localization.validate_messages(dict(messages, **{source: replacement}))
        with self.assertRaises(BoardError):
            localization.validate_messages({'Local Codex plugin':'Plugin local'})

    def test_tags_and_direction(self):
        for source, expected in [('zh_TW','zh-Hant'),('zh-HK','zh-Hant'),('zh-cn','zh-Hans'),('zh-Latn','zh-Latn'),('en-gb','en-GB'),('ja','ja'),('fr-ca','fr-CA')]:
            self.assertEqual(expected, localization.language_tag(source))
        for bad in ["en' onclick='bad", '', 'English', '../fr', '<ar>']:
            with self.assertRaises(BoardError):
                localization.language_tag(bad)
        self.assertEqual('rtl', localization.Translator('ar').direction)

    def test_translation_cannot_move_visible_values_into_links(self):
        messages={key:key for key in localization.MESSAGES}
        source=next(key for key in messages if key.startswith('<h2>%s saved</h2>'))
        moved=source.replace('%s','',1)+'%s'
        # Separate markup/type lists miss this corruption of positional arguments.
        self.assertEqual(localization.MARKUP.findall(source),localization.MARKUP.findall(moved))
        self.assertEqual(localization.FORMATS.findall(source),localization.FORMATS.findall(moved))
        with self.assertRaises(BoardError):
            localization.validate_messages(dict(messages,**{source:moved}))


class LocaleFlowTests(unittest.TestCase):
    setUp = test_stdio.StdioTests.setUp
    tearDown = test_stdio.StdioTests.tearDown
    session = test_stdio.StdioTests.session
    http = test_stdio.StdioTests.http
    confirm_mock = test_stdio.StdioTests.confirm_mock
    wait = test_stdio.StdioTests.wait

    def chat(self, name='chat-a'):
        return dict(scope=self.scope, conversation_id=name)

    def language(self, language, name='chat-a'):
        return self.one.call('board_language', dict(self.chat(name), language=language))

    def test_concurrent_conversations_and_projects_do_not_share_language(self):
        pages = []
        for chat, language, label in [('chat-a','zh-Hant','新增 API'),('chat-b','en-US','Add API'),('chat-c','zh-Hans','新增 API')]:
            self.language(language, chat)
            result = self.one.call('board_api_setup', dict(self.chat(chat), user_request='Synthetic request: add API'))
            self.assertEqual(language, result['presentation']['language'])
            pages.append((result['setup_url'], language, label))
        with ThreadPoolExecutor(max_workers=3) as pool:
            for (url, language, label), (status, body) in zip(pages, pool.map(lambda item:self.http(item[0]), pages)):
                self.assertEqual(200, status)
                self.assertIn("lang='%s'" % language, body)
                self.assertIn(label, body)
        other = self.one.call('board_bind_project', {'workspace_path':str(self.root/'project-b')})
        other = {key:other[key] for key in ('project_id','project_token')}
        self.assertEqual('en-US', self.one.call('board_language', dict(scope=other, conversation_id='chat-a'))['language'])
        self.one.close()
        fresh = self.session()
        self.assertEqual('zh-Hant', fresh.call('board_language', self.chat())['language'])

    def test_setup_post_renewal_and_errors_follow_ticket_language(self):
        self.language('zh-Hant')
        result = self.one.call('board_setup', dict(self.chat(),user_request='Synthetic request: configure Vertex'))
        url=result['setup_url']; origin=url.rsplit('/confirm/',1)[0]; token=urlsplit(url).path.split('/')[-1]
        status, body=self.http(origin+'/setup','POST',dict(token=token,project='synthetic-project'))
        self.assertEqual(200,status); self.assertIn('專案已儲存',body)
        next_path = re.search(r"href='(/confirm/[^']+)'", body).group(1)
        self.assertIn("lang='zh-Hant'",self.http(origin+next_path)[1])
        status,body=self.http(url)
        self.assertEqual(400,status);self.assertIn('確認連結已過期或已使用',body)
        self.assertIn("lang='zh-Hant'",body)
        status,body=self.http(origin+'/setup','POST',dict(token=next_path.split('/')[-1],project='INVALID PROJECT'))
        self.assertEqual(400,status);self.assertIn('技術詳情',body)

    def test_api_save_and_management_keep_language_and_original_names(self):
        self.language('zh-Hans')
        result=self.one.call('board_api_setup',dict(self.chat(),user_request='Synthetic request: add API'))
        url=result['setup_url'];origin=url.rsplit('/confirm/',1)[0];token=urlsplit(url).path.split('/')[-1]
        fields=dict(token=token,connection='',preset='kimi',name='Example <service> 原名',base_url='https://api.moonshot.ai/v1',api_key='SYNTHETIC-SECRET')
        status,body=self.http(origin+'/api/save','POST',fields)
        self.assertEqual(200,status);self.assertIn('已储存',body);self.assertIn('Example &lt;service&gt; 原名',body)
        self.assertNotIn('SYNTHETIC-SECRET',body)
        connection=self.one.call('board_connections',{})['connections'][0]['id']
        status,body=self.http(origin+'/api/manage','POST',dict(token=token,connection=connection,action='disable'))
        self.assertEqual(200,status);self.assertIn('已停用',body);self.assertIn("lang='zh-Hans'",body)
        self.language('en-US')
        status,body=self.http(url+'?connection='+connection)
        self.assertEqual(200,status);self.assertIn("value='enable'>Enable</button>",body)
        status,body=self.http(origin+'/api/manage','POST',dict(token=token,connection=connection,action='enable'))
        self.assertEqual(200,status);self.assertIn("value='disable'>Disable</button>",body)

    def test_language_switch_preserves_approved_work_and_raw_answers(self):
        self.language('zh-Hant')
        proposal=self.one.call('board_prepare',dict(self.chat(),plan=plan(deliverable_language='ja')))
        job=proposal['job_id'];url=proposal['approval_url']
        self.assertIn('確認董事會方案',self.http(url)[1])
        self.language('en-US')
        self.assertIn('Confirm board plan',self.http(url)[1])
        self.confirm_mock(url)
        self.wait(self.one,job)
        before=self.one.call('board_result',dict(self.chat(),job_id=job))
        with sqlite3.connect(str(self.home/'board.sqlite3')) as db:
            frozen=db.execute('SELECT plan,plan_hash,approved_hash FROM jobs WHERE id=?',(job,)).fetchone()
            ballots=db.execute('SELECT * FROM review_packets WHERE job=? ORDER BY slot',(job,)).fetchall()
        self.language('zh-Hans')
        after=self.one.call('board_result',dict(self.chat(),job_id=job))
        self.assertEqual('zh-Hans',after['presentation']['language'])
        self.assertEqual(before['results'],after['results'])
        with sqlite3.connect(str(self.home/'board.sqlite3')) as db:
            self.assertEqual(frozen,db.execute('SELECT plan,plan_hash,approved_hash FROM jobs WHERE id=?',(job,)).fetchone())
            self.assertEqual(ballots,db.execute('SELECT * FROM review_packets WHERE job=? ORDER BY slot',(job,)).fetchall())
        approved=json.loads(frozen[0])
        self.assertEqual('ja',approved['stage_contracts']['deliverable_language'])
        self.assertEqual('en-US',approved['stage_contracts']['language'])
        self.assertNotIn('zh-Hans',frozen[0])
        # A chat displaying an explicitly selected old job uses its own language.
        self.language('en-US','view-chat')
        displayed=self.one.call('board_result',dict(self.chat('view-chat'),job_id=job))
        self.assertEqual('en-US',displayed['presentation']['language'])

    def test_other_language_catalog_is_complete_scoped_and_validated(self):
        self.language('zh-Hant')
        template=self.one.call('board_language',dict(self.chat(),language='fr-FR'))
        self.assertEqual('needs_translation',template['status'])
        self.assertEqual('zh-Hant',self.one.call('board_language',self.chat())['language'])
        # Synthetic fixture checks catalog plumbing, not translation quality.
        messages=copy.deepcopy(template['templates'])
        messages.update({'API connections':'Connexions API','Add API':'Ajouter une API','Name':'Nom'})
        args=dict(self.chat(),language='fr-FR',messages=messages,template_hash=template['template_hash'])
        with self.assertRaises(AssertionError):
            self.one.call('board_language',dict(args,template_hash='stale'))
        self.assertEqual('ready',self.one.call('board_language',args)['status'])
        result=self.one.call('board_api_setup',dict(self.chat(),user_request='Synthetic request: add API'))
        body=self.http(result['setup_url'])[1]
        self.assertIn("lang='fr-FR'",body);self.assertIn('Connexions API',body);self.assertIn('Ajouter une API',body)
        self.assertEqual('en-US',self.one.call('board_language',self.chat('unrelated'))['language'])
        bad=dict(self.scope,project_token='invalid')
        with self.assertRaises(AssertionError):
            self.one.call('board_language',dict(args,scope=bad))
        with self.assertRaises(AssertionError):
            self.one.call('board_api_setup',dict(scope=self.scope,user_request='Synthetic request: add API'))

    def test_stale_custom_catalog_never_blocks_status_results_or_cancellation(self):
        template=self.one.call('board_language',dict(self.chat(),language='ja'))
        self.one.call('board_language',dict(self.chat(),language='ja',messages=template['templates'],template_hash=template['template_hash']))
        proposal=self.one.call('board_prepare',dict(self.chat(),plan=plan()))
        with sqlite3.connect(str(self.home/'board.sqlite3')) as db:
            db.execute('UPDATE conversation_languages SET template_hash=?',('previous-templates',))
        for name in ('board_status','board_result'):
            response=self.one.call(name,dict(self.chat(),job_id=proposal['job_id']))
            self.assertEqual('needs_translation',response['presentation']['interface_status'])
        self.assertEqual(400,self.http(proposal['approval_url'])[0])
        with self.assertRaises(AssertionError):
            self.one.call('board_approval_link',dict(self.chat(),job_id=proposal['job_id']))
        self.assertEqual('needs_translation',self.one.call('board_language',self.chat())['status'])
        cancelled=self.one.call('board_cancel',dict(self.chat(),job_id=proposal['job_id']))
        self.assertEqual('cancelled',cancelled['state'])
