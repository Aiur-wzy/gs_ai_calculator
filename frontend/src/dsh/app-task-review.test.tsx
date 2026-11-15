import {describe, expect, it} from "vitest";
import {renderToStaticMarkup} from "react-dom/server";
import {parseTaskReview, TaskReviewDetails} from "./app-task-review";
import {candidateReviewAvailable} from "./app-domain-candidate";

const facts = {contract:"genshin-task-evidence-v1",authority:"advisory_only",acceptance_effect:"none",
  status:"differences",differences:[{id:"r1",summary:"第二轮回能尚未输入"}],
  requirements:[{disposition:"approximation",description:"命中时间按无卡肉参考"}]};
describe("advisory task facts in candidate GUI",()=>{
  it("shows understandable differences and approximations in one closed disclosure",()=>{
    const html=renderToStaticMarkup(<TaskReviewDetails review={parseTaskReview(facts)}/>);
    expect(html).toContain("与要求对照：1 项待核对");
    expect(html).toContain("第二轮回能尚未输入");
    expect(html).toContain("命中时间按无卡肉参考");
    expect(html).not.toContain("open=");
    expect(html).not.toContain("genshin-task-evidence-v1");
  });
  it("does not invent completeness or become candidate admission",()=>{
    const html=renderToStaticMarkup(<TaskReviewDetails review={parseTaskReview({...facts,differences:[]})}/>);
    expect(html).toContain("未记录或未检查");
    expect(candidateReviewAvailable({dshSessionId:"s",conversationId:"c",projectionRevision:1,
      availableCommands:[{commandId:"candidate.review",enabled:true}]})).toBe(true);
  });
  it("retains more than twelve differences and lets users inspect original requirements and positions",()=>{
    const differences=Array.from({length:18},(_,i)=>({id:`req.${i}`,summary:`第 ${i+1} 项仍待核对`,evidence_refs:[{reference:"input",pointer:`/actions/${i}`}]}));
    const html=renderToStaticMarkup(<TaskReviewDetails review={parseTaskReview({...facts,differences,
      task_sources:[{id:"task.original",description:"每组攻击至少一次扩冰，不是全轴只扩冰一次"}],
      requirements:[{id:"req.17",description:"第六组扩冰",disposition:"pending",adopted_refs:[{reference:"input",pointer:"/mechanics/last"}]}]})}/>);
    expect(html).toContain("18 项待核对");expect(html).toContain("第 18 项仍待核对");
    expect(html).toContain("查看本任务的原文");expect(html).toContain("每组攻击至少一次扩冰");
    expect(html).toContain("input /mechanics/last");expect(html).not.toContain("完整度");
  });
  it("ignores invalid auxiliary data and hides an absent task",()=>{
    expect(parseTaskReview({...facts,authority:"approved"})).toBeUndefined();
    expect(renderToStaticMarkup(<TaskReviewDetails review={parseTaskReview({...facts,status:"no_task_source"})}/>)).toBe("");
    expect(parseTaskReview({...facts,differences:[null,{}]})).toMatchObject({differences:[]});
  });
});
