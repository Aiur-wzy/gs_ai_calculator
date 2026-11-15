"""Regenerate the maintained current Skill resource map from source files."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def authoring_navigation(root: Path):
    """Render navigation, not copies of the live schema or execution authority."""
    skill = root / "skill"
    source = skill / "references/app-mechanics/authoring-intents.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    target = source.with_name("authoring-guide.md")

    def link(resource):
        return Path(os.path.relpath(skill / resource, target.parent)).as_posix()

    lines = ["# 从计算意图找到输入与观察位置", "",
             "本页从同目录 `authoring-intents.json` 生成，只做导航；schema、权限和执行支持以本次App能力为准。条目不是必须逐项完成的阶段或必读许可。已有正确输入可直接继续，未读本页不影响工具权限。", "",
             "先说明想发生什么，再取当前格式，参考片段组成完整输入，最后到实际执行中查证。完整例子用虚构角色，不授予真实角色机制的执行资格。文件中的片段指针相对例子的第一段完整JSON；不是工具参数，也不能把局部片段当完整方案提交。", "",
             "原生或连接当前候选的CLI，使用下列 `resource` 与 `pointer`；独立CLI使用 `capabilities --section plan_contract --pointer 指针`。尚为白板时先按实际能力切到原生输入或读取独立能力说明，不能把 unavailable 当数据库缺失。", "",
             "## 输入导航", ""]
    for row in data["intents"]:
        schema = row["schema"]
        observe = row["observe"]
        request = json.dumps(observe["request"], ensure_ascii=False, separators=(",", ":"))
        lines += [f"### {row['task']}", "", row["distinction"], "",
                  f"- 格式：`resource={schema['resource']}`，`pointer={schema['pointer']}`。",
                  f"- 写法与范围：[相关说明]({link(row['guide']['resource'])})；[完整例子]({link(row['example']['resource'])})中片段 `{row['fragment']['pointer']}`。",
                  f"- 已有结果：`{observe['command']}` 或 `{observe['native_tool']}`，请求体 `{request}`。参数外层按当前工具schema，沿返回的分页读取所需项。", ""]
    lines += ["## 读回后判断", "",
              "输入存在、来源核对、引擎执行、任务符合游戏机制分别核对。结果中找不到收益时，先辨别未输入、条件不成立、尚未执行到、输入被拒和真正的执行缺陷；不是统一填更多增伤或排除来源。", "",
              "没有可执行资料时，继续查游戏事实或用已有通用接口表达；未知范围如实保留。这里的示例和导航不自动创建来源、不确认资料、不更改已有结果，也不要求把所有副本都写成某种固定工作表。", ""]
    target.write_text("\n".join(lines), encoding="utf-8")
    return {"resource": source.relative_to(skill).as_posix(),
            "generated_resource": target.relative_to(skill).as_posix(),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "intent_count": len(data["intents"])}


def update(root: Path = ROOT):
    skill = root / "skill"
    navigation = authoring_navigation(root)
    records = []
    for path in sorted(skill.rglob("*.md")):
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        headings = [line.lstrip("# ") for line in text.splitlines() if line.startswith("#")]
        records.append({"resource": path.relative_to(skill).as_posix(),
                        "title": headings[0] if headings else path.stem,
                        "headings": headings, "bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest()})
    out = root / "docs/design/skill"
    manifest = {"contract": "genshin-current-skill-resources-v1", "scope": "source_not_release_qualification",
                "resource_count": len(records), "resources": records,
                "authoring_navigation": navigation}
    (out / "current-resources.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 当前Skill资源与读取位置", "",
             "本页由`scripts/update_skill_resource_map.py`从正式Skill生成，说明当前源码；包内资源和原生接线须另按下一版总清单验收。旧52页审计是r8历史快照，不再作为当前路径索引。", "",
             "主Skill→当前问题/工作卡→相关游戏主题、资料或App适配。所选角色附录按默认配方分批读取，其他资料按需可取；目录不是权限白名单，不要求全库读取。工作卡正文、真实工具与领域上下文的同源投影仍由App/DSH接缝实际提供。", "",
             "| 资源 | 内容/入口 | 文件位置 |", "|---|---|---|"]
    for row in records:
        relative = "../../../skill/" + row["resource"]
        lines.append(f"| `{row['resource']}` | {row['title']} | [打开]({relative}) |")
    lines += ["", "精确标题/小节及源码SHA见[current-resources.json](current-resources.json)。修改正文或目录后重生成；SHA只证明当前文件内容，不证明机制事实或实际执行正确。", ""]
    (out / "current-resource-map.md").write_text("\n".join(lines), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    result = update()
    print(json.dumps({"resources": result["resource_count"], "status": "source_map_updated"}))
