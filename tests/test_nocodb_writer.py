import unittest
from unittest.mock import Mock, patch

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


if __name__ == '__main__':
    unittest.main()
