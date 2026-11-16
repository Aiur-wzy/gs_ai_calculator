// Shared draft-only editor. Equipment changes never infer a replacement finished panel.
"use strict";
window.EW={
  baseFields:[["base_atk","基础攻击力（角色 + 武器白值）"],["base_hp","基础生命值"],["base_def","基础防御力"]],
  panelFields:[["atk","攻击力"],["hp","生命值"],["def","防御力"],["em","元素精通"],
    ["crit_rate","暴击率 %",true],["crit_dmg","暴击伤害 %",true],["energy_recharge","元素充能效率 %",true]],
  elements:[["all","全"],["physical","物理"],["pyro","火"],["hydro","水"],["electro","雷"],["cryo","冰"],["anemo","风"],["geo","岩"],["dendro","草"]],
  slots:[["EQUIP_BRACER","生之花"],["EQUIP_NECKLACE","死之羽"],["EQUIP_SHOES","时之沙"],["EQUIP_RING","空之杯"],["EQUIP_DRESS","理之冠"]],
  panelNote:"这里填写所选装备对应的初始成品面板，包含武器、圣遗物词条与两件套等常驻属性；轴内条件增益由引擎另算。换装不会自动推算新的成品面板。",
};
EW.mount=(container,options={})=>{
  const actor=UI.clone(options.actorDraft||{}),original=JSON.stringify(actor),line=options.versionLine||"release",
    api=(...args)=>(options.api||callApi)(...args),native=options.weaponShape==="native",
    gearKey=()=>JSON.stringify([actor.weapon?{weapon_id:actor.weapon.weapon_id,entity_ref:actor.weapon.entity_ref??null,level:actor.weapon.level,ascension:actor.weapon.ascension,
      ...(native?{refinement:actor.weapon.refinement,refinements:actor.weapon.refinements}:{refinement:actor.weapon.refinement})}:null,(actor.artifact_sets||[]).map(s=>({set_id:s.set_id,entity_ref:s.entity_ref??null,count:s.count})),actor.artifact_presence??null]),
    initialGear=gearKey(),root=UI.el("div",null,"ew-workbench"),invalid=new Map(),details=new Map();
  let alive=true,searchRequest=0,selectionRequest=0,timer=null,activeSearch=null,lockedDataVersion=options.dataVersion,confirmed=!!options.panelConfirmed,weaponType=options.weaponType||"",
    setMode=actor.artifact_sets?.[0]?.count===2?"2+2":actor.artifact_sets?.length?"4":"none";
  actor.panel ||= {};actor.panel.dmg_bonus ||= {};actor.artifact_sets ||= [];
  actor.panel_basis_mode ||= (actor.db_character_id || actor.character_ref?.kind === "provisional")
    ? "database"
    : "manual";
  const baseInputs=new Map(),originalBases=Object.fromEntries(EW.baseFields.map(([key])=>[key,actor.panel[key]??null]));
  const contextMenu=window.AppContextMenu?.forDocument?.(document,window),menuDisposers=[];
  let basisRequest=0,basisPending=false,basisAttempted=false,basisData=null,basisMessage="",basisKey=null,refreshBasis=()=>{},searchSurface=null;
  const sourceKey=()=>JSON.stringify([actor.id??null,actor.character_ref??null,actor.db_character_id??null,actor.level,line,actor.weapon?.entity_ref??null,actor.weapon?.weapon_id??null,actor.weapon?.level??null,actor.weapon?.ascension??null]);
  const current=()=>alive&&(!options.isCurrent||options.isCurrent()),gearChanged=()=>!!options.requirePanelConfirmation||gearKey()!==initialGear;
  const note=UI.el("p",EW.panelNote,"ew-note"),confirmation=UI.el("div",null,"ew-confirmation"),confirmationInput=UI.el("input"),feedback=UI.el("p",null,"ew-feedback");
  feedback.setAttribute("role","status");confirmationInput.type="checkbox";
  const updateConfirmation=()=>{confirmation.hidden=!gearChanged();confirmationInput.checked=confirmed;};
  const change=(kind,extra={})=>{if(!current())return;if(["weapon","sets"].includes(kind))confirmed=false;
    if(["weapon","identity"].includes(kind)&&basisKey!==sourceKey())refreshBasis("source");
    updateConfirmation();options.onChange?.(UI.clone(actor),{kind,equipmentChanged:gearChanged(),panelConfirmed:confirmed,...extra});};
  confirmationInput.addEventListener("change",()=>{confirmed=confirmationInput.checked;change("confirmation");});
  confirmation.append(UI.el("p","装备已变更。请修改右侧面板，或核对当前数值确实适用于新装备。"),UI.field("已核对当前面板对应所选装备",confirmationInput));
  const numeric=(label,value,save,{integer=false,min=0,max=null,onCreate}={})=>{
    const input=UI.el("input");input.type="number";input.step=integer?"1":"any";input.value=value??"";input.min=String(min);if(max!=null)input.max=String(max);
    onCreate?.(input);
    const commit=()=>{if(!current()||input.readOnly)return;const raw=String(input.value).trim(),v=raw===""?null:Number(raw);
      if(input.validity?.badInput||v!=null&&(!Number.isFinite(v)||v<min||integer&&!Number.isInteger(v)||max!=null&&v>max)){
        invalid.set(label,`${label}请输入${integer?"整数":"有效数值"}${max!=null?`（${min}～${max}）`:`（不小于 ${min}）`}`);input.setAttribute("aria-invalid","true");return;}
      invalid.delete(label);input.setAttribute("aria-invalid","false");save(v);};
    input.addEventListener("input",commit);input.addEventListener("change",commit);return UI.field(label,input);
  };
  const textInput=(value,save,multiline=false)=>{const input=UI.el(multiline?"textarea":"input");if(!multiline)input.type="text";else input.rows=2;input.value=value??"";
    input.addEventListener("input",()=>{if(current())save(input.value);});input.addEventListener("change",()=>{if(current())save(input.value);});return input;};
  const card=title=>{const section=UI.el("section",null,"ew-card");section.append(UI.el("h4",title));return section;};
  const menuError=error=>{feedback.textContent=`操作未完成：${error?.message||error}`;};
  const overflow=(label,items)=>{
    if(!contextMenu){const menu=UI.el("details",null,"ew-overflow"),summary=UI.el("summary",label||"更多");menu.append(summary);
      for(const item of items){const button=UI.button(item.label,item.run,item.danger?"danger":"");if(item.enabled===false)button.disabled=true;menu.append(button);}return menu;}
    const trigger=UI.button("⋯",event=>{event.preventDefault?.();event.stopPropagation?.();
      if(contextMenu.activeTarget()===trigger){contextMenu.close({restoreFocus:true});return;}
      contextMenu.open({target:trigger,trigger,returnFocus:trigger,items,label:label||"更多操作",onError:menuError});
    },"ew-overflow-trigger");
    trigger.setAttribute("aria-label",label||"更多操作");trigger.setAttribute("title",label||"更多操作");
    menuDisposers.push(contextMenu.bind(trigger,{items,label:label||"更多操作",onError:menuError}));return trigger;
  };
  const bindCardMenu=(target,label,trigger,items)=>{if(!contextMenu)return;
    menuDisposers.push(contextMenu.bind(target,{items,label,trigger:(event,keyboard)=>keyboard?trigger:target,returnFocus:()=>trigger,onError:menuError}));};
  const effectText=(label,description)=>{const row=UI.el("div",null,"ew-effect-description");row.append(UI.el("span",label),
    window.DV?.richText?DV.richText(description,"ew-description"):UI.el("span",description));return row;};
  const layout=UI.el("div",null,"ew-columns"),gear=UI.el("div",null,"ew-gear"),panel=card("初始面板"),identity=UI.el("div",null,"ew-grid ew-identity"),
    identitySection=UI.el("details",null,"ew-identity-section");
  identitySection.append(UI.el("summary",`角色成长 · ${actor.level??"等级待补"} 级${actor.constellation==null?"":` · ${actor.constellation} 命`}`));
  let levelInput;
  identity.append(numeric("角色等级",actor.level,v=>{actor.level=v;change("identity");},{integer:true,min:1,max:100,onCreate:input=>levelInput=input}));
  const levelShortcuts=UI.el("div",null,"ew-level-shortcuts");
  for(const level of [90,100])levelShortcuts.append(UI.button(`${level} 级`,()=>{if(!current())return;actor.level=level;levelInput.value=String(level);invalid.delete("角色等级");levelInput.setAttribute("aria-invalid","false");change("identity");}));
  identity.append(levelShortcuts);
  if(options.showConstellation!==false)identity.append(UI.field("命座",UI.select([{value:"",label:"尚未指定"},...Array.from({length:7},(_,i)=>({value:i,label:`${i} 命`}))],actor.constellation??"",v=>{if(v==="")delete actor.constellation;else actor.constellation=Number(v);change("identity");})));
  identity.append(UI.field("角色突破",UI.select([{value:"",label:"尚未指定"},...Array.from({length:7},(_,i)=>({value:i,label:`突破 ${i}`}))],actor.ascension??"",v=>{actor.ascension=v===""?null:Number(v);change("identity");})));
  for(const [key,label] of [["normal","普攻"],["skill","战技"],["burst","爆发"]])identity.append(numeric(`${label}天赋基级`,actor.talent_base_levels?.[key],v=>{
    actor.talent_base_levels ||= {};if(v==null)delete actor.talent_base_levels[key];else actor.talent_base_levels[key]=v;change("identity");},{integer:true,min:1,max:10}));
  identity.append(UI.el("p","天赋填写不含命座加级的基级；留空为未知。命座加级由相应规则处理。","ew-note"));
  const weaponCard=card("武器"),weaponName=UI.el("strong",null,"ew-selection-name"),weaponControls=UI.el("div",null,"ew-grid"),weaponDescription=UI.el("div",null,"ew-effect-note"),weaponButtons=UI.el("div",null,"ew-toolbar");
  const setCard=card("圣遗物套装"),setControls=UI.el("div",null,"ew-set-selections"),setDescription=UI.el("div",null,"ew-effect-note");
  const searchPanel=UI.el("section",null,"ew-search-panel ew-search-drawer");searchPanel.hidden=true;searchPanel.setAttribute("aria-label","装备搜索");searchPanel.setAttribute("role","dialog");
  const resetSearch=()=>{activeSearch=null;searchRequest++;selectionRequest++;if(timer!=null){clearTimeout(timer);timer=null;}searchPanel.innerHTML="";};
  const closeSearch=(reason="cancel",options={})=>{const surface=searchSurface;
    if(surface){const accepted=surface.finish?.(reason,options)!==false;if(accepted&&searchSurface===surface)searchSurface=null;return accepted;}
    resetSearch();searchPanel.hidden=true;return true;};
  const safeName=(kind,id,fallback)=>details.get(`${kind}:${id}`)?.[kind==="weapon"?"weapon":"artifact_set"]?.name||fallback||"名称待查询";
  const describeWeapon=()=>{
    weaponName.textContent=actor.weapon?safeName("weapon",actor.weapon.weapon_id,actor.weapon.name):"未选择武器";
    if(actor.weapon?.entity_ref?.kind==="provisional")weaponName.textContent+= " · 临时资料";
    weaponDescription.innerHTML="";const detail=actor.weapon&&details.get(`weapon:${actor.weapon.weapon_id}`);if(!detail)return;
    const rows=(detail.effects||[]).map(group=>{const rank=native?actor.weapon.refinements?.find(r=>r.source_index===group.source_index)?.refinement:actor.weapon.refinement;
      return group.refinements?.find(r=>r.refinement===rank);}).filter(Boolean);
    for(const effect of rows){const description=effect.desc_rendered?.plain||effect.desc_rendered||effect.desc_template||effect.description||"被动说明待补";
      weaponDescription.append(effectText(`${effect.name||"武器被动"}：`,typeof description==="string"?description:"被动说明待补"));}
    if(!rows.length)weaponDescription.append(UI.el("p",detail.weapon?.has_effect===false?"此武器没有被动效果。":"被动触发与覆盖由计算规则决定，应用后检查待完善项。"));
  };
  const renderWeapon=()=>{
    weaponControls.innerHTML="";describeWeapon();if(!actor.weapon)return;const w=actor.weapon,detail=details.get(`weapon:${w.weapon_id}`),states=detail?.weapon?.available_states||[];
    weaponControls.append(numeric("武器等级",w.level,v=>{w.level=v;change("weapon");},{integer:true,min:1,max:detail?.weapon?.max_level||100}),
      UI.field("武器突破",UI.select([{value:"",label:"尚未指定"},...(states.length?states:Array.from({length:7},(_,i)=>({ascension:i}))).map(s=>({value:s.ascension,label:`突破 ${s.ascension}${s.min_level!=null?`（${s.min_level}～${s.max_level} 级）`:""}`}))],w.ascension??"",v=>{w.ascension=v===""?null:Number(v);change("weapon");})));
    if(native)weaponControls.append(UI.field("武器整体精炼",UI.select([{value:"",label:"尚未指定"},...[1,2,3,4,5].map(r=>({value:r,label:`精炼 ${r} 阶`}))],w.refinement??"",v=>{
      if(v==="")delete w.refinement;else {w.refinement=Number(v);for(const row of w.refinements||[])row.refinement=w.refinement;}change("weapon");renderWeapon();})));
    const refinements=native?(w.refinements||[]):[{refinement:w.refinement}];
    for(const ref of refinements){const group=detail?.effects?.find(g=>g.source_index===ref.source_index),ranks=group?.refinements?.map(r=>r.refinement)||[1,2,3,4,5];
      weaponControls.append(UI.field(refinements.length>1?`精炼 · 效果组 ${ref.source_index+1}`:"精炼",UI.select([{value:"",label:"尚未指定"},...ranks.map(r=>({value:r,label:`精炼 ${r} 阶`}))],ref.refinement??"",v=>{
        ref.refinement=v===""?null:Number(v);if(!native)w.refinement=ref.refinement;
        else if(w.refinement!=null){if(ref.refinement==null)delete w.refinement;else{w.refinement=ref.refinement;for(const row of w.refinements||[])row.refinement=w.refinement;}}
        change("weapon");if(native)renderWeapon();else describeWeapon();})));}
  };
  const describeSets=()=>{setDescription.innerHTML="";
    for(const selected of actor.artifact_sets){if(selected.entity_ref?.kind==="provisional"){setDescription.append(UI.el("p",`${selected.name||"临时套装"}：仅限当前方案，效果需由 Agent 材料化并在正式结果标注近似。`,"ew-note"));continue;}const detail=details.get(`set:${selected.set_id}`);if(!detail)continue;
      for(const entry of detail.effects||[]){const count=entry.required_count??entry.required_pieces??entry.piece_count??entry.count,effect=entry.effect||entry;if(count!=null&&count>selected.count)continue;
        const description=effect.desc_rendered?.plain||effect.desc_rendered||effect.desc_template||effect.description;if(typeof description==="string")setDescription.append(effectText(count?`${count} 件：`:"",description));}}
  };
  const presenceControls=UI.el("div",null,"ew-grid"),presenceNote=UI.el("p","这里只记录是否装备，不分配主副词条。无套装效果不等于五槽未装备。","ew-note");
  const renderPresence=()=>{presenceControls.innerHTML="";for(const [key,label] of EW.slots)presenceControls.append(UI.field(`${label}装备状态`,UI.select([
    {value:"unknown",label:"未知"},{value:"empty",label:"未装备"},{value:"equipped",label:"已装备"}],actor.artifact_presence?.[key]||"unknown",v=>{
      actor.artifact_presence ||= Object.fromEntries(EW.slots.map(([slot])=>[slot,"unknown"]));actor.artifact_presence[key]=v;change("presence");})));
  };
  const invalidatePresence=()=>{if(actor.artifact_presence){actor.artifact_presence=Object.fromEntries(EW.slots.map(([slot])=>[slot,"unknown"]));presenceNote.textContent="套装已变更；五槽存在状态已置为未知，请按实际装备重新确认。";renderPresence();}};
  const updateSetChoices=()=>{
    for(const [index,button] of Array.from(setControls.children).entries()){
      if(button.tagName.toLowerCase()!=="button")continue;
      const selected=actor.artifact_sets[index];
      button.textContent=selected?`${safeName("set",selected.set_id,selected.name)} · ${selected.count} 件`:`选择第 ${index+1} 套`;
      button.disabled=index===1&&!actor.artifact_sets[0];
    }
    describeSets();
  };
  const renderSets=()=>{setControls.innerHTML="";
    if(setMode==="none")setControls.append(UI.el("p","不启用圣遗物套装效果；保留当前给定面板。","ew-note"));
    else for(let index=0;index<(setMode==="2+2"?2:1);index++){
      const button=UI.button("",()=>openSearch("set",index,button),"ew-choice");
      button.setAttribute("aria-label",`选择第 ${index+1} 套`);setControls.append(button);}
    updateSetChoices();
  };
  const selectItem=async(kind,item,index,list)=>{
    const selected=++selectionRequest,search=searchRequest;feedback.textContent="正在核对装备资料…";
    try{const response=await api(kind==="weapon"?"weapon_detail":"artifact_set_detail",item.id,line);
      if(!current()||selected!==selectionRequest||search!==searchRequest||searchPanel.hidden)return;
      if(response.code){feedback.textContent=response.message||"装备资料读取失败，请重试。";return;}const detail=response.data,rejection=options.canSelect?.(detail);
      if(lockedDataVersion&&JSON.stringify(lockedDataVersion)!==JSON.stringify(detail.data_version)){
        feedback.textContent="当前方案与装备资料版本不同，请先使用“查看并应用新资料”核对更新，再更换装备。";return;}
      if(rejection){feedback.textContent=rejection;return;}
      if(kind==="weapon"){
        if(weaponType&&detail.weapon?.weapon_type&&detail.weapon.weapon_type!==weaponType){feedback.textContent="此武器类型不适用于当前角色。";return;}
        const states=detail.weapon?.available_states||[],last=states.at(-1),max=detail.weapon?.max_level||last?.max_level||90;
        actor.weapon=actor.weapon?.weapon_id===item.id?{...actor.weapon,name:detail.weapon?.name||item.name}:{weapon_id:item.id,name:detail.weapon?.name||item.name,level:max,ascension:last?.ascension??6,
          ...(native?{refinement:1,refinements:(detail.effects||[]).map(g=>({source_index:g.source_index,affix_group_id:g.affix_group_id,refinement:g.refinements?.[0]?.refinement??null})),selected_by:"user"}:{refinement:1})};
        details.set(`weapon:${item.id}`,detail);renderWeapon();
      }else{
        if(index===1&&!actor.artifact_sets[0]){feedback.textContent="请先选择第一套。";return;}
        if(actor.artifact_sets.some((s,i)=>i!==index&&s.set_id===item.id)){feedback.textContent="两件组合需选择两个不同套装；同一套装请选择四件套。";return;}
        invalidatePresence();actor.artifact_sets[index]={set_id:item.id,name:detail.artifact_set?.name||item.name,count:setMode==="4"?4:2};
        details.set(`set:${item.id}`,detail);updateSetChoices();
      }
      lockedDataVersion ||= detail.data_version;closeSearch("selected");feedback.textContent="已更新草稿。请核对初始面板后应用。";change(kind==="weapon"?"weapon":"sets",{dataVersion:detail.data_version});
    }catch(error){if(current()&&selected===selectionRequest)feedback.textContent=`装备资料读取失败：${error.message||error}`;}
  };
  const openSearch=async(kind,index=0,opener=null)=>{
    closeSearch("replaced",{restoreFocus:false});searchPanel.hidden=false;const session={};activeSearch=session;let page=1;
    const search=UI.el("input"),title=UI.el("h4",kind==="weapon"?"搜索并切换武器":"搜索并切换圣遗物套装"),list=UI.el("div",null,"ew-search-results"),nav=UI.el("div",null,"ew-toolbar");
    search.type="search";search.placeholder=kind==="weapon"?"输入武器中文名":"输入套装中文名";
    const run=async()=>{
      if(!current()||searchPanel.hidden||activeSearch!==session)return;
      if(kind==="weapon"&&actor.db_character_id&&!weaponType){list.innerHTML="";list.append(UI.el("p","请等待角色武器类型核对完成。"));return;}
      const req=++searchRequest;selectionRequest++;list.innerHTML="";nav.innerHTML="";list.append(UI.el("p","正在搜索…"));
      try{const response=await api(kind==="weapon"?"list_weapons":"list_artifact_sets",...kind==="weapon"?[search.value.trim(),weaponType,0,"codex",line,page,30]:[search.value.trim(),0,"codex",line,page,30]);
        if(!current()||req!==searchRequest||searchPanel.hidden)return;list.innerHTML="";if(response.code){list.append(UI.el("p",response.message||"查询失败，请重试。","eq-error"));return;}
        const rows=response.data.items||[];list.append(UI.el("p",`共 ${response.data.total} 项 · 第 ${page} 页`,"ew-note"));
        if(!rows.length)list.append(UI.el("p","没有找到匹配装备，试试缩短关键词。"));
        for(const item of rows){const selected=kind==="weapon"?actor.weapon?.weapon_id===item.id:actor.artifact_sets[index]?.set_id===item.id;
          list.append(UI.button(`${item.name||"中文名待补"}${item.rarity?` · ${item.rarity}星`:""}${selected?" · 当前选择":""}`,()=>selectItem(kind,item,index,list),"ew-search-item"));}
        const prev=UI.button("上一页",()=>{page--;run();}),next=UI.button("下一页",()=>{page++;run();});prev.disabled=page===1;next.disabled=page*30>=response.data.total;nav.append(prev,next);
      }catch(error){if(current()&&req===searchRequest){list.innerHTML="";list.append(UI.el("p",`查询失败：${error.message||error}`,"eq-error"));}}
    };
    const submit=()=>{if(timer!=null)clearTimeout(timer);timer=null;page=1;run();};
    search.addEventListener("input",()=>{searchRequest++;selectionRequest++;if(timer!=null)clearTimeout(timer);timer=setTimeout(submit,250);});
    search.addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();submit();}});
    searchPanel.append(title,UI.field("按中文名搜索",search),UI.button("搜索",submit),list,nav,UI.button("关闭装备搜索",()=>closeSearch("button")));
    if(window.AppSurfaceKernel){searchSurface=AppSurfaceKernel.create({surface:searchPanel,trigger:opener,returnFocus:opener,kind:"drawer",ariaLabel:"装备搜索",initialFocus:search,
      onClosed:(_reason,node)=>{resetSearch();if(searchSurface===node)searchSurface=null;}});
      if(!searchSurface){resetSearch();searchPanel.hidden=true;feedback.textContent="当前还有一项操作需要先处理，装备搜索尚未打开。";return;}
    }else search.focus?.();
    if(kind==="weapon"&&!weaponType&&actor.db_character_id){
      list.append(UI.el("p","正在核对角色武器类型…"));try{const response=await api("character_detail",actor.db_character_id,line);
        if(!current()||activeSearch!==session||searchPanel.hidden)return;
        if(response.code||!response.data.character?.weapon_type){list.innerHTML="";list.append(UI.el("p",response.message||"角色武器类型尚未确认，请先绑定数据库角色。","eq-error"));return;}
        weaponType=response.data.character.weapon_type;
      }catch(error){if(current()){list.innerHTML="";list.append(UI.el("p",`角色资料读取失败：${error.message||error}`,"eq-error"));}return;}
    }
    await run();
  };
  const clearWeapon=()=>{actor.weapon=null;closeSearch("clear");renderWeapon();change("weapon");};
  const weaponMenuItems=[{id:"clear-weapon",label:"清除武器选择",run:clearWeapon,danger:true}];
  let weaponChoice;weaponChoice=UI.button("选择或更换武器",()=>openSearch("weapon",0,weaponChoice));
  const weaponMore=overflow("武器更多操作",weaponMenuItems);weaponButtons.append(weaponChoice,weaponMore);
  weaponCard.append(weaponName,weaponButtons,weaponControls,weaponDescription);
  bindCardMenu(weaponCard,"武器操作",weaponMore,weaponMenuItems);
  const setModeSelect=UI.select([{value:"4",label:"四件套"},{value:"2+2",label:"两件 + 两件"},{value:"none",label:"无套装效果"}],setMode,value=>{
    setMode=value;invalidatePresence();actor.artifact_sets=value==="none"?[]:actor.artifact_sets.slice(0,value==="4"?1:2).map(s=>({...s,count:value==="4"?4:2}));closeSearch();renderSets();change("sets");});
  const clearArtifacts=()=>{actor.artifact_presence=Object.fromEntries(EW.slots.map(([slot])=>[slot,"empty"]));actor.artifact_sets=[];setMode="none";setModeSelect.value="none";closeSearch("clear");renderSets();renderPresence();change("sets");};
  const artifactMenuItems=[{id:"clear-artifacts",label:"确认五槽均未装备（清除套装）",run:clearArtifacts,danger:true}],artifactMore=overflow("套装更多操作",artifactMenuItems);
  setCard.append(UI.el("p","只选择套装组合，不分配五件圣遗物的主副词条。","ew-note"),UI.field("套装组合",setModeSelect),setControls,setDescription,presenceNote,presenceControls,
    artifactMore);
  bindCardMenu(setCard,"圣遗物套装操作",artifactMore,artifactMenuItems);
  renderPresence();
  gear.append(weaponCard,setCard,searchPanel);
  const primary=UI.el("div",null,"ew-grid ew-panel-grid");
  for(const [key,label,pct] of EW.panelFields)primary.append(numeric(label,actor.panel[key]==null?null:actor.panel[key]*(pct?100:1),v=>{actor.panel[key]=v==null?null:v/(pct?100:1);change("panel");}));
  const bonuses=UI.el("div",null,"ew-grid ew-panel-grid");
  for(const [key,label] of EW.elements)bonuses.append(numeric(`${label}伤害加成 %`,actor.panel.dmg_bonus[key]==null?null:actor.panel.dmg_bonus[key]*100,v=>{if(v==null)delete actor.panel.dmg_bonus[key];else actor.panel.dmg_bonus[key]=v/100;change("panel");}));
  const baseCard=UI.el("details",null,"ew-card ew-basis-card"),bases=UI.el("div",null,"ew-grid"),basisStatus=UI.el("div",null,"ew-basis-status");basisStatus.setAttribute("role","status");
  baseCard.append(UI.el("summary","基础属性 · 与成品面板分开"));
  const basisMode=UI.select([{value:"database",label:"自动读取数据库基础值"},{value:"manual",label:"手动填写基础值"}],actor.panel_basis_mode,value=>{
    if(!current())return;actor.panel_basis_mode=value;refreshBasis("mode");change("basis_mode");});
  baseCard.append(UI.field("基础值来源",basisMode),UI.el("p","基础生命与防御是角色白值；基础攻击 = 角色白攻 + 武器白攻。不会替代上面的成品面板。","ew-note"));
  for(const [key,label] of EW.baseFields)bases.append(numeric(label,actor.panel[key],v=>{actor.panel[key]=v;change("panel");},{onCreate:input=>baseInputs.set(key,input)}));
  baseCard.append(bases,basisStatus,UI.button("重新读取基础值",()=>refreshBasis("retry")));
  const displayNumber=value=>typeof value==="number"&&Number.isFinite(value)?value.toLocaleString("zh-CN",{maximumFractionDigits:3}):"未知";
  const paintBasis=()=>{
    for(const [key,input] of baseInputs){input.readOnly=actor.panel_basis_mode==="database";input.value=actor.panel[key]??"";}
    basisStatus.innerHTML="";
    if(actor.panel_basis_mode==="manual")basisStatus.append(UI.el("p",basisMessage||"手动基础值：请填写对应角色等级及所选武器的白值；不会自动校验或补算。","ew-note"));
    else{
      basisStatus.append(UI.el("p",basisPending?"正在读取当前等级与武器的基础属性，读取完成前请勿应用。":basisMessage||"数据库基础属性已对齐到草稿；应用或保存后才改动当前方案。","ew-note"));
      if(basisData){
        basisStatus.append(UI.el("p",`角色白攻 ${displayNumber(basisData.character_base_atk)} + 武器白攻 ${displayNumber(basisData.weapon_contribution?.base_atk)} = 基础攻击 ${displayNumber(actor.panel.base_atk)}`),
          UI.el("p",`角色 ${actor.level??"未填"} 级 · 基础生命 ${displayNumber(basisData.character_base_hp)} · 基础防御 ${displayNumber(basisData.character_base_def)}`));
        for(const issue of basisData.missing_inputs||[])basisStatus.append(UI.el("p",issue.message||"基础资料不完整，请补充信息。","ew-note"));
        if(basisData.source_refs?.length)basisStatus.append(UI.details("数据库基础属性来源",basisData.source_refs));
      }
      const changes=EW.baseFields.filter(([key])=>originalBases[key]!== (actor.panel[key]??null));
      if(!basisPending&&changes.length)basisStatus.append(UI.el("p",`草稿基础值变化：${changes.map(([key,label])=>`${label} ${displayNumber(originalBases[key])} → ${displayNumber(actor.panel[key])}`).join("；")}。成品面板未改动。`,"ew-basis-diff"));
    }
  };
  refreshBasis=async(reason="initial")=>{
    const request=++basisRequest,key=sourceKey();basisKey=key;basisData=null;basisPending=false;basisAttempted=false;
    if(actor.panel_basis_mode==="manual"){
      if(reason==="source"){for(const [name] of EW.baseFields)actor.panel[name]=null;basisMessage="角色等级或武器白值来源已改变，请重新填写手动基础值；旧来源数值已清空。";}
      else basisMessage="手动基础值：请填写对应角色等级及所选武器的白值；不会自动校验或补算。";
      paintBasis();return;
    }
    if(actor.character_ref?.kind==="provisional"||actor.weapon?.entity_ref?.kind==="provisional"){
      basisAttempted=true;
      basisMessage="当前基础值涉及仅限本方案的临时资料。编辑器不会把它伪装成数据库值；由 Agent 完成数据库核对和来源授权后，引擎会校验并填入或拒绝。";
      paintBasis();change("basis");return;
    }
    for(const [name,label] of EW.baseFields){actor.panel[name]=null;invalid.delete(label);baseInputs.get(name)?.setAttribute("aria-invalid","false");}
    if(!actor.db_character_id||actor.level==null){basisAttempted=true;basisMessage="请绑定数据库角色并填写等级，或选择“手动填写基础值”。";paintBasis();change("basis");return;}
    basisPending=true;basisMessage="";paintBasis();change("basis");
    try{
      const response=await api("character_panel_basis",actor.db_character_id,actor.level,line,UI.clone(actor.weapon||null));
      if(!current()||request!==basisRequest||key!==sourceKey()||actor.panel_basis_mode!=="database")return;
      basisPending=false;basisAttempted=true;
      if(response.code)basisMessage=response.message||"基础属性读取失败，可重新读取或切换手动填写。";
      else if(lockedDataVersion&&!response.data.data_version)basisMessage="无法核验本次基础属性的来源快照，基础值未填入。请核对数据库资料，或明确使用手动基础值。";
      else if(lockedDataVersion&&JSON.stringify(lockedDataVersion)!==JSON.stringify(response.data.data_version))basisMessage="当前方案与数据库资料版本不同，基础值未填入。请先查看并应用新资料，或明确使用手动基础值。";
      else{
        basisData=response.data;
        for(const [name] of EW.baseFields){const value=basisData.panel_base?.[name];actor.panel[name]=typeof value==="number"&&Number.isFinite(value)?value:null;}
        basisMessage=basisData.state==="ready"?"数据库基础属性已对齐到草稿；应用或保存后才改动当前方案。":"已读取可确认的基础属性，缺项保留未知；成品面板仍由用户填写。";
      }
      paintBasis();change("basis",{panelBasis:basisData});
    }catch(error){if(!current()||request!==basisRequest||key!==sourceKey())return;basisPending=false;basisAttempted=true;basisMessage=`基础属性读取失败：${error.message||error}。可重新读取或切换手动填写。`;paintBasis();change("basis");}
  };
  const advanced=UI.el("details",null,"ew-extra");advanced.append(UI.el("summary","面板来源与备注"),UI.field("面板来源与装备备注",textInput(actor.notes,v=>{actor.notes=v;change("notes");},true)));
  panel.append(primary,UI.el("p",native?"百分比直接输入显示值，例如暴击率 66、充能 140。原方案未填写的可选属性沿用既有计算口径；明确没有加成时请填 0。已知数值清空后须重新填写才能应用。":"百分比直接输入显示值，例如暴击率 66、充能 140。留空表示未知，不会按零计算。","ew-note"),UI.el("h4","伤害加成"),bonuses,baseCard,advanced);
  identitySection.append(identity);layout.append(gear,panel);root.append(identitySection,note,confirmation,layout,feedback);container.append(root);renderWeapon();renderSets();updateConfirmation();
  // Loading labels and descriptions must not rebuild or overwrite focused inputs.
  const hydrate=async(kind,id)=>{try{const response=await api(kind==="weapon"?"weapon_detail":"artifact_set_detail",id,line);if(!current())return;
    if(response.code){feedback.textContent="部分已选装备的说明未能读取，原选择与面板仍保留。";return;}details.set(`${kind}:${id}`,response.data);
    if(kind==="weapon"&&actor.weapon?.weapon_id===id)describeWeapon();else if(kind==="set")updateSetChoices();
  }catch{if(current())feedback.textContent="部分装备说明读取失败，原草稿已保留。";}};
  if(actor.weapon?.weapon_id)hydrate("weapon",actor.weapon.weapon_id);actor.artifact_sets.forEach(s=>{if(s.set_id)hydrate("set",s.set_id);});
  const cached=options.basisSnapshot;
  if(actor.panel_basis_mode==="database"&&cached?.key===sourceKey()&&cached.attempted&&
      JSON.stringify(cached.values)===JSON.stringify(Object.fromEntries(EW.baseFields.map(([key])=>[key,actor.panel[key]??null])))&&
      JSON.stringify(lockedDataVersion??null)===JSON.stringify(cached.lockedDataVersion??null)){
    basisKey=sourceKey();basisAttempted=true;basisData=UI.clone(cached.data);basisMessage=cached.message;paintBasis();
  }else refreshBasis();
  return {
    draft:()=>UI.clone(actor),dirty:()=>JSON.stringify(actor)!==original,
    basisSnapshot:()=>basisAttempted&&!basisPending&&actor.panel_basis_mode==="database"?{key:sourceKey(),attempted:true,data:UI.clone(basisData),message:basisMessage,
      lockedDataVersion:UI.clone(lockedDataVersion??null),values:Object.fromEntries(EW.baseFields.map(([key])=>[key,actor.panel[key]??null]))}:null,
    validate:()=>{const errors=[...invalid.values()];if(actor.level==null)errors.push("请填写角色等级。");if(basisPending)errors.push("基础属性仍在读取，请等待完成后再应用。");
      if(gearChanged()&&!confirmed)errors.push("装备已变更，请核对当前面板并勾选确认，再应用。");
      const w=actor.weapon,detail=w&&details.get(`weapon:${w.weapon_id}`),states=detail?.weapon?.available_states||[];
      if(w&&w.level!=null&&w.ascension!=null&&states.length&&!states.some(s=>s.ascension===w.ascension&&w.level>=s.min_level&&w.level<=s.max_level))errors.push("武器等级与突破阶段不匹配，请按资料中的范围调整。");
      if(w?.refinement!=null&&(w.refinements||[]).some(row=>row.refinement!=null&&row.refinement!==w.refinement))errors.push("武器整体精炼与效果槽精炼不一致，请重新选择整体精炼。");
      if(actor.artifact_presence&&(actor.artifact_sets||[]).reduce((n,row)=>n+row.count,0)>Object.values(actor.artifact_presence).filter(v=>v!=="empty").length)errors.push("套装件数与明确未装备的槽位冲突，请核对五槽状态。");
      return errors;},
    destroy:()=>{alive=false;basisRequest++;closeSearch("destroy",{restoreFocus:false});for(const dispose of menuDisposers.splice(0))dispose();},
  };
};
