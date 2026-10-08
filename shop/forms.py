import re
import uuid

from django import forms

from .models import Redemption

UF_CHOICES = [("", "—")] + [
    (uf, uf)
    for uf in "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
]
CEP_RE = re.compile(r"^(\d{5})-?(\d{3})$")


class VariantChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.name


class RedemptionForm(forms.Form):
    request_token = forms.UUIDField(widget=forms.HiddenInput)
    variant = VariantChoiceField(queryset=None, label="Tamanho / variação", empty_label=None, widget=forms.RadioSelect)
    delivery = forms.ChoiceField(label="Entrega", widget=forms.RadioSelect)
    full_name = forms.CharField(label="Nome completo", max_length=120, required=False)
    cep = forms.CharField(label="CEP", max_length=9, required=False)
    street = forms.CharField(label="Rua", max_length=200, required=False)
    number = forms.CharField(label="Número", max_length=20, required=False)
    complement = forms.CharField(label="Complemento", max_length=100, required=False)
    district = forms.CharField(label="Bairro", max_length=100, required=False)
    city = forms.CharField(label="Cidade", max_length=100, required=False)
    uf = forms.ChoiceField(label="UF", choices=UF_CHOICES, required=False)
    phone = forms.CharField(label="Telefone (com DDD)", max_length=20, required=False)

    def __init__(self, product, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product
        variants = product.variants.filter(active=True, stock__gt=0)
        self.fields["variant"].queryset = variants
        if len(variants) == 1:
            self.fields["variant"].initial = variants[0].pk
        self.fields["delivery"].choices = product.delivery_choices()
        if len(self.fields["delivery"].choices) == 1:
            self.fields["delivery"].initial = self.fields["delivery"].choices[0][0]
        if not self.is_bound:
            self.fields["request_token"].initial = uuid.uuid4()

    def clean_cep(self):
        value = self.cleaned_data["cep"].strip()
        if not value:
            return ""
        match = CEP_RE.match(value)
        if not match:
            raise forms.ValidationError("CEP inválido (use 00000-000).")
        return f"{match.group(1)}-{match.group(2)}"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("delivery") == Redemption.Delivery.MAIL:
            for name in Redemption.REQUIRED_ADDRESS_FIELDS:
                if not cleaned.get(name) and name not in self.errors:
                    self.add_error(name, "Obrigatório para envio pelos Correios.")
            if any(name in self.errors for name in Redemption.ADDRESS_FIELDS):
                self.add_error(None, "Confira o endereço de envio.")
        else:
            # Pickup: the address is irrelevant, so junk in hidden fields must not block the submit.
            for name in Redemption.ADDRESS_FIELDS:
                self.errors.pop(name, None)
                cleaned.pop(name, None)
        return cleaned

    def address(self) -> dict:
        return {name: self.cleaned_data.get(name, "") for name in Redemption.ADDRESS_FIELDS}
