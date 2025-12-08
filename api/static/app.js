// TopicStreams Frontend Application
class TopicStreamsApp {
    apiBase = '/api/v1';
    maxNewsItems = 50;
    topics = new Map();
    newsItems = [];
    activeTopicSubscriptions = new Set();

    constructor() {
        this.init();
    }

    init() {
        this.bindEvents();
        this.loadInitialData();
        this.initWebSocket();
        this.startStatusUpdates();
    }

    bindEvents() {
        // Topic management
        document.getElementById('add-topic-btn').addEventListener('click', () => this.addTopic());
        document.getElementById('topic-input').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') this.addTopic();
        });

        // Feed controls
        document.getElementById('clear-feed').addEventListener('click', () => this.clearFeed());
        document.getElementById('topic-filter').addEventListener('change', () => this.filterNews());
    }

    async loadInitialData() {
        await Promise.all([
            this.loadTopics(),
            this.loadLogs(),
            this.updateStatus()
        ]);
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
            this.updateTopicFilter();
        } catch (error) {
            console.error('Failed to load topics:', error);
            this.showError('Failed to load topics');
        }
    }

    async loadLogs() {
        try {
            const response = await fetch(`${this.apiBase}/logs?limit=50`);
            const logs = await response.json();
            this.renderLogs(logs);
        } catch (error) {
            console.error('Failed to load logs:', error);
        }
    }

    async updateStatus() {
        try {
            // Get topics count
            const topicsResponse = await fetch(`${this.apiBase}/topics`);
            const topics = await topicsResponse.json();
            const activeTopics = topics.filter(t => t.is_active).length;

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

            // Get last scrape info from logs
            const logsResponse = await fetch(`${this.apiBase}/logs?limit=1`);
            const logs = await logsResponse.json();
            const lastScrape = logs.length > 0 ? this.formatTime(logs[0].scraped_at) : 'Never';

            // Update status display
            document.getElementById('active-topics').textContent = activeTopics;
            document.getElementById('total-news').textContent = totalNews;
            document.getElementById('last-scrape').textContent = lastScrape;
            document.getElementById('scraper-status').innerHTML =
                `<i class="fas fa-check-circle success"></i> <span class="success">Active</span>`;

        } catch (error) {
            console.error('Failed to update status:', error);
            document.getElementById('scraper-status').innerHTML =
                `<i class="fas fa-exclamation-triangle error"></i> <span class="error">Error</span>`;
        }
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

            const subscribeBtn = card.querySelector('.subscribe-btn');
            const deleteBtn = card.querySelector('.delete-btn');

            if (this.activeTopicSubscriptions.has(topic.name)) {
                subscribeBtn.innerHTML = '<i class="fas fa-minus"></i>';
                subscribeBtn.classList.add('subscribed');
                subscribeBtn.title = 'Remove from real-time feed';
            } else {
                subscribeBtn.title = 'Add to real-time feed';
            }

            subscribeBtn.addEventListener('click', () => this.toggleTopicSubscription(topic.name, subscribeBtn));
            deleteBtn.addEventListener('click', () => this.deleteTopic(topic.name));

            container.appendChild(card);
        }
    }

    updateTopicFilter() {
        const select = document.getElementById('topic-filter');
        const currentValue = select.value;

        select.innerHTML = '<option value="">All Watched Topics</option>';

        // Only show subscribed topics in the filter
        const subscribedTopics = Array.from(this.activeTopicSubscriptions).sort((a, b) => a.localeCompare(b));

        if (subscribedTopics.length === 0) {
            const option = document.createElement('option');
            option.value = "";
            option.textContent = "No topics being watched";
            option.disabled = true;
            select.appendChild(option);
        } else {
            for (const topicName of subscribedTopics) {
                const option = document.createElement('option');
                option.value = topicName;
                option.textContent = topicName;
                select.appendChild(option);
            }
        }

        // Keep current value if it's still valid
        if (currentValue && subscribedTopics.includes(currentValue)) {
            select.value = currentValue;
        } else {
            select.value = "";
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
        button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Adding...';

        try {
            const response = await fetch(`${this.apiBase}/topics`, {
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
            button.innerHTML = '<i class="fas fa-plus"></i> Add Topic';
        }
    }

    async deleteTopic(topicName) {
        if (!confirm(`Are you sure you want to delete the topic "${topicName}"?`)) {
            return;
        }

        try {
            const response = await fetch(`${this.apiBase}/topics/${encodeURIComponent(topicName)}`, {
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

    toggleTopicSubscription(topicName, button) {
        if (this.activeTopicSubscriptions.has(topicName)) {
            this.unsubscribeFromTopic(topicName);
            button.innerHTML = '<i class="fas fa-plus"></i>';
            button.classList.remove('subscribed');
            button.title = 'Add to real-time feed';
        } else {
            this.subscribeToTopic(topicName);
            button.innerHTML = '<i class="fas fa-minus"></i>';
            button.classList.add('subscribed');
            button.title = 'Remove from real-time feed';
        }
    }

    subscribeToTopic(topicName) {
        if (this.activeTopicSubscriptions.has(topicName)) {
            return; // Already subscribed
        }

        // Connect to WebSocket for this topic
        this.connectTopicWebSocket(topicName);
        this.activeTopicSubscriptions.add(topicName);
        this.showSuccess(`Added "${topicName}" to real-time feed`);
        this.updateTopicFilter();
    }

    unsubscribeFromTopic(topicName) {
        this.activeTopicSubscriptions.delete(topicName);
        this.closeTopicWebSocket(topicName);
        this.resetReconnectAttempts(topicName);
        this.showSuccess(`Removed "${topicName}" from real-time feed`);
        this.updateTopicFilter();
    }

    initWebSocket() {
        // WebSocket connections are managed per topic subscription
    }

    connectTopicWebSocket(topicName) {
        // Close existing WebSocket if it exists
        this.closeTopicWebSocket(topicName);

        const wsUrl = `ws://localhost:5000/api/v1/ws/news/${encodeURIComponent(topicName)}`;
        const ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            console.log(`Connected to WebSocket for topic: ${topicName}`);
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                // The WebSocket manager sends raw NewsEntry objects directly
                if (data?.title && data?.url) {
                    this.addNewsItem(data);
                }
            } catch (error) {
                console.error('Failed to parse WebSocket message:', error);
            }
        };

        ws.onclose = () => {
            console.log(`Disconnected from WebSocket for topic: ${topicName}`);
            // Clear the WebSocket reference
            if (this.topicWebSockets?.has(topicName)) {
                this.topicWebSockets.delete(topicName);
            }

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

        // Store WebSocket reference
        if (!this.topicWebSockets) {
            this.topicWebSockets = new Map();
        }
        if (!this.reconnectAttempts) {
            this.reconnectAttempts = new Map();
        }
        this.topicWebSockets.set(topicName, ws);
        this.resetReconnectAttempts(topicName);
    }

    closeTopicWebSocket(topicName) {
        if (this.topicWebSockets?.has(topicName)) {
            const ws = this.topicWebSockets.get(topicName);
            if (ws?.readyState === WebSocket.OPEN) {
                ws.close();
            }
            this.topicWebSockets.delete(topicName);
        }
    }

    getReconnectAttempts(topicName) {
        return this.reconnectAttempts ? (this.reconnectAttempts.get(topicName) || 0) : 0;
    }

    incrementReconnectAttempts(topicName) {
        if (this.reconnectAttempts) {
            const current = this.reconnectAttempts.get(topicName) || 0;
            this.reconnectAttempts.set(topicName, current + 1);
        }
    }

    resetReconnectAttempts(topicName) {
        if (this.reconnectAttempts) {
            this.reconnectAttempts.set(topicName, 0);
        }
    }

    addNewsItem(newsItem) {
        this.newsItems.unshift(newsItem);
        if (this.newsItems.length > this.maxNewsItems) {
            this.newsItems = this.newsItems.slice(0, this.maxNewsItems);
        }

        this.renderNews();
    }

    renderNews() {
        const container = document.getElementById('news-container');
        const filter = document.getElementById('topic-filter').value;

        const filteredNews = filter
            ? this.newsItems.filter(item => item.topic === filter)
            : this.newsItems;

        if (filteredNews.length === 0) {
            if (this.activeTopicSubscriptions.size === 0) {
                container.innerHTML = '<div class="no-news">No topics being watched. Click the "+" button on topics to start watching real-time updates.</div>';
            } else if (filter) {
                container.innerHTML = `<div class="no-news">No recent news for "${filter}".</div>`;
            } else {
                container.innerHTML = '<div class="no-news">No recent news from watched topics. News updates will appear here when available.</div>';
            }
            return;
        }

        const template = document.getElementById('news-item-template');
        container.innerHTML = '';

        for (const item of filteredNews) {
            const newsElement = template.content.cloneNode(true);

            newsElement.querySelector('.news-title').textContent = item.title;
            newsElement.querySelector('.news-time').textContent = this.formatTime(item.scraped_at);
            newsElement.querySelector('.news-topic').textContent = item.topic;
            newsElement.querySelector('.news-link').href = item.url;
            newsElement.querySelector('.news-snippet').textContent = item.snippet;

            container.appendChild(newsElement);
        }
    }

    filterNews() {
        this.renderNews();
    }

    clearFeed() {
        this.newsItems = [];
        this.renderNews();
        this.showSuccess('Feed cleared');
    }

    renderLogs(logs) {
        const container = document.getElementById('logs-container');

        if (logs.length === 0) {
            container.innerHTML = '<div class="no-news">No logs available</div>';
            return;
        }

        container.innerHTML = '';
        for (const log of logs) {
            const logEntry = document.createElement('div');
            logEntry.className = `log-entry ${log.success ? 'success' : 'error'}`;

            let statusMessage = '';
            if (log.success) {
                if (log.http_status_code) {
                    statusMessage = `Scrape successful (HTTP ${log.http_status_code})`;
                } else {
                    statusMessage = 'Scrape successful';
                }
            } else if (log.error_message) {
                statusMessage = `Error: ${log.error_message}`;
            } else if (log.http_status_code) {
                statusMessage = `HTTP error ${log.http_status_code}`;
            } else {
                statusMessage = 'Scrape failed';
            }

            logEntry.innerHTML = `
                <span class="log-time">${this.formatTime(log.scraped_at)}</span>
                <span class="log-topic">${log.topic}</span>
                <span class="log-message">${statusMessage}</span>
            `;

            container.appendChild(logEntry);
        }
    }


    startStatusUpdates() {
        // Update status every 30 seconds
        setInterval(() => {
            this.updateStatus();
            this.loadLogs();
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

    showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.innerHTML = `
            <i class="fas fa-${type === 'success' ? 'check-circle' : 'exclamation-triangle'}"></i>
            ${message}
        `;

        // Add toast styles if not already added
        if (!document.querySelector('#toast-styles')) {
            const style = document.createElement('style');
            style.id = 'toast-styles';
            style.textContent = `
                .toast {
                    position: fixed;
                    top: 20px;
                    right: 20px;
                    padding: 16px 20px;
                    background: white;
                    border-radius: 8px;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
                    z-index: 10000;
                    display: flex;
                    align-items: center;
                    gap: 10px;
                    max-width: 400px;
                    animation: slideIn 0.3s ease-out;
                }
                .toast.success {
                    border-left: 4px solid #10b981;
                    color: #10b981;
                }
                .toast.error {
                    border-left: 4px solid #ef4444;
                    color: #ef4444;
                }
                .toast.info {
                    border-left: 4px solid #3b82f6;
                    color: #3b82f6;
                }
            `;
            document.head.appendChild(style);
        }

        document.body.appendChild(toast);

        setTimeout(() => {
            toast.style.animation = 'slideOut 0.3s ease-out forwards';
            setTimeout(() => toast.remove(), 300);
        }, 4000);
    }
}

// Initialize the app when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    // noqa: S1848
    new TopicStreamsApp();
});

// Add slide out animation
if (!document.querySelector('#slide-out-styles')) {
    const style = document.createElement('style');
    style.id = 'slide-out-styles';
    style.textContent = `
        @keyframes slideOut {
            to {
                opacity: 0;
                transform: translateX(100%);
            }
        }
    `;
    document.head.appendChild(style);
}