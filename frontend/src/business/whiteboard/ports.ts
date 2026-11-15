/** Explicit ports for the single whiteboard document and App UI effects.
 * JsonFacade is the compatibility boundary for existing extensible form records;
 * domain/schema validation, revision acceptance and file CAS remain in Python.
 */
export type JsonFacade=Record<string,any>;
export interface WhiteboardPorts {
  owner:JsonFacade; ui:JsonFacade; document:any; events:any;
  query:(selector:string)=>any; api:(method:string,...args:any[])=>Promise<any>;
  status:(message:string,...details:any[])=>void;
  plansDir:()=>string; confirm:(message:string)=>boolean; clipboard:()=>any;
  services:{transport?:any;workbench?:any;contextMenu?:any;dialog?:any;workspace?:any;plan?:any;theme?:any;
    display?:any;effects?:any;projection?:any;equipment?:any;capacity?:any;};
}
