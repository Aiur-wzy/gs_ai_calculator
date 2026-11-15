/** Development-only CLI projection of the production App document applier.
 * No browser window, model, private session file or alternate editor is used.
 * Native GUI picker/visual acceptance remains a separate user responsibility.
 */
import {legacyDomainFixture} from "./legacy-domain-test-fixture.mjs";

let raw="";
// A pipe chunk may end inside a Chinese/emoji codepoint.  Decode across
// chunks, preserving exactly the reviewed payload before Host CAS checks.
process.stdin.setEncoding("utf8");
for await (const chunk of process.stdin) raw+=chunk;
let close;
try {
  const fixture=await legacyDomainFixture(["whiteboard.js","agent-document-adapter.js","candidate-application.js"],
    {PL:{state:{plan:null,request:0,sourceEpoch:0},draftChanged:()=>false},RT:{state:{}},RS:{},WS:{tab:()=>"whiteboard"}});
  close=fixture.close;
  const w=fixture.context,input=w.JSONTransport.parse(raw);
  if(input.kind!=="whiteboard"||!input.board||!input.reviewed||typeof input.conversation_id!=="string")throw new Error("projection_input_invalid");
  // Rendering geometry is outside a no-window projection. WB's real model,
  // application method and snapshot/CAS code remain unmodified.
  w.WB.render=()=>{};
  w.WB.replace({board:w.UI.clone(input.board),path:input.path||"",file_sha:input.file_sha||null},false,false);
  const application=w.CandidateEditorApplication.create(),before=application.capture("whiteboard");
  const actual=await application.applyReviewed(input.reviewed,before,input.conversation_id);
  // Capture after the real consumer, rather than echoing the requested target.
  const captured=application.capture("whiteboard");
  if(actual.json!==captured.json||actual.payloadJson!==captured.payloadJson)throw new Error("projection_capture_changed");
  process.stdout.write(w.JSONTransport.stringify({contract:"genshin-production-document-projection-v1",
    scope:"production_frontend_document_application",real_gui_tested:false,
    document:captured.document,application_payload:captured.applicationPayload}));
} catch {
  process.stderr.write("production_document_projection_failed\n");
  process.exitCode=1;
} finally { close?.(); }
