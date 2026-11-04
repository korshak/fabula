import time
from unittest.mock import patch

from fabula import server as srv
from fabula.advisor import recommend
from fabula.serializer import serialize_book
from tests.test_forms import book, provider
from tests.test_server_guards import ServerCase


class PlanEndpoints(ServerCase):
    def test_review_job_saves_report_and_changed_plan_invalidates_it(self):
        project = srv.Handler.manager.current
        original = project.read_source()
        self.addCleanup(project.write_source, original)
        b = book()
        project.write_source(serialize_book(b))
        p = provider()
        with patch.object(project, 'registry') as registry:
            registry.return_value.get.return_value = p
            status, raw = self.request('POST', '/api/plan-review', body={'provider':'fake'})
            self.assertEqual(status, 200)
            job_id = self.json_body(raw)['job']['id']
            for _ in range(100):
                if project.jobs[job_id]['status'] != 'running':
                    break
                time.sleep(.01)
            self.assertEqual(project.jobs[job_id]['status'], 'done')
            status, raw = self.request('GET', '/api/plan-review?provider=fake')
            self.assertEqual(status, 200)
            self.assertEqual(self.json_body(raw)['report']['verdict'], 'ok')
            b['settings']['target_words'] = 1100
            project.write_source(serialize_book(b))
            _, raw = self.request('GET', '/api/plan-review?provider=fake')
            self.assertIsNone(self.json_body(raw)['report'])

    def test_review_without_form_is_rejected_before_model(self):
        project = srv.Handler.manager.current
        original = project.read_source(); self.addCleanup(project.write_source, original)
        b = book(); b['settings'].pop('form')
        project.write_source(serialize_book(b))
        p = provider()
        with patch.object(project, 'registry') as registry:
            registry.return_value.get.return_value = p
            status, _ = self.request('POST', '/api/plan-review', body={'provider':'fake'})
            self.assertEqual(status, 400)
            p.generate.assert_not_called()

    def test_draft_receives_chosen_settings_before_planning(self):
        project = srv.Handler.manager.current
        p = provider(); p.status.return_value = {'available':True, 'reason':''}
        destination = self.tmp / 'draft-from-api'
        chosen = {'form':'novella','target_words':12000}
        with patch.object(project, 'registry') as registry, patch('fabula.draft.draft_book') as draft:
            registry.return_value.get.return_value = p
            draft.return_value = book()
            job = project.draft_project_job(str(destination), 'notes', 'uk', 'fake', chosen)
            for _ in range(100):
                if job['status'] != 'running': break
                time.sleep(.01)
            self.assertEqual(job['status'], 'done', job['error'])
            self.assertEqual(draft.call_args.kwargs['settings'], chosen)
            self.assertEqual(draft.call_args.args[:2], ('notes', 'uk'))

    def test_advisor_preserves_form_budget_instead_of_growing_to_fit_scenes(self):
        b = book(); b['settings']['target_words'] = 400
        advice = recommend('', b)
        self.assertEqual(advice['settings']['target_words'], 400)
