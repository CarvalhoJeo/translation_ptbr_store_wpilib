from django import forms


class LinkForm(forms.Form):
    transifex_username = forms.CharField(
        label="Seu usuário no Transifex",
        max_length=200,
        help_text="Ex.: joaosilva (o nome que aparece no seu perfil do Transifex).",
    )


class EmailForm(forms.Form):
    email = forms.EmailField(label="E-mail para avisos dos resgates", max_length=254)
