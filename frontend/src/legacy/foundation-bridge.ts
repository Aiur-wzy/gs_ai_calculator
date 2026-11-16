import {canReceiveFocus, focusableElements} from "@/design-system/focus/dom-focus";
import {FocusManager} from "@/design-system/focus/focus-manager";
import {SurfaceCoordinator} from "@/design-system/surfaces/coordinator";
import {DomSurfaceHost} from "@/design-system/surfaces/dom-surface-host";
import {createHostEnvelopeAdapter} from "@/host/adapter";
import {createAppStateStore} from "@/state/store";

export const domFocus = Object.freeze({canReceiveFocus, focusableElements});

export const GUI_FOUNDATION_CONTRACT = "genshin-gui-foundation-v2" as const;

export function createGuiFoundation() {
  const store = createAppStateStore();
  const host = createHostEnvelopeAdapter(store);
  const surfaces = new SurfaceCoordinator(store);
  let domHost: DomSurfaceHost | null = null;
  let domDocument: Document | null = null;
  const attachDom = ({document, appRoot, overlayRoot}: {document: Document; appRoot: HTMLElement; overlayRoot: HTMLElement}): DomSurfaceHost => {
    if (domHost) {
      if (domDocument !== document) throw new Error("gui_foundation_dom_document_changed");
      return domHost;
    }
    domDocument = document;
    domHost = new DomSurfaceHost(document, appRoot, overlayRoot, surfaces, store);
    return domHost;
  };
  return Object.freeze({contract: GUI_FOUNDATION_CONTRACT, store, host, surfaces, FocusManager, attachDom});
}
