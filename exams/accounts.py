from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(label="Email", max_length=150,
                                widget=forms.EmailInput(attrs={"autocomplete": "username"}))

    def clean_username(self):
        return self.cleaned_data["username"].strip().lower()


class StudentLoginView(LoginView):
    template_name = "login.html"
    authentication_form = EmailAuthenticationForm

    def get_success_url(self):
        profile = getattr(self.request.user, "student_profile", None)
        if profile:
            if profile.password_change_required:
                return reverse("password_change")
            return self.get_redirect_url() or reverse("enter")
        return self.get_redirect_url() or reverse("admin:index" if self.request.user.is_staff else "enter")


class StudentPasswordChangeView(PasswordChangeView):
    template_name = "password_change.html"
    success_url = reverse_lazy("enter")

    def form_valid(self, form):
        response = super().form_valid(form)
        profile = getattr(self.request.user, "student_profile", None)
        if profile and profile.password_change_required:
            profile.password_change_required = False
            profile.save(update_fields=["password_change_required"])
        return response


class RequireStudentPasswordChange:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            profile = getattr(request.user, "student_profile", None)
            if profile and profile.password_change_required and request.path not in (
                reverse("password_change"), reverse("logout"), reverse("healthz"),
            ) and not request.path.startswith("/static/"):
                return redirect("password_change")
        return self.get_response(request)
