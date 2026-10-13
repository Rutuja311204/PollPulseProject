"""Django forms for creator pages. Business rules live in the services;
forms handle HTML widgets, type conversion and showing errors next to fields."""
from django import forms

from core.exceptions import ValidationFailed

from .permissions import COLLABORATOR_ROLES
from .survey_service import DESCRIPTION_MAX, TITLE_MAX, validate_description, validate_settings, validate_title


class StyledForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, (forms.Select, forms.RadioSelect)):
                widget.attrs.setdefault("class", "form-select")
            else:
                widget.attrs.setdefault("class", "form-control")


class SurveyDetailsForm(StyledForm):
    title = forms.CharField(max_length=TITLE_MAX, widget=forms.TextInput(attrs={"placeholder": "e.g. Course feedback – Semester 3"}))
    description = forms.CharField(required=False, max_length=DESCRIPTION_MAX,
                                  widget=forms.Textarea(attrs={"rows": 3, "placeholder": "What is this survey for? Respondents see this text."}))

    def clean_title(self):
        try:
            return validate_title(self.cleaned_data["title"])
        except ValidationFailed as exc:
            raise forms.ValidationError(exc.errors)

    def clean_description(self):
        try:
            return validate_description(self.cleaned_data.get("description"))
        except ValidationFailed as exc:
            raise forms.ValidationError(exc.errors)


class NewSurveyForm(SurveyDetailsForm):
    STARTS = [("blank", "Start from a blank survey"), ("suggest", "Start with suggested questions")]
    start = forms.ChoiceField(choices=STARTS, initial="blank", widget=forms.RadioSelect)
    topic = forms.CharField(required=False, max_length=120,
                            widget=forms.TextInput(attrs={"placeholder": "e.g. Python workshop, college canteen"}))
    objective = forms.CharField(required=False, max_length=300,
                                widget=forms.TextInput(attrs={"placeholder": "What do you want to learn? (optional)"}))

    def clean(self):
        data = super().clean()
        if data.get("start") == "suggest" and len((data.get("topic") or "").strip()) < 3:
            self.add_error("topic", "Describe the topic so we can suggest questions.")
        return data


DATETIME_WIDGET = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class SurveySettingsForm(StyledForm):
    access = forms.ChoiceField(choices=[("public", "Anyone with the link"), ("login", "Signed-in users only")])
    identity = forms.ChoiceField(choices=[("anonymous", "Anonymous — don't record who answered"),
                                          ("identified", "Identified — record the respondent's username")])
    duplicate_protection = forms.ChoiceField(choices=[
        ("session", "One response per browser (session)"),
        ("user", "One response per account (requires sign-in)"),
        ("none", "Allow multiple responses (e.g. a shared kiosk)"),
    ])
    display_mode = forms.ChoiceField(choices=[("all", "All questions on one page"), ("one_per_page", "One question at a time")])
    allow_edit = forms.BooleanField(required=False, label="Respondents can edit their response after submitting")
    allow_save_resume = forms.BooleanField(required=False, label="Respondents can save a draft and finish later")
    results_public = forms.BooleanField(required=False, label="Show aggregated results to respondents (no text answers)")
    start_at = forms.DateTimeField(required=False, widget=DATETIME_WIDGET, input_formats=["%Y-%m-%dT%H:%M"])
    end_at = forms.DateTimeField(required=False, widget=DATETIME_WIDGET, input_formats=["%Y-%m-%dT%H:%M"])
    max_responses = forms.IntegerField(required=False, min_value=1, max_value=1_000_000,
                                       widget=forms.NumberInput(attrs={"placeholder": "No limit"}))
    thank_you_message = forms.CharField(required=False, max_length=1000, widget=forms.Textarea(attrs={"rows": 3,
                                        "placeholder": "Thank you! Your response has been recorded."}))
    consent_text = forms.CharField(required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 3,
                                   "placeholder": "Optional. If set, respondents must tick 'I agree' before submitting."}))

    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        try:
            self.cleaned_settings = validate_settings(data)
        except ValidationFailed as exc:
            for error in exc.errors:
                self.add_error(None, error)
        return data


class CollaboratorForm(StyledForm):
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"placeholder": "Their PollPulse username"}))
    role = forms.ChoiceField(choices=[(r, r.capitalize()) for r in COLLABORATOR_ROLES])
