// TopicStreams Ops / Monitor — scrapes scrape observability from /api/v1/metrics.
// Vanilla, mirroring app.js conventions: textContent everywhere (no innerHTML on
// server values), template clones for repeating rows, polls on /api/v1/config.
class MonitorApp {
    apiBase = '/api/v1';
    pollIntervalMs = 30000;
    windowSeconds = 3600;

    constructor() {
        this.init();
    }

    async init() {
        const sel = document.getElementById('window-select');
        // Restore the last window choice so a refresh keeps the view.
        try {
            const saved = localStorage.getItem('ts-monitor-window');
            if (saved && [...sel.options].some((o) => o.value === saved)) sel.value = saved;
        } catch (e) {
            /* localStorage unavailable */
        }
        this.windowSeconds = parseInt(sel.value, 10);
        sel.addEventListener('change', () => {
            this.windowSeconds = parseInt(sel.value, 10);
            try {
                localStorage.setItem('ts-monitor-window', String(this.windowSeconds));
            } catch (e) {
                /* localStorage unavailable */
            }
            this.refresh();
        });

        try {
            const r = await fetch(`${this.apiBase}/config`);
            if (r.ok) this.pollIntervalMs = (await r.json()).status_poll_interval_ms;
        } catch (e) {
            console.error('Failed to load UI config, using defaults:', e);
        }

        await this.refresh();
        setInterval(() => this.refresh(), this.pollIntervalMs);
    }

    async refresh() {
        try {
            const r = await fetch(`${this.apiBase}/metrics?window=${this.windowSeconds}`);
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const m = await r.json();
            this.render(m);
            document.getElementById('last-updated').textContent =
                'updated ' + this.clockTime(m.generated_at);
        } catch (e) {
            console.error('metrics fetch failed:', e);
            document.getElementById('last-updated').textContent = 'update failed — retrying…';
        }
    }

    render(m) {
        this.renderKpis(m);
        this.renderEngines(m.engines || []);
        this.renderCycles(m.recent_cycles || []);
        this.renderFailures(m.recent_failures || []);
    }

    renderKpis(m) {
        const o = m.overall || {};
        this.setText('kpi-topics', this.fmtInt(m.active_topics));
        this.setText('kpi-filed', this.fmtInt(m.total_news));
        this.setText('kpi-success', this.fmtPct(o.success_rate));
        this.setText('kpi-fresh', this.fmtFresh(m.feed_freshness_seconds));
        const cyc = (m.recent_cycles && m.recent_cycles[0]) || null;
        this.setText('kpi-cycle', cyc ? `${this.fmtDur(cyc.duration_seconds)}` : '–');
        this.setText(
            'kpi-scrapes',
            `${this.fmtInt(o.scrapes)} (${this.fmtInt(o.blocked)} / ${this.fmtInt(o.failures)})`
        );
    }

    renderEngines(engines) {
        const body = document.getElementById('engines-body');
        body.replaceChildren();
        if (!engines.length) {
            const td = document.createElement('td');
            td.colSpan = 11;
            td.className = 'muted center';
            td.textContent = 'no scrapes in this window';
            body.appendChild(this.rowFromCells(td));
            return;
        }
        const tpl = document.getElementById('engine-row-template');
        for (const e of engines) {
            const row = tpl.content.firstElementChild.cloneNode(true);
            row.querySelector('.edot').dataset.health = e.health;
            row.querySelector('.ename').textContent = e.engine;

            const hl = row.querySelector('.ehealth');
            hl.textContent = e.health;
            hl.dataset.health = e.health;
            row.querySelector('.c-health').dataset.health = e.health;

            row.querySelector('.c-scrapes').textContent = this.fmtInt(e.scrapes);
            row.querySelector('.c-success').textContent = this.fmtPct(e.success_rate);
            row.querySelector('.c-latency').textContent = `${this.fmtMs(e.avg_latency_ms)} / ${this.fmtMs(e.p95_latency_ms)}`;
            row.querySelector('.c-items').textContent = this.fmtInt(e.entries_parsed);

            const zp = row.querySelector('.c-zparse');
            zp.textContent = e.zero_parse || '';
            zp.classList.toggle('hot', e.zero_parse > 0);

            const bk = row.querySelector('.c-blocks');
            bk.textContent = e.blocked || '';
            bk.classList.toggle('hot', e.blocked > 0);

            const fl = row.querySelector('.c-fails');
            fl.textContent = e.failures || '';
            fl.classList.toggle('warm', e.failures > 0);

            const st = row.querySelector('.estatus');
            st.textContent = e.last_http_status == null ? '–' : String(e.last_http_status);
            st.dataset.state = this.statusState(e.last_http_status);
            if (e.http_status_breakdown) {
                const parts = Object.entries(e.http_status_breakdown)
                    .map(([code, n]) => `${code}: ${n}`)
                    .join('  ');
                st.title = parts;
            }

            row.querySelector('.elast').textContent = this.relTime(e.last_scrape_at);
            body.appendChild(row);
        }
    }

    renderCycles(cycles) {
        // Sparkline: oldest → newest (left → right), height scaled to the max.
        const spark = document.getElementById('cycle-spark');
        spark.replaceChildren();
        const ordered = cycles.slice(0, 30).reverse(); // oldest first
        if (ordered.length) {
            const max = Math.max(...ordered.map((c) => c.duration_seconds), 1);
            for (const c of ordered) {
                const bar = document.createElement('span');
                bar.className = 'spark__bar';
                bar.dataset.ok = c.success ? '1' : '0';
                bar.style.height = `${Math.max(8, (c.duration_seconds / max) * 100)}%`;
                bar.title = `${this.fmtDur(c.duration_seconds)} · ${this.relTime(c.started_at)}${c.success ? '' : ' · failed'}`;
                spark.appendChild(bar);
            }
        }

        const list = document.getElementById('cycles-list');
        list.replaceChildren();
        if (!cycles.length) {
            const d = document.createElement('div');
            d.className = 'muted';
            d.textContent = 'no cycles recorded yet';
            list.appendChild(d);
            return;
        }
        const tpl = document.getElementById('cycle-row-template');
        for (const c of cycles) {
            const node = tpl.content.firstElementChild.cloneNode(true);
            node.querySelector('.cycle__dot').dataset.ok = c.success ? '1' : '0';
            node.querySelector('.cycle__when').textContent = this.relTime(c.started_at);
            node.querySelector('.cycle__dur').textContent = this.fmtDur(c.duration_seconds);
            node.querySelector('.cycle__meta').textContent =
                `${c.topics_count} topics · ${c.entries_parsed} parsed · ${c.new_events} new`;
            if (c.error) node.title = c.error;
            list.appendChild(node);
        }
    }

    renderFailures(failures) {
        const list = document.getElementById('failures-list');
        list.replaceChildren();
        if (!failures.length) {
            const d = document.createElement('div');
            d.className = 'muted';
            d.textContent = 'no recent failures 🟢';
            list.appendChild(d);
            return;
        }
        const tpl = document.getElementById('failure-row-template');
        for (const f of failures) {
            const node = tpl.content.firstElementChild.cloneNode(true);
            node.querySelector('.failure__time').textContent = this.relTime(f.scraped_at);
            const badge = node.querySelector('.failure__engine');
            badge.textContent = f.engine;
            badge.dataset.engine = f.engine;
            node.querySelector('.failure__topic').textContent = f.topic;
            const st = node.querySelector('.failure__status');
            st.textContent = f.http_status_code == null ? 'err' : String(f.http_status_code);
            st.dataset.state = this.statusState(f.http_status_code);
            node.querySelector('.failure__msg').textContent = f.error_message || '';
            list.appendChild(node);
        }
    }

    // ── formatting helpers ────────────────────────────────────────────────

    setText(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    rowFromCells(...cells) {
        const tr = document.createElement('tr');
        for (const c of cells) tr.appendChild(c);
        return tr;
    }

    fmtInt(n) {
        return n == null ? '–' : Number(n).toLocaleString();
    }

    fmtPct(rate) {
        return rate == null ? '–' : `${Math.round(rate * 100)}%`;
    }

    fmtMs(ms) {
        if (ms == null) return '–';
        return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
    }

    fmtFresh(s) {
        if (s == null) return '–';
        if (s < 60) return `${Math.round(s)}s`;
        if (s < 3600) return `${Math.round(s / 60)}m`;
        return `${(s / 3600).toFixed(1)}h`;
    }

    fmtDur(sec) {
        return `${Number(sec).toFixed(1)}s`;
    }

    statusState(code) {
        if (code == null) return 'unknown';
        if (code === 200) return 'ok';
        if ([429, 403, 503].includes(code)) return 'blocked';
        if (code >= 400) return 'error';
        return 'ok';
    }

    // Relative time from an ISO/naive-UTC timestamp.
    relTime(ts) {
        if (!ts) return '–';
        const dateStr =
            ts.includes('Z') || ts.includes('+') || ts.includes('-', 10) ? ts : ts + 'Z';
        const date = new Date(dateStr);
        if (Number.isNaN(date.getTime())) return '–';
        const diffMs = Date.now() - date.getTime();
        const mins = Math.floor(diffMs / 60000);
        const hours = Math.floor(diffMs / 3600000);
        const days = Math.floor(diffMs / 86400000);
        if (diffMs < 0) return 'just now'; // clock skew
        if (mins < 1) return 'just now';
        if (mins < 60) return `${mins}m ago`;
        if (hours < 24) return `${hours}h ago`;
        if (days < 7) return `${days}d ago`;
        return date.toLocaleDateString();
    }

    clockTime(ts) {
        if (!ts) return '–';
        const dateStr =
            ts.includes('Z') || ts.includes('+') || ts.includes('-', 10) ? ts : ts + 'Z';
        const date = new Date(dateStr);
        if (Number.isNaN(date.getTime())) return '–';
        return date.toLocaleTimeString();
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new MonitorApp();
});
