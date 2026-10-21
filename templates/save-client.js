/* Shared by both learning pages. No polling or request deadline. */
(function (root) {
  "use strict";
  const clone = value => JSON.parse(JSON.stringify(value));
  function content(value) {
    const clean = clone(value);
    delete clean.saved_at;
    delete clean.review_saved_at;
    const stable = item => Array.isArray(item) ? item.map(stable) : item && typeof item === "object"
      ? Object.fromEntries(Object.keys(item).sort().map(key => [key, stable(item[key])])) : item;
    return JSON.stringify(stable(clean));
  }
  function create(options) {
    const { api, preview, context, read, apply, status } = options;
    const controls = {};
    for (const name of ["cancel-wait", "recover-save", "restore-draft", "export-draft"]) {
      controls[name] = document.getElementById(name);
    }
    let etag = null, disk = null, revision = 0, dirty = false, suspended = false;
    let active = null, draftTimer = null, candidate = null, uncertain = null, storageWarning = "", draftConflictWarning = "";
    const owner = root.crypto && root.crypto.randomUUID ? root.crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    const key = preview ? null : `daily-srl:draft:2:${context.workspace_id}:${context.kind}:${context.date}:${context.runtime_id}`;
    function announce(message, error = false) {
      status(message + storageWarning + draftConflictWarning, error || !!storageWarning || !!draftConflictWarning);
    }
    function clearOwnDraft() {
      try {
        const stored = localStorage.getItem(key);
        if (stored && JSON.parse(stored).owner === owner) localStorage.removeItem(key);
      } catch (_) {}
      draftConflictWarning = "";
    }
    function buttons() {
      if (controls["cancel-wait"]) controls["cancel-wait"].hidden = !active;
      if (controls["recover-save"]) controls["recover-save"].hidden = !suspended || !!active;
      if (controls["restore-draft"]) controls["restore-draft"].hidden = !candidate || !etag || candidate.ready === false || !!active;
      if (controls["export-draft"]) controls["export-draft"].hidden = !dirty && !candidate;
    }
    function flush() {
      clearTimeout(draftTimer);
      if (!key || !dirty) return;
      try {
        const current = read();
        const stored = localStorage.getItem(key);
        const existing = stored ? JSON.parse(stored) : null;
        if (existing && existing.owner !== owner && content(existing.state) !== content(current)) {
          draftConflictWarning = " 本机已有另一个页面的不同草稿，已保留；当前页尚未写入本机草稿，请先导出需要保留的内容。";
          status("当前填写内容仍在页面中。" + storageWarning + draftConflictWarning, true);
          buttons(); return;
        }
        localStorage.setItem(key, JSON.stringify({ context, owner, baseETag: etag, state: current, written_at: new Date().toISOString() }));
        draftConflictWarning = "";
      } catch (_) {
        storageWarning = " 本机草稿暂存不可用；关闭或刷新可能丢失未保存内容，请先复制或导出。";
        status("当前填写内容仍在页面中。" + storageWarning, true);
      }
      buttons();
    }
    try {
      const stored = key && localStorage.getItem(key);
      if (stored) {
        const value = JSON.parse(stored);
        if (value.context.runtime_id === context.runtime_id && value.context.workspace_id === context.workspace_id && value.state) candidate = value;
      }
    } catch (_) {
      storageWarning = " 本机草稿暂存不可用；关闭或刷新可能丢失未保存内容，请先复制或导出。";
    }
    function edit() {
      if (preview) return;
      revision += 1; dirty = true;
      clearTimeout(draftTimer);
      draftTimer = setTimeout(flush, 300);
      buttons();
    }
    async function request(method, payload, mode) {
      const controller = new AbortController();
      let rejectCancel;
      const cancellation = new Promise((_, reject) => { rejectCancel = reject; });
      const operation = { cancel() {
        const error = new Error("已取消等待；保存结果尚未确认，填写内容仍保留。");
        error.cancelled = true;
        rejectCancel(error); controller.abort();
      } };
      active = operation; buttons();
      try {
        return await Promise.race([(async () => {
          const headers = { "X-Daily-SRL-Context": context.runtime_id, "X-Daily-SRL-Protocol": "2" };
          if (method === "POST") {
            headers["Content-Type"] = "application/json";
            headers["If-Match"] = etag;
            if (mode) headers["X-Daily-SRL-Save"] = mode;
          }
          const response = await fetch(api, { method, headers, cache: "no-store", signal: controller.signal,
            ...(method === "POST" ? { body: JSON.stringify(payload) } : {}) });
          if (response.status === 409 || response.status === 412) {
            const error = new Error(response.status === 409
              ? "页面对应的学习记录已更新，请保留草稿并从对话重新打开页面。"
              : "已保存内容有新的版本；当前填写内容已保留，请先检查保存状态，再选择恢复草稿或读取文件。");
            error.conflict = true; error.runtime = response.status === 409; throw error;
          }
          if (!response.ok) {
            const error = new Error("保存服务暂时无法完成请求。");
            error.rejected = response.status >= 400 && response.status < 500; throw error;
          }
          const data = await response.json();
          const receivedContext = response.headers.get("X-Daily-SRL-Context");
          const receivedETag = response.headers.get("ETag");
          if (receivedContext !== context.runtime_id || !receivedETag) {
            const error = new Error("页面与保存服务版本不一致，请保留草稿并从对话重新打开页面。");
            error.conflict = true; error.runtime = true; throw error;
          }
          return { state: method === "POST" ? data.state : data, etag: receivedETag };
        })(), cancellation]);
      } finally {
        if (active === operation) active = null;
        buttons();
      }
    }
    function remember(result) { etag = result.etag; disk = clone(result.state); }
    function restore(value) {
      const next = clone(value.state);
      next.saved_at = null;
      if (context.kind === "daily-page") next.review_saved_at = null;
      apply(next, true); revision += 1; dirty = true; suspended = false; candidate = null; uncertain = null;
      flush(); buttons();
      announce("已恢复本机草稿，尚未正式保存；请检查后点击保存。");
    }
    async function load(discard = false) {
      if (preview) return { ok: false };
      if (active) return { ok: false };
      if (discard && (dirty || candidate) && !root.confirm("重新读取会用已保存内容替换当前页面。请先导出需要保留的草稿。是否继续？")) return { ok: false };
      const startRevision = revision;
      const priorDisk = disk ? clone(disk) : clone(read());
      if (discard && dirty) flush();
      announce("正在读取已保存内容；可以继续填写，也可以取消等待。");
      try {
        const result = await request("GET");
        remember(result); suspended = false; uncertain = null;
        const pendingEdits = revision !== startRevision || (!discard && dirty);
        if (pendingEdits) {
          if (content(priorDisk) !== content(result.state)) {
            candidate = { context, baseETag: null, state: read() }; suspended = true;
            announce("文件中的内容已变化，页面上的填写内容已保留；请选择恢复草稿或重新读取已保存内容。", true);
          } else announce("已连接保存服务，保留了页面上的最新填写内容；尚未保存。");
          flush();
        } else if (!discard && candidate && candidate.baseETag === etag) {
          restore(candidate);
        } else {
          apply(result.state, true); dirty = false;
          if (!discard && candidate && content(candidate.state) !== content(result.state)) {
            suspended = true;
            announce("文件与本机草稿不同。当前显示已保存内容；可选择恢复草稿，或继续使用已保存内容。", true);
          } else {
            candidate = null;
            clearOwnDraft();
            announce("已读取保存的内容。");
          }
        }
        buttons(); return { ok: true, pendingEdits };
      } catch (error) {
        suspended = true; flush(); buttons();
        announce(error.cancelled ? error.message : "暂时无法读取，当前填写内容已保留；请检查连接后重试。", true);
        return { ok: false };
      }
    }
    async function recover() {
      if (active || preview) return { ok: false };
      const pending = uncertain;
      announce("正在检查文件中的保存结果；当前填写内容保留在页面中。");
      try {
        const result = await request("GET");
        const same = pending && content(result.state) === content(pending.state);
        const correctMode = !!result.state.saved_at && (!pending || context.kind !== "daily-page" || (pending.mode === "review"
          ? !!result.state.review_saved_at
          : pending.priorReview ? result.state.review_saved_at === pending.priorReview : !result.state.review_saved_at));
        remember(result); suspended = false; uncertain = null;
        if (same && correctMode) {
          if (revision === pending.revision) {
            apply(result.state, false); dirty = false;
            clearOwnDraft();
          } else flush();
          announce(revision === pending.revision ? "已核对：刚才的内容已保存。" : "已核对之前的保存；最新填写内容仍未保存。");
          buttons(); return { ok: true, confirmed: true, current: revision === pending.revision, state: result.state };
        }
        candidate = { context, baseETag: pending ? pending.baseETag : null, state: read() };
        suspended = true; dirty = true; flush(); buttons();
        announce("已读取文件，但未确认当前内容已保存。请点击“恢复草稿”保留当前填写并准备重新保存，或重新读取已保存内容；不会自动重发。", true);
        return { ok: false };
      } catch (error) {
        suspended = true; flush(); buttons();
        announce(error.cancelled ? error.message : "仍无法确认保存结果。填写内容已保留，请回到对话恢复保存服务后再检查。", true);
        return { ok: false };
      }
    }
    async function save(next, mode) {
      if (preview) return { ok: false };
      if (active || suspended || !etag) {
        flush(); announce("请先检查保存状态或读取已保存内容；尚未发送新的保存请求。", true); buttons();
        return { ok: false };
      }
      const submitted = { state: clone(next), revision, mode, baseETag: etag, priorReview: disk && content(next) === content(disk) ? disk.review_saved_at : null };
      dirty = true; flush();
      announce(mode === "draft" ? "正在暂存填写进度……" : "正在保存填写内容……");
      try {
        const result = await request("POST", submitted.state, mode);
        remember(result);
        const current = revision === submitted.revision;
        if (current) {
          apply(result.state, false); dirty = false;
          clearOwnDraft();
        } else flush();
        buttons(); return { ok: true, current, state: result.state };
      } catch (error) {
        suspended = true; uncertain = submitted; flush(); buttons();
        if (error.conflict || error.cancelled || error.rejected) {
          candidate = { context, baseETag: submitted.baseETag, state: read(), ready: false };
          announce(error.message, true); buttons(); return { ok: false };
        }
        // Only an ambiguous failed POST needs one exceptional read, never a blind POST replay.
        return await recover();
      }
    }
    if (controls["cancel-wait"]) controls["cancel-wait"].addEventListener("click", () => { if (active) active.cancel(); });
    if (controls["recover-save"]) controls["recover-save"].addEventListener("click", recover);
    if (controls["restore-draft"]) controls["restore-draft"].addEventListener("click", () => {
      if (!active && candidate && candidate.ready !== false && etag) restore(dirty ? { ...candidate, state: read() } : candidate);
    });
    if (controls["export-draft"]) controls["export-draft"].addEventListener("click", () => {
      flush();
      const value = dirty ? read() : candidate && candidate.state;
      if (!value) return;
      const blob = new Blob([JSON.stringify({ draft_only: true, context, state: value }, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = `${context.date}-${context.kind}-未正式保存草稿.json`; link.click();
      URL.revokeObjectURL(url);
    });
    if (!preview) {
      root.addEventListener("pagehide", flush);
      root.addEventListener("beforeunload", event => {
        flush();
        if (dirty || active || uncertain) { event.preventDefault(); event.returnValue = ""; }
      });
    }
    buttons();
    return { load, save, edit, flush, recover, cancel: () => active && active.cancel(),
      isSuspended: () => suspended, isBusy: () => !!active, notify: announce };
  }
  root.DailySRLSaveClient = { create };
})(globalThis);
