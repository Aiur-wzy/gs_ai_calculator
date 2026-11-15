"""Synthetic complete source-family fixture; production profile remains unchanged."""


def runtime_files():
    files = {
        "BinOutput/Common/ConfigGlobalCombat.json": {
            "opaque": [False, 0, None, "Fixture_Ability"],
            "looks_executable": {"$type": "AddAbility", "abilityName": "Fixture_Ability"},
        },
        "BinOutput/Talent/EquipTalents/Fixture.json": {"Fixture_Effect": [
            {"$type": "AddAbility", "abilityName": "Fixture_Ability", "unknown_preserved": False},
            {"$type": "ModifyAbility", "abilityName": "Fixture_Ability", "paramSpecial": "Rate", "paramDelta": "%1"},
        ]},
        "BinOutput/Ability/Temp/EquipAbilities/Fixture.json": [{"Default": {
            "abilityName": "Fixture_Ability", "abilitySpecials": {"Rate": 0},
            "onAdded": [{"$type": "ApplyModifier", "modifierName": "Fixture_Modifier"}],
            "modifiers": {"Fixture_Modifier": {"duration": 2, "properties": {"Actor_AttackRatio": "Rate"}, "unknown_preserved": [0, False, None]}},
        }}],
        "BinOutput/Avatar/Fixture.json": {},
        "BinOutput/Gadget/Fixture.json": {},
        "BinOutput/AnimatorConfig/Fixture.json": {},
        "BinOutput/AbilityPath/AbilityPathData.json": {},
        "BinOutput/AnimPattern/AnimPatternData.json": {},
        "ExcelBinOutput/GadgetExcelConfigData.json": [],
        "ExcelBinOutput/MaterialExcelConfigData.json": [],
    }
    files.update({f"ExcelBinOutput/{name}ExcelConfigData.json": [] for name in
                  ("AttackAttenuation", "ElementState", "ElementCoeff", "ReactionEnergy")})
    return files
