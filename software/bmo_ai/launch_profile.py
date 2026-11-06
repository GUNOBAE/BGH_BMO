"""Pure launch defaults shared by module, shell and generated candidate icons.

Character Life includes the reviewed bundled games. This grants no microphone,
camera, arm, wake or music permission; those retain their explicit opt-ins.
"""


def playable_games_enabled(requested, *, character_life=False):
    """None selects the product profile; explicit False always stays disabled."""
    if requested is not None and type(requested) is not bool:
        raise ValueError('invalid_playable_games_option')
    if type(character_life) is not bool:
        raise ValueError('invalid_character_life_option')
    return character_life if requested is None else requested
