import os
import tempfile
import unittest
from unittest.mock import patch

from content_bot.media import transcription


class InstagramCarouselTests(unittest.TestCase):
    def test_skips_video_and_downloads_images_after_it(self):
        source = {
            'post_type': 'carousel',
            'author': 'author',
            'description': 'caption',
            'image_urls': ['first', 'last'],
            'media_items': [
                {'url': 'first', 'is_video': False},
                {'url': 'video', 'is_video': True},
                {'url': 'last', 'is_video': False},
            ],
            'video_url': '',
            'instagram_context': object(),
            'ok': True,
        }

        def fake_download(url, path, context=None):
            with open(path, 'wb') as output:
                output.write(url.encode('ascii'))
            return True

        with tempfile.TemporaryDirectory() as tmp_dir, \
                patch.object(transcription, '_ig_source_instaloader', return_value=source), \
                patch.object(transcription, '_ig_source_oembed', return_value={'ok': False}), \
                patch.object(transcription, '_ig_source_scrape', return_value={'ok': False}), \
                patch.object(transcription, '_download_image', side_effect=fake_download) as download:
            result = transcription.process_instagram_url(
                'https://www.instagram.com/p/abc123/', tmp_dir
            )

            self.assertEqual(2, len(result['media_files']))
            downloaded = []
            for path in result['media_files']:
                with open(path, 'rb') as source_file:
                    downloaded.append(source_file.read().decode('ascii'))
            self.assertEqual(['first', 'last'], downloaded)
            self.assertEqual(['first', 'last'], [call.args[0] for call in download.call_args_list])
            self.assertTrue(all(os.path.exists(path) for path in result['media_files']))


if __name__ == '__main__':
    unittest.main()
