import unittest
from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parent.parent / "static" / "index.html"


class VideoFormTests(unittest.TestCase):
    def test_seedance_non_human_image_confirmation_contract(self):
        html = INDEX_HTML.read_text(encoding="utf-8")

        self.assertTrue('id="v-no-human-confirm-wrap"' in html, "missing declaration wrapper")
        self.assertTrue(
            'type="checkbox" id="v-no-human-confirm"' in html,
            "missing declaration checkbox",
        )
        self.assertTrue("我确认参考图不含真人" in html, "missing declaration label")
        self.assertTrue("请先确认参考图不含真人" in html, "missing submit guard")
        self.assertFalse("请切换模型或移除图片" in html, "old blanket block remains")


if __name__ == "__main__":
    unittest.main()
