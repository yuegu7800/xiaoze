import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_api_profile import ApiProfile

FAKE = dict(endpoint='https://fictional.example/v1/chat/completions',
            model='fictional-model', api_key='sk-fictional-only-123456')


class ProfileTests(unittest.TestCase):
    def test_encrypted_roundtrip_and_forget_scope(self):
        with tempfile.TemporaryDirectory() as folder, patch('requests.post', side_effect=AssertionError('network')):
            profile = ApiProfile(folder)
            self.assertIsNone(profile.load())
            sibling = Path(folder) / 'fictional-chat.json'
            sibling.write_text('{}')
            profile.save(FAKE)
            for value in FAKE.values():
                self.assertNotIn(value.encode(), profile.path.read_bytes())
            self.assertEqual(profile.load(), FAKE)
            profile.forget()
            self.assertIsNone(profile.load())
            self.assertTrue(sibling.exists())

    def test_atomic_failure_preserves_saved_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = ApiProfile(folder)
            profile.save(FAKE)
            before = profile.path.read_bytes()
            with patch('local_api_profile.os.replace', side_effect=OSError('fictional')):
                with self.assertRaises(OSError):
                    profile.save(dict(FAKE, model='changed'))
            self.assertEqual(profile.path.read_bytes(), before)
            self.assertEqual(list(Path(folder).iterdir()), [profile.path])
            with patch('local_api_profile._crypt', side_effect=RuntimeError('fictional')):
                with self.assertRaises(RuntimeError):
                    profile.save(FAKE)
            self.assertEqual(profile.path.read_bytes(), before)

    def test_corrupt_safe_and_oversize_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = ApiProfile(folder)
            profile.path.write_text('fictional-secret-corrupt')
            with self.assertRaises(RuntimeError) as caught:
                profile.load()
            self.assertNotIn('fictional-secret', str(caught.exception))
            self.assertEqual(profile.path.read_text(), 'fictional-secret-corrupt')
            with self.assertRaises(RuntimeError):
                profile.save(dict(FAKE, model='x' * 4097))
            self.assertEqual(profile.path.read_text(), 'fictional-secret-corrupt')


if __name__ == '__main__':
    unittest.main()
