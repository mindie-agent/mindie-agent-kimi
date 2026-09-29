"""Goal-level configuration outcomes using real saved state, no model calls."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from support import SCRIPTS, make_config

sys.path.insert(0, str(SCRIPTS))


class ProductFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, MINDIE_KIMI_CONFIG=str(make_config(self.root)),
                                      XDG_CONFIG_HOME=str(self.root / 'xdg'))
        self.environment.start()
        self.addCleanup(self.environment.stop)
        import entry
        self.entry = entry

    def test_install_and_saved_choice_do_not_hide_missing_configuration(self):
        import consent
        self.assertEqual(self.entry.status_payload()['experience'], 'needs-configuration')
        consent.record_choice('contribute')
        payload = self.entry.status_payload()
        self.assertEqual(payload['experience'], 'needs-configuration')
        self.assertEqual(payload['choices'], [])

    def test_invalid_destination_leaves_configuration_retryable(self):
        import consent
        with self.assertRaises(ValueError):
            self.entry._apply_choice('ses_product', str(self.root), 'contribute',
                                     repository='invalid', account='sample-user')
        self.assertIsNone(consent.load()['choice'])
        self.assertEqual(self.entry.status_payload()['experience'], 'needs-configuration')

    def test_removed_modes_cannot_complete_new_setup(self):
        import consent
        for word in ('read-only', 'later'):
            with self.assertRaises(ValueError):
                self.entry._parse_native_setup(word)
            with self.assertRaises(ValueError):
                self.entry._apply_choice('ses_product', str(self.root), word)
        self.assertIsNone(consent.load()['choice'])

    def test_enable_prepares_existing_task_and_reports_service_failure(self):
        import admission
        import knowledge_service
        admission.activate('ses_product', project_root=str(self.root), root_session='ses_product')
        with patch.object(knowledge_service, 'ensure_service', side_effect=OSError('controlled failure')) as start:
            payload = self.entry._apply_entry_choice({}, 'ses_product', str(self.root),
                dict(choice='contribute', repository='owner/knowledge', account='sample-user'))
        start.assert_called_once()
        self.assertEqual(payload['experience'], 'unavailable')
        self.assertEqual(payload['sharing']['service'], 'not-started:OSError')

    def test_legacy_declines_are_disabled_not_fresh_setup_or_success(self):
        import consent
        for choice in ('read-only', 'later', 'disabled'):
            consent.record_choice(choice)
            payload = self.entry.status_payload()
            self.assertEqual(payload['experience'], 'disabled')
            self.assertEqual(payload['choices'], [])
            self.assertEqual(consent.load()['choice'], choice)
