import os
import shutil
import subprocess
import sys
import json
import unittest

# The module under test lives in the same directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

class TestActivityExtractor(unittest.TestCase):
    def setUp(self):
        self.guid = "5303510f-4ff7-4c40-bfba-ad0cae290efd"
        self.test_dir = f"/tmp/test_act_{self.guid}"
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_extract_only_html_granular(self):
        import extract_activities
        ok = extract_activities.extract_activity(
            self.guid,
            dest_dir=self.test_dir,
            only_html=True,
            verbose=False,
        )
        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(os.path.join(self.test_dir, "index.html")))
        # In only_html mode, audio 1.mp3 should NOT be downloaded
        self.assertFalse(os.path.isfile(os.path.join(self.test_dir, "1.mp3")))

        # Check that index.html contains CDN fallback for 1.mp3
        with open(os.path.join(self.test_dir, "index.html"), "r") as f:
            html = f.read()
        self.assertIn("https://ogm-large-cdn.eba.gov.tr/materyal/Uygulama/5303510f-4ff7-4c40-bfba-ad0cae290efd/1.mp3", html)

    def test_extract_with_audio(self):
        import extract_activities
        ok = extract_activities.extract_activity(
            self.guid,
            dest_dir=self.test_dir,
            no_audio=False,
            only_html=False,
            verbose=False,
        )
        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(os.path.join(self.test_dir, "index.html")))
        self.assertTrue(os.path.isfile(os.path.join(self.test_dir, "1.mp3")))
        self.assertGreater(os.path.getsize(os.path.join(self.test_dir, "1.mp3")), 1000000)

if __name__ == "__main__":
    unittest.main()
