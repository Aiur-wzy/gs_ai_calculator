import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {Script,type Context} from "node:vm";
import {beforeEach,afterEach,describe,it,expect,vi} from "vitest";
import {callAppDocumentApi} from "./app-document-transport";
type Transport={parse(value:string):unknown;stringify(value:unknown):string};
beforeEach(async()=>{
  const context=(globalThis as typeof globalThis&{jsdom:{getInternalVMContext():Context}}).jsdom.getInternalVMContext();
  new Script(await readFile(resolve(process.cwd(),"../src/genshin_app/gui/web/json-transport.js"),"utf8")).runInContext(context);
  Object.defineProperty(window,"JSONTransport",{value:new Script("window.JSONTransport").runInContext(context),configurable:true});
});
afterEach(()=>{Reflect.deleteProperty(window,"JSONTransport");document.head.replaceChildren();});
describe("lossless App domain wire",()=>{
  it("keeps original numeric spelling through candidate input and return values",async()=>{
    const transport=(window as unknown as {JSONTransport:Transport}).JSONTransport;
    const document=transport.parse('{"large":9007199254740991,"decimal":0.100000000000000005}');
    const direct=vi.fn(),wrapped=vi.fn(async(_method:string,encoded:string)=>{expect(encoded).toContain("9007199254740991");expect(encoded).toContain("0.100000000000000005");return '{"code":0,"data":{"large":9007199254740991,"decimal":0.100000000000000005}}';});
    const response=await callAppDocumentApi({document_json_call:wrapped,dsh_client_bind_document:direct},"dsh_client_bind_document",[{document}]);
    expect(transport.stringify(response)).toContain("9007199254740991");expect(transport.stringify(response)).toContain("0.100000000000000005");expect(direct).not.toHaveBeenCalled();
  });
  it("does not silently fall back to lossy calls when the desktop wire is missing",async()=>{
    const direct=vi.fn();await expect(callAppDocumentApi({dsh_client_candidate:direct},"dsh_client_candidate",[])).rejects.toThrow("transport_unavailable");expect(direct).not.toHaveBeenCalled();
  });
  it("retains the existing explicit rejection of unsafe integer documents",()=>{
    const transport=(window as unknown as {JSONTransport:Transport}).JSONTransport;
    expect(()=>transport.parse('{"unsupported_integer":9007199254740993}')).toThrow("安全整数");
  });
});
