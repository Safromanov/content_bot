import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from content_bot.detector import ContentType, detect
from content_bot.media import transcription


class ThreadsTests(unittest.TestCase):
    def test_detects_threads_post_urls(self):
        urls = (
            'https://threads.net/@author/post/ABC123',
            'https://threads.com/@author/post/ABC123',
            'https://threads.com/t/ABC123',
            'https://threads.com/share/_yCJG6t9U/',
        )
        for url in urls:
            message = SimpleNamespace(
                text=url,
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

    def test_share_url_uses_public_metadata_and_desktop_headers(self):
        page = (
            '<meta property="og:title" content="Author Name (@author) in Threads">'
            '<meta property="og:description" content="Public post text">'
            '<meta property="og:image" content="https://cdn.example/post.jpg">'
        )
        response = Mock(text=page)
        response.raise_for_status.return_value = None
        session = Mock()
        session.headers = {}
        session.get.return_value = response

        def fake_download(url, path, instagram_context=None, request_session=None):
            with open(path, 'wb') as output:
                output.write(b'image')
            return True

        with tempfile.TemporaryDirectory() as tmp_dir, \
                patch.object(transcription.req, 'Session', return_value=session), \
                patch.object(transcription, '_download_image', side_effect=fake_download):
            result = transcription.process_threads_url(
                'https://www.threads.com/share/ABC123/', tmp_dir
            )

            self.assertIn('Windows NT', session.headers['User-Agent'])
            self.assertEqual('Author Name (@author) in Threads', result['title'])
            self.assertEqual('author', result['author'])
            self.assertEqual('Public post text', result['description'])
            self.assertEqual('photo', result['post_type'])
            self.assertEqual(1, len(result['media_files']))


if __name__ == '__main__':
    unittest.main()
