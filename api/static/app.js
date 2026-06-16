// TopicStreams Frontend Application
class TopicStreamsApp {
    apiBase = '/api/v1';
    feedPageSize = 20;
    topics = new Map();
    activeTopicSubscriptions = new Set();
    topicWebSockets = new Map();
    reconnectAttempts = new Map();
    apiKey = localStorage.getItem('topicstreams-api-key') || '';

    // Real-time feed: a single chronological stream backed by the DB. Live
    // WebSocket entries prepend at the top; scrolling loads older pages via an
    // id cursor (feedCursor = next_before_id from the API).
    feedFilter = '';            // '' = all topics, else a topic name
    feedCursor = null;          // id cursor for the next (older) page
    feedHasMore = true;
    feedLoading = false;
    feedError = false;
    feedIds = new Set();        // rendered entry ids, for dedup
    feedRequestToken = 0;       // guards against stale responses after a reset

    constructor() {
        this.init();
    }

    init() {
        this.bindEvents();
        this.setupFeedObserver();
        this.loadInitialData();
        this.startStatusUpdates();
    }

    bindEvents() {
        // Topic management
        document.getElementById('add-topic-btn').addEventListener('click', () => this.addTopic());
        document.getElementById('topic-input').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') this.addTopic();
        });

        // Feed controls
        document.getElementById('topic-filter').addEventListener('change', () => {
            this.feedFilter = document.getElementById('topic-filter').value;
            this.resetFeed();
        });
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

    // Write endpoints require X-API-Key when the server has API_KEY set.
    // On 401, prompt once for the key, remember it, and retry.
    async fetchWithAuth(url, options = {}) {
        const doFetch = () => {
            const headers = { ...(options.headers || {}) };
            if (this.apiKey) headers['X-API-Key'] = this.apiKey;
            return fetch(url, { ...options, headers });
        };

        let response = await doFetch();
        if (response.status === 401) {
            const key = prompt('This action requires an API key. Enter API key:');
            if (key?.trim()) {
                this.apiKey = key.trim();
                localStorage.setItem('topicstreams-api-key', this.apiKey);
                response = await doFetch();
            }
        }
        return response;
    }

    async loadInitialData() {
        await Promise.all([
            this.loadTopics(),
            this.updateStatus()
        ]);
        this.resetFeed();
    }

    async loadTopics() {
        try {
            const response = await fetch(`${this.apiBase}/topics`);
            const topics = await response.json();

            this.topics.clear();
            for (const topic of topics) {
                this.topics.set(topic.name, topic);
            }

            this.renderTopics();
            this.syncSubscriptions();
            this.updateTopicFilter();
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

    async updateStatus() {
        try {
            const topicsResponse = await fetch(`${this.apiBase}/topics`);
            const topics = await topicsResponse.json();
            const activeNames = new Set(topics.filter(t => t.is_active).map(t => t.name));

            // Get total news count (sum of all topics)
            let totalNews = 0;
            for (const topic of topics) {
                try {
                    const newsResponse = await fetch(`${this.apiBase}/news/${encodeURIComponent(topic.name)}?limit=1`);
                    const newsData = await newsResponse.json();
                    totalNews += newsData.total;
                } catch (error) {
                    console.warn(`Failed to fetch news count for topic "${topic.name}":`, error);
                }
            }

            // Limit must cover at least one full scrape cycle so we can judge the
            // latest outcome per topic; one log is written per topic per cycle.
            const logsResponse = await fetch(`${this.apiBase}/logs?limit=30`);
            const logs = await logsResponse.json();

            document.getElementById('active-topics').textContent = activeNames.size;
            document.getElementById('total-news').textContent = totalNews;
            this.applyScraperHealth(logs, activeNames);
        } catch (error) {
            console.error('Failed to update status:', error);
            this.setStatus('offline', 'offline', "Can't reach the API");
        }
    }

    // Derive a real health signal from recent scraper logs: are scrapes recent
    // and succeeding? Each log is one scrape attempt. This is the single
    // indicator that replaces the removed transmission-log panel.
    applyScraperHealth(logs, activeNames) {
        if (!logs.length) {
            this.setStatus('idle', 'idle', 'No scrapes recorded yet');
            return;
        }

        const last = logs[0];

        // Staleness adapts to the observed cadence instead of a fixed cutoff:
        // the largest gap between recent scrapes approximates one cycle, and we
        // flag "stalled" only after roughly three of them have been missed.
        const ageMs = Date.now() - this.parseScrapedAt(last.scraped_at);
        if (ageMs > this.staleThresholdMs(logs)) {
            this.setStatus('stalled', 'stalled', `No scrape since ${this.formatTime(last.scraped_at)}`);
            return;
        }

        // Judge by the latest scrape of each watched topic, so one failing feed
        // surfaces as a partial outage ("degraded") rather than hiding inside a
        // mostly-green window or dragging everything to "errors".
        const latest = new Map();
        for (const log of logs) {                       // newest first
            if (activeNames && activeNames.size && !activeNames.has(log.topic)) continue;
            if (!latest.has(log.topic)) latest.set(log.topic, log);
        }
        const tracked = Array.from(latest.values());
        if (!tracked.length) {
            this.setStatus('live', 'live', 'Scraping cleanly');
            return;
        }

        const failed = tracked.filter(l => !l.success);
        if (failed.length === tracked.length) {
            this.setStatus('error', 'errors',
                `All ${tracked.length} feeds failing — ${this.scrapeFailReason(failed[0])}`);
        } else if (failed.length > 0) {
            this.setStatus('degraded', 'degraded',
                `${failed.length} of ${tracked.length} feeds failing: ${failed.map(l => l.topic).join(', ')}`);
        } else {
            this.setStatus('live', 'live', `All ${tracked.length} feeds scraping cleanly`);
        }
    }

    // Expected cycle period ≈ the largest gap between recent scrapes; allow
    // ~3 missed cycles before "stalled", clamped to a sane 5–30 min window.
    staleThresholdMs(logs) {
        let maxGap = 0;
        for (let i = 0; i < logs.length - 1; i++) {
            const gap = this.parseScrapedAt(logs[i].scraped_at) - this.parseScrapedAt(logs[i + 1].scraped_at);
            if (gap > maxGap) maxGap = gap;
        }
        if (!maxGap) return 15 * 60 * 1000;             // too few logs to estimate
        return Math.min(30 * 60 * 1000, Math.max(5 * 60 * 1000, maxGap * 3));
    }

    scrapeFailReason(log) {
        return log.error_message
            || (log.http_status_code ? `HTTP ${log.http_status_code}` : 'scrape failed');
    }

    setStatus(state, label, detail) {
        const onair = document.querySelector('.onair');
        if (onair) {
            onair.dataset.state = state;
            onair.title = detail || '';
        }
        document.getElementById('scraper-status').textContent = label;
    }

    parseScrapedAt(timestamp) {
        const s = timestamp.includes('Z') || timestamp.includes('+') || timestamp.includes('-', 10)
            ? timestamp
            : timestamp + 'Z';
        return new Date(s).getTime();
    }

    renderTopics() {
        const container = document.getElementById('topic-cards');
        const activeTopics = Array.from(this.topics.values()).filter(t => t.is_active);

        if (activeTopics.length === 0) {
            container.innerHTML = '<div class="no-news">No active topics. Add a topic to get started!</div>';
            return;
        }

        const template = document.getElementById('topic-card-template');
        container.innerHTML = '';

        for (const topic of activeTopics) {
            const card = template.content.cloneNode(true);

            card.querySelector('.topic-name').textContent = topic.name;

            const deleteBtn = card.querySelector('.delete-btn');
            deleteBtn.addEventListener('click', () => this.deleteTopic(topic.name));

            container.appendChild(card);
        }
    }

    updateTopicFilter() {
        const select = document.getElementById('topic-filter');
        const currentValue = select.value;

        select.innerHTML = '<option value="">All topics</option>';

        // Every active topic is watched, so the filter lists them all.
        const topicNames = Array.from(this.topics.values())
            .filter(t => t.is_active)
            .map(t => t.name)
            .sort((a, b) => a.localeCompare(b));

        for (const topicName of topicNames) {
            const option = document.createElement('option');
            option.value = topicName;
            option.textContent = topicName;
            select.appendChild(option);
        }

        // Keep the current selection if it still exists, else fall back to All.
        select.value = currentValue && topicNames.includes(currentValue) ? currentValue : '';

        // If the filtered topic was just deleted, drop back to the all-topics
        // stream so the feed doesn't keep showing a dead filter.
        if (this.feedFilter && !topicNames.includes(this.feedFilter)) {
            this.feedFilter = '';
            this.resetFeed();
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
        this.updateTopicFilter();
    }

    unsubscribeFromTopic(topicName) {
        this.activeTopicSubscriptions.delete(topicName);
        this.closeTopicWebSocket(topicName);
        this.resetReconnectAttempts(topicName);
        this.updateTopicFilter();
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
                const backoffTime = Math.min(30000, 5000 * Math.pow(2, this.getReconnectAttempts(topicName)));
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
        const path = this.feedFilter
            ? `/news/${encodeURIComponent(this.feedFilter)}`
            : '/news';

        try {
            const response = await fetch(`${this.apiBase}${path}?${params}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();

            // A reset (filter change) happened mid-flight — discard this page.
            if (token !== this.feedRequestToken) return;

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
                // nothing to scroll, so pull the next page until it does.
                const container = document.getElementById('news-container');
                if (this.feedHasMore && container.scrollHeight <= container.clientHeight) {
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
        // NewsEntry has no snippet field; show the source instead.
        node.querySelector('.news-snippet').textContent = entry.source || entry.domain || '';

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
            status.textContent = this.feedFilter
                ? `No news yet for "${this.feedFilter}".`
                : 'No news yet. Add a topic above and entries will stream in as they’re scraped.';
            return;
        }
        if (!this.feedHasMore) {
            status.textContent = 'You’ve reached the earliest entry.';
            return;
        }
        status.textContent = '';
    }

    startStatusUpdates() {
        // Refresh the masthead status strip every 30 seconds.
        setInterval(() => {
            this.updateStatus();
        }, 30000);
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