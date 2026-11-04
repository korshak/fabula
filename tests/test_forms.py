import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fabula.agent import WriterAgent
from fabula.compiler import compile_source, iter_scenes
from fabula.draft import draft_book
from fabula.forms import (budget_conflict, cached_review, ensure_plan_review,
                          review_plan, save_review)
from fabula.prompts import book_bible, critique_prompt
from fabula.scaffold import starter_book, write_project
from fabula.serializer import serialize_book
from fabula.state import ProjectState
from tests.test_cli import CliCase

OK = {'verdict': 'ok', 'summary': 'One focused choice with room for its consequences.', 'issues': []}
ISSUES = {'verdict': 'issues', 'summary': 'A second independent story starts after the climax.',
          'issues': [{'problem': 'New arc after climax', 'evidence': 'The return to Earth',
                      'options': [{'action': 'split', 'proposal': 'Make the Earth journey a separate story.'}]}]}


def provider(answer=OK):
    p = Mock(name='provider')
    p.name = 'fake'
    p.cfg = {'type': 'fake'}
    p.generate.return_value = json.dumps(answer)
    return p


def book():
    b = starter_book('en')
    b['settings'].update(form='short_story', target_words=1000)
    next(iter_scenes(b))[2]['show'] = ['Ann chooses to leave; then feels the cost.']
    return b


class Forms(unittest.TestCase):
    def test_form_and_author_exception_survive_roundtrip(self):
        b = book()
        b['settings']['form_override'] = 'Two viewpoints expose the same choice.'
        r = compile_source(serialize_book(b))
        self.assertTrue(r['ok'], r['diagnostics'])
        for key in ('form', 'target_words', 'form_override'):
            self.assertEqual(r['book']['settings'][key], b['settings'][key])

    def test_invalid_settings_are_compile_errors(self):
        for key, value, code in [('form', 'poem', 'FAB111'), ('target_words', -1, 'FAB112'),
                                 ('target_words', 1.5, 'FAB112')]:
            b = book(); b['settings'][key] = value
            r = compile_source(serialize_book(b))
            self.assertFalse(r['ok'])
            self.assertIn(code, [d['code'] for d in r['diagnostics']])

    def test_budget_conflict_is_warning_but_incomplete_lengths_are_not(self):
        b = book(); b['settings']['target_words'] = 200
        r = compile_source(serialize_book(b))
        self.assertTrue(r['ok'])
        self.assertIn('FAB205', [d['code'] for d in r['diagnostics']])
        next(iter_scenes(b))[2]['length'] = None
        self.assertIsNone(budget_conflict(b))

    def test_contract_reaches_writer_and_scene_critic_in_both_languages(self):
        for lang, label in [('en', 'Short story'), ('uk', 'Оповідання')]:
            b = book(); b['settings']['language'] = lang
            self.assertIn(label, book_bible(b))
            self.assertIn(label, critique_prompt(b, next(iter_scenes(b))[2]['name'], 'text'))

    def test_unknown_form_rejected_before_any_provider_call(self):
        p = provider()
        with self.assertRaises(ValueError):
            draft_book('notes', 'en', p, settings={'form': 'poem'}, on_progress=lambda _: None)
        p.generate.assert_not_called()

    def test_author_choices_reach_planner_and_override_model_defaults(self):
        raw = {'title': 'T', 'form': 'novel', 'target_words': 90000,
               'characters': [{'name': 'Ann'}], 'acts': [{'chapters': [{'scenes': [
                   {'name': 'Choice', 'pov': 'Ann', 'show': ['She decides.'], 'length': [800,1200]}]}]}]}
        p = provider(); p.generate.side_effect = [json.dumps(raw), json.dumps(OK)]
        b = draft_book('A choice.', 'en', p, on_progress=lambda _: None,
                       settings={'form': 'short_story', 'target_words': 1000})
        self.assertEqual(b['settings']['form'], 'short_story')
        self.assertEqual(b['settings']['target_words'], 1000)
        system = p.generate.call_args_list[0].args[0]
        self.assertIn('Short story', system)
        self.assertIn('1000', system)
        with tempfile.TemporaryDirectory() as d:
            write_project(Path(d), b)
            compiled = compile_source((Path(d)/'book.fab').read_text())['book']
            self.assertIsNotNone(cached_review(compiled, p, Path(d)/'build'))

    def test_review_does_not_rewrite_author_material(self):
        b = book(); before = copy.deepcopy(b)
        report = review_plan(b, provider(ISSUES))
        self.assertEqual(b, before)
        self.assertEqual(report['issues'][0]['options'][0]['action'], 'split')

    def test_model_cannot_approve_an_impossible_budget(self):
        b = book(); b['settings']['target_words'] = 200
        report = review_plan(b, provider())
        self.assertEqual(report['verdict'], 'issues')
        self.assertTrue(report['issues'])

    def test_malformed_review_never_counts_as_approval(self):
        for bad in [[], {}, {'verdict':'ok', 'summary':'yes', 'issues':[{}]},
                    {'verdict':'issues', 'summary':'no', 'issues':[]}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                review_plan(book(), provider(bad))

    def test_cache_invalidates_plot_budget_form_and_provider_changes(self):
        with tempfile.TemporaryDirectory() as d:
            b = book(); p = provider()
            save_review(Path(d), review_plan(b, p))
            self.assertIsNotNone(cached_review(b, p, Path(d)))
            for key, value in [('target_words', 1100), ('form', 'novella')]:
                changed = copy.deepcopy(b); changed['settings'][key] = value
                self.assertIsNone(cached_review(changed, p, Path(d)))
            changed = copy.deepcopy(b); next(iter_scenes(changed))[2]['show'].append('Another adventure')
            self.assertIsNone(cached_review(changed, p, Path(d)))
            p.cfg = {'type':'fake', 'model':'other'}
            self.assertIsNone(cached_review(b, p, Path(d)))

    def test_conflict_stops_before_prose_and_waiver_keeps_report(self):
        with tempfile.TemporaryDirectory() as d:
            b = book(); p = provider(ISSUES); state = ProjectState(Path(d))
            agent = WriterAgent(b, p, on_progress=lambda _: None)
            with self.assertRaises(ValueError):
                agent.write_scene(next(iter_scenes(b))[2]['name'], state)
            self.assertEqual(p.generate.call_count, 1)  # Only the review, no prose call.
            self.assertFalse(list(Path(d).rglob('v*.md')))
            b['settings']['form_override'] = 'Keep the two arcs as a deliberate diptych.'
            ensure_plan_review(b, p, Path(d), lambda _: None)
            self.assertEqual(p.generate.call_count, 1)
            self.assertEqual(cached_review(b, p, Path(d))['verdict'], 'issues')

    def test_legacy_projects_do_not_gain_extra_calls(self):
        with tempfile.TemporaryDirectory() as d:
            p = provider()
            ensure_plan_review(starter_book('en'), p, Path(d), lambda _: None)
            p.generate.assert_not_called()

    def test_short_story_template_sets_form(self):
        self.assertEqual(starter_book('en', 'short_story')['settings']['form'], 'short_story')


class FormCLI(CliCase):
    def test_conflict_exits_cleanly_before_prose(self):
        self.run_cli('init', 'brief', '--form', 'short_story', '--target-words', '1000')
        p = provider(ISSUES); p.status.return_value = {'available': True, 'reason': ''}
        with patch('fabula.cli.load_registry') as reg:
            reg.return_value.get.return_value = p
            code, out, _ = self.run_cli('check-plan', 'brief')
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(out)['verdict'], 'issues')
            code, _, err = self.run_cli('write', 'brief')
        self.assertEqual(code, 1)
        self.assertIn('form_override', err)
        self.assertNotIn('Traceback', err)
        self.assertEqual(p.generate.call_count, 1)

    def test_init_accepts_explicit_form_budget_and_ships_codex_profile(self):
        self.assertEqual(self.run_cli('init', 'brief', '--form', 'short_story', '--target-words', '1000')[0], 0)
        b = json.loads(self.run_cli('compile', 'brief')[1])['book']
        self.assertEqual(b['settings']['form'], 'short_story')
        cfg = json.loads((self.tmp/'brief'/'models.json').read_text())
        self.assertEqual(cfg['providers']['codex-cli']['type'], 'codex-cli')

    def test_check_plan_outputs_json_and_saves_report(self):
        self.run_cli('init', 'brief', '--form', 'short_story', '--target-words', '1000')
        p = provider(); p.status.return_value = {'available': True, 'reason': ''}
        with patch('fabula.cli.load_registry') as reg:
            reg.return_value.get.return_value = p
            code, out, _ = self.run_cli('check-plan', 'brief')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['verdict'], 'ok')
        self.assertTrue((self.tmp/'brief'/'build'/'plan-review.json').exists())
