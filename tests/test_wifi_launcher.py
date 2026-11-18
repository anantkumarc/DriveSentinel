import unittest

from dms.launcher import phone_stream_url


class PhoneStreamUrlTests(unittest.TestCase):
    def test_phone_address_gets_mjpeg_path(self):
        self.assertEqual(phone_stream_url('192.168.1.1:8080'), 'http://192.168.1.1:8080/video')
        self.assertEqual(phone_stream_url('http://192.168.1.1:8080/'), 'http://192.168.1.1:8080/video')

    def test_explicit_stream_path_is_preserved(self):
        self.assertEqual(phone_stream_url('http://camera:8080/custom'), 'http://camera:8080/custom')

    def test_invalid_input_is_rejected(self):
        for value in ('', 'http://', 'http://user:pass@camera/', 'ftp://camera', 'http://camera:invalid'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                phone_stream_url(value)
