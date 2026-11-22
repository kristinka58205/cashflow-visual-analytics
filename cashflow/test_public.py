from django.test import TestCase
from .public import GUIDES


class PublicGuideTests(TestCase):
    def test_guides_are_public_and_keep_shared_navigation(self):
        for slug, title, first, second in GUIDES:
            response = self.client.get(f"/guide/{slug}/")
            self.assertContains(response, title)
            self.assertContains(response, first)
            self.assertContains(response, second)
            self.assertContains(response, 'aria-label="Хлебные крошки"')
            for target, *_ in GUIDES:
                self.assertContains(response, f'/guide/{target}/')
        self.assertEqual(self.client.get('/guide/unknown/').status_code, 404)
