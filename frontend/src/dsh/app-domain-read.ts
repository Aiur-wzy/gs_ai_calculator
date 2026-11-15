import {useCallback,useEffect,useRef,useSyncExternalStore} from "react";
import type {AppDomainBinding,AppDomainBindings} from "./app-domain-candidate";

type Snapshot<T>={identity:string;value?:T;error?:string;loading:boolean};
type Entry<T>={snapshot:Snapshot<T>;listeners:Set<()=>void>;generation:number;revision?:number;pending?:Promise<void>;
  last?:()=>Promise<T>;dirty:boolean};
const registries=new WeakMap<AppDomainBindings,Map<string,Entry<unknown>>>();
const maximumEntries=128;

function notify(entry:Entry<unknown>){for(const listener of entry.listeners)listener();}
function refresh(entry:Entry<unknown>){
  if(!entry.last||entry.pending||!entry.listeners.size)return;
  const generation=entry.generation,read=entry.last;
  entry.dirty=false;
  entry.snapshot={...entry.snapshot,error:undefined,loading:true};notify(entry);
  entry.pending=Promise.resolve().then(read).then(value=>{
    if(entry.generation===generation)entry.snapshot={identity:entry.snapshot.identity,value,loading:false};
  },reason=>{
    if(entry.generation===generation)entry.snapshot={...entry.snapshot,error:reason instanceof Error?reason.message:"app_domain_read_failed",loading:false};
  }).finally(()=>{
    entry.pending=undefined;notify(entry);
    if(entry.dirty)refresh(entry);
  });
}
function invalidate(entry:Entry<unknown>){
  if(!entry.dirty||entry.pending)entry.generation++;
  entry.dirty=true;refresh(entry);
}
function entryFor(bindings:AppDomainBindings,identity:string):Entry<unknown>{
  let registry=registries.get(bindings);
  if(!registry){
    registry=new Map();registries.set(bindings,registry);
    const entries=registry;
    bindings.subscribeInvalidation?.(sessionId=>{
      for(const [id,entry] of entries)if(id.startsWith(`${sessionId}\u0000`))invalidate(entry);
    });
  }
  let entry=registry.get(identity);
  if(entry){registry.delete(identity);registry.set(identity,entry);return entry;}
  entry={snapshot:{identity,loading:true},listeners:new Set(),generation:0,dirty:true};registry.set(identity,entry);
  for(const [id,old] of registry){
    if(registry.size<=maximumEntries)break;
    if(id!==identity&&!old.pending&&!old.listeners.size)registry.delete(id);
  }
  return entry;
}

/** App resource snapshots remain window-local and never grant write authority. */
export function useAppDomainRead<T>(bindings:AppDomainBindings,binding:AppDomainBinding,resource:string,read:()=>Promise<T>){
  const reader=useRef(read);reader.current=read;
  const identity=`${binding.dshSessionId}\u0000${binding.conversationId}\u0000${resource}`;
  const entry=entryFor(bindings,identity);
  const subscribe=useCallback((listener:()=>void)=>{entry.listeners.add(listener);return()=>{entry.listeners.delete(listener);};},[entry]);
  const state=useSyncExternalStore(subscribe,()=>entry.snapshot) as Snapshot<T>;
  useEffect(()=>{
    entry.last=reader.current;
    if(entry.revision!==binding.projectionRevision){entry.revision=binding.projectionRevision;invalidate(entry);}
    else if(entry.dirty)refresh(entry);
  },[entry,binding.projectionRevision]);
  return {...state,refresh:()=>{entry.last=reader.current;invalidate(entry);}};
}
