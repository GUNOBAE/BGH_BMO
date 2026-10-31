"""User field regressions: offline policy only, not live speech/face acceptance."""
import unittest
from bmo_ai.character_director import CharacterDirector, requested_acting
from bmo_ai.expressive_voice import ExpressivePolicy

CASES = {
    'express a mad or angry?': 'angry',
    'Can you express a mad or angry face?': 'angry',
    'Please show me an angry face.': 'angry',
    'Show anger.': 'angry',
    'BMO, can you act mad?': 'angry',
    'get angry': 'angry',
    'Get mad.': 'angry',
    'Be angry.': 'angry',
    'Become angry.': 'angry',
    'Can you get angry?': 'angry',
    'BMO, please get angry.': 'angry',
    'Please get really angry for a silly scene.': 'angry',
    'Could you get a little mad at me?': 'angry',
    'I want you to get angry.': 'angry',
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
    "Don't get angry.": None,
    'Please do not become angry.': None,
    'Please never get really mad at me.': None,
    'Stop getting angry.': None,
    'Do not pretend to be happy.': None,
    '우는 연기를 하지 마': None,
    '울어 보지 마': None,
    '기뻐해 보지 마': None,
    'Never imitate a crying baby.': None,
    '슬픈 척 하지 마': None,
    "Don't show me a sad face.": None,
    '오늘 힘들었어': None,
    'I feel angry.': None,
    'I get angry.': None,
    'I become angry when people are rude.': None,
    'How do you get angry?': None,
    'Why do people get mad?': None,
    'My friend told me to get angry.': None,
    'Get me a book about angry people.': None,
    'Get Angry Birds on the screen.': None,
    'Get really angry-looking stickers for me.': None,
    'Translate "get angry" into Korean.': None,
    'What does `get mad` mean?': None,
    '> Get angry.': None,
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
            ('Get angry.', 'angry', 'theatrical_angry'),
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

    def test_get_angry_keeps_angry_delivery_instead_of_calm_fallback(self):
        # This matches the failure: the model's acting label used to be denied
        # even when the user plainly said "get angry", collapsing face AND TTS.
        for model_emotion in ('neutral', 'angry'):
            with self.subTest(model_emotion=model_emotion):
                plan = CharacterDirector().resolve(model_emotion,
                    metadata={'source': 'acting', 'delivery_id': 'theatrical_angry'},
                    message='Get angry.')
                voice = ExpressivePolicy().plan('Grr. That is my last cookie!',
                    {'emotion': plan.face_emotion, 'emotion_validation': 'validated',
                     'character_plan': plan.public()}, model='s2.1-pro-free')
                self.assertEqual((plan.face_emotion, plan.delivery_id),
                                 ('angry', 'theatrical_angry'))
                self.assertEqual(voice.emotion, 'angry')
                self.assertEqual(voice.mechanism, 'fish_character_s2_v1')
                self.assertTrue(voice.override_soft)
                self.assertTrue(voice.explicit_acting)
                self.assertIn('[angry,', voice.text)
                self.assertNotIn('[calm,', voice.text)

    def test_get_angry_does_not_override_real_distress(self):
        plan = CharacterDirector().resolve('angry',
            metadata={'source': 'acting'},
            message='Get angry for me, my father died.')
        self.assertEqual((plan.face_emotion, plan.delivery_id), ('sad', 'empathetic'))
        self.assertFalse(plan.explicit_acting)

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
