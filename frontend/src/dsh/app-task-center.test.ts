import {describe,expect,it} from "vitest";
import {taskFacts,taskRoundLabel,type TaskFacts} from "./app-task-center";
import type {SessionStatus} from "@deepseek-ai/dsh-client-ui-session/client";
const summary={blank:false,running:false};
const facts:TaskFacts={sessionId:"a",lastTurn:{turn:1,reason:{kind:"completed"}},agentRunning:false,admission:{state:"idle"},accounting:{provider_unsettled:0}};
describe("App read-only Task Center",()=>{
  it("does not label an idle Session with no last-turn evidence as completed",()=>{expect(taskRoundLabel(summary,undefined,undefined)).toBe("尚无已核实轮次结果");expect(taskRoundLabel(summary,undefined,{...facts,lastTurn:null})).toBe("尚无已核实轮次结果");expect(taskRoundLabel(summary,undefined,facts)).toBe("本轮结束");});
  it("keeps model termination distinct from pending user interactions and actual resource admission",()=>{
    expect(taskRoundLabel(summary,{running:true,pendingInteraction:{kind:"question",sessionId:"a",key:"q"}} as SessionStatus,facts)).toBe("等待回答");expect(taskRoundLabel(summary,undefined,{...facts,admission:{state:"queued"}})).toBe("等待运行资源");expect(taskRoundLabel(summary,undefined,{...facts,lastTurn:{turn:2,reason:{kind:"error",error:{code:"provider_error"}}}})).toBe("本轮失败");
  });
  it("does not treat unsettled accounting reservations as task failure",()=>{expect(taskRoundLabel(summary,undefined,{...facts,accounting:{provider_unsettled:3,tool_capacity_reservations:{read:6}}})).toBe("本轮结束");});
  it("requires exact Session identity and preserves Host-owned usage/admission facts",()=>{
    const raw={session_id:"a",last_turn:{turn:1,reason:{kind:"completed"}},agent_running:null,admission:{state:"idle",capacity:1},accounting:{provider_unsettled:2}};expect(taskFacts(raw,"a").accounting.provider_unsettled).toBe(2);expect(()=>taskFacts(raw,"other")).toThrow("app_task_status_invalid");expect(()=>taskFacts({code:1,reason:"dsh_status_unavailable",message:"暂不可用"},"a")).toThrow("暂不可用");
  });
});
