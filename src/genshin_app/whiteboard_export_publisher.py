"""Publish a new, non-overwriting Agent handoff bundle; never update the input board."""
from __future__ import annotations

import json
from pathlib import Path

from genshin_app.equipment.files import publish_files
from genshin_app.query_cache import protect_inputs
from genshin_app.whiteboard_identity import board_fingerprint
from genshin_app.whiteboard_schema import pending_items


def publish_whiteboard_handoff(board, plan, output_dir, db_path=None):
    output_dir = Path(output_dir).resolve()
    paths = {"board_path": output_dir / "whiteboard.json", "native_path": output_dir / "native.pending.plan.json",
             "instructions_path": output_dir / "交给Agent.md"}
    protect_inputs(paths.values(), [db_path] if db_path is not None else [])
    instruction = "\n".join([
        "# 用户白板计算交接", "", f"白板：{board['title']}", f"业务指纹：{board_fingerprint(board)}", "",
        "先读取项目Skill，再读取whiteboard.json及native.pending.plan.json。默认保留已有角色、动作、时点、同秒先后、装备与面板；用户明确要求调整时，可在授权范围内修改候选。",
        "仅检查或补全计算不代表允许重新排轴；未经授权的调整只给建议。角色/动作修改权限由Skill和用户请求约束，程序校验数据一致性。",
        "未知CD、动作占用、能耗、产球、附着、伤害和Buff需要查询资料；null不是0。当前mechanics_model为null且hits为空，禁止作为已完成计算。",
        "动作variant为空表示用户未指定变体，可依据资料补为适用变体并写completion_notes；已选变体默认保留，用户明确要求时可修改候选，不把空值默认当点按。",
        "capacity_intents仅是用户选择的容量模型草稿引用；它不等于accepted_fingerprint、容量数值或执行确认。context为null时保持未知；混合null与已声明场景时不得把一个意图的场景推广给其他意图。",
        "action_admission_intents仅保存用户选择的精确模型引用、动作与显式初态；handoff中的proposal没有accepted_fingerprint，也不是执行确认。只有fresh preview后才可确认。context或initial_ready_in为null时必须保持pending，不能把缺失初态当成[]。",
        "固定面板已包含套装与武器常驻属性；不分配逐件圣遗物词条。天赋默认10级，命座激活对应等级提升时13级，须核对实际适用技能。",
        "保留extensions.whiteboard_handoff。获授权修改角色或动作时，同步其中的候选board、action_map和实际原生输入。新攻击必须列入候选白板；辅助切人/等待放auxiliary_action_ids，补充与假设写completion_notes。",
        "补齐原生机制来源，先自动计算，再检查伤害、Buff、附着、能量、漏项，多轮补正；需要外部计算补充时使用专门账。",
        "原动作不可执行时保留诊断，不伪造通过；已有调整授权则修订候选，否则提出建议。对空白面板/未选装备的补齐明确记录，未知数据不造数。",
        "使用equipment deliver生成完整calculated.json，再用whiteboard result --input 对应候选白板.json --result calculated.json核验。未改白板时使用whiteboard.json；修改后另存候选白板，供用户审核后连同结果应用，不覆盖原交接文件。",
        "白板只接受完整计算包且逐项对照真实原生输入；事件轴和伤害轴只读。通过绑定检查不等于游戏机制全部正确。", "",
        "## 用户说明", "", board["notes"] or "无", "",
    ])
    outputs = [(paths["board_path"], json.dumps(board, ensure_ascii=False, indent=2).encode("utf-8")),
               (paths["native_path"], json.dumps(plan, ensure_ascii=False, indent=2).encode("utf-8")),
               (paths["instructions_path"], instruction.encode("utf-8"))]
    published = publish_files(outputs, input_paths=[db_path] if db_path is not None else [])
    return {"board_fingerprint": board_fingerprint(board), "output_dir": str(output_dir),
            **{key: str(value) for key, value in paths.items()}, **published, "pending": pending_items(board)}
