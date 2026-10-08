import hmac
import logging
import time
from dataclasses import asdict

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from shop.maintenance import erase_old_addresses

from .client import TransifexClient, TransifexError
from .source import ResourceTranslationsSource
from .sync import sync_all, track_project_resources

logger = logging.getLogger(__name__)


def _is_vercel_cron(request) -> bool:
    secret = settings.CRON_SECRET
    if not secret:
        return False
    received = request.headers.get("Authorization", "")
    return hmac.compare_digest(received.encode(), f"Bearer {secret}".encode())


@require_GET
def cron_sync(request):
    """Daily Vercel Cron entry point: track new resources, then sync within the time budget."""
    if not _is_vercel_cron(request):
        return HttpResponse(status=401)
    try:
        addresses_erased = erase_old_addresses(timezone.now())
    except Exception:
        logger.exception("falha ao apagar endereços antigos")
        addresses_erased = -1
    if not settings.TRANSIFEX_API_TOKEN:
        logger.error("TRANSIFEX_API_TOKEN não configurado")
        return JsonResponse(
            {"error": "TRANSIFEX_API_TOKEN não configurado", "addresses_erased": addresses_erased}, status=500
        )

    deadline = time.monotonic() + settings.SYNC_TIME_BUDGET_SECONDS
    client = TransifexClient(settings.TRANSIFEX_API_TOKEN)
    body = {"resources_added": 0, "track_error": ""}
    try:
        body["resources_added"] = track_project_resources(client, settings.TRANSIFEX_PROJECT)
    except TransifexError as exc:
        # Still sync the resources we already know about.
        logger.exception("falha ao listar recursos do Transifex")
        body["track_error"] = str(exc)[:500]

    source = ResourceTranslationsSource(client, settings.TRANSIFEX_LANGUAGE, settings.LAUNCH_AT)
    result = sync_all(source, timezone.now(), settings.LAUNCH_AT, deadline=deadline)
    body.update(asdict(result))
    body["addresses_erased"] = addresses_erased
    logger.info("cron sync: %s", body)
    return JsonResponse(body)
