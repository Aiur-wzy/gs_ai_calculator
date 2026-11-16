import {describe,it,expect} from "vitest";
import {createTimelineContext} from "./context";

describe("result availability is distinct from calculation validity",()=>{
  it("describes existing unloaded calculation without enabling result consumers",()=>{
    const value=createTimelineContext({resultAvailability:"not_loaded"});
    expect(value.r).toBeNull();expect(value.current).toBe(false);
    expect(value.views.damage.notes.join(" ")).toContain("尚未载入");
    expect(value.views.damage.notes.join(" ")).not.toContain("尚未计算");
  });
  it("retains invalid-result warning even when a caller reports a calculation exists",()=>{
    const value=createTimelineContext({result:{hits:[{expected:999}]},current:false,resultAvailability:"not_loaded"});
    expect(value.r).toBeNull();expect(value.views.damage.notes.join(" ")).toContain("已失效");
  });
  it("does not infer an existing calculation when no metadata or result exists",()=>{
    expect(createTimelineContext().views.damage.notes.join(" ")).toContain("尚未计算");
  });
});
