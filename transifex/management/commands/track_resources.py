from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from transifex.client import TransifexClient
from transifex.sync import track_project_resources


class Command(BaseCommand):
    help = "Cadastra todos os recursos de um projeto do Transifex como monitorados (não altera os existentes)."

    def add_arguments(self, parser):
        parser.add_argument("--project", default=settings.TRANSIFEX_PROJECT)

    def handle(self, *args, project, **options):
        if not settings.TRANSIFEX_API_TOKEN:
            raise CommandError("TRANSIFEX_API_TOKEN não configurado no .env.")
        client = TransifexClient(settings.TRANSIFEX_API_TOKEN)
        added = track_project_resources(client, project)
        self.stdout.write(self.style.SUCCESS(f"{added} recursos novos cadastrados."))
