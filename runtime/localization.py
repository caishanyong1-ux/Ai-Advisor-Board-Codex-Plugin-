"""Conversation-scoped presentation, separate from frozen execution contracts."""
import html
import json
import re

from .core import BoardError, canonical, digest, text_field
from .ui_messages import MESSAGES

TEMPLATE_HASH = digest(sorted(MESSAGES))
# Include incomplete markup fragments: templates also surround dynamic hrefs/buttons.
MARKUP = re.compile(r'<[^<>]*(?:>|$)|^[^<>]*>|[<>]')
FORMATS = re.compile(r'%(?:\.\d+)?[sdf%]')
STRUCTURE = re.compile(MARKUP.pattern + '|' + FORMATS.pattern)


def language_tag(value):
    value = text_field(value, 'language', 64).replace('_', '-')
    if not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', value):
        raise BoardError('Use a language tag such as en-US, zh-Hans, zh-Hant, or ja')
    parts = value.split('-')
    parts = [parts[0].lower()] + [s.title() if len(s) == 4 else s.upper() if len(s) in {2, 3} else s.lower() for s in parts[1:]]
    if parts[0] == 'zh' and not any(len(s) == 4 for s in parts[1:]):
        return 'zh-Hant' if any(s in {'TW', 'HK', 'MO'} for s in parts) else 'zh-Hans'
    return '-'.join(parts)


def built_in(language):
    if language.split('-')[0] == 'en':
        return 'en-US'
    for script in ('Hans', 'Hant'):
        if language.startswith('zh-' + script):
            return 'zh-' + script
    return None


def validate_messages(messages):
    if not isinstance(messages, dict) or set(messages) != set(MESSAGES):
        raise BoardError('Translate every interface template, retaining its English key; partial catalogs are not accepted')
    if len(canonical(messages).encode()) > 300000:
        raise BoardError('Interface translations exceed 300KB')
    for source, translated in messages.items():
        text_field(translated, 'interface translation', 16000)
        # Interleave markup and placeholders, so translating cannot move a value
        # from visible text into an href/action or across a form boundary.
        if (STRUCTURE.findall(source) != STRUCTURE.findall(translated)
                or FORMATS.sub('', source).count('%') != FORMATS.sub('', translated).count('%')):
            raise BoardError('Translations must preserve HTML markup, attributes, links, and ordered format placeholders exactly')
        if any(ord(c) < 32 and c not in '\n\t' for c in translated):
            raise BoardError('Interface translations contain unsupported control characters')
    return messages


class Translator:
    def __init__(self, language='en-US', messages=None, ready=True):
        self.language = language
        self.messages = messages or {}
        self.catalog = built_in(language)
        self.ready = ready
        self.direction = 'rtl' if language.split('-')[0] in {'ar', 'fa', 'he', 'ur', 'ps', 'dv'} else 'ltr'

    def __call__(self, source):
        if source in self.messages:
            return self.messages[source]
        if self.catalog and self.catalog != 'en-US':
            return MESSAGES.get(source, {}).get(self.catalog, source)
        return source

    def metadata(self):
        return {'language': self.language,
                'interface_status': 'ready' if self.ready else 'needs_translation',
                'instruction': 'Present user-facing reports, status, questions, choices, and interface explanations in this conversation language. Internal model work stays en-US; formal artifacts follow their approved deliverable language. Original saved answers and technical records remain verbatim.'}

    def require_ready(self):
        if not self.ready:
            raise BoardError('Interface templates changed; refresh this conversation language with board_language before opening forms')

    def error(self, error):
        detail = str(error)
        if detail in MESSAGES:
            return '<p>' + html.escape(self(detail)) + '</p>'
        return '<p>' + html.escape(self('This action could not be completed. Return to Codex with the technical details below.')) + '</p><details><summary>' + html.escape(self('Technical details')) + '</summary><pre>' + html.escape(detail) + '</pre></details>'


def preference(store, scope, conversation, language=None, messages=None, template_hash=None):
    text_field(conversation, 'conversation_id', 240)
    with store.connect() as db:
        project = store.authorize(db, scope)
        if language is not None:
            language = language_tag(language)
            catalog = built_in(language)
            if catalog is None and messages is None:
                return translation_request(language)
            if messages is not None:
                if catalog is not None:
                    raise BoardError('Built-in language catalogs cannot be replaced')
                if template_hash != TEMPLATE_HASH:
                    raise BoardError('Interface templates changed; fetch and translate the current template')
                validate_messages(messages)
            db.execute('INSERT OR REPLACE INTO conversation_languages VALUES(?,?,?,?,?)',
                       (project['id'], conversation, language, canonical(messages or {}), TEMPLATE_HASH))
        elif messages is not None or template_hash is not None:
            raise BoardError('Supply language when submitting translations')
        translator = read(db, project['id'], conversation)
        if not translator.ready:
            return translation_request(translator.language)
        return dict(translator.metadata(), status='ready')


def translation_request(language):
    return {'status': 'needs_translation', 'language': language, 'template_hash': TEMPLATE_HASH,
            'templates': {source: source for source in MESSAGES},
            'instruction': 'Using the current Codex, translate every value into the requested language; keep English keys, HTML/attributes/links, and ordered placeholders exactly. Submit messages and template_hash to board_language before opening forms. No external model call or new approval is needed. The existing preference has not changed. Status, results, cancellation, and recovery remain available.'}


def read(db, project, conversation):
    row = db.execute('SELECT * FROM conversation_languages WHERE project=? AND conversation=?', (project, conversation)).fetchone()
    if row:
        messages = json.loads(row['messages'])
        ready = bool(built_in(row['language'])) or (row['template_hash'] == TEMPLATE_HASH and set(messages) == set(MESSAGES))
        return Translator(row['language'], messages, ready)
    return Translator()


def context(store, scope=None, conversation=None, job=None):
    if (scope is None) != (conversation is None) and not (scope is not None and job):
        raise BoardError('Supply both scope and conversation_id for conversation-language settings')
    if scope is None:
        return Translator()
    with store.connect() as db:
        project = store.authorize(db, scope, job) if job else store.authorize(db, scope)
        project_id = project['project'] if job else project['id']
        if conversation is None and job:
            row = db.execute('SELECT t.conversation FROM board_tasks t JOIN task_jobs j ON j.task=t.id WHERE j.job=?', (job,)).fetchone()
            conversation = row['conversation'] if row else None
        if conversation:
            text_field(conversation, 'conversation_id', 240)
            return read(db, project_id, conversation)
    return Translator()


def bind_ticket(store, token, scope=None, conversation=None):
    if scope is None and conversation is None:
        return
    context(store, scope, conversation)
    with store.connect() as db:
        project = store.authorize(db, scope)
        row = db.execute('SELECT job FROM tickets WHERE token=?', (token,)).fetchone()
        if not row:
            raise BoardError('Unknown confirmation link')
        if row['job']:
            store.authorize(db, scope, row['job'])
        db.execute('INSERT OR REPLACE INTO ticket_languages VALUES(?,?,?)', (token, project['id'], conversation))


def for_ticket(store, token):
    # Resolve presentation even for expired links; authorization still uses ticket_info.
    with store.connect() as db:
        row = db.execute('SELECT project,conversation FROM ticket_languages WHERE token=?', (token,)).fetchone()
        if not row:
            row = db.execute('SELECT t.project,t.conversation FROM tickets k JOIN task_jobs j ON j.job=k.job JOIN board_tasks t ON t.id=j.task WHERE k.token=?', (token,)).fetchone()
        return read(db, row['project'], row['conversation']) if row else Translator()


def copy_ticket(store, previous, new):
    with store.connect() as db:
        db.execute('INSERT INTO ticket_languages SELECT ?,project,conversation FROM ticket_languages WHERE token=?', (new, previous))
