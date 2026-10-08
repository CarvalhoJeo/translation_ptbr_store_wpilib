from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from transifex.client import TransifexClient
from transifex.source import ResourceTranslationsSource
from transifex.sync import sync_all


class Command(BaseCommand):
    help = "Busca traduções e revisões novas no Transifex e credita pontos. Rode via cron a cada 30–60 min."

    def handle(self, *args, **options):
        if not settings.TRANSIFEX_API_TOKEN:
            raise CommandError("TRANSIFEX_API_TOKEN não configurado no .env.")
        client = TransifexClient(settings.TRANSIFEX_API_TOKEN)
        source = ResourceTranslationsSource(client, settings.TRANSIFEX_LANGUAGE, settings.LAUNCH_AT)
        result = sync_all(source, timezone.now(), settings.LAUNCH_AT)
        summary = (
            f"{result.resources_ok} recursos ok, {result.resources_failed} com erro, "
            f"{result.new_events} eventos novos, {result.claimed_late} eventos pendentes creditados"
        )
        if result.resources_failed:
            raise CommandError(summary + " (veja last_error no admin)")
        self.stdout.write(self.style.SUCCESS(summary))
