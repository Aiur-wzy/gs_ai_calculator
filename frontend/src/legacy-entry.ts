import {createGuiFoundation, GUI_FOUNDATION_CONTRACT, domFocus} from "@/legacy/foundation-bridge";

declare global {
  interface Window {
    GenshinGuiFoundationV2?: Readonly<{
      contract: typeof GUI_FOUNDATION_CONTRACT;
      create: typeof createGuiFoundation;
      focus: typeof domFocus;
      runtime(): ReturnType<typeof createGuiFoundation>;
      acceptEnvelope(input: unknown): ReturnType<ReturnType<typeof createGuiFoundation>["host"]["accept"]>;
    }>;
  }
}

if (window.GenshinGuiFoundationV2) throw new Error("gui_foundation_v2_duplicate_owner");
let singleton: ReturnType<typeof createGuiFoundation> | null = null;
const runtime = () => singleton ?? (singleton = createGuiFoundation());
window.GenshinGuiFoundationV2 = Object.freeze({
  contract: GUI_FOUNDATION_CONTRACT,
  create: createGuiFoundation,
  focus: domFocus,
  runtime,
  acceptEnvelope: (input: unknown) => runtime().host.accept(input),
});
