# SQL examples

Run with `observecontext query SQL`; use `query -` to read SQL from stdin. Limit time windows and page with stable ordering for large datasets. Check the response's truncation flag.

Slowest recent requests:

```sql
SELECT operation,service,request_id,method,route,duration_ms,status
FROM traces
WHERE started_at >= strftime('%Y-%m-%d %H:%M:%fZ','now','-1 hour')
ORDER BY duration_ms DESC,id LIMIT 30
```

Snapshot and SQL phase averages, restricted to server traces:

```sql
SELECT t.service,s.name,count(s.id) AS samples,avg(s.duration_ms) AS avg_ms,max(s.duration_ms) AS max_ms
FROM spans s JOIN traces t ON t.id=s.trace
WHERE t.service='peoplecontext' AND t.started_at >= strftime('%Y-%m-%d %H:%M:%fZ','now','-24 hours')
GROUP BY t.service,s.name ORDER BY avg_ms DESC
```

Compare client and server requests within an operation (replace the operation ID):

```sql
SELECT correlation_id,service,request_id,duration_ms,status,route
FROM traces WHERE operation='replace_operation_id'
ORDER BY started_at,service LIMIT 20
```

Drill into a request's phases (replace operation, service and request ID):

```sql
SELECT s.ordinal,s.name,s.offset_ms,s.duration_ms
FROM spans s JOIN traces t ON t.id=s.trace
WHERE t.operation='replace_operation_id' AND t.service='peoplecontext' AND t.request_id='0123456789abcdef0123456789abcdef'
ORDER BY s.ordinal
```

`sql.scan` includes SQLite stepping, so `sql.execute` alone understates query cost. Snapshot construction is measured separately. Source timestamps from different hosts can be skewed: compare durations and correlation, not timestamp subtraction. These observations can motivate a cache or DuckDB experiment but do not establish its benefit without a controlled comparison.

SQL applies requester visibility. Ordinary users see their uploads; an authorized broad viewer may see multiple owners with the same service/request ID. Operation IDs keep drill-downs separate. Correlation strings are untrusted matching metadata.
