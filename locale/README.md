# Translations

All text in the interface is marked for translation. English is the source language.

To add a language, for example Swedish:

1. Add it to `LANGUAGES` in `config/settings.py`: `("sv", _("Swedish"))`.
2. Extract the strings (needs GNU gettext installed):

       python manage.py makemessages -l sv

3. Translate `locale/sv/LC_MESSAGES/django.po`.
4. Compile: `python manage.py compilemessages` (the Docker build does this for you).

The language follows the visitor's browser settings.
