from datetime import datetime, timezone


def get_widget_data(widget_id: str) -> dict:
    return {
        "widgetId": widget_id,
        "type": "summary",
        "title": "文本清洗",
        "data": {
            "status": "ready",
            "description": "清理空白、空行和大小写转换 API 已就绪"
        },
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
