import {afterEach,describe,expect,it,vi} from "vitest";
import {flushSync} from "react-dom";
import {createRoot} from "react-dom/client";
import type {Context} from "@deepseek-ai/cordis";
import {createWindowSessionController,WindowDraftRestoreError} from "./app-window-session-controller";

vi.mock("@deepseek-ai/dsh-client-ui-primitives",()=>({Button:({children,size:_size,variant:_variant,...props}:any)=><button {...props}>{children}</button>}));

function inputsFixture(){
  const values=new Map<'a'|'b',{text:string;references:never[]}>([['a',{text:'Original A',references:[]}],['b',{text:'Original B',references:[]}]]);
  const subscribers=new Set<()=>void>(),blocks=new Map<string,unknown>();
  const inputs=new Map([...values.keys()].map(id=>[id,{
    get draftSnapshot(){return values.get(id);},
    state:{getSnapshot:()=>({phase:'plain',draftRev:0,attachmentIds:[]}),subscribe:(listener:()=>void)=>{subscribers.add(listener);return()=>subscribers.delete(listener);}},
    setDraft:vi.fn((value:any)=>{values.set(id,structuredClone(value));for(const listener of subscribers)listener();}),
    removeAttachment:vi.fn(),addAttachments:vi.fn(()=>true),
  }]));
  const effects:(()=>void)[]=[],cleanups:(()=>void)[]=[];
  const ctx={sessions:{list:{getSnapshot:()=>({byId:{a:{retainedBy:{mainView:1}},b:{retainedBy:{}}}})},
    retain:(id:string)=>({ready:Promise.resolve(),binding:{ctx:{id}},release:vi.fn()}),binding:(id:string)=>({ctx:{id}})},
    conversation:{input:{for:({id}:{id:'a'|'b'})=>inputs.get(id)},blocks:{storeFor:(id:string)=>({getSnapshot:()=>blocks.get(id)}),set:(id:string,value:unknown)=>blocks.set(id,value)},resolveDraftAttachments:()=>[],createDrafts:()=>[],releaseDraftAttachment:vi.fn()},
    uiWorkspace:{openSession:vi.fn()},layout:{selectPanel:vi.fn()},
    slots:{inject:(_name:string,cb:()=>void)=>cb(),register:vi.fn()},effect:(cb:()=>()=>void)=>effects.push(()=>cleanups.push(cb())),
  } as unknown as Context;
  return {ctx,inputs,values,blocks,effects,cleanups,notify:()=>{for(const listener of subscribers)listener();}};
}
const transfer={session_id:'a',drafts:{a:{semantic:{text:'Incoming A',references:[]},files:[]},b:{semantic:{text:'Incoming B',references:[]},files:[]}}};
const cleanupCallbacks:(()=>void)[]=[];
afterEach(()=>{for(const cleanup of cleanupCallbacks.splice(0).reverse())cleanup();vi.useRealTimers();delete document.body.dataset.agentWindow;delete (window as any).pywebview;});

describe('window draft transfer is atomic across sessions',()=>{
  it('rolls back earlier drafts when a later attachment restoration rejects',async()=>{
    const f=inputsFixture(),controller=createWindowSessionController(f.ctx,write=>write());cleanupCallbacks.push(()=>controller.dispose());
    f.inputs.get('b')!.addAttachments.mockReturnValueOnce(false);
    await expect(controller.adopt(transfer,{generation:10})).rejects.toMatchObject({cancelSafe:true});
    expect(f.values.get('a')!.text).toBe('Original A');expect(f.values.get('b')!.text).toBe('Original B');
    expect(f.ctx.uiWorkspace.openSession).not.toHaveBeenCalled();
    await controller.adopt(transfer,{generation:10});expect(f.values.get('a')!.text).toBe('Incoming A');expect(f.values.get('b')!.text).toBe('Incoming B');
  });
  it('retains the first baseline after a rollback itself fails and a retry fails again',async()=>{
    const f=inputsFixture(),controller=createWindowSessionController(f.ctx,write=>write());cleanupCallbacks.push(()=>controller.dispose());
    let rejectRestoreA=true;
    const inputA=f.inputs.get('a')!,setA=inputA.setDraft.getMockImplementation()!;
    inputA.setDraft.mockImplementation((value:any)=>{if(value.text==='Original A'&&rejectRestoreA)throw Error('restore temporarily unavailable');setA(value);});
    f.inputs.get('b')!.addAttachments.mockReturnValueOnce(false);
    const first=await controller.adopt(transfer,{generation:10}).catch(error=>error);
    expect(first).toBeInstanceOf(WindowDraftRestoreError);expect(first.cancelSafe).toBe(false);
    expect(f.values.get('a')!.text).toBe('Incoming A');
    rejectRestoreA=false;f.inputs.get('b')!.addAttachments.mockReturnValueOnce(false);
    await expect(controller.adopt(transfer,{generation:10})).rejects.toMatchObject({cancelSafe:true});
    expect(f.values.get('a')!.text).toBe('Original A');expect(f.values.get('b')!.text).toBe('Original B');
  });
  it('never creates input or navigates after retirement while waiting for the official session lease',async()=>{
    const f=inputsFixture();let ready!:()=>void;const pending=new Promise<void>(resolve=>{ready=resolve;});
    vi.spyOn(f.ctx.sessions,'retain').mockImplementation((id:any)=>({ready:pending,binding:{ctx:{id}},release:vi.fn()}) as never);
    const controller=createWindowSessionController(f.ctx,write=>write());
    const adopted=controller.adopt(transfer,{generation:10}).catch(error=>error);controller.dispose();ready();
    expect(await adopted).toBeInstanceOf(WindowDraftRestoreError);
    expect(f.inputs.get('a')!.setDraft).not.toHaveBeenCalled();expect(f.ctx.uiWorkspace.openSession).not.toHaveBeenCalled();
  });
});

async function pluginFixture({delayed=false,loseAck=false,child=false}={}){
  vi.useFakeTimers();vi.resetModules();
  const module=await import('./app-window-presentation');
  const f=inputsFixture();if(child)document.body.dataset.agentWindow='detached';
  let state:any={role:child?'detached':'main',generation:4,presentation:child?'detached':'returning',return_source_id:'child-1',handoff:child?null:{session_id:'a',draft:{semantic:{text:'Incoming A',references:[]},files:[]}},commands:[]};
  let defer=delayed;
  const input=f.inputs.get('a')!,set=input.setDraft.getMockImplementation()!;
  input.setDraft.mockImplementation((value:any)=>{
    if(value.text==='Incoming A')expect(f.blocks.get('a')).toBeDefined();
    if(value.text==='Incoming A'&&defer)return;set(value);
  });
  const api={dsh_window_poll:vi.fn(async()=>state),dsh_window_state:vi.fn(async()=>state),
    dsh_window_main_ready:vi.fn(async()=>{if(loseAck){loseAck=false;throw Error('ACK temporarily lost');}state={...state,presentation:'main',handoff:null};return state;}),
    dsh_window_cancel_return:vi.fn(async request=>{expect(request).toEqual({generation:4,source_window_id:'child-1'});state={...state,presentation:'detached',generation:5,handoff:null};return state;}),
    dsh_window_return:vi.fn(async(hand:any)=>{state={...state,presentation:'returning',generation:5,handoff:hand};return state;}),
  };
  (window as any).pywebview={api};module.createWindowPresentationPlugin().apply(f.ctx);
  for(const effect of f.effects)effect();
  cleanupCallbacks.push(()=>{for(const cleanup of f.cleanups.reverse())cleanup();});
  const node=document.createElement('div');document.body.append(node);const view=createRoot(node);
  flushSync(()=>view.render(<module.WindowRecoveryControl/>));cleanupCallbacks.push(()=>{flushSync(()=>view.unmount());node.remove();});
  await vi.advanceTimersByTimeAsync(1);
  return {...f,api,node,input,module,view,release:()=>{defer=false;},setState:(value:any)=>{state=value;},state:()=>state};
}

describe('main window restoration fence and recovery controls',()=>{
  it('fences a cold main composer before waiting and restores originals before allowing cancellation',async()=>{
    const f=await pluginFixture({delayed:true});
    expect(f.blocks.get('a')).toBeDefined();expect(f.api.dsh_window_main_ready).not.toHaveBeenCalled();
    expect(f.node.textContent).not.toContain('取消返回，继续');
    await vi.advanceTimersByTimeAsync(10001);flushSync(()=>{});
    expect(f.values.get('a')!.text).toBe('Original A');expect(f.node.textContent).toContain('取消返回，继续在独立窗口');
    const attempts=f.input.setDraft.mock.calls.length;
    await vi.advanceTimersByTimeAsync(1501);expect(f.input.setDraft.mock.calls.length).toBe(attempts);
    flushSync(()=>[...f.node.querySelectorAll('button')].find(button=>button.textContent==='取消返回，继续在独立窗口')!.click());
    await vi.advanceTimersByTimeAsync(1);expect(f.api.dsh_window_cancel_return).toHaveBeenCalledOnce();
    expect(f.state().presentation).toBe('detached');expect(f.blocks.get('a')).toBeDefined();expect(f.values.get('a')!.text).toBe('Original A');
  });
  it('retries a failed restore when explicitly requested, then sends the ACK',async()=>{
    const f=await pluginFixture({delayed:true});await vi.advanceTimersByTimeAsync(10001);flushSync(()=>{});
    f.release();flushSync(()=>[...f.node.querySelectorAll('button')].find(button=>button.textContent==='重试恢复主窗口')!.click());
    await vi.advanceTimersByTimeAsync(751);expect(f.api.dsh_window_main_ready).toHaveBeenCalledOnce();expect(f.values.get('a')!.text).toBe('Incoming A');expect(f.blocks.get('a')).toBeUndefined();
  });
  it('lost ACK only retries the acknowledgement, without restoring input again or offering cancellation',async()=>{
    const f=await pluginFixture({loseAck:true});expect(f.api.dsh_window_main_ready).toHaveBeenCalledOnce();
    expect(f.node.textContent).not.toContain('取消返回，继续');const writes=f.input.setDraft.mock.calls.length;
    await vi.advanceTimersByTimeAsync(751);expect(f.api.dsh_window_main_ready).toHaveBeenCalledTimes(2);expect(f.input.setDraft.mock.calls.length).toBe(writes);expect(f.blocks.get('a')).toBeUndefined();
  });
  it('releases a source capture fence when a failed return is cancelled back to that source',async()=>{
    const f=await pluginFixture({child:true});
    await window.GenshinWindowPresentation!.requestReturn();expect(f.blocks.get('a')).toBeDefined();
    f.setState({...f.state(),generation:6,presentation:'detached',handoff:null});
    await vi.advanceTimersByTimeAsync(751);expect(f.blocks.get('a')).toBeUndefined();expect(f.values.get('a')!.text).toBe('Original A');
  });
  it('does not offer cancellation if the destination rollback is incomplete',async()=>{
    const f=await pluginFixture({delayed:true});
    f.input.setDraft.mockImplementation(()=>{throw Error('target input unavailable');});
    await vi.advanceTimersByTimeAsync(10001);flushSync(()=>{});
    expect(f.node.textContent).toContain('重试恢复主窗口');expect(f.node.textContent).not.toContain('取消返回，继续');
    expect(f.api.dsh_window_main_ready).not.toHaveBeenCalled();expect(f.state().presentation).toBe('returning');
  });
  it('keeps the handoff and retry action when the source window cannot be restored',async()=>{
    const f=await pluginFixture({delayed:true});await vi.advanceTimersByTimeAsync(10001);flushSync(()=>{});
    f.api.dsh_window_cancel_return.mockRejectedValueOnce(Error('source window unavailable'));
    flushSync(()=>[...f.node.querySelectorAll('button')].find(button=>button.textContent==='取消返回，继续在独立窗口')!.click());
    await vi.advanceTimersByTimeAsync(1);flushSync(()=>{});
    expect(f.state().presentation).toBe('returning');expect(f.node.textContent).toContain('source window unavailable');
    expect(f.values.get('a')!.text).toBe('Original A');expect(f.blocks.get('a')).toBeDefined();
    expect([...f.node.querySelectorAll('button')].find(button=>button.textContent==='重试恢复主窗口')!.disabled).toBe(false);
  });
  it('serializes retry and cancel across duplicate visible recovery controls',async()=>{
    const f=await pluginFixture({delayed:true});await vi.advanceTimersByTimeAsync(10001);
    flushSync(()=>f.view.render(<><f.module.WindowRecoveryControl/><f.module.WindowRecoveryControl/></>));
    let finish!:(value:any)=>void;f.api.dsh_window_cancel_return.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
    const writes=f.input.setDraft.mock.calls.length;
    flushSync(()=>[...f.node.querySelectorAll('button')].find(button=>button.textContent==='取消返回，继续在独立窗口')!.click());
    const retryButtons=[...f.node.querySelectorAll('button')].filter(button=>button.textContent==='重试恢复主窗口');
    expect(retryButtons).toHaveLength(2);expect(retryButtons.every(button=>button.disabled)).toBe(true);
    for(const button of retryButtons)button.click();await vi.advanceTimersByTimeAsync(1501);
    expect(f.input.setDraft.mock.calls.length).toBe(writes);expect(f.api.dsh_window_cancel_return).toHaveBeenCalledOnce();
    finish({...f.state(),generation:5,presentation:'detached',handoff:null});await vi.advanceTimersByTimeAsync(1);
  });
  it('does not acknowledge or navigate when the renderer retires during semantic restoration',async()=>{
    const f=await pluginFixture({delayed:true});
    for(const cleanup of f.cleanups.splice(0).reverse())cleanup();
    f.values.set('a',{text:'Incoming A',references:[]});f.notify();
    await vi.advanceTimersByTimeAsync(1);
    expect(f.api.dsh_window_main_ready).not.toHaveBeenCalled();expect(f.ctx.uiWorkspace.openSession).not.toHaveBeenCalled();
  });
  it('preserves a Host-reported error even if its presentation has settled',async()=>{
    const f=await pluginFixture({delayed:true});
    flushSync(()=>f.view.render(<f.module.WindowControls ctx={f.ctx} props={{sessionId:'a',inputActions:{persistDraft(){}}} as any}/>));
    await vi.advanceTimersByTimeAsync(10001);flushSync(()=>{});
    expect(f.node.textContent).toContain('引用或命令尚未完整恢复');
    f.api.dsh_window_main_ready.mockImplementationOnce(async()=>{const next={...f.state(),presentation:'main',handoff:null,error:'Host result still needs attention'};f.setState(next);return next;});
    f.release();flushSync(()=>[...f.node.querySelectorAll('button')].find(button=>button.textContent==='重试恢复主窗口')!.click());
    await vi.advanceTimersByTimeAsync(751);flushSync(()=>{});await vi.advanceTimersByTimeAsync(1);flushSync(()=>{});
    expect(f.node.textContent).toContain('Host result still needs attention');
    await vi.advanceTimersByTimeAsync(1501);expect(f.node.textContent).toContain('Host result still needs attention');
  });
  it('clears only a previous handoff error after success, keeping a fresh local failure through later stable polls',async()=>{
    const f=await pluginFixture({delayed:true});
    flushSync(()=>f.view.render(<f.module.WindowControls ctx={f.ctx} props={{sessionId:'a',inputActions:{persistDraft(){}}} as any}/>));
    await vi.advanceTimersByTimeAsync(10001);flushSync(()=>{});
    expect(f.node.textContent).toContain('引用或命令尚未完整恢复');
    f.release();flushSync(()=>[...f.node.querySelectorAll('button')].find(button=>button.textContent==='重试恢复主窗口')!.click());
    await vi.advanceTimersByTimeAsync(751);flushSync(()=>{});await vi.advanceTimersByTimeAsync(1);flushSync(()=>{});
    expect(f.state().presentation).toBe('main');expect(f.node.textContent).not.toContain('引用或命令尚未完整恢复');
    (f.api as any).dsh_window_open=vi.fn(async()=>{throw Error('new opening rejection');});
    flushSync(()=>[...f.node.querySelectorAll('button')].find(button=>button.textContent==='独立窗口')!.click());
    await vi.advanceTimersByTimeAsync(1);flushSync(()=>{});
    expect(f.node.textContent).toContain('new opening rejection');
    await vi.advanceTimersByTimeAsync(1501);expect(f.node.textContent).toContain('new opening rejection');
  });
  it('keeps a new locally rejected operation during stable main polling',async()=>{
    const f=await pluginFixture();
    flushSync(()=>f.view.render(<f.module.WindowControls ctx={f.ctx} props={{sessionId:'a',inputActions:{persistDraft(){}}} as any}/>));
    (f.api as any).dsh_window_open=vi.fn(async()=>{throw Error('new opening rejection');});
    flushSync(()=>[...f.node.querySelectorAll('button')].find(button=>button.textContent==='独立窗口')!.click());
    await vi.advanceTimersByTimeAsync(1);flushSync(()=>{});
    expect(f.node.textContent).toContain('new opening rejection');
    await vi.advanceTimersByTimeAsync(1501);expect(f.node.textContent).toContain('new opening rejection');
  });

});
