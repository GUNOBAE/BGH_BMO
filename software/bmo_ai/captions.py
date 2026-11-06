"""Private utterance captions; no translation service, history or logging."""
import re

MODES = ('off', 'en', 'ko')


def clean_caption(value):
    if (type(value) is not str or not value.strip() or len(value) > 600
            or any(ord(c) < 32 and c not in '\n\t' for c in value)
            or any(marker in value for marker in ('<', '>', '[', ']', '```', '{"'))):
        return ''
    return ' '.join(value.split())


def caption_pair(speech, translations=None):
    """Reuse the actual clean speech in its language; missing translation hides."""
    translations = translations if type(translations) is dict else {}
    result = {lang: clean_caption(translations.get(lang)) for lang in ('en', 'ko')}
    hangul = type(speech) is str and re.search('[가-힣]', speech)
    latin = type(speech) is str and re.search('[a-zA-Z]', speech)
    if hangul and latin:
        return result  # Mixed names/scripts do not identify a translation language.
    native = 'ko' if hangul else 'en'
    result[native] = clean_caption(speech)
    return result
