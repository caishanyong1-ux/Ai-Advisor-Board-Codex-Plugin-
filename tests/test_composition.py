import copy
import time
from unittest.mock import patch

from test_board import BoardFixture, FakeLive, live_plan
from runtime.core import BoardError, normalize_plan, executable_plan


class CompositionTests(BoardFixture):
    def setUp(self):
        super().setUp()
        self.board.providers['vertex'] = FakeLive()

    def member(self, ident, effort=None):
        model = self.board.catalog()['models'][ident]
        return dict(model=ident, role='Independent adviser', rationale='Synthetic capability fit',
                    assignment='Assess the same complete question independently.',
                    value_rationale='Synthetic complementary model capabilities',
                    effort=effort or model['efforts'][0], max_output_tokens=2000)

    def test_paid_board_cannot_shrink_below_codex_plus_four_models(self):
        members = [self.member(m) for m in ['gemini-4-argon', 'claude-sonnet-5-5',
                   'deepseek-ai/deepseek-v4.1-flash-maas']]
        for count in (1, 2, 3):
            with self.subTest(count=count), self.assertRaisesRegex(BoardError, 'four|4'):
                self.board._plan(live_plan(members=members[:count]))

    def test_different_roles_or_efforts_do_not_make_duplicate_models_distinct(self):
        members = [dict(self.member('gemini-4-argon', effort=effort), role=role)
                   for effort, role in [('low', 'Planner'), ('medium', 'Analyst'),
                                        ('high', 'Writer'), ('high', 'Reviewer')]]
        with self.assertRaisesRegex(BoardError, 'distinct|duplicate'):
            self.board._plan(live_plan(members=members))

    def test_same_model_on_two_routes_counts_once(self):
        catalog = self.board.catalog()
        alias = dict(copy.deepcopy(catalog['models']['gemini-4-argon']),
                     id='custom/synthetic/gemini-4-argon', execution_provider='custom')
        catalog['models'][alias['id']] = alias
        members = [self.member(m) for m in ['gemini-4-argon', 'claude-sonnet-5-5',
                   'deepseek-ai/deepseek-v4.1-flash-maas']]
        members.append(dict(members[0], model=alias['id']))
        with patch.object(self.board, 'catalog', return_value=catalog):
            with self.assertRaisesRegex(BoardError, 'distinct|duplicate'):
                self.board._plan(live_plan(provider='live', members=members))

    def test_maas_route_suffix_does_not_create_a_distinct_model(self):
        catalog = self.board.catalog()
        native = 'deepseek-ai/deepseek-v4.1-flash-maas'
        alias = dict(copy.deepcopy(catalog['models'][native]), id='custom/synthetic/deepseek',
                     wire_model='deepseek-v4.1-flash', execution_provider='custom')
        catalog['models'][alias['id']] = alias
        members = [self.member(m) for m in [native, 'gemini-4-argon', 'claude-sonnet-5-5']]
        members.append(dict(members[0], model=alias['id']))
        with patch.object(self.board, 'catalog', return_value=catalog):
            with self.assertRaisesRegex(BoardError, 'duplicate'):
                self.board._plan(live_plan(provider='live', members=members))

    def test_same_maker_different_models_are_allowed_and_approval_still_required(self):
        members = [self.member(m) for m in ['gemini-4-argon', 'gemini-3.8-flash',
                   'deepseek-ai/deepseek-v4.1-flash-maas', 'zai-org/glm-5.2-maas']]
        proposal = self.prepare(live_plan(members=members))
        self.assertEqual('awaiting_approval', proposal['state'])
        self.assertEqual(5, proposal['plan']['composition']['total_models_including_codex'])
        self.assertEqual(4, proposal['plan']['composition']['external_models'])
        self.assertEqual([], self.board.providers['vertex'].calls)

    def test_legacy_small_board_cannot_resume_paid_dispatch(self):
        raw = live_plan(members=[self.member('gemini-4-argon')])
        frozen = normalize_plan(raw, self.board.catalog(), self.board.providers['vertex'].target)
        with self.assertRaisesRegex(BoardError, 'four|4'):
            executable_plan(frozen)

    def test_user_selected_external_chairman_remains_an_extra_approved_model(self):
        proposal = self.prepare(live_plan(chairman=self.member('mistral-medium-3')))
        self.assertEqual('mistral-medium-3', proposal['plan']['chairman']['model'])
        self.assertEqual(5, proposal['plan']['composition']['external_models'])
        self.assertEqual(6, proposal['plan']['composition']['total_models_including_codex'])
        self.assertEqual('awaiting_approval', proposal['state'])

    def test_incomplete_recommendation_cannot_offer_single_model_board(self):
        with self.assertRaisesRegex(BoardError, 'four|4'):
            self.with_selection(live_plan(members=[self.member('gemini-4-argon')]))

    def test_three_levels_keep_four_models_and_cannot_change_after_selection(self):
        for level in ('highest', 'balanced', 'economy'):
            with self.subTest(level=level):
                raw = self.with_selection(live_plan(thinking_level=level))
                proposal = self.board.prepare(self.scope, raw)
                self.assertEqual(level, proposal['plan']['thinking_level'])
                self.assertEqual(4, proposal['plan']['composition']['external_models'])
                changed = dict(raw, thinking_level='economy' if level != 'economy' else 'highest')
                with self.assertRaisesRegex(BoardError, 'thinking level'):
                    self.board.prepare(self.scope, changed)
        raw = live_plan()
        raw.pop('thinking_level')
        with self.assertRaisesRegex(BoardError, 'thinking_level'):
            self.board._plan(raw)

    def test_full_reason_report_survives_expiry_but_stays_project_scoped(self):
        raw = self.with_selection(live_plan())
        report = self.board.selection_details(self.scope, raw['selection_id'])
        self.assertEqual(set(report['catalog_review']['considered_models']), {m['model'] for m in report['candidates']})
        self.assertTrue(all(m['value_reason'] for m in report['candidates']))
        self.assertEqual(4, len(report['route_comparisons']))
        with self.assertRaises(BoardError):
            self.board.selection_details(self.scope2, raw['selection_id'])
        with self.board.store.connect() as db:
            db.execute('UPDATE model_selections SET expires=?', (time.time() - 1,))
        self.assertEqual(report, self.board.selection_details(self.scope, raw['selection_id']))
        with self.assertRaisesRegex(BoardError, 'older than'):
            self.board.prepare(self.scope, raw)

    def test_missing_route_comparisons_and_unselected_assessments_block_research(self):
        raw = self.with_selection(live_plan())
        report = self.board.selection_details(self.scope, raw['selection_id'])
        keys = {'model','effort','task_fit','value_reason','limitations','recommend','evidence_indices'}
        args = dict(scope=self.scope, task_type=report['task_type'], criteria=report['criteria'],
                    evidence=report['evidence'], candidates=[{k:v for k,v in m.items() if k in keys} for m in report['candidates']],
                    catalog_fingerprint=report['catalog_review']['catalog_fingerprint'],
                    considered_models=report['catalog_review']['considered_models'], thinking_level=report['thinking_level'],
                    difficulty_assessment=report['difficulty_assessment'], route_comparisons=report['route_comparisons'])
        with self.assertRaisesRegex(BoardError, 'routes'):
            self.board.recommend(**dict(args, route_comparisons=[]))
        with self.assertRaisesRegex(BoardError, 'every considered model'):
            self.board.recommend(**dict(args, candidates=[m for m in args['candidates'] if m['recommend']]))
