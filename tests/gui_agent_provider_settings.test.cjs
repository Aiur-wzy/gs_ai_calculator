const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),vm=require('node:vm'),R=require('./helpers/gui_runtime.cjs');

function harness(overrides={}){
  const state={connection:'disconnected',busy:null,candidates:[],models:[],cliId:'',connectedCliId:null,scanEpoch:0,scan:null,providers:[],model:'',effort:'',capabilities:{},runtimeStatusCursor:0,runtimeStatusGeneration:0,runtimeStatusMonitor:0,resumeSupervisor:null,sessions:new Map(),request:0,connectionGeneration:0,hostUi:{},closing:false,budget:null,budgetDraft:null,budgetLoading:false,budgetSaving:false,budgetEpoch:0,dshSettings:null,dshSettingsDraft:'',dshSettingsLoading:false,dshSettingsSaving:false,dshSettingsEpoch:0,...overrides.state};
  const nodes={...overrides.nodes},calls=[],notices=[];let refreshes=0,transportInvalidations=0;
  const world={console,Date,window:null};world.window=world;world.performance={now:()=>0};world.setTimeout=()=>0;world.setInterval=()=>1;world.addEventListener=()=>{};
  vm.createContext(world);R.loadWebFile(world,path.join(__dirname,'../src/genshin_app/gui/web/agent-provider-settings.js'));
  const api=overrides.api||((name,...args)=>{calls.push([name,...args]);return Promise.resolve({code:0,data:{}});});
  const controller=world.AgentProviderSettings.create({state,nodes,ui:{el(){return{};},button(){return{};},input(){return{};},field(){return{append(){}};}},window:world,document:{visibilityState:'visible',addEventListener(){}},api,clone:value=>JSON.parse(JSON.stringify(value)),stableErrorCode:value=>typeof value==='string'?value:'',errorText:(_value,fallback)=>fallback,notice:(message,error=false)=>notices.push([message,error]),active:()=>null,selected:()=>overrides.selected||null,readModel:session=>session,isActive:()=>false,schedule:()=>{},refresh:()=>{refreshes++;},workbenchProjection:()=>({axes:{runtime_state:'ready'}}),text:value=>JSON.stringify(value),transport:()=>({invalidateAll(){transportInvalidations++;}})});
  return{world,state,nodes,calls,notices,controller,get refreshes(){return refreshes;},get transportInvalidations(){return transportInvalidations;}};
}

test('provider settings is an injected controller and does not depend on window.AC',()=>{
  const h=harness({state:{connection:'ready',cliId:'deepseek-api',connectedCliId:'deepseek-api',candidates:[{id:'deepseek-api',supported:true}],models:[{model:'deepseek-reasoner',supportedReasoningEfforts:[{reasoningEffort:'high'},{reasoningEffort:'max'}],defaultReasoningEffort:'high'},{model:'deepseek-chat',supportedReasoningEfforts:[],defaultReasoningEffort:null}]}});
  assert.equal(h.world.AC,undefined);
  assert.deepEqual([...h.controller.efforts(h.state.models[0])],['high','max']);
  assert.equal(h.controller.validEffort(h.state.models[0],'max'),true);
  assert.equal(h.controller.defaultEffort(h.state.models[0]),'high');
  assert.equal(h.controller.noReasoningModel(h.state.models[1]),true);
  assert.equal(h.controller.validEffort(h.state.models[1],null),true);
});

test('runtime status rejects an older resume generation before mutating accepted facts',()=>{
  const h=harness({state:{connection:'ready',cliId:'deepseek-api',connectedCliId:'deepseek-api',candidates:[{id:'deepseek-api',supported:true}],runtimeStatusCursor:5,runtimeStatusGeneration:2}});
  assert.equal(h.controller.applyRuntimeStatus({contract:'native-runtime-status-v1',next_seq:6,subscription_generation:1,events:[{phase:'ready',runtime_state:'ready',system_resume_generation:1}]}),false);
  assert.equal(h.state.runtimeStatusCursor,5);
  assert.equal(h.refreshes,0);
  assert.equal(h.controller.applyRuntimeStatus({contract:'native-runtime-status-v1',next_seq:6,subscription_generation:2,events:[{phase:'ready',runtime_state:'ready',runtime_health:'healthy',system_resume_generation:2}]}),true);
  assert.equal(h.state.runtimeStatusCursor,6);
  assert.equal(h.state.hostUi.runtime_health,'healthy');
  assert.equal(h.refreshes,1);
});

test('budget save sends only sparse reviewed overrides',async()=>{
  let request=null;
  const snapshot={generation:3,policy_id:'native-turn-budget',policy_version:2,follows_defaults:false,defaults:{provider_steps:20,web_search_calls:8},effective:{provider_steps:24,web_search_calls:8},overrides:{provider_steps:24},limits:{provider_steps:{adjustable:true,min:1,max:64},web_search_calls:{adjustable:true,min:0,max:32}}};
  const h=harness({state:{budget:snapshot,budgetDraft:{provider_steps:24,web_search_calls:8}},api:async(name,...args)=>{request=[name,...args];return{code:0,data:snapshot};}});
  await h.controller.saveBudget();
  assert.equal(JSON.stringify(request),JSON.stringify(['agent_update_budget_settings',{provider_steps:24},3]));
  assert.equal(h.state.budgetSaving,false);
  assert.match(h.notices.at(-1)[0],/下一次用户消息/);
});

test('DSH settings keep sparse overrides and apply to the next runtime',async()=>{
  let request=null;
  const snapshot={generation:2,policy_version:1,follows_defaults:false,apply_scope:'next_runtime',defaults:{compaction:{thresholdRatio:.8,headroomTokens:65536}},effective:{compaction:{thresholdRatio:.8,headroomTokens:70000,maxTokens:70000,modelPolicies:[]}},overrides:{compaction:{headroomTokens:70000}},default_sources:{compaction:{thresholdRatio:{version:'0.2.1-rc.5'}}}};
  const h=harness({state:{dshSettings:snapshot,dshSettingsDraft:JSON.stringify(snapshot.overrides)},api:async(name,...args)=>{request=[name,...args];return{code:0,data:snapshot};}});
  await h.controller.saveDshSettings();
  assert.equal(JSON.stringify(request),JSON.stringify(['agent_update_dsh_settings',{compaction:{headroomTokens:70000}},2]));
  assert.match(h.notices.at(-1)[0],/重启App/);
});

test('model capacity uses installed route capacity and DSH dynamic threshold',()=>{
  const h=harness({state:{connection:'ready',cliId:'deepseek-api',connectedCliId:'deepseek-api',candidates:[{id:'deepseek-api',supported:true}],model:'deepseek-flash',models:[{model:'deepseek-flash',contextWindow:1000000,maxTokens:65536}],budget:{effective:{per_request_output_tokens:256000}},dshSettings:{effective:{compaction:{thresholdRatio:.8,headroomTokens:65536,retainRatio:.16,modelPolicies:[]}}}}});
  assert.equal(JSON.stringify(h.controller.modelCompactionSpec()),JSON.stringify({
    model:h.state.models[0],context:1000000,reserved:65536,headroom:65536,ratio:.8,messageBudget:934464,pressureBudget:868928,threshold:800000,retainTokens:149514,valid:true,
  }));
});

test('model capacity uses a smaller App output budget before the model cap',()=>{
  const h=harness({state:{connection:'ready',cliId:'deepseek-api',connectedCliId:'deepseek-api',candidates:[{id:'deepseek-api',supported:true}],model:'deepseek-flash',models:[{model:'deepseek-flash',contextWindow:200000,maxTokens:65536}],budget:{effective:{per_request_output_tokens:32768}},dshSettings:{effective:{compaction:{thresholdRatio:.8,headroomTokens:65536,retainRatio:.16,modelPolicies:[]}}}}});
  assert.equal(JSON.stringify(h.controller.modelCompactionSpec()),JSON.stringify({
    model:h.state.models[0],context:200000,reserved:32768,headroom:65536,ratio:.8,messageBudget:167232,pressureBudget:101696,threshold:101696,retainTokens:26757,valid:true,
  }));
});

test('model capacity rejects a top-level retention that exceeds the routed threshold',()=>{
  const h=harness({state:{connection:'ready',cliId:'deepseek-api',connectedCliId:'deepseek-api',candidates:[{id:'deepseek-api',supported:true}],model:'deepseek-flash',models:[{model:'deepseek-flash',contextWindow:200000,maxTokens:65536}],budget:{effective:{per_request_output_tokens:256000}},dshSettings:{effective:{compaction:{thresholdRatio:.8,headroomTokens:120000,retainRatio:.16,modelPolicies:[]}}}}});
  const spec=h.controller.modelCompactionSpec();
  assert.equal(spec.threshold,14464);
  assert.equal(spec.retainTokens,21514);
  assert.equal(spec.valid,false);
});

test('model capacity applies exact-model retention before validating the threshold',()=>{
  const h=harness({state:{connection:'ready',cliId:'deepseek-api',connectedCliId:'deepseek-api',candidates:[{id:'deepseek-api',supported:true}],model:'deepseek-flash',models:[{model:'deepseek-flash',contextWindow:200000,maxTokens:65536}],budget:{effective:{per_request_output_tokens:32768}},dshSettings:{effective:{compaction:{thresholdRatio:.8,headroomTokens:65536,retainRatio:.16,modelPolicies:[{provider:'deepseek-official',model:'deepseek-flash',headroomTokens:100000,retainTokens:70000}]}}}}});
  const spec=h.controller.modelCompactionSpec();
  assert.equal(spec.threshold,67232);
  assert.equal(spec.retainTokens,70000);
  assert.equal(spec.valid,false);
});

test('native API key leaves the injected DOM synchronously before connect resolves',async()=>{
  let resolve,received;
  const pending=new Promise(done=>{resolve=done;});
  const h=harness({state:{candidates:[{id:'deepseek-api',kind:'native_api',supported:true}],cliId:'deepseek-api'},nodes:{cli:{value:'deepseek-api'},apiKey:{value:'secret-key'},rememberKey:{checked:true}},api:(name,request)=>{received=[name,JSON.parse(JSON.stringify(request))];return pending;}});
  const operation=h.controller.connect();
  assert.equal(h.nodes.apiKey.value,'');
  assert.deepEqual(received,['agent_connect',{id:'deepseek-api',kind:'native_api',api_key:'secret-key',remember_credential:true}]);
  resolve({code:1,reason:'account_catalog_auth'});
  await operation;
  assert.equal(h.state.connection,'disconnected');
});

test('CLI discovery stays in settings instead of replacing a native API chat notice',async()=>{
  const candidates=[
    {id:'deepseek-api',kind:'native_api',supported:true},
    {id:'codex-cli',kind:'cli',supported:true},
    {id:'other-cli',kind:'cli',supported:false},
  ];
  const h=harness({
    state:{connection:'ready',cliId:'deepseek-api',connectedCliId:'deepseek-api',candidates},
    api:async name=>{
      assert.equal(name,'agent_detect');
      return{code:0,data:{candidates,scan:{complete:false,issues:[{message:'目录未完成扫描'}]},providers:[]}};
    },
  });
  await h.controller.detect();
  assert.equal(h.controller.shouldSurfaceDiscoveryNotice(),false);
  assert.deepEqual(h.notices,[]);
  assert.match(h.controller.discoveryText(),/发现 2 个 Agent CLI 候选/);
});

test('CLI discovery remains visible when a CLI is selected or no usable provider exists',async()=>{
  const cli={id:'codex-cli',kind:'cli',supported:true};
  const selected=harness({state:{candidates:[cli],cliId:cli.id},api:async()=>({code:0,data:{candidates:[cli],scan:{complete:true,issues:[]},providers:[]}})});
  assert.equal(selected.controller.shouldSurfaceDiscoveryNotice(),true);
  await selected.controller.detect();
  assert.match(selected.notices.at(-1)[0],/发现 1 个 Agent CLI 候选/);

  const unavailable={id:'deepseek-api',kind:'native_api',supported:false};
  const blocked=harness({state:{candidates:[unavailable],cliId:'deepseek-api'},api:async()=>({code:0,data:{candidates:[unavailable],scan:{complete:true,issues:[]},providers:[]}})});
  assert.equal(blocked.controller.shouldSurfaceDiscoveryNotice(),true);
  await blocked.controller.detect();
  assert.match(blocked.notices.at(-1)[0],/未发现 Agent CLI/);
});
