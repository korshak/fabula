import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from fabula.providers import CodexCLIProvider, DEFAULT_CONFIG, ProviderError, Registry, validate_config


class CodexProvider(unittest.TestCase):
    def setUp(self):
        self.provider = CodexCLIProvider('codex-cli', {'type':'codex-cli', 'timeout':12, 'model':'chosen-model'})

    def test_registry_and_scaffold_config_support_codex(self):
        self.assertEqual(validate_config(DEFAULT_CONFIG), [])
        self.assertIsInstance(Registry(DEFAULT_CONFIG).get('codex-cli'), CodexCLIProvider)

    def test_final_file_only_and_no_shell_interpolation(self):
        seen = {}
        def run(cmd, **kwargs):
            seen.update(cmd=cmd, **kwargs)
            output = Path(cmd[cmd.index('--output-last-message')+1])
            output.write_text('  Fiction, not logs.\n', encoding='utf-8')
            return subprocess.CompletedProcess(cmd, 0, 'debug logs', '')
        with patch('fabula.providers.subprocess.run', side_effect=run):
            result = self.provider.generate('Instructions with "quotes"\nand українська.', '$(do-not-run)', 'medium')
        self.assertEqual(result, 'Fiction, not logs.')
        self.assertEqual(seen['input'], '$(do-not-run)')
        self.assertNotIn('shell', seen)
        self.assertEqual(seen['timeout'], 12)
        self.assertIn('chosen-model', seen['cmd'])
        self.assertIn('read-only', seen['cmd'])
        self.assertIn('--ephemeral', seen['cmd'])
        self.assertIn('--ignore-user-config', seen['cmd'])
        self.assertFalse(Path(seen['cwd']).exists())

    def test_missing_binary_and_auth_failure_and_timeout_and_empty_output(self):
        errors = [FileNotFoundError(), subprocess.TimeoutExpired('codex', 12),
                  subprocess.CompletedProcess([], 1, '', 'Please log in'),
                  subprocess.CompletedProcess([], 0, 'logs only', '')]
        for error in errors:
            with self.subTest(error=error):
                opts = {'side_effect': error} if isinstance(error, Exception) else {'return_value':error}
                with patch('fabula.providers.subprocess.run', **opts), self.assertRaises(ProviderError):
                    self.provider.generate('sys','prompt')

    def test_status_does_not_claim_auth_has_been_probed(self):
        with patch('fabula.providers.shutil.which', return_value=None):
            self.assertFalse(self.provider.status()['available'])
        with patch('fabula.providers.shutil.which', return_value='/bin/codex'):
            status = self.provider.status()
            self.assertTrue(status['available'])
            self.assertTrue(status['reason'])
