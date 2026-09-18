from django.shortcuts import render


def dashboard(request):
    context = {
        "total_lost": 24,
        "total_found": 18,
        "my_reports": 5,
        "possible_matches": 3,
    }

    return render(request, "dashboard.html", context)