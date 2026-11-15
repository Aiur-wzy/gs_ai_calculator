/** Injected legacy editor ports; no browser globals or calculation ownership. */
export type LegacyEditorPort = Record<string, any>;
export interface MechanicsEditor extends LegacyEditorPort {
  caps: any;
  shown: boolean;
  words: Record<string, string>;
}
export interface MechanicsEditorPorts {
  UI: LegacyEditorPort; PL: LegacyEditorPort; RT: LegacyEditorPort;
  RC: LegacyEditorPort; RS: LegacyEditorPort; DS: LegacyEditorPort; EF: LegacyEditorPort;
  extensions: LegacyEditorPort;
  callApi(method: string, ...args: any[]): Promise<any>;
  setStatus(message: string, options?: any): void;
  $(selector: string): any;
}
