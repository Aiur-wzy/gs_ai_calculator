// 描述显示工具：按模板格式与本工具显示规则替换参数，原文/未知标记可追溯。
"use strict";

window.DF = {};

DF.roundInteger = (value) => Math.sign(value) * Math.round(Math.abs(value));

DF.format = (template, params = []) => {
  const unresolved = [];
  const text = String(template ?? "").replace(/\{param(\d+)(?::([^}]*))?\}/g, (token, id, fmt) => {
    const value = params[Number(id) - 1];
    let formatted;
    if (typeof value === "number" && Number.isFinite(value)) {
      if (!fmt) formatted = String(value);
      // P 未指定小数位：本工具保留最多两位百分比小数、去尾零；原值仍可查看。
      else if (fmt === "P" && Number.isFinite(value * 100)) formatted = `${Number((value * 100).toFixed(2))}%`;
      else if (fmt === "I") formatted = String(DF.roundInteger(value));
      else {
        const match = /^F([0-9])([P]?)$/.exec(fmt);
        const scaled = value * (match?.[2] ? 100 : 1);
        if (match && Number.isFinite(scaled)) formatted = scaled.toFixed(Number(match[1]))
          + (match[2] ? "%" : "");
      }
    }
    if (formatted === undefined) { unresolved.push(token); return token; }
    return formatted;
  });
  return {text, unresolved: [...new Set(unresolved)]};
};

DF.rawValue = (value) => value === undefined || value === null ? "缺失" : String(value);

// 对齐 curated 层 ASCENSION_PROP_CN 的明确属性单位，未知属性保留原数值。
DF.PERCENT_ASCENSION = new Set(["暴击率", "暴击伤害", "攻击力%", "生命值%", "防御力%",
  "元素充能效率", "治疗加成", "物理伤害加成", "火元素伤害加成", "雷元素伤害加成",
  "水元素伤害加成", "冰元素伤害加成", "风元素伤害加成", "岩元素伤害加成", "草元素伤害加成"]);
DF.ascension = (property, value) => {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return DF.PERCENT_ASCENSION.has(property) && Number.isFinite(value * 100)
    ? `${(value * 100).toFixed(1)}%` : String(value);
};

// 这里只识别有限的游戏文本标记；返回 token，不生成 HTML。
DF.tokens = (text) => {
  const source = String(text ?? "").replace(/\\n/g, "\n").replace(/\r\n?/g, "\n");
  const pattern = /<br\s*\/?>|<\/?(?:b|i|u)>|<color=#[0-9a-f]{6}(?:[0-9a-f]{2})?>|<\/color>|\{LINK#[\w.:-]+\}|\{\/LINK\}/gi;
  const tokens = [];
  let cursor = 0;
  for (const match of source.matchAll(pattern)) {
    if (match.index > cursor) tokens.push({type: "text", value: source.slice(cursor, match.index)});
    const raw = match[0];
    const lower = raw.toLowerCase();
    if (lower.startsWith("<br")) tokens.push({type: "text", value: "\n"});
    else if (lower.startsWith("</") || lower === "{/link}") {
      tokens.push({type: "close", tag: lower === "{/link}" ? "link" : lower.slice(2, -1), raw});
    } else {
      tokens.push({type: "open", tag: lower.startsWith("{link") ? "link"
        : lower.startsWith("<color") ? "color" : lower.slice(1, -1),
      color: lower.startsWith("<color") ? raw.slice(7, -1) : null, raw});
    }
    cursor = match.index + raw.length;
  }
  if (cursor < source.length) tokens.push({type: "text", value: source.slice(cursor)});
  return tokens;
};
