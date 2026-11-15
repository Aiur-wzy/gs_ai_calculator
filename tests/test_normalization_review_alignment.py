from scripts.normalization_review_alignment import body_signature, unique_array_pairs


def test_same_body_different_types_reordered_has_no_identity_proof():
    old = [{"$type": "ApplyModifier", "modifierName": "same"},
           {"$type": "RemoveModifier", "modifierName": "same"}]
    new = [{"$type": "ENCODEDTWO", "NEWFIELD": "same"},
           {"$type": "ENCODEDONE", "NEWFIELD": "same"}]
    assert body_signature(old[0]) == body_signature(old[1])
    assert unique_array_pairs(old, new) == ()


def test_unique_stable_values_match_even_after_reordering_and_type_encoding():
    old = [{"$type": "ApplyModifier", "modifierName": "a"},
           {"$type": "RemoveModifier", "modifierName": "b"}]
    new = [{"$type": "ENCODEDTWO", "NEWFIELD": "b"},
           {"$type": "ENCODEDONE", "NEWFIELD": "a"}]
    assert unique_array_pairs(old, new) == ((0, 1), (1, 0))


def test_repeated_scalars_are_not_paired_by_index_or_with_a_single_new_sibling():
    assert unique_array_pairs([True, True, False, -1, -1], [True, False, -1]) == ((2, 1),)
    assert body_signature(True) != body_signature(1)
