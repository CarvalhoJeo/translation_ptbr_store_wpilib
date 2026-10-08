import pytest

pytestmark = pytest.mark.django_db


def test_home_loads_site_stylesheet(client):
    html = client.get("/").content.decode()
    assert "/static/css/site.css" in html


def test_home_explains_how_it_works_and_offers_github_login(client):
    html = client.get("/").content.decode()
    assert "Como funciona" in html
    assert "/contas/github/login/" in html


def test_login_page_uses_store_layout_not_allauth_default(client):
    html = client.get("/contas/login/").content.decode()
    assert "/static/css/site.css" in html
    assert "Loja de Traduções WPILib" in html
    assert "Menu:" not in html


def test_home_shows_current_point_rates(client):
    from ledger.models import PointRates

    rates = PointRates.current()
    rates.translated_word, rates.reviewed_word = 3, 1
    rates.save()
    html = client.get("/").content.decode()
    assert "3 pontos</strong> por palavra traduzida" in html
    assert "1 ponto</strong> por palavra revisada" in html
