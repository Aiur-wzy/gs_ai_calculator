import {createTimelineContext,type TimelineInput,type TimelineEvent} from "./context";
import {projectActions} from "./actions";
import {projectHits} from "./hits";
import {projectElemental} from "./elemental";
import {projectResources} from "./resources";
import {projectCoverage} from "./coverage";
import {projectSpecialStates} from "./special-states";
import {enrichTimeline} from "./presentation";
import {copy,key,list,chinese,stateName,endReason} from "./shared";
export function buildTimelineProjection(input:TimelineInput={}) {
  const context=createTimelineContext(input);
  projectActions(context);
  const hits=projectHits(context), withHits={...context,...hits};
  projectElemental(context);projectResources(withHits);
  const coverage=projectCoverage(withHits);
  projectSpecialStates(context);
  enrichTimeline({...withHits,...coverage});
  const {views,readable,duration,characters}=context;
    const order = (event:TimelineEvent) => event.detail.trace_index ?? event.detail.activation_trace_index ?? event.detail.input_order ?? Number.MAX_SAFE_INTEGER;
    // Stable sort retains insertion order when two items have the same evidence.
    for (const view of Object.values(views)) {
      for (const event of view.events) event.label = readable(event.label);
      view.events.sort((a, b) => a.start - b.start || order(a) - order(b));
    }
    return {duration, characters, views, reportWindow:context.reportWindow, explanationDocument:context.explanationDocument,
      explanationFingerprint:context.explanationFingerprint};
}
  // Presentation only: equal windows for multiple recipients share one bar.
  // Different quota states, instances, refresh histories and gaps stay separate.
  export function groupCoverage(events:any[] = [], characters:any[] = []) {
    const out:any[] = [], groups = new Map<any,any>();
    for (const original of list(events)) {
      if (original.kind !== "coverage") { out.push(original); continue; }
      const comparison = {...original.detail}; delete comparison.receipt_id;
      const signature = key(original.sourceKey, original.start, original.end, comparison);
      let grouped = groups.get(signature);
      if (!grouped) {
        grouped = {...copy(original), character:original.detail?.owner ?? original.character,
          characters:[], detail:{...copy(original.detail), recipients:[]}};
        groups.set(signature, grouped); out.push(grouped);
      }
      if (!grouped.characters.includes(original.character)) grouped.characters.push(original.character);
      grouped.detail.recipients.push({character:original.character, event_id:original.id,
        detail:copy(original.detail)});
    }
    const names = new Map(list(characters).map(c => [c.id, c.label || c.name]));
    for (const event of groups.values()) event.beneficiary_names = event.characters.map((id:any) =>
      chinese(names.get(id), id == null ? "按目标选择器" : "接收角色"));
    return out;
  }

export {stateName,endReason};
