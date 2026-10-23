from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render

from ..forms import RateLimitedLoginForm, RegistrationForm
from ..services import audit


class SignInView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = RateLimitedLoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        audit.record(self.request.user, "auth.login", "Signed in", request=self.request)
        return response


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        audit.record(user, "auth.registered", "Account created", request=request)
        messages.success(request, "Welcome! Your account is ready.")
        return redirect("dashboard")
    return render(request, "accounts/register.html", {"form": form})
