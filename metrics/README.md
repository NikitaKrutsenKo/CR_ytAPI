# Metric Engine

Цей пакет зарезервований для обчислювального блоку. Він не повинен напряму імпортувати YouTube API client.

Пропонована залежність:

```text
models.YouTubeBatch -> metrics.* -> MetricsSnapshot + MetricsState
```
