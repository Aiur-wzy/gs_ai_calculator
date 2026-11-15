import {useEffect, useMemo, useRef, useState, useSyncExternalStore} from "react";
import {createPortal} from "react-dom";
import {
  Dialog,
  DialogTrigger,
  Heading,
  Menu,
  MenuItem,
  MenuTrigger,
  Modal,
  ModalOverlay,
  Popover,
} from "react-aria-components";
import {Button} from "@/design-system/primitives/Button";
import {SplitPane} from "@/design-system/layouts/SplitPane";
import {FocusManager} from "@/design-system/focus/focus-manager";
import {SurfaceCoordinator} from "@/design-system/surfaces/coordinator";
import {createHostEnvelopeAdapter} from "@/host/adapter";
import {initialAppState, type AppState, type SurfaceDescriptor, type ThemePreference} from "@/state/model";
import {createAppStateStore} from "@/state/store";
import {galleryEnvelope} from "./fixture";

const drawerDescriptor: SurfaceDescriptor = Object.freeze({
  id: "gallery-inspector-drawer",
  kind: "drawer",
  group: "right-drawer",
  ownerId: "foundation-gallery",
  dismissOnEscape: true,
  dismissOnOutside: true,
  modal: false,
});

function useAppState(store: ReturnType<typeof createAppStateStore>): AppState {
  return useSyncExternalStore(store.subscribe, store.getState, store.getState);
}

export function App() {
  const store = useMemo(() => createAppStateStore(initialAppState()), []);
  const host = useMemo(() => createHostEnvelopeAdapter(store), [store]);
  const surfaces = useMemo(() => new SurfaceCoordinator(store), [store]);
  const state = useAppState(store);
  const [inspectorWidth, setInspectorWidth] = useState(360);
  const drawerRef = useRef<HTMLElement>(null);
  const drawerTrigger = useRef<HTMLButtonElement>(null);
  const focusManager = useRef<FocusManager | null>(null);

  useEffect(() => { host.accept(galleryEnvelope); }, [host]);
  useEffect(() => {
    const appRoot = document.querySelector<HTMLElement>("#root");
    if (!appRoot) return;
    const manager = new FocusManager(document, appRoot, surfaces, store);
    focusManager.current = manager;
    manager.attach();
    return () => { manager.detach(); focusManager.current = null; };
  }, [store, surfaces]);

  const drawerOpen = state.surfaces.some(surface => surface.id === drawerDescriptor.id);
  useEffect(() => {
    if (!drawerOpen || !drawerRef.current) return;
    return focusManager.current?.bind(drawerDescriptor.id, {container: drawerRef.current, trigger: drawerTrigger.current, fallback: null});
  }, [drawerOpen]);

  const setTheme = (theme: ThemePreference) => {
    document.documentElement.dataset.theme = theme;
    store.transact(`theme:${theme}`, current => ({...current, revision: current.revision + 1, ui: Object.freeze({...current.ui, theme})}));
  };
  const openDrawer = () => { if (!drawerOpen) surfaces.open(drawerDescriptor); };
  const closeDrawer = () => surfaces.close(drawerDescriptor.id);
  const overlayRoot = document.querySelector("#app-overlay-root");

  const main = <main className="gf-card-grid" aria-label="Foundation V2 组件样片">
    <section className="gf-card">
      <h2>Host 投影</h2>
      <div className="gf-status-line"><span className="gf-status-dot" />Runtime 健康，Turn 可独立失败</div>
      <p>一份 Host envelope 只触发一次 Root Store transaction。命令直接来自 Host，不由界面推断。</p>
      <div className="gf-chip">Store revision {state.revision}</div>
      <div className="gf-chip">Projection revision {state.hostProjection?.projectionRevision ?? "—"}</div>
    </section>
    <section className="gf-card">
      <h2>可用命令</h2>
      <ul className="gf-command-list">
        {state.hostProjection?.availableCommands.map(command => <li className="gf-command-row" key={command.commandId}>
          <code>{command.commandId}</code><span className="gf-chip">{command.enabled ? "可用" : command.reasonCode ?? "不可用"}</span>
        </li>)}
      </ul>
    </section>
    <section className="gf-card">
      <h2>Surface 样片</h2>
      <p>菜单与对话框采用 React Aria。抽屉由统一 Surface/Focus 内核协调 Escape、外部点击和焦点恢复。</p>
      <div className="gf-gallery-actions">
        <MenuTrigger>
          <Button>打开菜单</Button>
          <Popover className="gf-popover">
            <Menu className="gf-menu" aria-label="样片操作">
              <MenuItem className="gf-menu-item" id="inspect">查看详情</MenuItem>
              <MenuItem className="gf-menu-item" id="copy">复制引用</MenuItem>
              <MenuItem className="gf-menu-item" id="discard">丢弃草稿</MenuItem>
            </Menu>
          </Popover>
        </MenuTrigger>
        <DialogTrigger>
          <Button>打开对话框</Button>
          <ModalOverlay className="gf-modal-overlay" isDismissable>
            <Modal className="gf-modal">
              <Dialog className="gf-dialog">
                {({close}) => <>
                  <Heading slot="title">确认本地操作</Heading>
                  <p>这是离线 Gallery，不会写入真实排轴文档。</p>
                  <Button tone="primary" onPress={close}>关闭</Button>
                </>}
              </Dialog>
            </Modal>
          </ModalOverlay>
        </DialogTrigger>
        <Button ref={drawerTrigger} onPress={openDrawer}>打开抽屉</Button>
      </div>
    </section>
    <section className="gf-card">
      <h2>失败不等于死锁</h2>
      <p>{state.issues[0]?.message}</p>
      <p className="gf-gallery-note">未封口草稿不会显示“审核”，但仍可查看、继续、重新校验或丢弃。</p>
    </section>
  </main>;

  const inspector = <>
    <section className="gf-inspector-section"><h2>Inspector</h2><span className="gf-chip">只读投影</span></section>
    <section className="gf-inspector-section"><h2>Envelope</h2><div className="gf-code">{state.lastEnvelopeId ?? "尚未接纳"}</div></section>
    <section className="gf-inspector-section"><h2>当前焦点</h2><div className="gf-code">{String(state.hostProjection?.focus?.recommended_stage ?? "无")}</div></section>
    <section className="gf-inspector-section"><h2>Required actions</h2><div>{state.hostProjection?.requiredActions.length ?? 0}</div></section>
  </>;

  return <div className="gf-gallery-shell">
    <header className="gf-gallery-header">
      <div><div className="gf-gallery-kicker">离线开发样片 · 不接管生产入口</div><h1>GUI Foundation V2</h1></div>
      <div className="gf-gallery-actions">
        <Button onPress={() => setTheme("light")}>浅色</Button>
        <Button onPress={() => setTheme("dark")}>深色</Button>
        <Button onPress={() => setTheme("system")}>跟随系统</Button>
      </div>
    </header>
    <SplitPane primary={main} secondary={inspector} secondaryWidth={inspectorWidth} minimum={280} maximum={520} onWidthChange={setInspectorWidth} />
    {drawerOpen && overlayRoot && createPortal(<>
      <div className="gf-drawer-backdrop" aria-hidden="true" />
      <aside className="gf-drawer" ref={drawerRef} tabIndex={-1} aria-label="移动 Inspector">
        <div className="gf-drawer-header"><h2>Inspector</h2><Button onPress={closeDrawer}>关闭</Button></div>
        {inspector}
      </aside>
    </>, overlayRoot)}
  </div>;
}
