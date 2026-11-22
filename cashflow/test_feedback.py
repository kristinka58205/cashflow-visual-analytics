from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Feedback


class FeedbackTests(TestCase):
    def test_public_form_validates_and_limits_repeats(self):
        self.client.post('/feedback/', {'subject':'','message':'x'})
        self.assertEqual(Feedback.objects.count(), 0)
        self.assertEqual(self.client.post('/feedback/', {'subject':'Ошибка','message':'Подробности','author':999}).status_code, 302)
        self.assertIsNone(Feedback.objects.get().author)
        self.client.post('/feedback/', {'subject':'Ещё','message':'Подробности'})
        self.assertEqual(Feedback.objects.count(), 1)

    def test_queue_is_staff_only_and_escapes_messages(self):
        user = get_user_model().objects.create_user('reviewer')
        self.client.force_login(user)
        self.client.post('/feedback/', {'subject':'Ошибка','message':'<script>bad()</script>'})
        item = Feedback.objects.get()
        self.assertEqual(item.author, user)
        self.assertEqual(self.client.get('/cabinet/feedback/').status_code, 403)
        user.is_staff = True; user.save()
        self.assertContains(self.client.get('/cabinet/feedback/'), '&lt;script&gt;')
        self.assertEqual(self.client.post('/cabinet/feedback/', {'id':item.pk,'status':'wrong'}).status_code,400)
        self.assertEqual(self.client.post('/cabinet/feedback/', {'id':'bad','status':'closed'}).status_code,400)
        self.client.post('/cabinet/feedback/', {'id':item.pk,'status':'closed'})
        item.refresh_from_db(); self.assertEqual(item.status,'closed')
