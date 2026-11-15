import type { MechanicsEditor, MechanicsEditorPorts } from "./contracts";
export function installMechanicsSchemaForm(MC: MechanicsEditor, ports: MechanicsEditorPorts): void {
  const {
    UI,
    PL,
    RT,
    RC,
    RS,
    DS,
    EF,
    extensions,
    callApi,
    setStatus,
    $
  } = ports;
  MC.default = (s: any): any => {
    if (s.const !== undefined) return s.const;
    if (s.oneOf) return MC.default(s.oneOf[0]);
    if (s.enum) return s.enum[0];
    const type = Array.isArray(s.type) ? s.type.find((t: any): any => t !== "null") : s.type;
    if (type === "object") {
      const value = Object.fromEntries((s.required || []).map((k: any): any => [k, MC.default(s.properties[k])]));
      if (s.properties?.availability?.const === "fixed") value.status = "pending";
      return value;
    }
    if (type === "array") return [];
    if (type === "boolean") return false;
    if (type === "null") return null;
    if (type === "number" || type === "integer") return s.minimum ?? 0;
    return "";
  };
  MC.branch = (s: any, v: any): any => s.oneOf?.findIndex((x: any): any => x.type === "object" ? v !== null && typeof v === "object" && Object.entries(x.properties || {}).every(([key, child]: any): any => (child.const === undefined || v[key] === child.const) && (!child.enum || v[key] === undefined || child.enum.includes(v[key]))) : x.type === "null" ? v === null : typeof v === x.type || x.type === "integer" && Number.isInteger(v)) ?? -1;
  MC.form = (box: any, s: any, value: any, set: any, label: any, redraw: any): any => {
    if (s.oneOf) {
      const i = Math.max(0, MC.branch(s, value));
      box.append(UI.field(`${label} · 表达方式`, UI.select(s.oneOf.map((branch: any, j: any): any => ({
        value: String(j),
        label: branch["x-label"] || (branch.properties?.kind?.const ? MC.label(branch.properties.kind.const) : branch.properties?.kind?.enum?.includes("all") ? "组合条件" : j === 0 ? "固定数值" : "属性比例")
      })), String(i), (v: any): any => {
        set(MC.default(s.oneOf[Number(v)]));
        redraw();
      })));
      MC.form(box, s.oneOf[i], value, set, label, redraw);
      return;
    }
    const type = Array.isArray(s.type) ? s.type.find((t: any): any => t !== "null") : s.type;
    if (Array.isArray(s.type) && s.type.includes("null")) {
      box.append(UI.check(`${label} · ${s["x-null-label"] || "未知"}`, value === null, (yes: any): any => {
        set(yes ? null : MC.default({
          ...s,
          type
        }));
        redraw();
      }));
      if (value === null) return;
    }
    if (type === "null") {
      box.append(UI.el("p", `${label}：${s["x-null-label"] || "空值"}`));
      return;
    }
    if (s.const !== undefined) {
      box.append(UI.el("p", `${label}：${s["x-const-label"] || MC.label(s.const)}`));
      return;
    }
    if (s.enum) {
      box.append(UI.field(label, UI.select(s.enum.map((v: any): any => ({
        value: String(v),
        label: s["x-enum-labels"]?.[v] || (s.enum.includes("all") && s.enum.includes("any") ? v === "all" ? "全部子条件成立" : "至少一项子条件成立" : MC.label(v))
      })), String(value), (v: any): any => set(s.enum.find((x: any): any => String(x) === v)))));
      return;
    }
    if (type === "array") {
      const group = UI.el("fieldset", null, "eq-part");
      group.append(UI.el("legend", label));
      box.append(group);
      (value || []).forEach((v: any, i: any): any => {
        const row = UI.el("section", null, "eq-part");
        group.append(row);
        MC.form(row, s.items, v, (n: any): any => {
          value[i] = n;
        }, `${label} ${i + 1}`, redraw);
        row.append(UI.button("移除此项", (): any => {
          value.splice(i, 1);
          redraw();
        }));
      });
      group.append(UI.button(`添加${label}`, (): any => {
        value.push(MC.default(s.items));
        redraw();
      }));
      return;
    }
    if (type === "object") {
      const group = UI.el("fieldset", null, "eq-part");
      group.append(UI.el("legend", label));
      box.append(group);
      if (s.properties) {
        Object.entries(s.properties).forEach(([key, child]: any): any => {
          const required = (s.required || []).includes(key),
            shown = Object.hasOwn(value, key),
            name = child["x-label"] || MC.label(key);
          if (!required) group.append(UI.check(`设置${name}`, shown, (yes: any): any => {
            if (yes) value[key] = MC.default(child);else delete value[key];
            redraw();
          }));
          if (required || shown) {
            if (key === "owner") group.append(UI.field(name, UI.select(DS.characters(RT.tpl(), PL.state.plan), value[key], (v: any): any => {
              value[key] = v;
            })));else MC.form(group, child, value[key], (v: any): any => {
              value[key] = v;
            }, name, redraw);
          }
        });
      } else {
        Object.entries(value).forEach(([k, v]: any): any => {
          const row = UI.el("div");
          group.append(row);
          MC.form(row, s.additionalProperties, v, (n: any): any => {
            value[k] = n;
          }, DS.character(k, RT.tpl(), PL.state.plan), redraw);
          row.append(UI.button("移除角色项", (): any => {
            delete value[k];
            redraw();
          }));
        });
        group.append(UI.field("添加角色项", UI.select([{
          value: "",
          label: "选择角色"
        }, ...DS.characters(RT.tpl(), PL.state.plan)], "", (v: any): any => {
          if (v && !Object.hasOwn(value, v)) {
            value[v] = MC.default(s.additionalProperties);
            redraw();
          }
        })));
      }
      return;
    }
    if (type === "boolean") {
      box.append(UI.check(label, !!value, set));
      return;
    }
    box.append(UI.field(label, ["source", "assumptions"].some((k: any): any => label.startsWith(MC.label(k))) ? UI.area(value, set) : UI.input(value, set, type === "number" || type === "integer")));
  };
}
