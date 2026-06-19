// Engines surface in operational priority order — the order we lead with and
// trust — rather than alphabetically. Anything unrecognised sorts after, by
// name, so a newly added engine still shows up predictably.
const ENGINE_ORDER = ['google', 'bing', 'yahoo', 'brave'];
function compareEngines(a, b) {
    const rank = (name) => {
        const i = ENGINE_ORDER.indexOf(name.toLowerCase());
        return i === -1 ? ENGINE_ORDER.length : i;
    };
    return rank(a) - rank(b) || a.localeCompare(b);
}

// Brand marks for the engine filter buttons. Engines without a logo here still
// render as a labelled button — the mark is a recognition aid, not the label.
const ENGINE_LOGOS = {
    google: '/static/engines/google.svg',
    bing: '/static/engines/bing.svg',
    yahoo: '/static/engines/yahoo.svg',
    brave: '/static/engines/brave.svg',
};

// TopicStreams Frontend Application
class TopicStreamsApp {
    apiBase = '/api/v1';
    feedPageSize = 20;
    // UI tuning defaults; overridden from GET /api/v1/config at startup so they
    // stay in sync with the server without a frontend rebuild.
    statusPollIntervalMs = 30000;
    wsReconnectBaseMs = 5000;
    wsReconnectMaxMs = 30000;
    topics = new Map();
    activeTopicSubscriptions = new Set();
    topicWebSockets = new Map();
    reconnectAttempts = new Map();
    apiKey = localStorage.getItem('topicstreams-api-key') || '';
    apiKeyDeclined = false;     // user dismissed the key prompt; stop auto-nagging

    // Real-time feed: a single chronological stream backed by the DB. Live
    // WebSocket entries prepend at the top; scrolling loads older pages via an
    // id cursor (feedCursor = next_before_id from the API).
    feedFilter = '';            // '' = all topics, else a topic name
    feedEngine = '';            // '' = all engines, else one engine name
    knownEngines = [];          // engines offered as filter buttons
    feedCursor = null;          // id cursor for the next (older) page
    feedHasMore = true;
    feedLoading = false;
    feedError = false;
    feedIds = new Set();        // rendered entry ids, for dedup
    feedRequestToken = 0;       // guards against stale responses after a reset

    constructor() {
        this.init();
    }

    async init() {
        this.bindEvents();
        this.setupFeedObserver();
        await this.loadConfig();
        this.loadInitialData();
        this.startStatusUpdates();
    }

    // One-time pull of the UI tuning values (page size, poll cadence, WS
    // backoff). Must resolve before the first feed page loads, since
    // feedPageSize drives the page request. On failure we keep the defaults.
    async loadConfig() {
        try {
            const response = await this.fetchWithAuth(`${this.apiBase}/config`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const cfg = await response.json();
            this.feedPageSize = cfg.feed_page_size;
            this.statusPollIntervalMs = cfg.status_poll_interval_ms;
            this.wsReconnectBaseMs = cfg.ws_reconnect_base_ms;
            this.wsReconnectMaxMs = cfg.ws_reconnect_max_ms;
        } catch (error) {
            console.error('Failed to load UI config, using defaults:', error);
        }
    }

    bindEvents() {
        // Topic management
        document.getElementById('add-topic-btn').addEventListener('click', () => this.addTopic());
        document.getElementById('topic-input').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') this.addTopic();
        });

        // Feed controls live as clickable buttons in their own rows: topic
        // slugs in the tracking strip (see renderTopics) and engine buttons in
        // the wire head (see updateEngineFilter), so there's nothing to bind
        // here.
    }

    // The feed-status row sits at the bottom of the scroll container and acts
    // as the sentinel: when it scrolls into view, load the next older page.
    setupFeedObserver() {
        const container = document.getElementById('news-container');
        const sentinel = document.getElementById('feed-status');
        this.feedObserver = new IntersectionObserver(
            (entries) => {
                if (entries.some((e) => e.isIntersecting)) this.loadFeedPage();
            },
            { root: container, rootMargin: '200px' }
        );
        this.feedObserver.observe(sentinel);
    }

    // Every endpoint requires `Authorization: Bearer <token>` when the server
    // has TOPICSTREAMS_API_KEY set. All requests flow through here. On a 401 we
    // prompt once for the token, store it, and retry; if the user dismisses the
    // prompt we latch that (apiKeyDeclined) so background polls and the parallel
    // startup fetches don't trigger a storm of prompts. A user action that
    // clears the latch (see addTopic) re-enables prompting.
    async fetchWithAuth(url, options = {}) {
        const doFetch = () => {
            const headers = { ...(options.headers || {}) };
            if (this.apiKey) headers['Authorization'] = `Bearer ${this.apiKey}`;
            return fetch(url, { ...options, headers });
        };

        let response = await doFetch();
        if (response.status === 401 && !this.apiKeyDeclined) {
            const key = prompt('This server requires an API token. Enter it:');
            if (key?.trim()) {
                this.apiKey = key.trim();
                localStorage.setItem('topicstreams-api-key', this.apiKey);
                response = await doFetch();
            } else {
                this.apiKeyDeclined = true;
            }
        }
        return response;
    }

    async loadInitialData() {
        await Promise.all([
            this.loadTopics(),
            this.loadEngines(),
            this.updateStatus()
        ]);
        this.resetFeed();
    }

    // Engines are an orthogonal feed filter (which search engine surfaced an
    // entry). Only engines with data are offered, so the list grows as more
    // engines are enabled in the scraper.
    async loadEngines() {
        try {
            const response = await this.fetchWithAuth(`${this.apiBase}/news/engines`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            this.updateEngineFilter(await response.json());
        } catch (error) {
            console.error('Failed to load engines:', error);
        }
    }

    updateEngineFilter(engines) {
        const next = Array.from(engines).sort(compareEngines);
        // Avoid rebuilding the button row when the engine set is unchanged.
        if (next.join(' ') === this.knownEngines.join(' ')) return;
        this.knownEngines = next;

        const container = document.getElementById('engine-filter');
        const tpl = document.getElementById('engine-btn-template');
        container.replaceChildren();
        for (const engine of next) {
            const btn = tpl.content.firstElementChild.cloneNode(true);
            btn.dataset.engine = engine;
            btn.title = `Show only results surfaced by ${engine}`;

            const img = btn.querySelector('.engine-btn__logo');
            const logo = ENGINE_LOGOS[engine.toLowerCase()];
            if (logo) { img.src = logo; } else { img.remove(); }

            btn.querySelector('.engine-btn__name').textContent = engine;
            btn.addEventListener('click', () => this.setEngineFilter(engine));
            container.appendChild(btn);
        }

        // The selected engine disappeared (no longer has data) — fall back to
        // the all-engines view so the feed doesn't stick on a dead filter.
        if (this.feedEngine && !next.includes(this.feedEngine)) {
            this.feedEngine = '';
            this.resetFeed();
        }
        this.applyEngineFilterStates();
    }

    // Tune the wire to one engine. Clicking the active engine again releases the
    // filter back to the all-engines stream. Mirrors the topic slugs.
    setEngineFilter(name) {
        this.feedEngine = this.feedEngine === name ? '' : name;
        this.applyEngineFilterStates();
        this.resetFeed();
    }

    applyEngineFilterStates() {
        const container = document.getElementById('engine-filter');
        container.classList.toggle('is-filtering', !!this.feedEngine);
        for (const btn of container.querySelectorAll('.engine-btn')) {
            const isActive = btn.dataset.engine === this.feedEngine;
            btn.classList.toggle('is-active', isActive);
            btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
        }
    }

    async loadTopics() {
        try {
            const response = await this.fetchWithAuth(`${this.apiBase}/topics`);
            const topics = await response.json();

            this.topics.clear();
            for (const topic of topics) {
                this.topics.set(topic.name, topic);
            }

            this.renderTopics();
            this.syncSubscriptions();
        } catch (error) {
            console.error('Failed to load topics:', error);
            this.showError('Failed to load topics');
        }
    }

    // Every active topic is watched automatically: open a WebSocket for any
    // newly seen topic and drop subscriptions for topics that no longer exist.
    syncSubscriptions() {
        const activeNames = new Set(
            Array.from(this.topics.values()).filter(t => t.is_active).map(t => t.name)
        );

        for (const name of activeNames) {
            if (!this.activeTopicSubscriptions.has(name)) {
                this.subscribeToTopic(name);
            }
        }

        for (const name of Array.from(this.activeTopicSubscriptions)) {
            if (!activeNames.has(name)) {
                this.unsubscribeFromTopic(name);
            }
        }
    }

    // Scrape health is computed server-side (recency, per-topic success, and
    // selector-rot when scrapes parse 0 items) and returned with the counts.
    async updateStatus() {
        try {
            const response = await this.fetchWithAuth(`${this.apiBase}/status`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const s = await response.json();

            document.getElementById('active-topics').textContent = s.active_topics;
            document.getElementById('total-news').textContent = s.total_news;
            this.setStatus(s.state, s.label, s.detail);
        } catch (error) {
            console.error('Failed to update status:', error);
            this.setStatus('offline', 'offline', "Can't reach the API");
        }
    }

    setStatus(state, label, detail) {
        const onair = document.querySelector('.onair');
        if (onair) {
            onair.dataset.state = state;
            onair.title = detail || '';
        }
        document.getElementById('scraper-status').textContent = label;
    }

    renderTopics() {
        const container = document.getElementById('topic-cards');
        const activeTopics = Array.from(this.topics.values()).filter(t => t.is_active);

        // The filtered topic may have just been deleted (or deactivated) — drop
        // the dead filter so the feed doesn't stick on a channel that's gone.
        if (this.feedFilter && !activeTopics.some(t => t.name === this.feedFilter)) {
            this.feedFilter = '';
            this.resetFeed();
        }

        if (activeTopics.length === 0) {
            container.innerHTML = '<div class="no-news">No active topics. Add a topic to get started!</div>';
            return;
        }

        const template = document.getElementById('topic-card-template');
        container.innerHTML = '';

        for (const topic of activeTopics) {
            const card = template.content.firstElementChild.cloneNode(true);
            card.dataset.topic = topic.name;

            const nameBtn = card.querySelector('.topic-name');
            nameBtn.textContent = topic.name;
            nameBtn.title = `Filter the wire to “${topic.name}”`;
            nameBtn.addEventListener('click', () => this.setTopicFilter(topic.name));

            const deleteBtn = card.querySelector('.delete-btn');
            deleteBtn.setAttribute('aria-label', `Stop tracking ${topic.name}`);
            deleteBtn.addEventListener('click', () => this.deleteTopic(topic.name));

            container.appendChild(card);
        }

        this.applyTopicFilterStates();
    }

    // Tune the wire to a topic. Clicking the active slug again releases the
    // filter back to the all-topics stream.
    setTopicFilter(name) {
        this.feedFilter = this.feedFilter === name ? '' : name;
        this.applyTopicFilterStates();
        this.resetFeed();
    }

    // Reflect the current filter on the slugs: highlight the active channel,
    // dim the rest, and expose its delete control.
    applyTopicFilterStates() {
        const container = document.getElementById('topic-cards');
        container.classList.toggle('is-filtering', !!this.feedFilter);
        for (const card of container.querySelectorAll('.topic-card')) {
            const isActive = card.dataset.topic === this.feedFilter;
            card.classList.toggle('is-active', isActive);
            card.querySelector('.topic-name')
                ?.setAttribute('aria-pressed', isActive ? 'true' : 'false');
        }
    }

    async addTopic() {
        const input = document.getElementById('topic-input');
        const topicName = input.value.trim();

        if (!topicName) {
            input.focus();
            return;
        }

        const button = document.getElementById('add-topic-btn');
        button.disabled = true;
        button.textContent = 'tracking…';
        // An explicit user action: re-enable the token prompt if it was dismissed.
        this.apiKeyDeclined = false;

        try {
            const response = await this.fetchWithAuth(`${this.apiBase}/topics`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: topicName })
            });

            if (!response.ok) {
                throw new Error('Failed to add topic');
            }

            input.value = '';
            await this.loadTopics();
            this.updateStatus();
            this.showSuccess(`Topic "${topicName}" added successfully!`);
        } catch (error) {
            console.error('Failed to add topic:', error);
            this.showError(`Failed to add topic "${topicName}"`);
        } finally {
            button.disabled = false;
            button.innerHTML = '+ track';
        }
    }

    async deleteTopic(topicName) {
        const confirmed = await this.confirmDialog({
            title: 'Stop tracking?',
            body: `“${topicName}” will leave the wire and stop being scraped. News already collected is kept.`,
            confirmLabel: 'stop tracking',
        });
        if (!confirmed) return;

        try {
            const response = await this.fetchWithAuth(`${this.apiBase}/topics/${encodeURIComponent(topicName)}`, {
                method: 'DELETE'
            });

            if (!response.ok) {
                throw new Error('Failed to delete topic');
            }

            // Unsubscribe from topic if subscribed
            if (this.activeTopicSubscriptions.has(topicName)) {
                this.unsubscribeFromTopic(topicName);
            }

            await this.loadTopics();
            this.updateStatus();
            this.showSuccess(`Topic "${topicName}" deleted successfully!`);
        } catch (error) {
            console.error('Failed to delete topic:', error);
            this.showError(`Failed to delete topic "${topicName}"`);
        }
    }

    subscribeToTopic(topicName) {
        if (this.activeTopicSubscriptions.has(topicName)) {
            return; // Already subscribed
        }

        // Connect to WebSocket for this topic
        this.connectTopicWebSocket(topicName);
        this.activeTopicSubscriptions.add(topicName);
    }

    unsubscribeFromTopic(topicName) {
        this.activeTopicSubscriptions.delete(topicName);
        this.closeTopicWebSocket(topicName);
        this.resetReconnectAttempts(topicName);
    }

    connectTopicWebSocket(topicName) {
        // Close existing WebSocket if it exists
        this.closeTopicWebSocket(topicName);

        // Derive from the page location: the app may be served on any host
        // port (e.g. compose maps HOST_PORT->API_PORT), and wss is needed
        // when served over https.
        const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
        const wsUrl = `${wsProtocol}://${window.location.host}/api/v1/ws/news/${encodeURIComponent(topicName)}`;
        const ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            console.log(`Connected to WebSocket for topic: ${topicName}`);
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                // The WebSocket manager sends raw NewsEntry objects directly
                if (data?.title && data?.url) {
                    this.handleLiveEntry(data);
                }
            } catch (error) {
                console.error('Failed to parse WebSocket message:', error);
            }
        };

        ws.onclose = () => {
            console.log(`Disconnected from WebSocket for topic: ${topicName}`);
            this.topicWebSockets.delete(topicName);

            // Reconnect if still subscribed (with backoff)
            if (this.activeTopicSubscriptions.has(topicName)) {
                const backoffTime = Math.min(this.wsReconnectMaxMs, this.wsReconnectBaseMs * Math.pow(2, this.getReconnectAttempts(topicName)));
                this.incrementReconnectAttempts(topicName);

                setTimeout(() => {
                    if (this.activeTopicSubscriptions.has(topicName)) {
                        this.connectTopicWebSocket(topicName);
                    }
                }, backoffTime);
            }
        };

        ws.onerror = (error) => {
            console.error(`WebSocket error for topic ${topicName}:`, error);
        };

        this.topicWebSockets.set(topicName, ws);
        this.resetReconnectAttempts(topicName);
    }

    closeTopicWebSocket(topicName) {
        const ws = this.topicWebSockets.get(topicName);
        if (ws?.readyState === WebSocket.OPEN) {
            ws.close();
        }
        this.topicWebSockets.delete(topicName);
    }

    getReconnectAttempts(topicName) {
        return this.reconnectAttempts.get(topicName) || 0;
    }

    incrementReconnectAttempts(topicName) {
        this.reconnectAttempts.set(topicName, this.getReconnectAttempts(topicName) + 1);
    }

    resetReconnectAttempts(topicName) {
        this.reconnectAttempts.set(topicName, 0);
    }

    // A live entry from a WebSocket: prepend it if it belongs in the current
    // view and we haven't already rendered it (a page fetch may race the push).
    handleLiveEntry(entry) {
        if (this.feedFilter && entry.topic !== this.feedFilter) return;
        if (this.feedEngine && !(entry.engines || []).includes(this.feedEngine)) return;
        if (entry.id != null && this.feedIds.has(entry.id)) return;

        if (entry.id != null) this.feedIds.add(entry.id);
        const list = document.getElementById('news-list');
        list.prepend(this.buildNewsItem(entry, true));
        this.refreshFeedStatus();
    }

    // Reset and reload the feed from the newest entry. Called on first load and
    // whenever the topic filter changes.
    resetFeed() {
        this.feedRequestToken += 1;
        this.feedCursor = null;
        this.feedHasMore = true;
        this.feedLoading = false;
        this.feedError = false;
        this.feedIds.clear();
        document.getElementById('news-list').innerHTML = '';
        this.refreshFeedStatus();
        this.loadFeedPage();
    }

    async loadFeedPage() {
        if (this.feedLoading || !this.feedHasMore) return;
        this.feedLoading = true;
        const token = this.feedRequestToken;
        this.refreshFeedStatus();

        const params = new URLSearchParams({ limit: this.feedPageSize });
        if (this.feedCursor != null) params.set('before_id', this.feedCursor);
        if (this.feedEngine) params.set('engine', this.feedEngine);
        const path = this.feedFilter
            ? `/news/${encodeURIComponent(this.feedFilter)}`
            : '/news';

        try {
            const response = await this.fetchWithAuth(`${this.apiBase}${path}?${params}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();

            // A reset (filter change) happened mid-flight — discard this page.
            if (token !== this.feedRequestToken) return;

            this.feedError = false;  // recovered after a prior failure
            const list = document.getElementById('news-list');
            for (const entry of data.entries) {
                if (entry.id != null && this.feedIds.has(entry.id)) continue;
                if (entry.id != null) this.feedIds.add(entry.id);
                list.appendChild(this.buildNewsItem(entry, false));
            }

            this.feedCursor = data.next_before_id;
            this.feedHasMore = data.next_before_id != null;
        } catch (error) {
            console.error('Failed to load news feed:', error);
            if (token === this.feedRequestToken) this.feedError = true;
        } finally {
            if (token === this.feedRequestToken) {
                this.feedLoading = false;
                this.refreshFeedStatus();
                // Auto-fill: if the page didn't fill the scroll area there's
                // nothing to scroll, so pull the next page until it does. Never
                // auto-fill after an error: the sentinel stays in view on an
                // empty list, so retrying without a gate becomes a request
                // storm (a single failing request would otherwise hammer the
                // API). The IntersectionObserver still retries on user scroll.
                const container = document.getElementById('news-container');
                if (
                    this.feedHasMore && !this.feedError &&
                    container.scrollHeight <= container.clientHeight
                ) {
                    this.loadFeedPage();
                }
            }
        }
    }

    buildNewsItem(entry, isLive) {
        const template = document.getElementById('news-item-template');
        const node = template.content.firstElementChild.cloneNode(true);

        node.querySelector('.news-title').textContent = entry.title;
        node.querySelector('.news-time').textContent = this.formatTime(entry.scraped_at);
        node.querySelector('.news-topic').textContent = entry.topic;
        node.querySelector('.news-link').href = entry.url;
        node.querySelector('.news-source').textContent = entry.source || entry.domain || '';

        // Excerpt/snippet under the headline; drop the element when absent.
        const excerpt = node.querySelector('.news-excerpt');
        if (entry.snippet) {
            excerpt.textContent = entry.snippet;
        } else {
            excerpt.remove();
        }

        // Badge each engine that surfaced this entry (orthogonal to the topic).
        const engineBox = node.querySelector('.news-engines');
        engineBox.replaceChildren();
        for (const engine of entry.engines || []) {
            const badge = document.createElement('span');
            badge.className = 'engine-badge';
            badge.dataset.engine = engine;
            badge.textContent = engine;
            engineBox.appendChild(badge);
        }

        if (isLive) node.classList.add('news-item--live');
        return node;
    }

    // The status row under the list doubles as empty state, loading spinner,
    // and the end-of-stream marker.
    refreshFeedStatus() {
        const status = document.getElementById('feed-status');
        const hasItems = this.feedIds.size > 0;

        if (this.feedError && !hasItems) {
            status.textContent = "Couldn't load the feed. It will retry as you scroll.";
            return;
        }
        if (this.feedLoading) {
            status.textContent = 'loading…';
            return;
        }
        if (!hasItems) {
            if (this.feedFilter || this.feedEngine) {
                const parts = [];
                if (this.feedFilter) parts.push(`"${this.feedFilter}"`);
                if (this.feedEngine) parts.push(`engine ${this.feedEngine}`);
                status.textContent = `No news yet for ${parts.join(' · ')}.`;
            } else {
                status.textContent = 'No news yet. Add a topic above and entries will stream in as they’re scraped.';
            }
            return;
        }
        if (!this.feedHasMore) {
            status.textContent = 'You’ve reached the earliest entry.';
            return;
        }
        status.textContent = '';
    }

    startStatusUpdates() {
        // Refresh the masthead status strip on the configured cadence, and pick
        // up any engines that started producing data.
        setInterval(() => {
            this.updateStatus();
            this.loadEngines();
        }, this.statusPollIntervalMs);
    }

    formatTime(timestamp) {
        // Parse the timestamp properly - append 'Z' to treat as UTC if no timezone
        const dateStr = timestamp.includes('Z') || timestamp.includes('+') || timestamp.includes('-', 10)
            ? timestamp
            : timestamp + 'Z';

        const date = new Date(dateStr);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);

        if (Number.isNaN(date.getTime())) {
            console.error('Invalid timestamp format:', timestamp);
            return 'Invalid time';
        }

        if (diffMins < 1) {
            return 'Just now';
        } else if (diffMins < 60) {
            return `${diffMins}m ago`;
        } else if (diffHours < 24) {
            return `${diffHours}h ago`;
        } else if (diffDays < 7) {
            return `${diffDays}d ago`;
        } else {
            return date.toLocaleDateString();
        }
    }

    showSuccess(message) {
        this.showToast(message, 'success');
    }

    showError(message) {
        this.showToast(message, 'error');
    }

    // A wire-desk styled confirmation, replacing the native confirm() dialog.
    // Resolves true if confirmed, false on cancel/Esc/backdrop.
    confirmDialog({ title, body, confirmLabel = 'confirm', cancelLabel = 'cancel' }) {
        return new Promise((resolve) => {
            const dialog = document.createElement('dialog');
            dialog.className = 'modal';

            const heading = document.createElement('h3');
            heading.className = 'modal__title';
            heading.textContent = title;

            const text = document.createElement('p');
            text.className = 'modal__body';
            text.textContent = body;   // textContent: topic names are user input

            const actions = document.createElement('div');
            actions.className = 'modal__actions';
            const cancel = document.createElement('button');
            cancel.className = 'modal__btn';
            cancel.textContent = cancelLabel;
            const confirm = document.createElement('button');
            confirm.className = 'modal__btn modal__btn--danger';
            confirm.textContent = confirmLabel;
            actions.append(cancel, confirm);

            dialog.append(heading, text, actions);
            document.body.appendChild(dialog);

            const close = (value) => {
                dialog.close();
                dialog.remove();
                resolve(value);
            };
            cancel.addEventListener('click', () => close(false));
            confirm.addEventListener('click', () => close(true));
            dialog.addEventListener('cancel', (e) => { e.preventDefault(); close(false); });
            dialog.addEventListener('click', (e) => { if (e.target === dialog) close(false); });

            dialog.showModal();
            confirm.focus();
        });
    }

    showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        // textContent, not innerHTML: messages embed raw user input (topic
        // names) and must not be parsed as HTML.
        toast.textContent = message;
        document.body.appendChild(toast);

        setTimeout(() => {
            toast.classList.add('toast--out');
            setTimeout(() => toast.remove(), 250);
        }, 4000);
    }
}

// Initialize the app when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new TopicStreamsApp();
});