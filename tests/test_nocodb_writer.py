import unittest
from unittest.mock import Mock, patch

import requests
from content_bot.detector import ContentType
from content_bot.storage import nocodb as nocodb_writer


class CategoryUpdateTests(unittest.TestCase):
    @patch.object(nocodb_writer.requests, 'patch')
    def test_updates_category(self, request_patch):
        response = Mock(ok=True)
        request_patch.return_value = response

        updated = nocodb_writer.update_record_category('42', 'AI')

        self.assertTrue(updated)
        self.assertEqual({'Id': '42', 'Category': 'AI'}, request_patch.call_args.kwargs['json'])

    @patch.object(nocodb_writer.requests, 'patch')
    def test_rejects_unknown_category(self, request_patch):
        self.assertFalse(nocodb_writer.update_record_category('42', 'Unknown'))
        request_patch.assert_not_called()


class IdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.task = {
            'task_id': 'task-1',
            'chat_id': 100,
            'message_id': 200,
            'content': {
                'type': ContentType.TEXT,
                'raw_text': 'Test content',
                'url': None,
            },
        }

    def test_source_id_is_stable_per_telegram_message(self):
        first = nocodb_writer.build_source_id(self.task)
        second = nocodb_writer.build_source_id(dict(self.task, task_id='different-retry-id'))
        other = nocodb_writer.build_source_id(dict(self.task, message_id=201))

        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    @patch.object(nocodb_writer, 'post_record')
    @patch.object(nocodb_writer, 'upload_files')
    @patch.object(nocodb_writer, 'find_record_by_source_id', return_value='42')
    def test_existing_source_skips_upload_and_post(self, find_record, upload_files, post_record):
        row_id = nocodb_writer.write_record(
            self.task, 'other', {}, 'Test', '', ['/tmp/file.jpg']
        )

        self.assertEqual('42', row_id)
        find_record.assert_called_once_with(nocodb_writer.build_source_id(self.task))
        upload_files.assert_not_called()
        post_record.assert_not_called()

    @patch.object(nocodb_writer.requests, 'post')
    def test_post_http_error_propagates_to_retry_worker(self, request_post):
        response = Mock(ok=False, status_code=500, text='failed')
        response.raise_for_status.side_effect = requests.HTTPError('failed')
        request_post.return_value = response

        with self.assertRaises(requests.HTTPError):
            nocodb_writer.post_record({'Text': '', 'Category': 'other'})


if __name__ == '__main__':
    unittest.main()
