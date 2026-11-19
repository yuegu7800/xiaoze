"""Shallow title detection recovery; synthetic pixels only, no desktop/API."""
import unittest
import re
from unittest.mock import Mock, patch

from PIL import Image

from core import ReadUnavailableError
from test_visual_adapter import upstream
from visual_adapter import message_frame_ocr, private_title_reader, read_title_items


def box(text='虚构群(10)', x=10, y=8, w=90, h=24):
    return dict(text=text, x=x, y=y, w=w, h=h)


class TitlePaddingTests(unittest.TestCase):
    def test_native_shallow_title_retries_without_new_pixels_and_restores_coordinates(self):
        image = Image.new('RGB', (678, 40), '#19191a')
        image.putpixel((12, 9), (250, 250, 250))
        def recognize(canvas):
            if canvas.size == image.size:
                return []
            self.assertEqual(canvas.size, (710, 112))
            self.assertEqual(canvas.crop((16, 36, 694, 76)).tobytes(), image.tobytes())
            self.assertEqual(canvas.getpixel((0, 0)), image.getpixel((677, 39)))
            return [box(x=26, y=44)]
        ocr = Mock(side_effect=recognize)
        self.assertEqual(read_title_items(ocr, image), [box()])
        self.assertEqual(ocr.call_count, 2)

    def test_success_never_uses_retry_or_changes_title(self):
        ocr = Mock(return_value=[box('另一个群(10)')])
        self.assertEqual(read_title_items(ocr, Image.new('RGB', (678, 40)))[0]['text'], '另一个群(10)')
        ocr.assert_called_once()

    def test_blank_stays_unverified_and_large_crop_has_no_extra_attempt(self):
        ocr = Mock(return_value=[])
        self.assertEqual(read_title_items(ocr, Image.new('RGB', (678, 40))), [])
        self.assertEqual(ocr.call_count, 2)
        ocr.reset_mock()
        self.assertEqual(read_title_items(ocr, Image.new('RGB', (678, 112))), [])
        ocr.assert_called_once()

    def test_padding_only_text_cannot_become_title(self):
        ocr = Mock(side_effect=[[], [box('伪标题(3)', x=0, y=0, w=10, h=10)]])
        self.assertEqual(read_title_items(ocr, Image.new('RGB', (678, 40))), [])

    def test_original_title_reader_uses_recovery_without_mutating_upstream(self):
        shot = Image.new('RGB', (1000, 400))
        class Native:
            _title_region = (.1, .1, .778, .2)
            _refresh = lambda self, **kw: shot
        ocr = Mock(side_effect=[[], [box(x=26, y=44)]])
        with patch.dict(upstream.__dict__, ocr_image=ocr, re=re):
            reader = private_title_reader(upstream.read_title)
            self.assertEqual(reader(Native()), '虚构群(10)')
            self.assertIs(upstream.read_title.__globals__['ocr_image'], ocr)

    def test_same_frame_title_retry_preserves_body_coordinates_and_exact_name(self):
        shot = Image.new('RGB', (1000, 500))
        title, message = (.1, .04, .778, .12), (.1, .2, .99, .9)
        joint = shot.crop((100, 20, 990, 450))
        def recognize(image):
            if image.size == (678, 40):
                return []
            if image.size == (710, 112):
                return [box('别的群(10)', x=26, y=44)]
            self.assertEqual(image.size, (890, 390))
            return [box('虚构正文', x=100, y=90)]
        result = message_frame_ocr(recognize, joint, shot, title, message)
        self.assertEqual(result, [box('别的群(10)'), box('虚构正文', x=100, y=130)])

    def test_same_frame_recovery_failure_never_reads_body(self):
        shot = Image.new('RGB', (1000, 500))
        ocr = Mock(return_value=[])
        with self.assertRaises(ReadUnavailableError) as error:
            message_frame_ocr(ocr, shot.crop((100, 20, 990, 450)), shot,
                              (.1, .04, .778, .12), (.1, .2, .99, .9))
        self.assertEqual(error.exception.reason, 'title_blank')
        self.assertEqual(ocr.call_count, 2)


if __name__ == '__main__':
    unittest.main()
