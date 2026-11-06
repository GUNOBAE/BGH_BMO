"""Current-turn acting requests; no model, network or device side effects."""
import re


def requested_acting(message):
    """Recognize performance requests without granting permission from metadata.

    Field phrases like 'express a mad or angry' and Korean sad acting must
    not fall through to the director's neutral fallback. Quoted/reporting
    examples do not grant acting permission; action negation remains scoped.
    """
    if type(message) is not str:
        return None
    message = message[:8000].replace("\u2019", "'")
    # Preserve apostrophes in don't/I'm; remove quoted examples and code.
    message = re.sub(r'`[^`]*`|"[^"\n]*"|“[^”\n]*”|(?<!\w)\'[^\'\n]*\'(?!\w)', ' ', message)
    message = re.sub(r'(?m)^\s*>[^\n]*', ' ', message)
    action = r'(?:pretend|act|acting|imitate|express|show|display|demonstrate|be|look|sound)'
    prefix = (r'^\s*(?:(?:okay|ok|now|bmo|beemo)[,!:]?\s+){0,3}'
              r'(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+|'
              r'i\s+(?:want|would like)\s+you\s+to\s+)?(?:please\s+)?'
              r'(?:playfully\s+)?')
    clauses = re.split(r'[.!?;\n]+', message)
    choices = (
        ('crying', r'cry|crying|sob|sobbing',
         r'울(?:어\s*(?:봐|보|줘|라)|는\s*척)|우는\s*연기',
         r'울지\s*마|(?:우는|울어)[^.!?;\n]{0,35}(?:하지|보지)\s*마'),
        ('angry', r'angry|anger|mad|annoyed',
         r'화내\s*(?:봐|보|줘|라)|화난\s*(?:척|연기|표정)|장난스럽게\s*화',
         r'화내지\s*마|(?:화난|화내는|화내)[^.!?;\n]{0,35}(?:하지|보지)\s*마'),
        ('sad', r'sad|sadness|sorrowful|unhappy',
         r'슬픈\s*(?:척|연기|표정)|슬퍼하는\s*(?:척|연기)|슬프게\s*(?:말해|해|해줘)|슬퍼해\s*(?:봐|줘)',
         r'슬퍼하지\s*마|슬프지\s*마|(?:슬픈|슬퍼하는|슬프게)[^.!?;\n]{0,35}(?:하지|보지)\s*마'),
        ('happy', r'happy|joyful|excited',
         r'기뻐해\s*(?:봐|보|줘)|(?:기쁜|기뻐하는|행복한)\s*(?:척|연기)',
         r'기뻐하지\s*마|(?:기쁜|기뻐하는|기뻐해|행복한)[^.!?;\n]{0,35}(?:하지|보지)\s*마'),
    )
    for emotion, words, korean, ko_negative in choices:
        for clause in clauses:
            denied = re.search(
                r"\b(?:don't|do not|never|stop|not)\s+[^.!?;\n]{0,70}\b(?:" + words + r')\b|'
                + ko_negative, clause, re.I)
            if denied:
                continue
            english_request = re.search(prefix + action + r'\b[^.!?;\n]{0,80}\b(?:' + words + r')\b', clause, re.I)
            if emotion == 'angry':
                # "Get angry" is a direct performance request too. Keep the
                # adjective next to the verb: a broad "get ... angry" match
                # would also authorize "get me a book about angry people".
                english_request = english_request or re.search(
                    prefix + r'(?:get|become)\s+'
                    r'(?:(?:really|very|a little|a bit|more|playfully)\s+){0,2}'
                    r'(?:angry|mad|annoyed)\b'
                    r'(?=\s*(?:$|,)|\s+(?:at|with|about|for|like|now|please|and)\b)',
                    clause, re.I)
            if emotion == 'crying':
                english_request = english_request or re.search(prefix + r'(?:cry|sob)\b(?:\s+(?:for me|please)){0,2}\s*$', clause, re.I)
            if english_request or re.search(korean, clause, re.I):
                return emotion
    return None
