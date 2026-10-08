import pytest


@pytest.mark.django_db
def test_home_renders_store_name(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Loja de Traduções WPILib" in response.content.decode()
