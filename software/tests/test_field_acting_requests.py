"""User field regressions: offline policy only, not live speech/face acceptance."""
import unittest
from bmo_ai.character_director import CharacterDirector, requested_acting

CASES = {
    'express a mad or angry?': 'angry',
    'Can you express a mad or angry face?': 'angry',
    'Please show me an angry face.': 'angry',
    'Show anger.': 'angry',
    'BMO, can you act mad?': 'angry',
    '장난스럽게 화내 봐': 'angry',
    '화내줘': 'angry',
    '화내라': 'angry',
    '화난 표정 보여줘': 'angry',
    '슬픈척 해봐': 'sad',
    '슬픈 척 해줘': 'sad',
    'Pretend to be sad.': 'sad',
    'Show me a sad expression.': 'sad',
    '슬프게 말해줘': 'sad',
    '우는 연기를 해봐': 'crying',
    'Please pretend to cry for this scene.': 'crying',
    'Cry for me please.': 'crying',
    'Cry!': 'crying',
    '울어줘': 'crying',
    'Please act angry for a silly scene.': 'angry',
    'Look playfully angry.': 'angry',
    'Playfully act angry.': 'angry',
    '기뻐해 봐': 'happy',
    'Please pretend to be happy.': 'happy',
    "I'm not sad; please pretend to cry.": 'crying',
    "I'm not sad; do not pretend to cry.": None,
    'Please stop crying.': None,
    "Please don't act angry.": None,
    "Please don’t express anger.": None,
    'Do not pretend to be happy.': None,
    '우는 연기를 하지 마': None,
    '울어 보지 마': None,
    '기뻐해 보지 마': None,
    'Never imitate a crying baby.': None,
    '슬픈 척 하지 마': None,
    "Don't show me a sad face.": None,
    '오늘 힘들었어': None,
    'I feel angry.': None,
    'Why are you angry?': None,
    'Explain how actors express anger.': None,
    "What does 'act angry' mean?": None,
    'Translate "Please act angry" into Korean.': None,
    '> Please act angry.': None,
    'My friend can act angry.': None,
    'Please explain sadness.': None,
    'Tell me a fact.': None,
}

class FieldActingRequests(unittest.TestCase):
    def test_field_and_preserved_request_cases(self):
        for message, expected in CASES.items():
            with self.subTest(message=message):
                self.assertEqual(requested_acting(message), expected)

    def test_nontext(self):
        for value in (None, 5, [], {}):
            self.assertIsNone(requested_acting(value))

    def test_reported_phrases_do_not_reset_model_acting_to_neutral(self):
        for message, emotion, delivery in (
            ('express a mad or angry?', 'angry', 'theatrical_angry'),
            ('슬픈척 해봐', 'sad', 'soft_sad'),
            ('Pretend to be sad.', 'sad', 'soft_sad'),
            ('울어줘', 'crying', 'theatrical_crying'),
        ):
            with self.subTest(message=message):
                plan = CharacterDirector().resolve('neutral',
                    metadata={'source': 'acting', 'delivery_id': 'calm'}, message=message)
                self.assertEqual((plan.face_emotion, plan.delivery_id), (emotion, delivery))
                self.assertEqual((plan.explicit_acting, plan.source), (True, 'acting'))
                self.assertEqual(plan.gesture_id, 'none')

    def test_sad_acting_is_not_crying_or_sobbing(self):
        plan = CharacterDirector().resolve('crying', 'sob',
            metadata={'source': 'acting'}, message='슬픈 척 해줘')
        self.assertEqual((plan.face_emotion, plan.reaction_id), ('sad', 'none'))

    def test_real_distress_still_has_priority(self):
        plan = CharacterDirector().resolve('crying', 'sob',
            message='Please act crying, my father died.', metadata={'source': 'acting'})
        self.assertEqual((plan.face_emotion, plan.delivery_id, plan.reaction_id),
                         ('sad', 'empathetic', 'none'))

    def test_model_acting_metadata_alone_still_does_not_grant_request(self):
        plan = CharacterDirector().resolve('angry',
            metadata={'source': 'acting', 'delivery_id': 'theatrical_angry'},
            message='What is two plus two?')
        self.assertEqual((plan.face_emotion, plan.delivery_id), ('neutral', 'calm'))

if __name__ == '__main__':
    unittest.main()
