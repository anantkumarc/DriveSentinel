import unittest
from dms.app import _manifest_source


class ManifestPrivacyTests(unittest.TestCase):
    def test_network_sources_are_not_saved_in_manifest(self):
        for source in ('http://user:secret@camera:8080/video?token=secret', 'rtsp://camera/stream?key=secret'):
            self.assertEqual(_manifest_source(source), 'network_stream')

    def test_local_sources_are_preserved(self):
        self.assertEqual(_manifest_source(0), '0')
        self.assertEqual(_manifest_source('sample.mp4'), 'sample.mp4')
