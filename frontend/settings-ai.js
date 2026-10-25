// Settings › AI: the AI services (each with its models) and the assistant.
// Chat in the Hub offers the models; who may use which is on the Hub page.
(function () {
    const $ = (id) => document.getElementById(id);
    const esc = (v) => (window.escapeHtml ? window.escapeHtml(String(v)) : String(v).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`));
    const HINTS = {
        'ollama-local': 'Free and private: Ollama on this NAS or another computer at home. Install it from ollama.com and download a model, like "ollama pull llama3.1".',
        'ollama-cloud': 'Ollama\'s own servers. Make a key at ollama.com › Settings › Keys.',
        openai: 'Make a key at platform.openai.com › API keys. Use costs money per question.',
        other: 'Any service that speaks the OpenAI chat API: OpenRouter, LM Studio, LocalAI, vLLM, Mistral, Groq...',
    };
    const THINKING = [['none', 'Answers at once'], ['on', 'Can think first'], ['effort', 'Can think, and how hard']];
    let settings = null;
    let editing = null;     // the service being edited: a copy, {id: '' } when new
    let found = null;       // the models the service listed, while editing

    async function api(path, options = {}) {
        const res = await fetch(`${API_BASE}${path}`, {
            ...options,
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'That did not work.');
        return data;
    }
    const host = (url) => { try { return new URL(url).host; } catch (_e) { return url || 'no address'; } };

    // ── The services ─────────────────────────────────────────────────────
    function serviceHtml(c) {
        const preset = settings.providers[c.provider] || {};
        const key = preset.needs_key ? (c.key_set ? 'key saved' : '<span class="ai-warn">needs a key</span>') : (c.key_set ? 'key saved' : '');
        const models = c.models.length
            ? c.models.map((m) => `<span class="ai-chip">${esc(m)}${(c.thinking || {})[m] && c.thinking[m] !== 'none' ? '<i title="Can think first">think</i>' : ''}</span>`).join('')
            : '<span class="ai-warn">No models yet: Edit › Find models.</span>';
        return `<div class="ai-svc" data-svc="${esc(c.id)}">
            <div class="ai-svc-head">
                <div><strong>${esc(c.name)}</strong><div class="ai-svc-sub">${esc(preset.label || c.provider)} · ${esc(host(c.base_url))}${key ? ` · ${key}` : ''}</div></div>
                <div class="ai-svc-btns"><button type="button" class="btn-secondary" data-edit>Edit</button><button type="button" class="btn-secondary btn-quiet" data-remove>Remove</button></div>
            </div>
            <div class="ai-chips">${models}</div>
        </div>`;
    }
    function editorHtml(c) {
        const providers = Object.entries(settings.providers).map(([id, p]) => `<option value="${id}"${id === c.provider ? ' selected' : ''}>${esc(p.label)}</option>`).join('');
        const saved = settings.connections.find((x) => x.id === c.id);
        const preset = settings.providers[c.provider] || {};
        const rows = c.models.map((m, i) => `<div class="ai-model-row">
                <span class="ai-model-name">${esc(m)}</span>
                <select data-thinking="${i}" aria-label="Thinking of ${esc(m)}">${THINKING.map(([v, label]) => `<option value="${v}"${(c.thinking[m] || 'none') === v ? ' selected' : ''}>${label}</option>`).join('')}</select>
                <button type="button" class="icon-btn" data-drop="${i}" aria-label="Remove ${esc(m)}" title="Remove">×</button>
            </div>`).join('');
        const offer = found === null ? '' : found.error
            ? `<p class="ai-warn">${esc(found.error)}</p>`
            : `<div class="ai-found"><span>Click to add:</span>${found.models.filter((m) => !c.models.includes(m)).map((m) => `<button type="button" class="ai-chip add" data-add="${esc(m)}">+ ${esc(m)}</button>`).join('') || '<em>all added</em>'}</div>`;
        return `<form class="ai-svc ai-svc-edit pw-form" id="ai-svc-form" novalidate>
            <label class="pw-field">Service <select id="svc-provider">${providers}</select><small>${esc(HINTS[c.provider] || '')}</small></label>
            <label class="pw-field">Name <input type="text" id="svc-name" maxlength="40" value="${esc(c.name || '')}" placeholder="${esc(preset.label || '')}"></label>
            <label class="pw-field">Address <input type="url" id="svc-url" value="${esc(c.base_url || '')}" placeholder="http://192.168.1.20:11434/v1" autocomplete="off" spellcheck="false"></label>
            <label class="pw-field"${c.provider === 'ollama-local' ? ' hidden' : ''}>API key <input type="password" id="svc-key" autocomplete="new-password" spellcheck="false" value="${esc(c.api_key || '')}" placeholder="${saved && saved.key_set ? 'Saved. Type to replace it.' : (preset.needs_key ? 'Needed' : 'Only if the service asks for one')}"><small>Stored on this NAS only, never shown again.</small></label>
            <div class="pw-field"><span>Models</span>
                <div class="ai-models">${rows || '<p class="ai-svc-sub">No models yet.</p>'}</div>
                <div class="ai-model-add"><input type="text" id="svc-model" placeholder="Add a model, like qwen3:8b" spellcheck="false" aria-label="Add a model"><button type="button" class="btn-secondary" id="svc-model-add">Add</button><button type="button" class="btn-secondary" id="svc-find">Find models</button></div>
                ${offer}
            </div>
            <div class="pw-error" id="svc-error" role="alert"></div>
            <div class="pw-ok" id="svc-ok" role="status"></div>
            <div class="modal-actions">
                <button type="button" class="btn-secondary" id="svc-test">Try it</button>
                <button type="button" class="btn-secondary btn-quiet" id="svc-cancel">Cancel</button>
                <button type="submit" class="btn-primary" id="svc-save">Save</button>
            </div>
        </form>`;
    }
    function renderServices() {
        const list = settings.connections.map((c) => (editing && editing.id === c.id ? editorHtml(editing) : serviceHtml(c)));
        if (editing && !editing.id) list.push(editorHtml(editing));
        $('ai-services').innerHTML = list.join('') || '<p class="set-group-desc">No AI service yet. Add one: Ollama at home is free and keeps everything in your house.</p>';
        $('ai-add').hidden = !!editing;
        if (editing) bindEditor();
    }
    function fromForm() {
        // What is typed now, into the copy being edited.
        editing.name = $('svc-name').value.trim();
        editing.base_url = $('svc-url').value.trim();
        editing.api_key = $('svc-key').value.trim();
        document.querySelectorAll('[data-thinking]').forEach((sel) => { editing.thinking[editing.models[Number(sel.dataset.thinking)]] = sel.value; });
    }
    function draft() {
        return { id: editing.id, name: editing.name, provider: editing.provider, base_url: editing.base_url, api_key: editing.api_key };
    }
    function addModel(name, thinking) {
        const m = String(name || '').trim();
        if (!m || editing.models.includes(m)) return;
        editing.models.push(m);
        editing.thinking[m] = thinking || 'none';
    }
    function bindEditor() {
        $('svc-provider').addEventListener('change', (e) => {
            fromForm();
            const preset = settings.providers[e.target.value] || {};
            const before = settings.providers[editing.provider] || {};
            if (!editing.name || editing.name === before.label) editing.name = preset.label;
            editing.base_url = preset.base_url || '';
            editing.provider = e.target.value;
            found = null;
            renderServices();
        });
        $('svc-model-add').addEventListener('click', () => {
            fromForm();
            const name = $('svc-model').value;
            if (!/^[A-Za-z0-9._:/@+-]{1,120}$/.test(name.trim())) { $('svc-error').textContent = 'Model names look like llama3.1 or gpt-4o-mini.'; return; }
            addModel(name, null);
            renderServices();
            $('svc-model').focus();
        });
        $('svc-model').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); $('svc-model-add').click(); } });
        $('svc-find').addEventListener('click', (e) => busy(e.target, 'Asking...', 'svc', async () => {
            fromForm();
            try {
                const data = await api('/ai/models', { method: 'POST', body: JSON.stringify({ connection: draft() }) });
                found = { models: data.models || [], thinking: data.thinking || {} };
            } catch (err) {
                found = { error: err.message, models: [] };
            }
            renderServices();
        }));
        document.querySelectorAll('[data-add]').forEach((b) => b.addEventListener('click', () => {
            fromForm();
            addModel(b.dataset.add, (found.thinking || {})[b.dataset.add]);
            renderServices();
        }));
        document.querySelectorAll('[data-drop]').forEach((b) => b.addEventListener('click', () => {
            fromForm();
            const m = editing.models.splice(Number(b.dataset.drop), 1)[0];
            delete editing.thinking[m];
            renderServices();
        }));
        $('svc-cancel').addEventListener('click', () => { editing = null; found = null; renderServices(); });
        $('svc-test').addEventListener('click', (e) => busy(e.target, 'Asking...', 'svc', async () => {
            fromForm();
            if (!editing.models.length) throw new Error('Add a model first.');
            const data = await api('/ai/test', { method: 'POST', body: JSON.stringify({ connection: draft(), model: editing.models[0] }) });
            $('svc-ok').textContent = `${editing.models[0]} answered: "${data.reply || '(nothing)'}"`;
        }));
        $('ai-svc-form').addEventListener('submit', (e) => {
            e.preventDefault();
            busy($('svc-save'), 'Saving...', 'svc', async () => {
                fromForm();
                const others = settings.connections.map((c) => (c.id === editing.id ? editing : c));
                if (!editing.id) others.push(editing);
                await saveServices(others);
                editing = null;
                found = null;
                renderServices();
                window.showToast?.('AI service saved.', 'success');
            });
        });
    }
    async function saveServices(list) {
        const connections = list.map((c) => ({
            id: c.id, name: c.name, provider: c.provider, base_url: c.base_url, models: c.models,
            thinking: c.thinking, api_key: c.api_key || '',
        }));
        settings = await api('/ai/settings', { method: 'POST', body: JSON.stringify({ connections }) });
        renderAssistant();
    }
    async function onServiceClick(e) {
        const box = e.target.closest('[data-svc]');
        if (!box || editing) return;
        const c = settings.connections.find((x) => x.id === box.dataset.svc);
        if (e.target.closest('[data-edit]')) {
            editing = { ...c, models: [...c.models], thinking: { ...(c.thinking || {}) }, api_key: '' };
            found = null;
            renderServices();
        } else if (e.target.closest('[data-remove]')) {
            if (!window.confirm(`Remove ${c.name}? Its models go out of Chat and the assistant.`)) return;
            try {
                await saveServices(settings.connections.filter((x) => x.id !== c.id));
                renderServices();
                window.showToast?.(`${c.name} removed.`, 'success');
            } catch (err) {
                window.showToast?.(err.message, 'error');
            }
        }
    }

    // ── The assistant ────────────────────────────────────────────────────
    function renderAssistant() {
        const conn = settings.connections.find((c) => c.id === settings.assistant.connection);
        $('ai-enabled').checked = !!settings.enabled;
        $('ai-summary').textContent = settings.enabled && conn
            ? `On. Answers come from ${settings.model} (${conn.name}).`
            : (settings.connections.length ? 'Off.' : 'Off. Needs an AI service, set up above.');
        const select = $('ai-model');
        select.innerHTML = settings.connections.filter((c) => c.models.length).map((c) => `<optgroup label="${esc(c.name)}">${
            c.models.map((m) => `<option value="${esc(`${c.id}/${m}`)}">${esc(m)}</option>`).join('')}</optgroup>`).join('');
        select.value = conn ? `${conn.id}/${settings.model}` : '';
        select.disabled = !select.options.length;
        const level = document.querySelector(`input[name="ai-level"][value="${settings.level === 'ask' ? 'ask' : 'read'}"]`);
        if (level) level.checked = true;
    }
    function chosen() {
        const v = $('ai-model').value;
        const cut = v.indexOf('/');
        const out = { level: document.querySelector('input[name="ai-level"]:checked')?.value || 'read' };
        if (cut > 0) out.assistant = { connection: v.slice(0, cut), model: v.slice(cut + 1) };
        return out;
    }

    async function busy(button, label, where, work) {
        const before = button.textContent;
        const err = where === 'svc' ? $('svc-error') : $('ai-error');
        const ok = where === 'svc' ? $('svc-ok') : $('ai-ok');
        button.disabled = true;
        button.textContent = label;
        if (err) err.textContent = '';
        if (ok) ok.textContent = '';
        try {
            await work();
        } catch (e) {
            const now = where === 'svc' ? $('svc-error') : $('ai-error');
            if (now) now.textContent = e.message; else window.showToast?.(e.message, 'error');
        } finally {
            button.disabled = false;
            button.textContent = before;
        }
    }

    async function load() {
        if (!$('ai-form')) return;
        try {
            settings = await api('/ai/settings');
        } catch (_e) {
            $('ai-summary').textContent = 'Unavailable.';
            return;
        }
        renderServices();
        renderAssistant();
        $('ai-services').addEventListener('click', onServiceClick);
        $('ai-add').addEventListener('click', () => {
            const p = settings.providers['ollama-local'];
            editing = { id: '', name: p.label, provider: 'ollama-local', base_url: p.base_url, models: [], thinking: {}, api_key: '' };
            found = null;
            renderServices();
            $('svc-name').focus();
        });
        $('ai-test').addEventListener('click', (event) => busy(event.target, 'Asking...', 'ai', async () => {
            const pick = chosen().assistant;
            if (!pick) throw new Error('Add an AI service with a model first.');
            const data = await api('/ai/test', { method: 'POST', body: JSON.stringify({ connection: { id: pick.connection }, model: pick.model }) });
            $('ai-ok').textContent = `It answered: "${data.reply || '(nothing)'}"`;
        }));
        $('ai-form').addEventListener('submit', (event) => {
            event.preventDefault();
            busy($('ai-save'), 'Saving...', 'ai', async () => {
                settings = await api('/ai/settings', { method: 'POST', body: JSON.stringify(chosen()) });
                renderAssistant();
                window.showToast?.('Assistant settings saved.', 'success');
            });
        });
        $('ai-enabled').addEventListener('change', async (event) => {
            const on = event.target.checked;
            try {
                settings = await api('/ai/settings', { method: 'POST', body: JSON.stringify({ ...chosen(), enabled: on }) });
                renderAssistant();
                window.alvaosAssistantChanged?.(settings.enabled);
                window.showToast?.(on ? 'The assistant is on. Find it at the top of every page.' : 'The assistant is off.', 'success');
            } catch (e) {
                event.target.checked = !on;
                $('ai-error').textContent = e.message;
            }
        });
    }

    load();
})();
