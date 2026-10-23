"""Public pages. Nothing here receives a model instance of Item — only
PublicItem objects built from an explicit field allow-list."""
from django.core.paginator import Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from ..models import Item, Location
from ..services import public


@require_GET
def home(request):
    recent = public.to_public_items(public.board_queryset({})[:6])
    stats = {
        "open_found": Item.objects.found().publicly_listed().count(),
        "returned": Item.objects.found().filter(status=Item.Status.RETURNED).count(),
    }
    return render(request, "public/home.html", {"recent": recent, "stats": stats})


def _board_page(request):
    params = request.GET
    paginator = Paginator(public.board_queryset(params), 12)
    page = paginator.get_page(params.get("page"))
    page.object_list = public.to_public_items(page.object_list)
    return page


@require_GET
def board(request):
    page = _board_page(request)
    query = request.GET.copy()
    query.pop("page", None)
    context = {"page": page, "items": page.object_list, "params": request.GET, "querystring": query.urlencode(),
               "search_backend": public.search_backend(), **public.filter_options()}
    if request.headers.get("x-requested-with") == "fetch":
        return render(request, "public/_board_results.html", context)
    return render(request, "public/board.html", context)


@require_GET
def board_json(request):
    """Public JSON endpoint (GET /api/board/). Uses the same redacted DTO."""
    page = _board_page(request)
    return JsonResponse({
        "results": [i.as_json() for i in page.object_list],
        "page": page.number, "pages": page.paginator.num_pages, "count": page.paginator.count,
    })


@require_GET
def item_detail(request, pk):
    item = public.public_item(pk)
    if item is None:
        raise Http404
    can_claim, reason = False, ""
    if item.is_found and request.user.is_authenticated:
        from ..services.claims import can_claim as check
        can_claim, reason = check(Item.objects.get(pk=pk), request.user)
    return render(request, "public/item_detail.html", {"item": item, "can_claim": can_claim, "claim_reason": reason})


@require_GET
def locations_json(request):
    """Active campus locations for the map picker (public, non-sensitive)."""
    rows = [{"id": loc.pk, "name": loc.full_path, "lat": c[0], "lng": c[1]}
            for loc in Location.objects.filter(is_active=True) if (c := loc.coordinates)]
    return JsonResponse({"locations": rows})
