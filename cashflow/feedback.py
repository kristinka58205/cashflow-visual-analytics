from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .models import Feedback


class FeedbackForm(forms.ModelForm):
    class Meta:
        model = Feedback
        fields = ["subject", "email", "message"]


class FeedbackUpdateForm(forms.Form):
    id = forms.IntegerField(min_value=1)
    status = forms.ChoiceField(choices=Feedback._meta.get_field("status").choices)


def staff_only(user):
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        raise PermissionDenied
    return True


def feedback(request):
    form = FeedbackForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        now = timezone.now().timestamp()
        last = request.session.get("feedback_last_sent")
        if isinstance(last, (int, float)) and now - last < 60:
            form.add_error(None, "Повторная отправка доступна через минуту.")
        else:
            item = form.save(commit=False)
            item.author = request.user if request.user.is_authenticated else None
            item.save()
            request.session["feedback_last_sent"] = now
            messages.success(request, "Обращение сохранено. Оно доступно администратору сервиса.")
            return redirect("feedback")
    return render(request, "cashflow/feedback.html", {"form": form})


@user_passes_test(staff_only)
def feedback_queue(request):
    if request.method == "POST":
        change = FeedbackUpdateForm(request.POST)
        if not change.is_valid():
            return render(request, "cashflow/feedback_queue.html", {"error": "Неизвестный статус"}, status=400)
        item = get_object_or_404(Feedback, pk=change.cleaned_data["id"])
        item.status = change.cleaned_data["status"]
        item.save(update_fields=["status", "updated_at"])
        return redirect("feedback_queue")
    status = request.GET.get("status", "")
    items = Feedback.objects.select_related("author")
    if status:
        items = items.filter(status=status) if status in dict(Feedback._meta.get_field("status").choices) else items.none()
    return render(request, "cashflow/feedback_queue.html", {"page": Paginator(items, 25).get_page(request.GET.get("page")), "statuses": Feedback._meta.get_field("status").choices, "selected": status})
