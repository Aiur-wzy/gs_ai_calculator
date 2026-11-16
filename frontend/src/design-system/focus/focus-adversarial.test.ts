import {it as test,afterEach} from 'vitest';
import assert from 'node:assert/strict';
import {canReceiveFocus,focusableElements} from './dom-focus';
import {FocusManager} from './focus-manager';
import {DomSurfaceHost} from '../surfaces/dom-surface-host';
import {SurfaceCoordinator} from '../surfaces/coordinator';
import {createAppStateStore} from '@/state/store';
import {confirmedNoFileWrite} from '@/business/file-write-outcome';
const dom=(html:string)=>{document.body.innerHTML=html;return {window};};
afterEach(()=>document.body.replaceChildren());
test('focus eligibility rejects invisible ancestors but permits descendant visibility override and fixed positioning',()=>{
 const w=dom('<main><div hidden><button id=h>hidden</button></div><div style="display:none"><button id=d>d</button></div><div inert><button id=i>i</button></div><div style="visibility:hidden"><button id=v>v</button><button style="visibility:visible" id=o>o</button></div><button style="position:fixed" id=f>f</button></main>');
 try{assert.deepEqual(focusableElements(w.window.document.querySelector<HTMLElement>('main')!).map(e=>e.id),['o','f']);}finally{document.body.replaceChildren();}
});
test('closed details permits only its first summary subtree; disabled fieldset permits only first legend controls',()=>{
 const w=dom('<main><details><summary id=s>first<button id=b>b</button></summary><summary id=second>second</summary><input id=x></details><fieldset disabled><legend><button id=legend>legend</button></legend><legend><button id=legend2>legend2</button></legend><input id=field></fieldset></main>');
 try{assert.deepEqual(focusableElements(w.window.document.querySelector<HTMLElement>('main')!).map(e=>e.id),['s','b','legend']);}finally{document.body.replaceChildren();}
});
test('positive tabindex sorts ahead of zero; anchors without href and negative tabindex are not tab stops',()=>{
 const w=dom('<main><a id=n>no href</a><button id=z>zero</button><button id=three tabindex=3>three</button><a id=link href=x>link</a><button id=one tabindex=1>one</button><button id=one2 tabindex=1>one2</button><button id=neg tabindex=-1>neg</button></main>');
 try{assert.deepEqual(focusableElements(w.window.document.querySelector<HTMLElement>('main')!).map(e=>e.id),['one','one2','three','z','link']);assert.equal(canReceiveFocus(w.window.document.querySelector<HTMLElement>('#neg')),true);}finally{document.body.replaceChildren();}
});
test('invalid preferred and autofocus cannot steal initial focus; hidden return trigger falls back',()=>{
 const w=dom('<main><button id=trigger>trigger</button><button id=fallback>fallback</button><button id=external>outside the surface</button></main><section tabindex=-1><button id=hidden autofocus hidden>hidden</button><button id=disabled disabled>disabled</button><button id=first>first</button></section>'),d=w.window.document;
 const store=createAppStateStore(),coordinator=new SurfaceCoordinator(store),manager=new FocusManager(d,d.querySelector<HTMLElement>('main')!,coordinator,store);manager.attach();
 try{for(const id of ['hidden','disabled','external']){manager.bind('x',{container:d.querySelector<HTMLElement>('section')!,trigger:d.querySelector<HTMLElement>('#trigger'),fallback:d.querySelector<HTMLElement>('#fallback'),initialFocus:d.querySelector<HTMLElement>('#'+id)});assert.equal(d.activeElement?.id,'first');d.querySelector<HTMLElement>('#trigger')!.hidden=true;manager.unbind('x');assert.equal(d.activeElement?.id,'fallback');}}finally{manager.detach();document.body.replaceChildren();}
});
for(const event of ['resize','blur','visibilitychange','workspacechange','pagehide'])test(`ambient ${event} preserves owned modal draft while dismissing ephemeral`,()=>{
 const w=dom('<main><button id=t>trigger</button></main><aside id=overlay></aside>'),d=w.window.document,store=createAppStateStore(),coordinator=new SurfaceCoordinator(store),host=new DomSurfaceHost(d,d.querySelector<HTMLElement>('main')!,d.querySelector<HTMLElement>('#overlay')!,coordinator,store),closed:Array<[string,string]>=[];
 try{for(const kind of (['modal','ephemeral'] as const)){const container=d.createElement('section');container.innerHTML='<input value=unsaved>';host.open({id:kind,ownerId:'owner',container,trigger:d.querySelector<HTMLElement>('#t'),fallback:null,kind,group:kind,modal:kind==='modal',onClosed:why=>closed.push([kind,why])});}const target=['visibilitychange','workspacechange'].includes(event)?d:w.window;target.dispatchEvent(new w.window.Event(event));assert.deepEqual(coordinator.list().map(x=>x.id),['modal']);assert.equal(d.querySelector<HTMLInputElement>('#overlay input')!.value,'unsaved');assert.deepEqual(closed,[['ephemeral',event]]);host.destroy('modal','owner-destroyed');assert.deepEqual(coordinator.list(),[]);}finally{host.dispose();document.body.replaceChildren();}
});
for(const [name,response,expected] of ([
 ['no receipt',undefined,false],['success',{code:0,details:{publication_state:'not_written'}},false],
 ['unknown failure',{code:1},false],['explicit no write',{code:1,details:{publication_state:'not_written'}},true],
 ['empty side effect account',{code:1,details:{published_paths:[],unknown_paths:[]}},true],
 ['partial side effect account',{code:1,details:{published_paths:[]}},false],
 ['contradictory written state',{code:1,reason:'invalid_plan',details:{publication_state:'written'}},false],
 ['contradictory unrecognized state',{code:1,reason:'invalid_plan',details:{publication_state:'new_publisher_state'}},false],
 ['explicit null state',{code:1,reason:'invalid_plan',details:{publication_state:null}},false],
 ['explicit undefined state',{code:1,reason:'invalid_plan',details:{publication_state:undefined}},false],
 ['malformed details',{code:1,reason:'invalid_plan',details:'invalid side effect account'},false],
 ['contradictory no write and published path',{code:1,reason:'invalid_plan',details:{publication_state:'not_written',published_paths:['saved.json'],unknown_paths:[]}},false],
 ['contradictory no write and unknown path',{code:1,details:{publication_state:'not_written',published_paths:[],unknown_paths:['maybe.json']}},false],
 ['malformed path account',{code:1,reason:'invalid_plan',details:{publication_state:'not_written',published_paths:'bad',unknown_paths:[]}},false],
 ['prepublication rejection',{code:1,reason:'invalid_plan'},true],
] as Array<[string,unknown,boolean]>))test('publication classifier: '+name,()=>assert.equal(confirmedNoFileWrite(response),expected));
