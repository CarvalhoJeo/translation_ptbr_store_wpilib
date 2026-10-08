# Loja de Traduções WPILib (pt-BR)

Loja de recompensas para quem traduz a documentação da WPILib (`frc-docs`) para o português no Transifex. Traduções e revisões feitas depois do lançamento viram pontos, que podem ser trocados por brindes.

## Rodando localmente

1. Instale o [uv](https://docs.astral.sh/uv/).
2. `cp .env.example .env` e preencha:
   - `TRANSIFEX_API_TOKEN`: crie em https://app.transifex.com/user/settings/api/
   - `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`: crie um OAuth App em https://github.com/settings/developers com callback `http://localhost:8000/contas/github/login/callback/`
3. `uv sync && uv run python manage.py migrate && uv run python manage.py createsuperuser`
4. `uv run python manage.py track_resources` para cadastrar os recursos do frc-docs
5. `uv run python manage.py runserver` e acesse http://localhost:8000

## Como os pontos funcionam

- 2 pontos por palavra traduzida e 1 por palavra revisada (ajustável no admin em "Taxas de pontos").
- Cada string conta uma vez para tradução e uma vez para revisão; retraduzir não gera pontos novos.
- Só conta o que foi feito a partir de `LAUNCH_AT`.
- Quem traduziu antes de vincular a conta não perde nada: os pontos ficam guardados e são creditados quando o admin aprova o vínculo.

Nunca commite o `.env`: este repositório é público.
