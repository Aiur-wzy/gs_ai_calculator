/** Lazy read-only explanation UI. All numbers come from the sealed calculator. */
const labels:Record<string,string>={atk:"攻击力",hp:"生命值",def:"防御力",base_atk:"基础攻击力",base_hp:"基础生命值",base_def:"基础防御力",em:"元素精通",multiplier:"本体倍率",
  scaling:"结算属性",crit_rate:"暴击率",crit_rate_raw:"暴击率输入",crit_rate_effective:"本段有效暴击率",crit_dmg:"暴击伤害",
  dmg_bonus:"伤害加成",base_mult:"基础区乘算",base_add:"基础区加算",reaction_flat:"反应专属加算",reaction_bonus:"反应加成",
  system_factor:"反应基础系数",base_bonus:"基础加成乘区",em_zone:"精通乘区",big_mult:"专属乘算",resistance:"抗性乘区",
  defense:"防御乘区",amplify:"增幅乘区",crit_expected:"暴击期望乘区",elevate:"提升乘区",base_in:"原始伤害基数",
  reaction_base:"等级反应基数",aggravate_flat:"激化基础加算",lunar_base:"月反应基础加成",stellar_base:"星反应基础加成",
  lunar_mult:"月反应乘算",stellar_mult:"星反应乘算",lunar_elevate:"月反应提升",stellar_elevate:"星反应提升",
  scaled_base:"本体倍率×结算属性×基础乘算",base_after_flat:"加入基础加算后",base_after_aggravate:"加入激化后",
  before_reaction_flat:"专属加算前",after_reaction_flat:"专属加算后",after_flat:"加入反应加算后",no_crit:"未计暴击的单次伤害",
  expected_per_count:"单次期望伤害",critical_per_count:"单次暴击伤害",base:"基础区终值",
  level:"角色等级",enemy_level:"敌人等级",def_reduction:"减防",def_ignore:"无视防御",amplify_factor:"基础增幅系数",
  atk_pct:"攻击力百分比",hp_pct:"生命值百分比",def_pct:"防御力百分比",atk_flat:"固定攻击力",hp_flat:"固定生命值",def_flat:"固定防御力",
  er:"元素充能效率",count:"次数",result_weight:"结果权重",crit:"暴击乘区",reaction:"元素反应",
  downstream_zones:"后续伤害／防御／抗性／反应／暴击乘区",def_reduction_effective:"实际减防",def_ignore_effective:"实际无视防御"};
const percents=new Set(["multiplier","crit_rate","crit_rate_raw","crit_rate_effective","crit_dmg","dmg_bonus","def_reduction","def_ignore","def_reduction_effective","def_ignore_effective"]);
const finite=(x:any)=>typeof x==="number"&&Number.isFinite(x);
const number=(x:number)=>Number(x.toFixed(5)).toLocaleString("zh-CN");
const format=(key:string,value:any)=>finite(value)?`${number(percents.has(key)?value*100:value)}${percents.has(key)?"%":""}`:"未提供";
const own=(table:any,key:any)=>table&&typeof key==="string"&&Object.hasOwn(table,key)?table[key]:null;

export function resolveDamageExplanation(projection:any,detail:any):any {
  const doc=projection?.explanationDocument,identity=projection?.explanationFingerprint;
  if(!doc)return {status:"missing"};
  if(doc.contract!=="damage-explanation-v1"||doc.schema_version!==1)return {status:"unsupported"};
  if(!identity||!detail?.explanation_fingerprint||identity!==detail.explanation_fingerprint)return {status:"stale"};
  if(!detail.explanation_ref)return {status:doc.status==="partial"?"omitted":"missing"};
  const event=own(doc.events,detail.explanation_ref);
  if(!event)return {status:"invalid"};
  const source=own(doc.sources,event.source_ref),panel=event.panel_ref==null?null:own(doc.panels,event.panel_ref);
  const formula=event.formula_ref==null?null:own(doc.formulas,event.formula_ref);
  const modifiers=(event.modifier_refs||[]).map((id:any)=>own(doc.modifiers,id));
  if(!source||(event.panel_ref!=null&&!panel)||(event.formula_ref!=null&&!formula)||modifiers.some((x:any)=>!x))return {status:"invalid"};
  return {status:"available",event,source,panel,formula,modifiers};
}

export function renderDamageExplanation(box:HTMLElement,event:any,ports:any):void {
  const {view,document,services}=ports,detail=event.detail||{};
  const el=(tag:string,text?:string)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;return n;};
  const wrapper=el("section");wrapper.className="tl-damage-explanation";
  wrapper.appendChild(el("h5","本段面板与倍率组成"));box.appendChild(wrapper);
  const identity=view.projection;
  const fresh=()=>view.projection===identity&&view.detailSelection===event;
  const resolved=resolveDamageExplanation(identity,detail);
  if(resolved.status!=="available"){
    const messages:Record<string,string>={missing:"此结果未保存本段解释，重新计算后可查看。",unsupported:"此解释版本暂不受支持。",
      stale:"方案或计算结果已经变化，请重新选择本段伤害。",omitted:"本段解释因容量限制未保存；伤害值仍以计算回执为准。",invalid:"本段解释引用不完整，请重新计算。"};
    wrapper.appendChild(el("p",messages[resolved.status]||"解释资料未提供。"));return;
  }
  const {event:record,source,panel,formula,modifiers}=resolved;
  const fields=(parent:HTMLElement,items:[string,any][])=>{
    const list=el("dl");list.className="tl-detail-fields";
    for(const [name,value] of items)if(value!=null){list.appendChild(el("dt",name));list.appendChild(el("dd",String(value)));}
    parent.appendChild(list);
  };
  const name=(key:string)=>labels[key]||services.display?.label?.("result_zones",key)||key;
  const char=(id:any)=>view.characterNames?.get(id)||"未指定角色";
  const outcome=record.outcome||{}, origin=outcome.origin;
  if(origin!=="engine_formula"){
    wrapper.appendChild(el("p",origin==="fixed_override"
      ?"本段采用有来源的单事件固定值。下方自动公式仅为原始基线，不是有效固定值的计算依据。"
      :"本段是外部计算补充；当前引擎没有计算其等效面板，不重复套用Buff或公式。"));
  }
  if(panel){
    const input=panel.inputs||{},stat=panel.scaling_stat||"atk";
    const primary:[string,any][]=[];
    if(finite(input.scaling))primary.push([`本段${name(stat)}`,format("scaling",input.scaling)]);
    for(const key of ["multiplier","em","crit_rate_raw","crit_rate_effective","crit_dmg","dmg_bonus"])
      if(finite(input[key]))primary.push([name(key),format(key,input[key])]);
    fields(wrapper,primary);
    wrapper.appendChild(el("p","仅列本段公式实际使用的属性；提供者、敌方及不同时点取样分别保留，不假定全属性都锁定在同一时刻。"));
  }
  const lazy=(title:string,draw:(node:HTMLElement)=>void)=>{
    const d=el("details"),summary=el("summary",title);let populated=false;d.appendChild(summary);
    d.addEventListener("toggle",()=>{if(d.open&&!populated&&fresh()){populated=true;draw(d);}});wrapper.appendChild(d);
  };
  lazy(origin==="engine_formula"?"基础区、公式与各乘区":"自动计算基线（仅供对照）",node=>{
    if(!formula){node.appendChild(el("p","未记录引擎公式；不能从最终伤害反推。"));return;}
    const expression=String(formula.expression||"").replace(/[a-z][a-z0-9_]*/gu,token=>labels[token]||token);
    node.appendChild(el("p",expression));
    const points=record.checkpoints||{},order=["scaled_base","base_in","reaction_base","base_after_flat","base_after_aggravate",
      "before_reaction_flat","after_reaction_flat","after_flat","def_reduction_effective","def_ignore_effective","no_crit","expected_per_count","critical_per_count"];
    fields(node,order.filter(key=>finite(points[key])).map(key=>[name(key),format(key,points[key])]));
    fields(node,Object.entries(record.zones||{}).filter(([,v])=>finite(v)).map(([key,value])=>[name(key),number(value as number)]));
    const normalization=record.normalization||{};
    if(normalization.status==="available"){
      fields(node,[[`相对本段${name(normalization.reference_stat)}的等效倍率`,`${number(normalization.value*100)}%`],
        ["折算检查点",name(normalization.checkpoint)],["参考属性值",format("scaling",normalization.reference_value)]]);
      for(const [key,title] of [["includes","折算包含"],["excludes","折算不包含"]]){
        const terms=normalization[key];if(Array.isArray(terms)&&terms.length)fields(node,[[title,terms.map(name).join("、")]]);
      }
      node.appendChild(el("p","等效倍率只作本段比较，不是原技能倍率；加算提供者和原始公式仍保留。"));
    }else node.appendChild(el("p",origin==="fixed_override"
      ?"本段采用单事件固定值，有效结果不由自动面板推导，因此不提供有效结果的等效倍率。"
      :origin==="external"?"本段由外部计算提供，不从最终值反推等效倍率。"
      :"本段不提供统一等效倍率：参考属性为零、未知，或使用等级反应基数。"));
  });
  lazy(`收益来源与加算项（${modifiers.length}项）`,node=>{
    if(!modifiers.length){node.appendChild(el("p","没有记录额外收益项。"));return;}
    for(const modifier of modifiers){
      const row=el("section"),execution=modifier.execution||{},sampling=modifier.sampling||{};
      const zone=modifier.zone||"";
      row.appendChild(el("h6",modifier.name_zh||name(zone)||"收益项"));
      fields(row,[["作用区域",name(zone)],["实际贡献",finite(modifier.value)?number(modifier.value):null],
        ["提供者",execution.provider?char(execution.provider):null],
        ["取样时点",finite(sampling.sample_t??execution.sample_t)?`${number(sampling.sample_t??execution.sample_t)}秒`:null],
        ["取样属性",sampling.stat?name(sampling.stat):null],
        ["取样属性值",finite(sampling.source_value)?number(sampling.source_value):null],
        ["单次表达式结果",finite(sampling.value)?number(sampling.value):null],
        ["百分比所乘基数",finite(modifier.basis_value)?number(modifier.basis_value):null],
        ["换算后的属性加值",finite(modifier.resolved_contribution)?number(modifier.resolved_contribution):null],
        ["生效层数",execution.layers]]);
      const expression=sampling.expression;
      if(expression&&typeof expression==="object")fields(row,[["属性系数",finite(expression.factor)?number(expression.factor):null],
        ["固定加值",finite(expression.add)?number(expression.add):null],["表达式下限",finite(expression.min)?number(expression.min):null],
        ["表达式上限",finite(expression.max)?number(expression.max):null]]);
      if(modifier.consumed===false)row.appendChild(el("p","本段未消费此属性收益。"));
      node.appendChild(row);
    }
  });
  lazy("输入基线、采样与完整证据",node=>{
    if(panel){fields(node,[["属性所属",char(panel.character)],["取样时间",finite(panel.sample_t)?`${number(panel.sample_t)}秒`:"各域分别取样"],
      ["数值投影时点",finite(panel.evaluation_t)?`${number(panel.evaluation_t)}秒`:null]]);
      fields(node,Object.entries(panel.baseline||{}).filter(([,v])=>finite(v)).map(([key,value])=>[name(key),format(key,value)]));}
    const pre=el("pre",JSON.stringify({source,panel,formula,event:record,modifiers},null,2));node.appendChild(pre);
  });
  fields(wrapper,[["结果来源",origin==="engine_formula"?"引擎公式":origin==="fixed_override"?"单事件固定值":"外部计算补充"],
    ["计算次数",outcome.count],["结果权重",outcome.result_weight]]);
}
