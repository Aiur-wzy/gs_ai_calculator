// Document-only JSON transport. Numeric spelling belongs to an object property,
// never to a path or numeric value shared with another document.
"use strict";
window.JSONTransport = (() => {
  // calculated.json remains capped at 96 MiB by the calculation/session
  // layers.  This is the outer GUI/RPC envelope budget, which must also carry
  // board and recovery metadata around that artifact.
  const MAX_BYTES = 192 * 1024 * 1024, MAX_MIB = MAX_BYTES / 1024 / 1024, MAX_DEPTH = 128;
  const nativeParse = JSON.parse.bind(JSON), nativeStringify = JSON.stringify.bind(JSON);
  const functionText = Function.prototype.toString;
  const nativeObjectText = functionText.call(Object), nativeArrayText = functionText.call(Array);
  function nativePrototype(proto, array) {
    if (proto === null) return true;
    const constructor = Object.getOwnPropertyDescriptor(proto, "constructor");
    if (!constructor || !("value" in constructor) || typeof constructor.value !== "function") return false;
    const owner = Object.getOwnPropertyDescriptor(constructor.value, "prototype");
    if (!owner || owner.value !== proto) return false;
    if (functionText.call(constructor.value) !== (array ? nativeArrayText : nativeObjectText)) return false;
    const parent = Object.getPrototypeOf(proto);
    return array ? Array.isArray(proto) && nativePrototype(parent, false) : parent === null;
  }
  const tokens = new WeakMap(), proxies = new WeakMap(), targets = new WeakMap();
  const changedArrays = new WeakSet(), inheritedWarnings = new WeakMap();
  const methods = Object.freeze([
    "dsh_client_bind_document","dsh_client_domain_binding","dsh_client_candidate","dsh_client_candidate_read","dsh_client_candidate_command",
    "dsh_client_whiteboard_apply_equipment","dsh_client_publish_whiteboard_result","dsh_client_save_whiteboard_result",
    "dsh_client_publish_candidate_input_draft","dsh_client_open_candidate_input_draft",
    "dsh_client_candidate_input_draft_preview_read",
    "dsh_client_request_candidate_input_draft",
    "dsh_client_import_candidate_input_draft",
    "dsh_client_domain_approvals","dsh_client_domain_approval_decide",
    "dsh_client_workspace_state","dsh_client_app_actions","dsh_client_app_action","dsh_client_file_dialog",
    "agent_budget_settings","agent_update_budget_settings","agent_reset_budget_settings","agent_dsh_settings","agent_update_dsh_settings","agent_reset_dsh_settings",
    "dsh_client_whiteboard_projection","dsh_client_saved_whiteboard",
    "dsh_client_session_status",
    "dsh_client_incompatible_applications","dsh_client_abandon_incompatible_application",
    "new_plan", "enable_mechanics", "assemble_attack_source", "assemble_periodic_source",
    "validate_mechanics_model", "export_formula_workbook", "load_plan", "save_plan",
    "compose_equipment_plan", "prepare_attribute_review", "preview_equipment_edit",
    "apply_data_update", "detach_character", "prepare_character_input", "character_panel_basis",
    "run_plan_calc", "inspect_calculation_diagnostics", "export_plan_result", "export_viewed_report", "read_viewed_report", "export_resolved_plan",
    "new_template", "load_template", "save_template", "run_calc", "check_rotation",
    "load_rotation_model", "export_result", "whiteboard_new", "whiteboard_validate",
    "whiteboard_load", "whiteboard_save", "whiteboard_handoff", "whiteboard_read_result", "open_saved_calculation",
    "whiteboard_apply_equipment", "action_capacity_model_candidates",
    "whiteboard_action_capacity_model_candidates", "action_admission_model_candidates",
    "action_admission_preview", "whiteboard_action_admission_model_candidates",
    "agent_start", "agent_send", "agent_resume", "agent_poll", "agent_wait_events",
    "agent_candidate", "agent_accept", "agent_candidate_applied",
    "agent_abort_candidate_application", "agent_recover_candidate_application",
    "agent_recover_last_applied_whiteboard_projection",
    "agent_publish_committed_whiteboard_result",
    "agent_candidate_read",
    "agent_whiteboard_apply_equipment",
    "agent_resolve_app_action", "agent_present_app_action",
    "agent_claim_app_action", "agent_settle_app_action",
    "agent_recover_app_action_without_replay",
    "agent_list_document_checkpoints", "agent_document_restore_status",
    "agent_create_document_checkpoint", "agent_preview_document_checkpoint",
    "agent_rename_document_checkpoint", "agent_delete_document_checkpoint",
    "agent_prepare_document_restore", "agent_mark_document_restore_applied",
    "agent_advance_document_restore_baseline", "agent_commit_document_restore",
    "agent_abort_document_restore", "agent_mark_document_restore_recovery_required",
    "agent_reconcile_document_restore",
  ]);
  const methodSet = new Set(methods);
  const fail = message => { throw new Error(`文档传输失败：${message}`); };
  const raw = value => targets.get(value) || value;
  const container = value => value !== null && typeof value === "object";
  function byteLength(text) {
    let bytes = 0;
    for (let i = 0; i < text.length; i++) {
      const code = text.charCodeAt(i);
      if (code < 0x80) bytes++;
      else if (code < 0x800) bytes += 2;
      else if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length
        && text.charCodeAt(i + 1) >= 0xdc00 && text.charCodeAt(i + 1) <= 0xdfff) { bytes += 4; i++; }
      else bytes += 3;
      if (bytes > MAX_BYTES) fail(`超过 ${MAX_MIB} MiB 传输上限。`);
    }
    return bytes;
  }
  function invalidate(target, key) {
    const record = tokens.get(target);
    if (!record) return;
    if (Array.isArray(target)) {
      if (record.size) changedArrays.add(target);
      record.clear();
    } else record.delete(String(key));
  }
  function wrap(value) {
    if (!container(value) || targets.has(value)) return value;
    if (proxies.has(value)) return proxies.get(value);
    const proxy = new Proxy(value, {
      get(target, key, receiver) {
        const own = Object.getOwnPropertyDescriptor(target, key);
        if (own && !("value" in own)) fail("不支持文档访问器属性。");
        const child = Reflect.get(target, key, receiver);
        if (own && "value" in own && own.configurable === false && own.writable === false) return child;
        return own && "value" in own ? wrap(child) : child;
      },
      set(target, key, child) {
        invalidate(target, key);
        if (!Object.hasOwn(target, key)) return Reflect.defineProperty(target, key,
          {value:raw(child), writable:true, enumerable:true, configurable:true});
        return Reflect.set(target, key, raw(child), target);
      },
      getOwnPropertyDescriptor(target, key) {
        const descriptor = Reflect.getOwnPropertyDescriptor(target, key);
        if (descriptor && "value" in descriptor && container(descriptor.value)
          && (descriptor.configurable || descriptor.writable)) return {...descriptor, value:wrap(descriptor.value)};
        return descriptor;
      },
      deleteProperty(target, key) {
        invalidate(target, key);
        return Reflect.deleteProperty(target, key);
      },
      defineProperty(target, key, descriptor) {
        if ("get" in descriptor || "set" in descriptor) fail("不支持文档访问器属性。");
        if ("value" in descriptor) invalidate(target, key);
        const next = {...descriptor}, previous = Object.getOwnPropertyDescriptor(target, key);
        const child = "value" in next ? next.value : previous?.value;
        // Store the child proxy before making a property immutable, so Proxy
        // invariants cannot expose a raw tracked child through Object.freeze.
        const configurable = next.configurable ?? previous?.configurable ?? false;
        const writable = next.writable ?? previous?.writable ?? false;
        if (container(child) && !configurable && !writable) next.value = wrap(child);
        else if ("value" in next) next.value = raw(child);
        return Reflect.defineProperty(target, key, next);
      },
      setPrototypeOf() { fail("不支持修改文档对象原型。"); },
    });
    proxies.set(value, proxy); targets.set(proxy, value);
    return proxy;
  }
  function preflight(text) {
    if (typeof text !== "string") fail("接口必须返回 JSON 字符串，不能回退为有损对象。");
    if (text.length > MAX_BYTES) fail(`超过 ${MAX_MIB} MiB 传输上限。`);
    byteLength(text);
    let depth = 0, quoted = false, escaped = false;
    for (const char of text) {
      if (quoted) {
        if (escaped) escaped = false;
        else if (char === "\\") escaped = true;
        else if (char === '"') quoted = false;
      } else if (char === '"') quoted = true;
      else if (char === "{" || char === "[") { if (++depth > MAX_DEPTH) fail("嵌套深度超过 128。"); }
      else if (char === "}" || char === "]") depth--;
    }
  }
  function parse(text) {
    preflight(text);
    const value = nativeParse(text);
    if (typeof value === "number") fail("数值不能单独作为文档根；请使用对象或数组。");
    let pos = 0;
    const ws = () => { while (/\s/u.test(text[pos] || "x")) pos++; };
    function stringToken() {
      const start = pos++;
      while (pos < text.length) {
        const char = text[pos++];
        if (char === "\\") pos++;
        else if (char === '"') return nativeParse(text.slice(start, pos));
      }
      fail("字符串未闭合。");
    }
    function read(current, parent, key) {
      ws();
      if (text[pos] === "{") {
        pos++; ws(); const seen = new Set();
        if (text[pos] !== "}") while (true) {
          ws(); const name = stringToken();
          if (seen.has(name)) fail("对象含重复字段。");
          seen.add(name); ws(); pos++;
          read(Object.getOwnPropertyDescriptor(current, name).value, current, name);
          ws(); if (text[pos] !== ",") break; pos++;
        }
        pos++; return;
      }
      if (text[pos] === "[") {
        pos++; ws(); let index = 0;
        if (text[pos] !== "]") while (true) {
          read(current[index], current, String(index++));
          ws(); if (text[pos] !== ",") break; pos++;
        }
        pos++; return;
      }
      if (text[pos] === '"') { stringToken(); return; }
      if (typeof current === "number") {
        const start = pos;
        while (pos < text.length && /[0-9eE+.\-]/u.test(text[pos])) pos++;
        const token = text.slice(start, pos);
        if (!Number.isFinite(current)) fail("不支持非有限数值。");
        if (/^-?\d+$/u.test(token) && !Number.isSafeInteger(current)) fail("整数字面量超出 JavaScript 安全整数范围。");
        if (!tokens.has(parent)) tokens.set(parent, new Map());
        tokens.get(parent).set(String(key), {token, value: current});
        return;
      }
      pos += current === null ? 4 : current ? 4 : 5;
    }
    read(value, null, null);
    return wrap(value);
  }
  function encode(value) {
    const chunks = [], active = new WeakSet(), notices = new Set();
    let bytes = 0, buffer = "";
    const emit = text => {
      bytes += byteLength(text);
      if (bytes > MAX_BYTES) fail(`超过 ${MAX_MIB} MiB 传输上限。`);
      buffer += text;
      if (buffer.length >= 8192) { chunks.push(buffer); buffer = ""; }
    };
    function visit(item, parent, key, depth) {
      if (item === null) { emit("null"); return; }
      if (typeof item === "string") {
        if (item.length > MAX_BYTES) fail(`超过 ${MAX_MIB} MiB 传输上限。`);
        emit(nativeStringify(item)); return;
      }
      if (typeof item === "boolean") { emit(item ? "true" : "false"); return; }
      if (typeof item === "number") {
        if (!Number.isFinite(item)) fail("不支持非有限数值。");
        const record = parent && tokens.get(parent)?.get(String(key));
        if (record && Object.is(record.value, item)) { emit(record.token); return; }
        if (Number.isInteger(item) && !Number.isSafeInteger(item)) fail("新整数超出 JavaScript 安全整数范围。");
        emit(Object.is(item, -0) ? "-0.0" : nativeStringify(item)); return;
      }
      if (!container(item)) fail("文档包含不支持的值类型。");
      const target = raw(item), array = Array.isArray(target), proto = Object.getPrototypeOf(target);
      if (!nativePrototype(proto, array)) fail("只支持普通对象和数组。");
      if (active.has(target)) fail("文档含循环引用。");
      if (depth >= MAX_DEPTH) fail("嵌套深度超过 128。");
      if (Object.getOwnPropertySymbols(target).length) fail("不支持 Symbol 字段。");
      if (changedArrays.has(target)) notices.add("数字数组经过编辑，旧数值词法已失效，请重新复核相关来源确认。");
      for (const notice of inheritedWarnings.get(target) || []) notices.add(notice);
      active.add(target); emit(array ? "[" : "{");
      if (array && target.length > MAX_BYTES / 2) fail(`超过 ${MAX_MIB} MiB 传输上限。`);
      const keys = array ? null : Object.keys(target);
      const length = array ? target.length : keys.length;
      let first = true;
      for (let index = 0; index < length; index++) {
        const name = array ? String(index) : keys[index];
        const descriptor = Object.getOwnPropertyDescriptor(target, name);
        if (descriptor && !("value" in descriptor)) fail("不支持文档访问器属性。");
        if (!array && descriptor.value === undefined) continue;
        if (!first) emit(","); first = false;
        if (!array) { emit(nativeStringify(name)); emit(":"); }
        visit(!descriptor || descriptor.value === undefined ? null : descriptor.value, target, name, depth + 1);
      }
      emit(array ? "]" : "}"); active.delete(target);
    }
    visit(value, null, null, 0);
    if (buffer) chunks.push(buffer);
    return {text: chunks.join(""), warnings: [...notices]};
  }
  const stringify = value => encode(value).text;
  function clone(value) {
    if (typeof value === "number") {
      if (!Number.isFinite(value)) fail("不支持非有限数值。");
      return value;
    }
    const encoded = encode(value), copied = parse(encoded.text);
    if (container(copied) && encoded.warnings.length) inheritedWarnings.set(raw(copied), encoded.warnings);
    return copied;
  }
  return Object.freeze({parse, stringify, encode, clone, methods, usesMethod: method => methodSet.has(method), MAX_BYTES, MAX_DEPTH});
})();
