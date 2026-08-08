import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from config import cfg
import text_utils


class MultiImageVisionTests(unittest.TestCase):
    def test_sends_all_images_in_one_gemini_request(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = []
            for index in range(3):
                path = Path(tmp_dir) / ('image_' + str(index) + '.jpg')
                path.write_bytes(b'image-' + str(index).encode('ascii'))
                paths.append(str(path))

            response = SimpleNamespace(text='Общее описание карусели')
            models = Mock()
            models.generate_content.return_value = response
            client = SimpleNamespace(models=models)

            with patch.object(cfg, 'GEMINI_API_KEY', 'test-key'), \
                    patch('google.genai.Client', return_value=client):
                result = text_utils.analyze_images(paths)

            self.assertEqual('Общее описание карусели', result)
            models.generate_content.assert_called_once()
            contents = models.generate_content.call_args.kwargs['contents']
            self.assertEqual(4, len(contents))  # prompt + 3 images


if __name__ == '__main__':
    unittest.main()
