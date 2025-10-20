// app/static/js/pos_customizations.js
(function () {
  // Evita doppio wiring
  if (window.__POS_CUSTOM_JS_WIRED__) return;
  window.__POS_CUSTOM_JS_WIRED__ = true;

  // -------- Utils ----------
  const toCents = (v) => Number.parseInt(v ?? 0, 10) || 0;
  const normChoices = (pr) => {
    if (Array.isArray(pr.choices)) return pr.choices;
    const csv = (typeof pr.choices_csv === 'string' ? pr.choices_csv : (typeof pr.choices === 'string' ? pr.choices : '')) || '';
    return csv.split(';').map(s => s.trim()).filter(Boolean);
  };
  const getDelta = (pr) => toCents(pr.delta ?? pr.price_delta_cents ?? 0);

  async function fetchPrompts(productId) {
    try {
      const r = await fetch(`/api/products/${productId}/prompts`, { cache: 'no-store' });
      if (!r.ok) return [];
      const arr = await r.json();
      if (!Array.isArray(arr)) return [];
      return arr.map(pr => ({
        name: String(pr.name || '').trim(),
        kind: String(pr.kind || 'single').toLowerCase(),
        required: !!pr.required,
        delta: getDelta(pr),
        choices: normChoices(pr),
      }));
    } catch {
      return [];
    }
  }

  // -------- Modal (senza observers) ----------
  let MODAL_OPEN = false;

  function lockPage(on) {
    const form = document.getElementById('pos-form');
    if (on) {
      if (form) form.style.pointerEvents = 'none';
      document.documentElement.classList.add('bs-no-scroll');
      document.body.classList.add('bs-no-scroll');
    } else {
      if (form) form.style.pointerEvents = '';
      document.documentElement.classList.remove('bs-no-scroll');
      document.body.classList.remove('bs-no-scroll');
    }
  }

  function openModal(title, prompts) {
    if (MODAL_OPEN) return Promise.resolve(null);
    MODAL_OPEN = true;
    lockPage(true);

    const overlay = document.createElement('div');
    overlay.style.cssText = `
      position:fixed; inset:0; z-index:2147483647;
      display:flex; align-items:flex-start; justify-content:center;
      background:rgba(0,0,0,.45); padding-top:10vh;
    `;

    const card = document.createElement('div');
    card.className = 'card';
    card.style.cssText = `
      width:min(560px,96vw); max-height:72vh; overflow:auto;
      border-radius:14px; background:var(--card,#111); color:var(--text,#eee);
      box-shadow:0 10px 30px rgba(0,0,0,.35), 0 2px 10px rgba(0,0,0,.25);
    `;
    card.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:10px 12px;border-bottom:1px solid var(--border,#333);">
        <div style="font-weight:700;">${title}</div>
        <button class="btn" data-close aria-label="Chiudi">✖</button>
      </div>
      <div id="m-body" style="padding:12px;"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;padding:10px 12px;border-top:1px solid var(--border,#333);">
        <button class="btn" data-close>Annulla</button>
        <button class="btn is-primary" id="m-ok">Conferma</button>
      </div>
    `;

    overlay.appendChild(card);
    document.body.appendChild(overlay);

    // Compila campi
    const body = card.querySelector('#m-body');
    prompts.forEach((pr, i) => {
      const group = document.createElement('div');
      group.style.marginBottom = '10px';
      group.dataset.delta = String(pr.delta || 0);

      if (pr.kind === 'boolean') {
        group.innerHTML = `
          <label class="text-s">
            <input type="checkbox" name="p_${i}" ${pr.required ? 'checked' : ''}>
            ${pr.name} ${pr.delta ? `(Δ ${(pr.delta/100).toFixed(2)}€)` : ''}
          </label>`;
      } else if (pr.kind === 'single') {
        const opts = pr.choices.map(c => `<option>${c}</option>`).join('');
        group.innerHTML = `
          <label class="text-s" style="display:block;margin-bottom:6px;">${pr.name}${pr.required ? ' *' : ''}</label>
          <select class="input" name="p_${i}">${opts}</select>`;
      } else { // multi
        const checks = pr.choices.map((c, j) => `
          <label class="text-s" style="display:inline-flex;gap:6px;align-items:center;margin:0 12px 6px 0;">
            <input type="checkbox" name="p_${i}_${j}" value="${c}"> ${c}
          </label>`).join('');
        group.innerHTML = `
          <label class="text-s" style="display:block;margin-bottom:6px;">${pr.name}${pr.required ? ' *' : ''}</label>
          <div>${checks}</div>`;
      }
      body.appendChild(group);
    });

    function close() {
      overlay.removeEventListener('click', onClick);
      card.querySelector('#m-ok')?.removeEventListener('click', onOk);
      overlay.remove();
      lockPage(false);
      MODAL_OPEN = false;
    }
    function onClick(ev) {
      const t = ev.target;
      if (t === overlay || (t && t.hasAttribute && t.hasAttribute('data-close'))) {
        close();
      }
    }
    function onOk() {
      const chosen = [];
      const groups = body.children;
      for (let i = 0; i < prompts.length; i++) {
        const pr = prompts[i];
        const g = groups[i];
        const baseDelta = toCents(g?.dataset.delta);
        if (pr.kind === 'boolean') {
          const chk = g.querySelector(`[name=p_${i}]`);
          const on = !!chk?.checked;
          chosen.push({ name: pr.name, value: on ? 'sì' : 'no', delta: on ? baseDelta : 0 });
        } else if (pr.kind === 'single') {
          const sel = g.querySelector(`[name=p_${i}]`);
          if (pr.required && (!sel || !sel.value)) { alert(`Seleziona: ${pr.name}`); return; }
          chosen.push({ name: pr.name, value: sel?.value || '', delta: baseDelta });
        } else {
          const checks = Array.from(g.querySelectorAll(`[name^=p_${i}_]:checked`));
          const vals = checks.map(c => c.value);
          if (pr.required && !vals.length) { alert(`Seleziona: ${pr.name}`); return; }
          chosen.push({ name: pr.name, value: vals.join('; '), delta: baseDelta * vals.length });
        }
      }
      resolver(chosen);
      close();
    }

    overlay.addEventListener('click', onClick, { passive: true });
    card.querySelector('#m-ok').addEventListener('click', onOk);

    let resolver;
    return new Promise(res => { resolver = res; });
  }

  // -------- Hook pubblico ----------
  window.wireCustomizationHook = function ({ gridSelector = '#products-area', plusBtnSelector = '.btn-add', addLine } = {}) {
    const grid = document.querySelector(gridSelector);
    if (!grid) return;

    const fallbackAdd = (line) => {
      const input = document.querySelector(`input[name="qty_${line.product_id}"]`);
      if (input) input.value = String((parseInt(input.value || '0', 10) || 0) + 1);
      const hidden = document.getElementById('cart-json');
      if (hidden && window.cart && window.cart.lines) hidden.value = JSON.stringify(window.cart.lines);
    };
    const add = (typeof addLine === 'function') ? addLine : fallbackAdd;

    grid.addEventListener('click', async (ev) => {
      const btn = ev.target.closest(plusBtnSelector);
      if (!btn || MODAL_OPEN) return;

      // evita eventuali doppie propagazioni
      ev.stopPropagation();

      const card = btn.closest('[data-product-id]');
      if (!card) return;

      const product = {
        id: parseInt(card.dataset.productId || '0', 10),
        name: card.dataset.productName || card.querySelector('.title')?.textContent?.trim() || 'Prodotto',
        price_cents: toCents(card.dataset.priceCents),
      };

      const prompts = await fetchPrompts(product.id);

      // nessun prompt -> aggiunta diretta
      if (!prompts.length) {
        add({
          product_id: product.id,
          name: product.name,
          qty: 1,
          unit_price_cents: product.price_cents,
          options: []
        });
        const hidden = document.getElementById('cart-json');
        if (hidden && window.cart?.lines) hidden.value = JSON.stringify(window.cart.lines);
        return;
      }

      // con prompt -> modale
      const chosen = await openModal(`Opzioni — ${product.name}`, prompts);
      if (!chosen) return; // annullata

      const delta = chosen.reduce((s, o) => s + toCents(o.delta), 0);
      add({
        product_id: product.id,
        name: product.name,
        qty: 1,
        unit_price_cents: product.price_cents + delta,
        options: chosen
      });

      const hidden = document.getElementById('cart-json');
      if (hidden && window.cart?.lines) hidden.value = JSON.stringify(window.cart.lines);
    });
  };
})();
