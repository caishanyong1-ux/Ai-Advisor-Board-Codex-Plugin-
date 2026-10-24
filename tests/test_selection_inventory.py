import copy
import time
from datetime import datetime, timezone
from unittest.mock import patch

from test_board import BoardFixture, FakeLive, live_plan
from runtime.core import BoardError, executable_plan
from runtime.catalog import model_available, register_vertex, Catalog
from runtime import recommendations


class SelectionInventoryTests(BoardFixture):
    def setUp(self):
        super().setUp()
        self.board.providers['vertex'] = FakeLive()
        self.board.edition = dict(self.board.edition, excluded_publishers=['anthropic'])
        grok = copy.deepcopy(self.board.catalog()['models']['xai/grok-4.7'])
        grok.update(id='direct-xai/grok-4.7', execution_provider='grok', efforts=['high'])
        self.grok = patch.object(self.board.grok, 'catalog', return_value={grok['id']: grok})
        self.grok.start()
        self.addCleanup(self.grok.stop)

    def research(self, **kwargs):
        stamp = datetime.now(timezone.utc).isoformat()
        sources = [dict(url=url, checked_at=stamp, kind='benchmark', finding='Synthetic fixture')
                   for url in ['https://first.example.test/benchmark', 'https://second.example.test/benchmark']]
        candidates = [dict(model=model, effort=effort, task_fit='Synthetic task fit',
                           value_reason='Synthetic selected-member value', evidence_indices=[0, 1], recommend=True)
                      for model, effort in [('gemini-4-argon','high'), ('direct-xai/grok-4.7','high'),
                                            ('deepseek-ai/deepseek-v4.1-flash-maas','none'), ('zai-org/glm-5.2-maas','none')]]
        candidates = kwargs.pop('candidates', candidates)
        seen = {m['model'] for m in candidates}
        for model in self.board.models()['models']:
            if model['selection_allowed'] and model['id'] not in seen:
                candidates.append(dict(model=model['id'], effort=model['efforts'][0], task_fit='Synthetic comparison',
                                       value_reason='Synthetic non-selection assessment', evidence_indices=[0,1], recommend=False))
        kwargs.setdefault('thinking_level', 'balanced')
        kwargs.setdefault('difficulty_assessment', 'Synthetic task difficulty')
        kwargs.setdefault('route_comparisons', [dict(model=m['model'], comparison='Synthetic route comparison', evidence_indices=[0,1]) for m in candidates])
        return self.board.recommend(self.scope, 'synthetic', 'Synthetic comparison', sources, candidates, **kwargs)

    def members(self):
        return [dict(model=model, effort=effort, role='Independent adviser', rationale='Synthetic fit',
                     value_rationale='Synthetic capability complement', max_output_tokens=2000)
                for model, effort in [('gemini-4-argon','high'), ('deepseek-ai/deepseek-v4.1-flash-maas','none'),
                                      ('zai-org/glm-5.2-maas','none'), ('mistral-medium-3','none')]]

    def review(self):
        inventory = self.board.models()
        return dict(catalog_fingerprint=inventory['catalog_fingerprint'],
                    considered_models=[m['model'] for m in inventory['inventory'] if m['selection_allowed']])

    def test_partial_catalog_exposes_remaining_models(self):
        page = self.board.models(limit=10)
        self.assertTrue(page.get('has_more'), 'A 30-row catalog slice silently hides other families')
        self.assertTrue(all(m['in_selection_pool'] for m in page['inventory']))
        self.assertEqual(page['total'], len(page['inventory']))
        next_page = self.board.models(limit=100, offset=page['next_offset'])
        self.assertEqual(page['total'], len(page['models']) + len(next_page['models']))
        self.assertFalse(next_page['has_more'])
        self.assertEqual(page['catalog_fingerprint'], next_page['catalog_fingerprint'])

    def test_two_familiar_models_cannot_skip_inventory_review(self):
        with self.assertRaisesRegex(BoardError, 'inventory|catalog'):
            self.research()

    def test_filtered_details_keep_the_complete_inventory(self):
        result = self.board.models(query='gemini-4-argon', limit=1)
        self.assertEqual(1, len(result['models']))
        self.assertTrue(any(m['publisher'] == 'deepseek-ai' for m in result['inventory']))
        self.assertGreater(result['catalog_total'], result['total'])
        self.assertEqual(self.board.models()['catalog_fingerprint'], result['catalog_fingerprint'])

    def test_partial_or_duplicate_consideration_is_rejected(self):
        review = self.review()
        with self.assertRaisesRegex(BoardError, 'Incomplete catalog'):
            self.research(**dict(review, considered_models=['gemini-4-argon', 'direct-xai/grok-4.7']))
        with self.assertRaisesRegex(BoardError, 'Incomplete catalog'):
            self.research(**dict(review, considered_models=review['considered_models'] * 2))

    def test_full_review_records_unselected_without_rejection_reasons(self):
        record = self.research(**self.review())
        plan = live_plan(members=self.members(),
                         selection_id=record['selection_id'])
        proposal = self.board.prepare(self.scope, plan)
        rows = proposal['plan']['model_selection']
        selected = [row for row in rows if row['status'] == 'selected']
        unselected = [row for row in rows if row['status'] == 'not_selected']
        self.assertEqual({m['model'] for m in self.members()}, {row['model'] for row in selected})
        self.assertTrue(any(row['model'] == 'direct-xai/grok-4.7' for row in unselected))
        self.assertTrue(any(row['publisher'] == 'meta' for row in unselected))
        self.assertTrue(all(set(row) == {'model', 'name', 'publisher', 'status'} for row in unselected))
        self.assertFalse(any(row['publisher'] == 'anthropic' for row in rows))
        self.assertEqual('awaiting_approval', proposal['state'])
        self.assertEqual([], self.mock.calls)

    def test_catalog_changes_require_new_review_but_preserve_saved_snapshot(self):
        record = self.research(**self.review())
        original = copy.deepcopy(record['catalog_review'])
        self.board.providers['vertex'].output_rate += 1
        with self.assertRaisesRegex(BoardError, 'current board_models inventory'):
            recommendations.validate_inventory(self.board, record)
        self.assertEqual(original, record['catalog_review'])

    def test_legacy_selection_needs_refresh_before_a_new_proposal(self):
        record = self.research(**self.review())
        record.pop('catalog_review')
        with self.assertRaisesRegex(BoardError, 'inventory'):
            recommendations.validate_inventory(self.board, record)

    def test_retired_vertex_route_is_not_revived_by_current_price(self):
        for ident in ['moonshotai/kimi-k2-thinking-maas', 'qwen/qwen3-next-80b-a3b-thinking-maas']:
            with self.subTest(model=ident), self.assertRaisesRegex(BoardError, 'retired'):
                model_available(dict(id=ident, wire_model=ident, execution_provider='vertex'))

    def test_retired_vertex_registration_is_rejected(self):
        with self.assertRaisesRegex(BoardError, 'retired'):
            register_vertex(Catalog(self.root / 'registry'), dict(id='moonshotai/kimi-k2-thinking-maas'))

    def test_retired_models_are_absent_from_inventory_details_and_proposal(self):
        catalog = self.board.catalog()
        base = catalog['models']['gemini-4-argon']
        retired = [
            dict(id='moonshotai/kimi-k2-thinking-maas', wire_model='moonshotai/kimi-k2-thinking-maas', publisher='moonshotai'),
            dict(id='synthetic/retired-native', retirement_at=time.time() - 60),
            dict(id='custom/synthetic/retired-model', execution_provider='custom', retirement_at=time.time() - 60),
        ]
        # A different provider's same-named model and a future retirement stay eligible.
        current = [
            dict(id='custom/synthetic/kimi-k2-thinking-maas', wire_model='kimi-k2-thinking-maas', execution_provider='custom'),
            dict(id='synthetic/future-retirement', retirement_at=time.time() + 86400),
        ]
        for overrides in retired + current:
            catalog['models'][overrides['id']] = dict(copy.deepcopy(base), **overrides)
        retired_ids = {model['id'] for model in retired}
        current_ids = {model['id'] for model in current}
        with patch.object(self.board, 'catalog', return_value=catalog):
            result = self.board.models(limit=100)
            self.assertTrue(retired_ids.isdisjoint(m['id'] for m in result['models']))
            self.assertTrue(retired_ids.isdisjoint(m['model'] for m in result['inventory']))
            self.assertNotIn('retired_vertex_routes', result)
            self.assertEqual(len(result['inventory']), result['catalog_total'])
            self.assertTrue(current_ids.issubset(m['model'] for m in result['inventory'] if m['selection_allowed']))
            for ident in retired_ids:
                self.assertEqual([], self.board.models(query=ident)['models'])
            review = self.review()
            self.assertTrue(retired_ids.isdisjoint(review['considered_models']))
            selection = self.research(**review)
            proposal = self.board.prepare(self.scope, live_plan(
                members=self.members(),
                selection_id=selection['selection_id']))
            self.assertTrue(retired_ids.isdisjoint(row['model'] for row in proposal['plan']['model_selection']))
            self.assertEqual('awaiting_approval', proposal['state'])

    def test_retired_model_cannot_be_added_back_as_unselected_candidate(self):
        catalog = self.board.catalog()
        ident = 'moonshotai/kimi-k2-thinking-maas'
        catalog['models'][ident] = dict(copy.deepcopy(catalog['models']['gemini-4-argon']),
                                       id=ident, wire_model=ident, publisher='moonshotai')
        candidate = dict(model=ident, effort='high', task_fit='Synthetic task fit',
                         value_reason='Synthetic value', evidence_indices=[0, 1], recommend=False)
        with patch.object(self.board, 'catalog', return_value=catalog):
            with self.assertRaisesRegex(BoardError, 'retired'):
                self.research(candidates=[candidate], **self.review())

    def test_vertex_retirement_does_not_disable_other_providers(self):
        model_available(dict(id='custom/synthetic/kimi-k2-thinking-maas',
                             wire_model='kimi-k2-thinking-maas', execution_provider='custom'))

    def test_old_approved_plan_cannot_dispatch_a_now_retired_vertex_route(self):
        model = copy.deepcopy(self.board.catalog()['models']['gemini-4-argon'])
        model.update(id='moonshotai/kimi-k2-thinking-maas', wire_model='moonshotai/kimi-k2-thinking-maas')
        from runtime.harness import contract
        frozen = dict(provider='vertex', schema_version=5, chairman='codex', members=[dict(catalog_snapshot=model)],peer_review=True,include_codex_member=True,rubric_profile='general',deliberation_harness=contract())
        with self.assertRaisesRegex(BoardError, 'retired'):
            executable_plan(frozen)
