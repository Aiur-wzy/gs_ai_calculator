// 时间轴视口：只改变显示比例和滚动位置，不接触方案、编辑版本或计算结果。
"use strict";

window.TV = {
  zoom: 1, maxZoom: 32, template: null, panMode: false, bound: false,
  labelWidth: 210, duration: 10, pixelsPerSecond: 1,
};

// Invalidity comes from editor/result notifications. Navigation does not inspect
// or serialize the potentially large native plan.
TV.invalidateData = () => { TV.projectionKey=null;TV.dataInvalid=true;TV.requestPaint(); };
TV.requestPaint = () => {
  if(!window.TD || TV.paintQueued)return;
  TV.paintQueued=true;
  const paint=()=>{
    if(!TV.paintQueued)return;
    TV.paintQueued=false;
    if(RT.cancelDrag)return; // keep the dragged SVG node until commit/cancel
    if(TV.dataInvalid || !TV.projection)TV.refreshProjection();
    TV.paintProjection();
  };
  if(window.requestAnimationFrame)window.requestAnimationFrame(paint);else paint();
};

TV.updateOverlays = () => {
  const wrap = $("#timeline-wrap");
  TV.labels?.setAttribute("transform", `translate(${wrap.scrollLeft || 0},0)`);
  TV.ruler?.setAttribute("transform", `translate(0,${wrap.scrollTop || 0})`);
  TV.corner?.setAttribute("transform", `translate(${wrap.scrollLeft || 0},${wrap.scrollTop || 0})`);
};

TV.prepare = (template, duration) => {
  TV.bind();
  const wrap = $("#timeline-wrap");
  if (TV.template !== template) {
    TV.cancelPan?.();
    TV.template = template;
    TV.zoom = 1;
    wrap.scrollLeft = 0;
    wrap.scrollTop = 0;
  }
  TV.duration = duration;
  const viewportWidth = Math.max(wrap.clientWidth, TV.labelWidth + 80);
  const plotWidth = viewportWidth - TV.labelWidth - 16;
  TV.pixelsPerSecond = plotWidth * TV.zoom / duration;
  const active = Boolean(template);
  $("#timeline-zoom-out").disabled = !active || TV.zoom <= 1;
  $("#timeline-zoom-in").disabled = !active || TV.zoom >= TV.maxZoom;
  $("#timeline-fit").disabled = !active;
  $("#timeline-pan").disabled = !active;
  $("#timeline-scale").textContent = `${Math.round(TV.zoom * 100)}%`;
  return TV.labelWidth + plotWidth * TV.zoom + 16;
};

// 1/2/5 的十进制间隔，始终留出足够文字空间，不按总时长硬编码刻度。
TV.tickStep = (pixelsPerSecond) => {
  const target = 64 / pixelsPerSecond;
  const power = 10 ** Math.floor(Math.log10(target));
  return [1, 2, 5, 10].find((n) => n * power >= target) * power;
};

TV.zoomAt = (factor, clientX) => {
  if (!RT.tpl()) return;
  const wrap = $("#timeline-wrap");
  const nextZoom = Math.min(TV.maxZoom, Math.max(1, TV.zoom * factor));
  if (nextZoom === TV.zoom) return;
  RT.cancelDrag?.();
  TV.cancelPan?.();
  const bounds = wrap.getBoundingClientRect();
  const anchor = clientX == null
    ? TV.labelWidth + (wrap.clientWidth - TV.labelWidth) / 2
    : Math.max(TV.labelWidth, Math.min(wrap.clientWidth, clientX - bounds.left - wrap.clientLeft));
  const seconds = ((wrap.scrollLeft || 0) + anchor - TV.labelWidth) / TV.pixelsPerSecond;
  TV.zoom = nextZoom;
  if(window.TD && TV.projection){
    const width=TV.prepare(RT.tpl(),TV.duration);$("#timeline").setAttribute("width",width);
    wrap.scrollLeft=Math.max(0,TV.labelWidth+seconds*TV.pixelsPerSecond-anchor);
    TV.paintProjection();return;
  }
  RT.renderTimeline();
  wrap.scrollLeft = Math.max(0, TV.labelWidth + seconds * TV.pixelsPerSecond - anchor);
  TV.updateOverlays();
};

TV.fit = () => {
  if (!RT.tpl()) return;
  RT.cancelDrag?.();
  TV.cancelPan?.();
  TV.zoom = 1;
  const wrap = $("#timeline-wrap");
  if(window.TD && TV.projection){
    const width=TV.prepare(RT.tpl(),TV.duration);$("#timeline").setAttribute("width",width);
    wrap.scrollLeft=0;wrap.scrollTop=0;TV.paintProjection();return;
  }
  RT.renderTimeline();
  wrap.scrollLeft = 0;
  wrap.scrollTop = 0;
  TV.updateOverlays();
};

TV.bind = () => {
  if (TV.bound) return;
  TV.bound = true;
  const wrap = $("#timeline-wrap");
  $("#timeline-zoom-out").addEventListener("click", () => TV.zoomAt(1 / 1.5));
  $("#timeline-zoom-in").addEventListener("click", () => TV.zoomAt(1.5));
  $("#timeline-fit").addEventListener("click", TV.fit);
  $("#timeline-pan").addEventListener("click", () => {
    TV.panMode = !TV.panMode;
    $("#timeline-pan").setAttribute("aria-pressed", String(TV.panMode));
    wrap.classList.toggle("pan-mode", TV.panMode);
  });
  wrap.addEventListener("scroll", () => {TV.updateOverlays();TV.requestPaint();}, {passive: true});
  document.addEventListener?.("genshin-theme-change", () => TV.requestPaint());
  wrap.addEventListener("wheel", (e) => {
    if (e.ctrlKey) {
      e.preventDefault();
      if (e.deltaY) TV.zoomAt(e.deltaY < 0 ? 1.2 : 1 / 1.2, e.clientX);
    } else if (e.shiftKey) {
      e.preventDefault();
      const unit = e.deltaMode === 1 ? 24 : e.deltaMode === 2 ? wrap.clientWidth : 1;
      wrap.scrollLeft += (e.deltaY || e.deltaX) * unit;
      TV.updateOverlays();
      TV.requestPaint();
    }
  }, {passive: false});
  wrap.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.altKey || e.metaKey || !RT.tpl()) return;
    if (e.key === "+" || e.key === "=") TV.zoomAt(1.5);
    else if (e.key === "-") TV.zoomAt(1 / 1.5);
    else if (e.key === "0") TV.fit();
    else if (e.key === "ArrowLeft") wrap.scrollLeft -= 60;
    else if (e.key === "ArrowRight") wrap.scrollLeft += 60;
    else if (e.key === "ArrowUp") wrap.scrollTop -= 48;
    else if (e.key === "ArrowDown") wrap.scrollTop += 48;
    else return;
    e.preventDefault();
    TV.updateOverlays();
    TV.requestPaint();
  });
  // 捕获阶段先判定平移，避免密集命中/增益区的左键同时触发调时。
  wrap.addEventListener("pointerdown", (e) => {
    if (!RT.tpl() || (e.button !== 0 && e.button !== 1)) return;
    // 原生滚动条以容器为目标，交还浏览器，空白画布仍可拖动。
    if (e.target === wrap) return;
    // SVG controls stay clickable even when the background pan tool is active.
    if (e.button === 0 && e.target.closest?.("[data-axis-control]")) return;
    const isEvent = e.target.closest?.(".tl-hit, .buff-bar, .buff-handle, .tl-switch, .tl-view-event, .tl-action-caption");
    if (e.button === 0 && !TV.panMode && isEvent) return;
    e.preventDefault();
    e.stopPropagation();
    RT.cancelDrag?.();
    TV.cancelPan?.();
    wrap.focus({preventScroll: true});
    try { wrap.setPointerCapture(e.pointerId); } catch { /* 合成指针 */ }
    const start = {x: e.clientX, y: e.clientY, left: wrap.scrollLeft, top: wrap.scrollTop};
    wrap.classList.add("panning");
    const move = (ev) => {
      if (ev.pointerId !== e.pointerId) return;
      wrap.scrollLeft = start.left + start.x - ev.clientX;
      wrap.scrollTop = start.top + start.y - ev.clientY;
      TV.updateOverlays();
      TV.requestPaint();
    };
    const finish = (ev) => {
      if (ev && ev.pointerId !== e.pointerId) return;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
      wrap.removeEventListener("lostpointercapture", finish);
      wrap.classList.remove("panning");
      TV.cancelPan = null;
      if (wrap.hasPointerCapture(e.pointerId)) wrap.releasePointerCapture(e.pointerId);
    };
    TV.cancelPan = finish;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
    wrap.addEventListener("lostpointercapture", finish);
  }, true);
};
