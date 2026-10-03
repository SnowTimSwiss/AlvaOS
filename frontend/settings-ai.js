// Settings › Assistant: which AI service answers, and on or off.
(function () {
    const $ = (id) => document.getElementById(id);
    const HINTS = {
        'ollama-local': 'Free and private: Ollama on this NAS or another computer at home. Install it from ollama.com and download a model, like "ollama pull llama3.1".',
        'ollama-cloud': 'Ollama\'s own servers. Make a key at ollama.com › Settings › Keys.',
        openai: 'Make a key at platform.openai.com › API keys. Use costs money per question.',
        other: 'Any service that speaks the OpenAI chat API: LM Studio, OpenRouter, LocalAI, vLLM...',
    };
    let settings = null;

    async function api(path, options = {}) {
        const res = await fetch(`${API_BASE}${path}`, {
            ...options,
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'That did not work.');
        return data;
    }

    function syncProvider(fill) {
        const id = $('ai-provider').value;
        const preset = (settings.providers || {})[id] || {};
        $('ai-provider-hint').textContent = HINTS[id] || '';
        if (fill) {
            $('ai-url').value = preset.base_url || '';
            $('ai-model').value = preset.model || '';
        }
        const sameProvider = id === settings.provider;
        $('ai-key-field').hidden = id === 'ollama-local';
        $('ai-key').placeholder = sameProvider && settings.key_set ? 'Saved. Type to replace it.' : (preset.needs_key ? 'Needed' : 'Only if the service asks for one');
    }

    function render() {
        $('ai-enabled').checked = !!settings.enabled;
        $('ai-summary').textContent = settings.enabled
            ? `On. Answers come from ${settings.model} at ${new URL(settings.base_url).host}.`
            : 'Off. Needs an AI service, set up below.';
        const select = $('ai-provider');
        select.innerHTML = Object.entries(settings.providers || {})
            .map(([id, p]) => `<option value="${id}">${window.escapeHtml ? window.escapeHtml(p.label) : p.label}</option>`).join('');
        select.value = settings.provider;
        $('ai-url').value = settings.base_url;
        $('ai-model').value = settings.model;
        $('ai-key').value = '';
        const level = document.querySelector(`input[name="ai-level"][value="${settings.level === 'ask' ? 'ask' : 'read'}"]`);
        if (level) level.checked = true;
        syncProvider(false);
    }

    function collect() {
        return {
            provider: $('ai-provider').value,
            base_url: $('ai-url').value.trim(),
            model: $('ai-model').value.trim(),
            api_key: $('ai-key').value.trim(),
            level: document.querySelector('input[name="ai-level"]:checked')?.value || 'read',
        };
    }

    async function busy(button, label, work) {
        const before = button.textContent;
        button.disabled = true;
        button.textContent = label;
        $('ai-error').textContent = '';
        $('ai-ok').textContent = '';
        try {
            await work();
        } catch (e) {
            $('ai-error').textContent = e.message;
        } finally {
            button.disabled = false;
            button.textContent = before;
        }
    }

    async function save(enabled) {
        settings = await api('/ai/settings', { method: 'POST', body: JSON.stringify({ ...collect(), enabled }) });
        render();
        window.alvaosAssistantChanged?.(settings.enabled);
    }

    async function load() {
        if (!$('ai-form')) return;
        try {
            settings = await api('/ai/settings');
        } catch (_e) {
            $('ai-summary').textContent = 'Unavailable.';
            return;
        }
        render();
        $('ai-provider').addEventListener('change', () => syncProvider(true));
        $('ai-test').addEventListener('click', (event) => busy(event.target, 'Asking...', async () => {
            const data = await api('/ai/test', { method: 'POST', body: JSON.stringify(collect()) });
            $('ai-ok').textContent = `It answered: "${data.reply || '(nothing)'}"`;
        }));
        $('ai-form').addEventListener('submit', (event) => {
            event.preventDefault();
            busy($('ai-save'), 'Saving...', async () => {
                await save(settings.enabled);
                window.showToast?.('Assistant settings saved.', 'success');
            });
        });
        $('ai-enabled').addEventListener('change', async (event) => {
            const on = event.target.checked;
            try {
                await save(on);
                window.showToast?.(on ? 'The assistant is on. Find it at the top of every page.' : 'The assistant is off.', 'success');
            } catch (e) {
                event.target.checked = !on;
                $('ai-error').textContent = e.message;
            }
        });
    }

    load();
})();
