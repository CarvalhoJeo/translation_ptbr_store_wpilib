from django import forms


class LinkForm(forms.Form):
    transifex_username = forms.CharField(
        label="Seu usuário no Transifex",
        max_length=200,
        help_text="Ex.: joaosilva (o nome que aparece no seu perfil do Transifex).",
    )
