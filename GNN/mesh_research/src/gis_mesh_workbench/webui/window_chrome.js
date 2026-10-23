(function (root) {
  'use strict';

  function init({ request, onError } = {}) {
    if (root.document?.body?.dataset?.desktopChrome !== 'true') return () => {};
    if (typeof request !== 'function') throw new TypeError('Cần hàm request API để điều khiển cửa sổ.');

    const reportError = typeof onError === 'function' ? onError : () => {};
    const buttons = Array.from(root.document.querySelectorAll('[data-window-action]'));
    const resizeHandles = Array.from(root.document.querySelectorAll('[data-window-resize]'));
    const dragRegions = Array.from(root.document.querySelectorAll('.window-chrome__drag-region'));
    const listeners = [];
    let maximizeRevision = 0;

    function bind(target, type, handler) {
      target.addEventListener(type, handler);
      listeners.push([target, type, handler]);
    }

    function setMaximized(maximized) {
      const button = buttons.find(item => item.dataset.windowAction === 'toggle_maximize');
      if (!button) return;
      const label = maximized ? 'Khôi phục cửa sổ' : 'Phóng to cửa sổ';
      button.setAttribute('aria-pressed', String(maximized));
      button.setAttribute('aria-label', label);
      button.setAttribute('title', label);
    }

    async function perform(action, button = null) {
      const revision = action === 'toggle_maximize' ? ++maximizeRevision : null;
      if (button) button.disabled = true;
      try {
        const result = await request('/api/window/control', { action });
        if (revision !== null && revision === maximizeRevision && typeof result?.maximized === 'boolean') {
          setMaximized(result.maximized);
        }
      } catch (error) {
        try { reportError(error); } catch (_) { /* An error reporter must not strand a titlebar control. */ }
      } finally {
        if (button) button.disabled = false;
      }
    }

    for (const button of buttons) {
      bind(button, 'click', () => { void perform(button.dataset.windowAction, button); });
    }

    for (const handle of resizeHandles) {
      bind(handle, 'pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        void perform(`resize-${handle.dataset.windowResize}`);
      });
    }

    for (const region of dragRegions) {
      bind(region, 'dblclick', event => {
        event.preventDefault();
        void perform('toggle_maximize');
      });
    }

    return () => { for (const [target, type, handler] of listeners) target.removeEventListener(type, handler); };
  }

  root.MeshCherryChrome = Object.freeze({ init });
})(typeof window === 'undefined' ? globalThis : window);
