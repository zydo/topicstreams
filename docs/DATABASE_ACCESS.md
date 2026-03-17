# Database Access

For debugging or manual inspection, you can access the PostgreSQL database directly using `psql`:

```bash
docker compose exec postgres psql -U newsuser -d newsdb
```

## Common SQL Queries

### View all active topics

```sql
SELECT id, name, created_at, is_active FROM topics WHERE is_active = TRUE ORDER BY created_at DESC;
```

### Count news entries per topic

```sql
SELECT topic, COUNT(*) as count FROM news_entries GROUP BY topic ORDER BY count DESC;
```

### View recent news entries for a topic

```sql
SELECT id, title, url, source, scraped_at
FROM news_entries
WHERE topic = 'artificial intelligence'
ORDER BY scraped_at DESC
LIMIT 10;
```

### Check scraper success rate

```sql
SELECT
    success,
    COUNT(*) as count,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) as percentage
FROM scraper_logs
GROUP BY success;
```

### View failed scrapes with error details

```sql
SELECT topic, scraped_at, http_status_code, error_message
FROM scraper_logs
WHERE success = FALSE
ORDER BY scraped_at DESC
LIMIT 20;
```

### Database size

```sql
SELECT pg_size_pretty(pg_database_size('newsdb')) as database_size;
```

### Table sizes

```sql
SELECT
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
```

## Backup and Restore

### Backup database

```bash
docker compose exec postgres pg_dump -U newsuser newsdb > backup.sql
```

### Restore database

```bash
docker compose exec -T postgres psql -U newsuser -d newsdb < backup.sql
```
