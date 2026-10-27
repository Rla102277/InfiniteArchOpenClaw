import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from aihq import core


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patch = patch.object(core, 'PROJECTS', Path(self.tmp.name))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_project_document_and_activity_created(self):
        p = core.create_project('Helpful Bot', 'Summarize my own bookmarked articles.')
        self.assertEqual(p['id'], 'helpful-bot')
        self.assertTrue((core.PROJECTS / 'helpful-bot' / 'docs' / 'HANDOFF.md').exists())
        self.assertEqual(core.state()['projects'][0]['stage'], 'discovery')
        self.assertEqual(len(core.state()['events']), 1)

    def test_duplicate_project_rejected(self):
        core.create_project('Helpful Bot', 'Helpful idea')
        with self.assertRaises(ValueError):
            core.create_project('Helpful Bot', 'Another idea')

    def test_features_are_explicit_requests_not_approved(self):
        core.create_project('Helpful Bot', 'Helpful idea')
        f = core.create_feature('helpful-bot', 'Weekly digest', 'Read my saved bookmarks')
        self.assertEqual(f['stage'], 'requested')
        self.assertEqual(core.state()['projects'][0]['feature_count'], 1)

    def test_approval_does_not_execute_or_merge(self):
        core.create_project('Helpful Bot', 'Helpful idea')
        evidence = core.PROJECTS / 'helpful-bot' / 'evidence' / 'test.log'
        evidence.write_text('Command: python -m unittest\nExit code: 0\nTests passed', encoding='utf-8')
        r = core.request_review('helpful-bot', 'Demo', 'evidence/test.log', 'feature/demo')
        accepted = core.decide_review('helpful-bot', r['id'], 'accepted')
        self.assertEqual(accepted['status'], 'accepted')
        self.assertEqual(core.state()['pending'], [])
        with self.assertRaises(ValueError):
            core.decide_review('helpful-bot', r['id'], 'accepted')

    def test_invalid_slug_and_review_ids_rejected(self):
        core.create_project('Helpful Bot', 'Helpful idea')
        for malicious in ('../secret', 'helpful-bot/../../etc', '/tmp'):
            with self.assertRaises(ValueError):
                core.project_path(malicious)
        with self.assertRaises(ValueError):
            core.decide_review('helpful-bot', '../../../tmp', 'accepted')

    def test_input_limits_and_ref_validation(self):
        with self.assertRaises(ValueError):
            core.create_project('Bad', '')
        core.create_project('Helpful Bot', 'Helpful idea')
        with self.assertRaises(ValueError):
            core.request_review('helpful-bot', 'Demo', '../elsewhere', 'feature/demo')
        evidence = core.PROJECTS / 'helpful-bot' / 'evidence' / 'check.log'
        evidence.write_text('Real tool output', encoding='utf-8')
        with self.assertRaises(ValueError):
            core.request_review('helpful-bot', 'Demo', 'evidence/check.log', '../../foo;rm -rf x')


if __name__ == '__main__':
    unittest.main()
