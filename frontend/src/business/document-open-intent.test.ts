import {describe,expect,it} from "vitest";
import {createDocumentOpenIntents} from "./document-open-intent";

describe("document open intent preserves the initiating input across effects",()=>{
  const fixture=()=>{const current:Record<string,unknown>={board:{},boardJson:"a",boardPath:"a",boardSha:"old",boardRequest:0,boardDraft:"{}",boardEditor:null,
    plan:{},template:{},input:"a",draft:null,planPath:"a",planSha:"old",resolvedSha:"old",request:0,sourceEpoch:0,owner:{},ownerConnected:true};
    return {current,owner:createDocumentOpenIntents(()=>({...current}))};};
  for(const key of ["board","boardJson","boardPath","boardSha","boardRequest","boardDraft","boardEditor","plan","template","input","draft","planPath","planSha","resolvedSha","request","sourceEpoch","owner","ownerConnected"]){
    it(`invalidates a late response after ${key} changes`,()=>{const {owner,current}=fixture(),intent=owner.begin();current[key]="changed";expect(intent.current()).toBe(false);});
  }
  it("a newer open or destroyed Owner invalidates the pending open",()=>{const {owner}=fixture(),first=owner.begin(),second=owner.begin();expect(first.current()).toBe(false);expect(second.current()).toBe(true);owner.invalidate();expect(second.current()).toBe(false);});
  it("adopts only identity changes made by explicit save-then-continue",()=>{const {owner,current}=fixture(),intent=owner.begin();current.boardPath="new";current.boardSha="saved";current.boardRequest=1;
    expect(intent.current()).toBe(false);expect(intent.acceptSavedIdentity()).toBe(true);expect(intent.current()).toBe(true);
    current.input="intervening edit";expect(intent.acceptSavedIdentity()).toBe(false);expect(intent.current()).toBe(false);});
  it("discarding an equipment draft never authorizes edits to applied input",()=>{const {owner,current}=fixture(),intent=owner.begin();current.boardDraft="discarded";expect(intent.acceptDiscardedDrafts()).toBe(true);
    current.boardJson="new author content";current.boardDraft="another discard";expect(intent.acceptDiscardedDrafts()).toBe(false);});
});
