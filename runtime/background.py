"""Durable Vertex background requests, bound to existing approved attempt rows.

Creation is never retried. Recovery only reads, cancels or deletes a saved ID.
The board owns attempt/ballot commits; this module owns the remote lifecycle.
"""
from __future__ import annotations

import copy
import json
import math
import re
import socket
import ssl
import threading
import time

from .core import BoardError, Store, canonical, digest
from .catalog import price_valid, usage_cost
from .streaming import HTTPSConnection, deadline, visible_text

TRANSPORT = 'vertex_interactions_background'
MODEL = 'gemini-4-argon'
TERMINAL = {'completed', 'failed', 'cancelled', 'incomplete'}
ID = re.compile(r'[A-Za-z0-9_.~-]{1,4096}\Z')
DISCLOSURE = 'Gemini 4 Argon uses the Preview Vertex Interactions API with background=true and store=true. Google stores the approved prompt and answer. The plugin retrieves a terminal receipt before requesting deletion; retention until successful deletion is governed by Google. Failed cancellation or deletion remains visible and recoverable. Only retrieving an existing ID is retried; new generation needs human approval.'


def enabled(member):
    return member.get('transport') == TRANSPORT


def validate(member):
    if not enabled(member) or member.get('model') != MODEL or member.get('catalog_snapshot', {}).get('api_family') != 'gemini':
        raise BoardError('Unsupported background model or transport')
    target = member.get('vertex_target', {})
    from .auth import project_id
    project_id(target.get('project_id'))
    project_id(target.get('quota_project_id'))
    if target.get('location') != 'global':
        raise BoardError('The verified background transport requires global Vertex')


def request_body(member, messages):
    validate(member)
    if len(messages) != 2 or [m.get('role') for m in messages] != ['system', 'user']:
        raise BoardError('Background board calls require one independent system/user packet')
    return {'model': member['model'], 'background': True, 'store': True,
            'system_instruction': messages[0]['content'], 'input': messages[1]['content'],
            'generation_config': {'thinking_level': member['effort'], 'thinking_summaries': 'none',
                                  'max_output_tokens': member['max_output_tokens']}}


def records(db, job, unresolved_only=False):
    rows = db.execute('SELECT b.attempt,b.record,a.state FROM background_requests b JOIN attempts a ON a.id=b.attempt WHERE a.job=?', (job,)).fetchall()
    result = []
    for row in rows:
        value = json.loads(row['record'])
        # No ID means no safe recovery: retain its reservation and require a new
        # human confirmation for a replacement, as with GenerateContent.
        unresolved = bool(value.get('interaction_id')) and (value.get('remote_status') not in TERMINAL or row['state'] in {'running', 'uncertain', 'awaiting_validation'})
        if not unresolved_only or unresolved:
            result.append((row['attempt'], value, row['state']))
    return result


def summary(db, job):
    fields = ('interaction_id', 'remote_status', 'remote_missing', 'cancel_requested', 'remote_deleted', 'cleanup_pending', 'last_error', 'last_cancel_error', 'deadline_at', 'create_attempts', 'replacement_approved_at')
    return [dict(attempt_id=attempt, attempt_state=state, **{k: value[k] for k in fields if k in value}) for attempt, value, state in records(db, job)]


def guard_dispatch(db, job, allow_lost_ids=False, step=None):
    for _, value, state in records(db, job):
        if step is not None and tuple(value.get('attempt_binding', {}).get(k) for k in ('round', 'stage', 'slot')) != step:
            continue
        if state in {'running', 'awaiting_validation'}:
            raise BoardError('Wait for the active background owner or recover the saved receipt for validation; no new generation')
        if value.get('interaction_id') and not value.get('remote_missing'):
            if value.get('remote_status') not in TERMINAL or state == 'uncertain':
                raise BoardError('Recover the existing background ID before approving or dispatching another generation')
        elif state == 'uncertain' and not value.get('replacement_approved_at') and not allow_lost_ids:
            raise BoardError('A background response or resource is unavailable. New generation requires human resume confirmation; its unknown charge remains reserved')


def acknowledge_lost_ids(db, job):
    # Called only inside the human confirmation transaction. It never invents an
    # ID or claims the old operation was cancelled or free.
    for attempt, value, state in records(db, job):
        if state == 'uncertain' and (not value.get('interaction_id') or value.get('remote_missing')):
            value['replacement_approved_at'] = time.time()
            db.execute('UPDATE background_requests SET record=? WHERE attempt=?', (canonical(value), attempt))


class RequestError(Exception):
    def __init__(self, label, status=None, not_dispatched=False):
        super().__init__(label)
        self.status = status
        self.not_dispatched = not_dispatched
        self.retryable = status is None or status == 429 or status >= 500


class Background:
    def __init__(self, home, auth):
        self.store, self.auth = Store(home), auth
        self.lock = threading.RLock()
        self.active = {}
        self.read_timeout = 30
        self.poll_seconds = 5

    def load(self, attempt):
        with self.store.connect() as db:
            row = db.execute('SELECT record FROM background_requests WHERE attempt=?', (attempt,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, attempt, **changes):
        # Merge changes transactionally so a concurrent cancel cannot be lost.
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT record FROM background_requests WHERE attempt=?', (attempt,)).fetchone()
            if not row:
                raise BoardError('Background request is not bound to an attempt')
            value = json.loads(row[0]); value.update(changes)
            value['updated_at'] = time.time()
            db.execute('UPDATE background_requests SET record=? WHERE attempt=?', (canonical(value), attempt))
        return value

    def cancel(self, attempt):
        if self.load(attempt) is None:
            return False
        self.save(attempt, cancel_requested=True)
        with self.lock:
            event = self.active.get(attempt)
            if event:
                event.set()
        return True

    def request(self, value, method, suffix='', body=None, not_after=None, cancelled=None):
        conn = timer = None
        phase = 'authentication'
        try:
            if cancelled is not None and cancelled.is_set():
                raise RequestError('Cancelled before dispatch', 408, True)
            if not_after is not None and time.time() >= not_after:
                raise RequestError('Approved deadline expired before dispatch', 408, True)
            token = self.auth.token()
            context = ssl.create_default_context()
            if cancelled is not None and cancelled.is_set():
                raise RequestError('Cancelled during authentication; no request sent', 408, True)
            if not_after is not None and time.time() >= not_after:
                raise RequestError('Approved deadline expired during authentication', 408, True)
            timeout = not_after - time.time() if not_after is not None else self.read_timeout
            conn = HTTPSConnection('aiplatform.googleapis.com', timeout=timeout, context=context)
            timer = deadline(conn, timeout)
            target = value['member']['vertex_target']
            path = '/v1beta1/projects/' + target['project_id'] + '/locations/global/interactions' + suffix
            headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                       'Accept': 'application/json', 'x-goog-user-project': target['quota_project_id'],
                       'X-Server-Timeout': str(math.ceil(timeout))}
            phase = 'sending request'
            conn.request(method, path, body=None if body is None else canonical(body).encode(), headers=headers)
            conn._aab_response_socket = conn.sock
            phase = 'waiting for response headers'
            response = conn.getresponse()
            if response.status not in {200, 201, 204}:
                raise RequestError('Google background HTTP ' + str(response.status), response.status)
            phase = 'reading response body'
            raw = response.read(12000001)
            if len(raw) > 12000000:
                raise RequestError('Background response exceeds 12 MB', 422)
            data = json.loads(raw) if raw else {}
            if not isinstance(data, dict):
                raise RequestError('Unsupported background response object', 422)
            return data
        except RequestError:
            raise
        except Exception as error:
            if conn is not None and getattr(conn, '_aab_aborted', False):
                kind = 'Local deadline expired'
            elif isinstance(error, (TimeoutError, socket.timeout)):
                kind = 'Local request timeout'
            else:
                kind = type(error).__name__
            raise RequestError(kind + ' while ' + phase, 401 if phase == 'authentication' else None, phase == 'authentication') from None
        finally:
            if timer:
                timer.cancel()
            if conn:
                conn.close()

    def accept(self, attempt, data, retrieved=False, progress=None):
        value = self.load(attempt)
        ident = data.get('id')
        if not isinstance(ident, str) or not ID.fullmatch(ident) or (value.get('interaction_id') and value['interaction_id'] != ident):
            raise RequestError('Missing or mismatched background interaction ID', 422)
        # Save the ID before interpreting the rest of an otherwise bad response.
        if not value.get('interaction_id'):
            value = self.save(attempt, interaction_id=ident, id_saved_at=time.time())
        member = value['member']
        if data.get('model') not in {None, member['model']}:
            raise RequestError('Unexpected background model', 422)
        remote = data.get('status')
        if remote not in TERMINAL | {'in_progress', 'requires_action'}:
            raise RequestError('Unknown background status', 422)
        if value.get('terminal_details_confirmed'):
            # A sparse cancel acknowledgement or duplicate snapshot must never
            # replace a durable terminal answer/usage receipt.
            return value['remote_status']
        # Text parts can be token fragments. Joining them with added newlines
        # corrupts words; each returned snapshot replaces the prior snapshot.
        content = visible_text(''.join(part['text'] for step in data.get('steps', []) if step.get('type') == 'model_output'
                                       for part in step.get('content', []) if part.get('type') == 'text' and isinstance(part.get('text'), str)))
        usage = data.get('usage') or {}
        def count(key):
            n = usage.get(key)
            return n if type(n) is int and n >= 0 else None
        inp, visible, thoughts = (count(k) for k in ('total_input_tokens', 'total_output_tokens', 'total_thought_tokens'))
        out = visible + thoughts if visible is not None and thoughts is not None else None
        final_usage = retrieved and remote in TERMINAL
        cost = None
        if final_usage and inp is not None and out is not None:
            try:
                cost = usage_cost(member, inp, out)
            except BoardError:
                pass  # Preserve the receipt and reservation if frozen rates cannot price it.
        safe = {'input_tokens': inp, 'output_tokens_including_reasoning': out, 'reasoning_tokens': thoughts,
                'provider_response_id': ident, 'returned_model': data.get('model'), 'finish_reason': remote,
                'transport': TRANSPORT, 'cost_basis': 'Estimated from final stored usage and approved uncached rates. Missing final usage retains the reservation; not a billing receipt.'}
        observed = value.get('last_observed_usage', {})
        observed.update({k: safe[k] for k in ('input_tokens', 'output_tokens_including_reasoning', 'reasoning_tokens') if safe[k] is not None})
        if observed and (inp is None or out is None):
            safe['last_observed_usage_not_final'] = observed
        complete = remote == 'completed' and retrieved and bool(content.strip()) and (out is None or out <= member['max_output_tokens'])
        prior = value.get('result', {})
        if not content and prior.get('content'):
            content = prior['content']
            safe['retained_partial'] = True
            complete = False
        result = {'content': content, 'usage': safe, 'cost': cost, 'complete': complete}
        self.save(attempt, remote_status=remote, remote_missing=False, result=result, last_observed_usage=observed, terminal_details_confirmed=bool(retrieved and remote in TERMINAL))
        if progress and content:
            progress(content)
        return remote

    def retrieve(self, attempt, progress=None):
        value = self.load(attempt)
        try:
            data = self.request(value, 'GET', '/' + value['interaction_id'])
        except RequestError as error:
            if error.status == 404:
                self.save(attempt, remote_missing=True)
            raise
        return self.accept(attempt, data, True, progress)

    def cleanup(self, attempt):
        value = self.load(attempt)
        if value.get('remote_deleted') or not value.get('terminal_details_confirmed'):
            return
        try:
            self.request(value, 'DELETE', '/' + value['interaction_id'])
        except RequestError as error:
            if error.status != 404:
                self.save(attempt, cleanup_pending=True, last_error=str(error))
                return
        self.save(attempt, remote_deleted=True, cleanup_pending=False)

    def cancel_remote(self, attempt, progress=None):
        value = self.load(attempt)
        self.save(attempt, cancel_requested=True)
        try:
            self.accept(attempt, self.request(value, 'POST', '/' + value['interaction_id'] + '/cancel', {}), progress=progress)
        except RequestError as error:
            # A lost cancel response/completion race still permits a GET of ID.
            self.save(attempt, last_cancel_error=str(error))
        remote = self.retrieve(attempt, progress)
        if remote not in TERMINAL:
            raise RequestError('Remote cancellation is unconfirmed; recover the saved ID')
        self.cleanup(attempt)

    def call(self, member, messages, timeout, attempt, cancelled, progress=None):
        from .providers import CallFailed
        validate(member)
        price_valid(member)
        body = request_body(member, messages)
        value = {'member': copy.deepcopy(member), 'request_sha256': digest(body), 'deadline_at': time.time() + timeout,
                 'create_attempts': 1, 'dispatch_intent_at': time.time(), 'transport': TRANSPORT}
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM attempts WHERE id=?', (attempt,)).fetchone()
            if not row or row['state'] != 'running' or cancelled.is_set():
                raise CallFailed('Background attempt was not dispatched', False)
            if db.execute('SELECT 1 FROM background_requests WHERE attempt=?', (attempt,)).fetchone():
                raise CallFailed('A background create intent already exists; recover its ID instead of generating again')
            job = db.execute('SELECT plan_hash,approved_hash FROM jobs WHERE id=?', (row['job'],)).fetchone()
            if not job or job['plan_hash'] != job['approved_hash']:
                raise CallFailed('Background attempt has no intact approval', False)
            value['plan_sha256'] = job['approved_hash']
            value['attempt_binding'] = {k: row[k] for k in ('job', 'round', 'stage', 'slot', 'model')}
            db.execute('INSERT INTO background_requests VALUES(?,?)', (attempt, canonical(value)))
        return self._run(attempt, cancelled, progress, body)

    def recover(self, attempt, cancelled=None, progress=None):
        from .providers import CallFailed
        value = self.load(attempt)
        if not value or not value.get('interaction_id'):
            raise CallFailed('No saved background ID; recovery cannot create a replacement')
        validate(value['member'])
        # No price expiry/runtime fingerprint gate: these requests only reconcile
        # the already-paid ID against its frozen destination and original rates.
        return self._run(attempt, cancelled or threading.Event(), progress)

    def _run(self, attempt, cancelled, progress, body=None):
        from .providers import CallFailed
        with self.lock:
            if attempt in self.active:
                raise CallFailed('Background request already has an active owner')
            self.active[attempt] = cancelled
        try:
            value = self.load(attempt)
            if body is not None:
                if cancelled.is_set() or value.get('cancel_requested') or time.time() >= value['deadline_at']:
                    self.save(attempt, last_error='Cancelled or expired before create dispatch', not_dispatched=True)
                    raise CallFailed('Background attempt was not dispatched', False)
                self.accept(attempt, self.request(value, 'POST', body=body, not_after=value['deadline_at'], cancelled=cancelled), progress=progress)
            while True:
                value = self.load(attempt)
                if value.get('terminal_details_confirmed'):
                    self.cleanup(attempt)
                    return self.load(attempt)['result']
                if value.get('remote_status') in TERMINAL:
                    self.retrieve(attempt, progress)
                    continue
                if cancelled.is_set() or value.get('cancel_requested') or time.time() >= value['deadline_at']:
                    self.cancel_remote(attempt, progress)
                    return self.load(attempt)['result']
                try:
                    remote = self.retrieve(attempt, progress)
                except RequestError as error:
                    self.save(attempt, last_error=str(error))
                    if not error.retryable:
                        raise
                    cancelled.wait(self.poll_seconds)
                    continue
                if remote == 'requires_action':
                    self.save(attempt, cancel_requested=True, last_error='Unexpected tool/action request')
                    continue
                if remote not in TERMINAL:
                    cancelled.wait(self.poll_seconds)
        except RequestError as error:
            value = self.save(attempt, last_error=str(error))
            if body is not None and not value.get('interaction_id') and error.not_dispatched:
                self.save(attempt, not_dispatched=True)
                raise CallFailed(str(error), False) from None
            hint = '; use board_recover_background for this saved ID' if value.get('interaction_id') else '; no ID received, no automatic generation retry'
            raise CallFailed(str(error) + hint) from None
        finally:
            with self.lock:
                self.active.pop(attempt, None)
