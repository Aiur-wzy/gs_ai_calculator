# 原生动作到结果的完整小例

这是一个虚构角色的两秒教学场景，用来学习原生输入如何连接。它不是角色攻略，也不替代真实角色资料或数据库身份。所有教学事实标为 `assumed`；采用这些输入只证明本例已声明的条件得到执行，不证明完整角色机制、长期循环或所有潜在收益都已覆盖。

## 要表达的事实

角色90级、攻击100、精通0、暴击率0、充能100%；敌人90级、抗性10%，初始少量火附着。全部面板是教学给定值，不从真实角色或装备反推。统计截止由题目明确为2秒。

| 时点 | 发生什么 | 本例应观察到什么 |
|---|---|---|
| 0～0.1秒 | 施放E；施放占用只有0.1秒 | 后台命中继续发生，不把最后一跳作为施放结束 |
| 0.2、0.4、0.6秒 | E有三个倍率1的冰伤命中；共用普通附着计数器 | 第一跳融化，后两跳仍有伤害但不再附着 |
| 0.2秒第一跳命中之后 | 获得100%全增伤，持续0.3秒；产生两枚冰微粒，飞行0.5秒 | 触发该效果的第一跳不倒享增伤；第二跳享受，第三跳已到期 |
| 0.7秒 | 两枚冰微粒到账，角色初始能量0 | 入账6点，不能把0.2秒产球当作已收到能量 |
| 1～1.1秒 | Q需要且扣除6点能量 | 因实际到账在先，Q可执行 |
| 1.2秒 | Q产生倍率2的物理伤害 | 明确不附着，不沿用E的附着器 |

本例的有限三段用 `hit_patterns` 一次声明，生成ID为 `skill.ticks:1` 至 `skill.ticks:3`；不用三个独立动作或 `count=3` 冒充不同时点的攻击。它展示有限序列，不宣称已经展示复杂召唤物的全部生命周期。

## 完整输入

把下面整个JSON保存为 `causal-draft.json`。这是可保存、可组合的未确认输入；故意保留一条待核来源，而不是预填不可核实的指纹。真实候选的用户输入、数据库版本及权限沿[共用编辑](input-editing.md)处理，不复制本例虚构角色作为真实角色。

```json
{
  "kind": "genshin-plan",
  "plan_version": 3,
  "plan_id": "211b4b68-c3c9-4f7e-8fb1-ab2d05adf511",
  "revision": 0,
  "direct_energy_policy": "energy-direct-base-er-v1",
  "manual_template": {
    "version": 2,
    "form": "resolved",
    "id_registry_version": 1,
    "type_definitions": {
      "damage_types": []
    },
    "meta": {
      "name": "原生因果教学",
      "assumptions": [
        "所有参数均为虚构教学输入；动态执行不证明真实角色机制"
      ]
    },
    "enemy": {
      "level": 90,
      "resistance": {
        "default": 0.1
      }
    },
    "characters": [
      {
        "id": "actor",
        "name": "演示角色",
        "level": 90,
        "stats": {
          "atk": 100,
          "base_atk": 50,
          "em": 0,
          "crit_rate": 0,
          "crit_dmg": 0.5,
          "energy_recharge": 1
        }
      }
    ],
    "hits": [
      {
        "id": "tick.source",
        "t": 0.2,
        "character": "actor",
        "skill": "演示技能",
        "hit_name": "tick.source",
        "damage_type_ids": [
          "damage.elemental_skill"
        ],
        "damage_system": "classic",
        "element": "cryo",
        "multiplier": 1
      },
      {
        "id": "burst.hit",
        "t": 1.2,
        "character": "actor",
        "skill": "演示技能",
        "hit_name": "burst.hit",
        "damage_type_ids": [
          "damage.elemental_burst"
        ],
        "damage_system": "classic",
        "element": "physical",
        "multiplier": 2
      }
    ],
    "buffs": [],
    "switches": [],
    "loop_duration": 2
  },
  "equipment_inputs": {},
  "effect_bindings": [
    {
      "binding_id": "teaching",
      "provider_character_id": "actor",
      "owner": {
        "kind": "manual",
        "note": "虚构教学条件"
      },
      "source_refs": [
        {
          "kind": "manual",
          "note": "教学规则：第一跳命中后产生0.3秒100%增伤并生成两枚冰微粒，0.5秒后到账"
        }
      ],
      "source_fingerprint": null,
      "coverage": {
        "state": "unreviewed",
        "reviewed_by": null,
        "source_fingerprint": null,
        "note": null
      },
      "parts": [
        {
          "part_id": "trigger",
          "label": "命中后增伤与产球",
          "state": "resolved",
          "rule": {
            "kind": "mechanics",
            "rule_id": "hit.benefit"
          },
          "delivery": {
            "kind": "mechanics"
          },
          "conditions": [],
          "dependencies": {
            "mode": "source_only",
            "context_fingerprint": null,
            "note": "演示条件已完整声明"
          },
          "reason": null
        }
      ]
    }
  ],
  "manual_effect_dependencies": [],
  "rotation_tuning": {
    "buff_hit_matches": []
  },
  "resolution": {
    "state": "pending"
  },
  "resolved_template": null,
  "rotation_model": {
    "schema_version": 1,
    "period": 2,
    "max_cycles": 3,
    "initial_frontline": "actor",
    "characters": [
      {
        "id": "actor",
        "element": "cryo",
        "energy_recharge": 1,
        "energy_capacity": 6,
        "initial_energy": 0,
        "status": "assumed",
        "source": "虚构教学场景，不是游戏角色事实"
      }
    ],
    "abilities": [
      {
        "id": "skill",
        "character": "actor",
        "action_type_id": "action.elemental_skill",
        "cooldown": null,
        "energy": null,
        "status": "assumed",
        "source": "虚构教学场景，不是游戏角色事实"
      },
      {
        "id": "burst",
        "character": "actor",
        "action_type_id": "action.elemental_burst",
        "cooldown": null,
        "energy": {
          "required": 6,
          "amount": 6
        },
        "status": "assumed",
        "source": "虚构教学场景，不是游戏角色事实"
      }
    ],
    "actions": [
      {
        "id": "skill.cast",
        "ability_id": "skill",
        "start": 0,
        "occupation_end": 0.1,
        "order": 0,
        "repeat": {
          "mode": "once"
        },
        "status": "assumed",
        "source": "虚构教学场景，不是游戏角色事实"
      },
      {
        "id": "burst.cast",
        "ability_id": "burst",
        "start": 1,
        "occupation_end": 1.1,
        "order": 100,
        "repeat": {
          "mode": "once"
        },
        "status": "assumed",
        "source": "虚构教学场景，不是游戏角色事实"
      }
    ],
    "events": [
      {
        "id": "burst.spend",
        "type": "energy_spend",
        "t": 1,
        "order": 101,
        "action_id": "burst.cast",
        "character": "actor",
        "required": 6,
        "amount": 6,
        "repeat": {
          "mode": "once"
        },
        "status": "assumed",
        "source": "虚构教学场景，不是游戏角色事实"
      }
    ],
    "initial_cooldowns": [],
    "damage_report": {
      "contract": "damage-report-window-v1",
      "mode": "explicit",
      "end": 2
    }
  },
  "mechanics_model": {
    "schema_version": 1,
    "rules": [
      {
        "id": "hit.benefit",
        "name_zh": "首次命中后短时增伤和产球",
        "owner": "actor",
        "source_id": "teaching:trigger",
        "trigger": {
          "event": "hit",
          "phase": "post",
          "event_ids": [
            "skill.ticks:1"
          ]
        },
        "beneficiaries": {
          "mode": "team",
          "scope": "locked"
        },
        "timing": {
          "delay": 0,
          "duration": 0.3,
          "cooldown": 0,
          "cooldown_anchor": "success"
        },
        "stacking": {
          "max": 1,
          "add": 1,
          "policy": "refresh"
        },
        "effects": [
          {
            "id": "bonus",
            "benefit_type_id": "benefit.dmg_bonus.all",
            "value": 1,
            "domain": "self"
          }
        ],
        "resources": [
          {
            "id": "particles",
            "kind": "particles",
            "delay": 0,
            "travel_time": 0.5,
            "count": 2,
            "particle_type": "particle",
            "element": "cryo",
            "source_part": "trigger"
          }
        ],
        "status": "assumed",
        "source": "虚构教学场景，不是游戏角色事实"
      }
    ],
    "hit_events": [
      {
        "hit_id": "burst.hit",
        "action_id": "burst.cast",
        "order": 110,
        "application": {
          "profile_id": "physical.none",
          "target_id": "enemy",
          "landed": true
        }
      }
    ],
    "hit_patterns": [
      {
        "id": "skill.ticks",
        "prototype_hit_id": "tick.source",
        "action_id": "skill.cast",
        "offsets": [
          0.2,
          0.4,
          0.6
        ],
        "orders": [
          10,
          20,
          30
        ],
        "application": {
          "profile_id": "cryo.skill",
          "target_id": "enemy",
          "landed": true
        }
      }
    ],
    "condition_events": [],
    "initial_state": {
      "rules": [
        {
          "rule_id": "hit.benefit",
          "cooldown_remaining": 0,
          "instances": []
        }
      ]
    },
    "resource_ownership": [
      {
        "rule_id": "hit.benefit",
        "action_id": "skill.cast",
        "source_part": "trigger",
        "replaces_event_ids": []
      }
    ],
    "elemental_model": {
      "schema_version": 1,
      "ruleset": "gauge-multi-aura-v4",
      "target": {
        "id": "enemy",
        "kind": "ordinary",
        "knowledge": "known",
        "initial_auras": [
          {
            "element": "pyro",
            "remaining_gu": 0.4,
            "decay_gu_per_second": 0.001,
            "source": "演示初始火底"
          }
        ],
        "freeze": {
          "freezable": true,
          "resistance": 0,
          "status": "assumed",
          "source": "虚构教学场景，不是游戏角色事实"
        },
        "status": "assumed",
        "source": "虚构教学场景，不是游戏角色事实"
      },
      "profiles": [
        {
          "id": "cryo.skill",
          "element": "cryo",
          "application": "apply",
          "gauge_units": 1,
          "icd": {
            "kind": "standard",
            "counter_tag": "skill.counter",
            "rule_group": "skill.standard"
          },
          "status": "assumed",
          "source": "虚构教学场景，不是游戏角色事实"
        },
        {
          "id": "physical.none",
          "element": "physical",
          "application": "none",
          "status": "assumed",
          "source": "虚构教学场景，不是游戏角色事实"
        }
      ],
      "swirl_damage": {
        "icd": {
          "kind": "sequence",
          "reset_seconds": 0.5,
          "sequence": [
            1,
            1,
            0
          ]
        },
        "status": "assumed",
        "source": "虚构教学场景，不是游戏角色事实"
      }
    }
  }
}
```

## 组合、核对和计算

以下命令接在本次真实CLI入口后，使用兼容的活动数据库目录；输入输出请换成自己的绝对路径。原生Agent也可以直接经App工具读写同一候选，无须先运行脚本或经过指定工作重点。

```text
equipment compose --plan causal-draft.json --output causal-composed.json
```

命令写出的 `causal-composed.json` 是组合后的方案；标准输出的 `data` 才是完整组合报告，请另外保存完整stdout为 `compose-response.json`。这一步预期 `state=pending`：`effect_bindings.teaching.coverage` 尚未核对。待核不等于没有这个表达接口。

核对报告中 `effect_requirements` 的 `binding_id=teaching`，确认本例所列的触发、数值、受益者、时长、产球与到账事实完全一致。本例只有这一条手工教学来源。随后把该项当前 `source_fingerprint` 填入本条绑定及其 `coverage.source_fingerprint`，覆盖状态改为 `complete` 并写明核对人和依据。**这不是用户批准，也不审核数据库事实；真实任务不能批量把未读项标为complete。**

下面的示例脚本只适用于已经逐项核对过的这份教学输入。它不读取源码、不调用内部App API，不自动批准真实来源。与公开 `equipment compose` 一起使用：

```python
import json
from pathlib import Path

plan = json.loads(Path("causal-composed.json").read_text(encoding="utf-8-sig"))
reply = json.loads(Path("compose-response.json").read_text(encoding="utf-8-sig"))
assert reply["code"] == 0
requirements = [r for r in reply["data"]["effect_requirements"]
                if r["binding_id"] == "teaching"]
assert len(requirements) == 1
bindings = [b for b in plan["effect_bindings"] if b["binding_id"] == "teaching"]
assert len(bindings) == 1 and len(plan["effect_bindings"]) == 1
binding = bindings[0]
binding["source_fingerprint"] = requirements[0]["source_fingerprint"]
binding["coverage"] = {
    "state": "complete", "reviewed_by": "教学场景作者",
    "note": "已逐项核对上述虚构条件，不授予真实游戏事实审核",
    "source_fingerprint": requirements[0]["source_fingerprint"],
}
plan["revision"] += 1
Path("causal-reviewed.json").write_text(
    json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
```

继续调用：

```text
equipment calc --plan causal-reviewed.json --output causal-result.json --energy-intent single_window_legal --summary
```

原生候选中的等价路径是：导入草稿或用已发布的候选编辑工具写入 → `gs_compose` → 定向读取报告 `/effect_requirements` → 仅核对这条教学来源并更新绑定 → `gs_calculate`。工具外层参数和当前修订读实际 `tool_contracts`；目录里有能力不等于当前工具具有所有权限。`single_window_legal` 说明只验证这两秒，不是无限循环结论。

## 如何知道真的执行了

下面是本例的可手算预期，独立于引擎汇总。防御乘数0.5、抗性乘数0.9，无暴击期望提升：

| 命中ID | 计算 | 期望伤害 |
|---|---|---:|
| skill.ticks:1 | 100×1×0.5×0.9×1.5，触发后Buff不倒算 | 67.5 |
| skill.ticks:2 | 100×1×0.5×0.9×2，有Buff但无附着 | 90 |
| skill.ticks:3 | 100×1×0.5×0.9，Buff已到期 | 45 |
| burst.hit | 100×2×0.5×0.9，物理伤害无附着 | 90 |

合计292.5，按明确2秒窗口得到DPS146.25。四个实际命中、对应附着结果、0.7秒入账和1秒扣能应同时成立，不能只看合计刚好一样。

已有 `causal-result.json` 时，用 `equipment review --calculated causal-result.json --request review.json`，或原生 `gs_review_result` 传相同请求体，分别查看：

```json
{"view":"mechanics","filters":{"action_id":"skill.cast"},"limit":20}
```

```json
{"view":"actions","limit":20}
```

```json
{"view":"damage_explanations","filters":{"event_id":"skill.ticks:2"},"sections":["identity","outcome","panel","modifiers","formula"],"limit":5}
```

注意分页 `next_offset`，上面不是一次把所有流水读完的承诺。机制视图核对 `elemental_application` 的附着许可与反应；动作视图核到账和扣能；伤害解释核实际面板、增益和公式。来源 `complete`、组合 `ready`、本次执行被接纳、任务符合真实游戏机制是四种不同结论。

## 用小改动分清问题

- 把微粒飞行改为1秒：到账变成1.2秒，1秒Q应能量不足。不是攻击低或增伤未满导致。
- 把初始火底改成空：第一跳仍可附着，但不融化；它的伤害从67.5变成45。后两跳的ICD不因此消失。
- 把增伤持续改为0.2秒：0.4秒恰好到期，第二跳不享受该增伤。刷新或端点语义必须看规则执行，不能肉眼拉长覆盖条。
- 去掉全部产球：这份输入的Q不能执行；不能据此说真实角色或装备永远无法循环，也不能把Q删掉使任务变成成功。

修改后重新组合、核对受影响来源，再计算；不要复制旧结果或旧确认。以上反例用于隔离因果，无需付费模型、真实队伍或网络。其他类型的输入按[意图导航](authoring-guide.md)及运行时schema扩展，不把本例的虚构参数推广给角色。
