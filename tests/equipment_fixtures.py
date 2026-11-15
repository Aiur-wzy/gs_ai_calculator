"""Small, internally consistent 11-file equipment snapshot for offline tests."""


def equipment_files():
    sources = {
        "Weapon": [{"id": 11401, "nameTextMapHash": 100, "descTextMapHash": 101,
                    "rankLevel": 4, "weaponType": "WEAPON_SWORD_ONE_HAND", "weaponPromoteId": 1,
                    "skillAffix": [12, 0], "weaponProp": [
                        {"propType": "FIGHT_PROP_BASE_ATTACK", "type": "ATK", "initValue": 40},
                        {"propType": "FIGHT_PROP_CHARGE_EFFICIENCY", "type": "ER", "initValue": 0.1}]}],
        "WeaponCurve": [{"level": i, "curveInfos": [
            {"type": "ATK", "arith": "ARITH_MULTI", "value": i / 10 + 1},
            {"type": "ER", "arith": "ARITH_MULTI", "value": 2}]} for i in range(1, 41)],
        "WeaponPromote": [
            {"weaponPromoteId": 1, "unlockMaxLevel": 20, "addProps": [{"propType": "FIGHT_PROP_BASE_ATTACK"}]},
            {"weaponPromoteId": 1, "promoteLevel": 1, "unlockMaxLevel": 40,
             "addProps": [{"propType": "FIGHT_PROP_BASE_ATTACK", "value": 25}, {}]}],
        "EquipAffix": [{"id": 12, "affixId": 917, "nameTextMapHash": 100, "descTextMapHash": 201,
                        "paramList": [0.2, 0, 2, 0], "addProps": [{}, {"propType": "FIGHT_PROP_HP_PERCENT", "value": 0.2}, {}]}],
        "WeaponCodex": [{"Id": 31, "weaponId": 11401}],
        "ReliquarySet": [{"setId": 15, "EquipAffixId": 12, "setNeedNum": [2], "containsList": [71543]}],
        "Reliquary": [{"id": 71543, "setId": 15, "nameTextMapHash": 100, "descTextMapHash": 101,
                       "equipType": "EQUIP_BRACER", "rankLevel": 5, "mainPropDepotId": 4000,
                       "appendPropDepotId": 501, "maxLevel": 21, "addPropLevels": [5, 9, 13, 17, 21, 25]}],
        "ReliquaryCodex": [{"Id": 41, "suitId": 15, "level": 5, "flowerId": 71543}],
        "ReliquaryMainProp": [
            {"id": 14001, "propDepotId": 4000, "propType": "FIGHT_PROP_HP", "affixName": "hp"},
            {"id": 14002, "propDepotId": 4000, "propType": "FIGHT_PROP_ICE_SUB_HURT", "affixName": "res"}],
        "ReliquaryAffix": [{"id": 501201, "depotId": 501, "propType": "FIGHT_PROP_CRITICAL", "group_id": 2, "propValue": 0.0272}],
        "ReliquaryLevel": [{"rank": 5, "level": i, "addProps": [{"propType": "FIGHT_PROP_HP", "value": 700 + i}]} for i in range(1, 22)],
    }
    return {f"ExcelBinOutput/{name}ExcelConfigData.json": value for name, value in sources.items()}
