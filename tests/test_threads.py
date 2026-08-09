import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from content_bot.detector import ContentType, detect
from content_bot.media import transcription


class ThreadsTests(unittest.TestCase):
    def test_detects_threads_post_urls(self):
        for domain in ('threads.net', 'threads.com'):
            message = SimpleNamespace(
                text='https://' + domain + '/@author/post/ABC123',
                caption=None,
                entities=None,
                caption_entities=None,
                forward_origin=None,
                photo=None,
                video=None,
                audio=None,
                voice=None,
                document=None,
            )
            self.assertEqual(ContentType.THREADS, detect(message)['type'])

    def test_downloads_images_after_video_in_carousel(self):
        payload = {
            'post': {
                'caption': {'text': 'Threads carousel caption'},
                'user': {'username': 'author'},
                'carousel_media': [
                    {'media_type': 1, 'image_versions2': {'candidates': [{'url': 'photo-1'}]}},
                    {'media_type': 2, 'image_versions2': {'candidates': [{'url': 'video-cover'}]}},
                    {'media_type': 1, 'image_versions2': {'candidates': [{'url': 'photo-2'}]}},
                ],
            },
        }
        page = '<script type="application/json">' + transcription.json.dumps(payload) + '</script>'
        response = Mock(ok=True, text=page)
        response.raise_for_status.return_value = None
        session = Mock()
        session.headers = {}
        session.cookies = Mock()
        session.get.return_value = response

        def fake_download(url, path, instagram_context=None, request_session=None):
            with open(path, 'wb') as output:
                output.write(url.encode('ascii'))
            return True

        with tempfile.TemporaryDirectory() as tmp_dir, \
                patch.object(transcription.req, 'Session', return_value=session), \
                patch.object(transcription, '_download_image', side_effect=fake_download) as download:
            result = transcription.process_threads_url(
                'https://www.threads.net/@author/post/ABC123', tmp_dir
            )

            self.assertEqual('carousel', result['post_type'])
            self.assertEqual('author', result['author'])
            self.assertEqual('Threads carousel caption', result['description'])
            self.assertEqual(['photo-1', 'photo-2'], [
                call.args[0] for call in download.call_args_list
            ])
            self.assertEqual(2, len(result['media_files']))
            self.assertTrue(all(os.path.exists(path) for path in result['media_files']))


if __name__ == '__main__':
    unittest.main()
